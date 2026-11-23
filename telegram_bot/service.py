"""Authorized Telegram ingress with ephemeral sessions and mandatory router dispatch."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
import uuid

from router.core import Request, decide
from telegram_bot.api import TelegramError
from operations.activity import Activity


@dataclass
class Session:
    private: bool
    history: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Bot:
    def __init__(self, settings, api, router, audit, proactive=None):
        self.settings, self.api, self.router, self.audit = settings, api, router, audit
        self.proactive = proactive
        self.boot_id = uuid.uuid4().hex
        self.sessions = {}
        self.last_update = -1
        self.last_poll_success = None
        self.stop = asyncio.Event()
        self.slots = asyncio.Semaphore(settings.concurrency)

    def event(self, update_id, authorized, status, **fields):
        self.audit({'timestamp': datetime.now(timezone.utc).isoformat(),
                    'message': 'telegram_event', 'update_id': update_id, 'boot_id': self.boot_id,
                    'authorized': authorized, 'status': status,
                    'latency_ms': fields.pop('latency_ms', 0.0), **fields})

    def admit(self, update):
        """Synchronous admission before task creation: no duplicate can race inference."""
        event_id = update.get('update_id') if isinstance(update, dict) else None
        if type(event_id) is not int or event_id < 0:
            self.event(None, False, 'invalid_update', success=False)
            return None
        if event_id <= self.last_update:
            self.event(event_id, False, 'duplicate', success=False)
            return None
        self.last_update = event_id
        message = update.get('message')
        if not isinstance(message, dict):
            self.event(event_id, False, 'unsupported_update', success=False)
            return None
        user, chat = message.get('from'), message.get('chat')
        if not isinstance(user, dict) or not isinstance(chat, dict):
            self.event(event_id, False, 'invalid_identity', success=False)
            return None
        uid, cid = user.get('id'), chat.get('id')
        authorized = (type(uid) is int and uid in self.settings.allowed_users
                      and type(cid) is int and cid == uid and chat.get('type') == 'private'
                      and user.get('is_bot') is False and not message.get('sender_chat'))
        if not authorized:
            # No reply to unauthorized users/groups; never reflect their data or IDs.
            self.event(event_id, False, 'rejected', success=False)
            return None
        self.event(event_id, True, 'received')
        text = message.get('text')
        try:
            valid = (isinstance(text, str) and bool(text.strip()) and len(text) <= 4096
                     and len(text.encode('utf-8')) <= self.settings.max_input_bytes
                     and type(message.get('message_id')) is int
                     and not any(k in message for k in ('forward_origin', 'via_bot')))
        except UnicodeError:
            valid = False
        return event_id, cid, text if valid else None

    async def handle_update(self, update):
        admitted = self.admit(update)
        if admitted is not None:
            await self.handle_admitted(admitted)

    async def handle_admitted(self, admitted):
        event_id, chat_id, text = admitted
        started = time.monotonic()
        session = self.sessions.setdefault(chat_id, Session(self.settings.default_private))
        async with session.lock:
            if text is None:
                await self._finish(event_id, chat_id, 'invalid_message',
                                   'Please send a text message of at most 4096 characters and 12000 UTF-8 bytes.', started)
                return
            if text.startswith('/') and not self.router.handles_skill(text):
                await self._command(event_id, chat_id, session, text, started)
                return
            async with self.slots:
                if self.stop.is_set():
                    await self._finish(event_id, chat_id, 'stopping', 'Bot is stopping; please resend after restart.', started)
                    return
                self._trim(session, text)
                request = Request(text, session.private, [dict(m) for m in session.history], principal_id=chat_id)
                session.private = decide(request, self.router.policy).private
                request = Request(text, session.private, request.history, principal_id=chat_id)
                result = None
                try:
                    result = await self.dispatch_routed(request)
                except Exception:
                    # Never stringify model/worker exceptions or send raw provider errors.
                    pass
                if isinstance(result, dict) and result.get('private') is True:
                    session.private = True
                model_ok = isinstance(result, dict) and result.get('success') is True
                if isinstance(result, dict) and (result.get('skill_execution') or result.get('route')=='memory'):
                    answer = result['response']
                    status = 'responded' if model_ok else 'held' if result.get('status') == 'held' else 'skill_failed'
                elif model_ok and isinstance(result.get('response'), str) and result['response'].strip():
                    answer, status = result['response'], 'responded'
                elif isinstance(result, dict) and result.get('status') == 'held':
                    answer, status = 'This private request is held. Local inference is unavailable or its context budget was exceeded.', 'held'
                    model_ok = False
                else:
                    answer, status = 'The model could not complete this request. Please try again later.', 'model_failed'
                    model_ok = False
                delivered = await self._finish(event_id, chat_id, status, answer, started, result, model_ok)
                if model_ok and delivered:
                    history_answer = ('[External skill data was displayed but omitted from model context.]'
                                      if result.get('skill_execution') or result.get('route')=='memory' else answer)
                    session.history.extend([{'role': 'user', 'content': text}, {'role': 'assistant', 'content': history_answer}])
                    self._trim(session, '')

    async def dispatch_routed(self, request):
        """The single model execution boundary; never fall back to another API or agent."""
        from persistence.objects import PersistenceFault
        try: return await asyncio.to_thread(self.router.run, request)
        except PersistenceFault:
            self.stop.set();raise

    def _trim(self, session, incoming):
        def size():
            return len(incoming.encode('utf-8')) + sum(len(m['content'].encode('utf-8')) for m in session.history)
        while session.history and (len(session.history) > self.settings.max_history_messages
                                   or size() > self.settings.max_history_bytes):
            del session.history[:2]  # Remove a whole user/assistant turn; keep privacy sticky.

    async def _command(self, event_id, chat_id, session, text, started):
        command = text.strip().lower()
        if command in {'/schedule','/alerts','/briefing now','/recap now'}:
            if self.proactive is None:
                answer = 'Proactive scheduling is disabled; ask the local operator to configure it.'
            elif chat_id != self.proactive.config.recipient_user_id:
                answer = 'This chat is not the configured notification recipient.'
            elif command in {'/schedule','/alerts'}:
                answer = await asyncio.to_thread(self.proactive.status,alerts=command=='/alerts')
            else:
                session.private = True
                status = await self.proactive.manual(chat_id,'morning' if command=='/briefing now' else 'evening',event_id)
                self.event(event_id,True,'proactive_manual',success=status=='sent',delivery_success=status=='sent',
                           origin='manual',notification_status=status,latency_ms=round((time.monotonic()-started)*1000,2))
                if status in {'sent','uncertain'}:
                    activity=getattr(self.router,'activity',None)
                    if activity: await asyncio.to_thread(activity.record,chat_id,'request_handled',event_id)
                    return
                answer = 'Briefing was not delivered. Check /alerts and the local delivery metadata before retrying.'
        elif command == '/new':
            session.history.clear()
            session.private = self.settings.default_private
            self.event(event_id,True,'conversation_reset',success=True,session_scope=Activity.scope(chat_id))
            answer = 'Started a fresh conversation. Privacy mode: ' + ('local only.' if session.private else 'automatic local/cloud routing.')
        elif command == '/private':
            session.private = True
            answer = 'Local-only privacy mode enabled for this conversation. Use /new to clear all context and restore the configured default.'
        elif command in {'/start', '/help'}:
            answer = ('Send text to use automatic local/cloud routing. Telegram carries all messages. '
                      '/private keeps model inference local; /new clears this chat context. '
                      'Skills: /calendar, /email, /search <query>, /weather. Email sending is disabled. '
                      'Proactive controls: /schedule, /alerts, /briefing now, /recap now. '
                      'Memory: /remember project = Project Cardinal, /recall project, /memory, /forget project. '
                      '/new keeps persistent synthetic preferences. Model switching is unavailable.')
        else:
            answer = 'Unsupported command. Use /help, /private, or /new. Model selection is automatic.'
        await self._finish(event_id, chat_id, 'command', answer, started)

    async def _finish(self, event_id, chat_id, status, answer, started, result=None, model_ok=False):
        delivered, failure = True, None
        try:
            await self.api.send(chat_id, answer)
        except TelegramError as exc:
            delivered, failure = False, exc.kind
        # Router/backend enforce model metadata; never log the result's response or history.
        result = result if isinstance(result, dict) else {}
        attempts = result.get('attempts') or []
        execution = attempts[-1].get('execution') if attempts else None
        self.event(event_id, True, status if delivered else 'delivery_failed',
                   route_request_id=result.get('request_id'), route=result.get('route'),
                   selected_model=result.get('selected_model'), model_success=model_ok and result.get('route') in {'local','cloud'},
                   memory_success=model_ok and result.get('route')=='memory', memory_execution=result.get('memory_execution'),
                   tool_success=model_ok and result.get('route') == 'tool', skill_execution=result.get('skill_execution'),
                   delivery_success=delivered, success=delivered and (model_ok or status == 'command'),
                   failure=failure or result.get('failure'), execution=execution,
                   latency_ms=round((time.monotonic() - started) * 1000, 2))
        activity=getattr(self.router,'activity',None)
        if activity:
            await asyncio.to_thread(activity.record,chat_id,'request_handled',event_id)
            if delivered: await asyncio.to_thread(activity.record,chat_id,'reply_delivered',event_id)
        return delivered

    async def run(self):
        pending = set()
        try:
            while not self.stop.is_set():
                done = {task for task in pending if task.done()}
                pending -= done
                for task in done:
                    task.result()  # Fail closed on audit/storage errors; no unobserved task exceptions.
                if len(pending) >= self.settings.max_pending:
                    await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    continue
                polling = asyncio.create_task(self.api.call('getUpdates', {
                    'offset': self.last_update + 1, 'timeout': self.settings.poll_seconds,
                    'limit': min(10, self.settings.max_pending - len(pending)), 'allowed_updates': ['message'],
                }))
                stopping = asyncio.create_task(self.stop.wait())
                try:
                    done, _ = await asyncio.wait({polling, stopping}, return_when=asyncio.FIRST_COMPLETED)
                    if stopping in done:
                        polling.cancel()
                        await asyncio.gather(polling, return_exceptions=True)
                        break
                    try:
                        updates = polling.result()
                    except TelegramError as exc:
                        self.event(None, False, 'poll_failed', success=False, failure=exc.kind)
                        if exc.kind in {'unauthorized_token', 'conflict', 'forbidden', 'invalid_response', 'rate_limit_long'}:
                            raise
                        try:
                            await asyncio.wait_for(self.stop.wait(), timeout=max(5, exc.retry_after or 0))
                        except TimeoutError:
                            pass
                        continue
                    if (not isinstance(updates, list) or len(updates) > min(10, self.settings.max_pending - len(pending))
                            or any(not isinstance(u, dict) or type(u.get('update_id')) is not int for u in updates)):
                        raise TelegramError('invalid_response')
                    self.last_poll_success = time.monotonic()
                    for update in sorted(updates, key=lambda u: u['update_id']):
                        admitted = self.admit(update)
                        if admitted is not None:
                            pending.add(asyncio.create_task(self.handle_admitted(admitted)))
                finally:
                    stopping.cancel()
                    if not polling.done():
                        polling.cancel()
                    await asyncio.gather(stopping, polling, return_exceptions=True)
        finally:
            self.stop.set()
            # Finish active bounded Hermes calls; queued requests are held from dispatch.
            results = await asyncio.gather(*pending, return_exceptions=True)
            self.sessions.clear()
            for result in results:
                if isinstance(result, Exception):
                    raise result
