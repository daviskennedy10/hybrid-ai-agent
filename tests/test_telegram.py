import asyncio
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError, URLError

from router.core import InferenceFailure, InferenceResult, JsonLog, Policy, Router
from telegram_bot.api import TelegramAPI, TelegramError
from telegram_bot.config import Settings
from telegram_bot.service import Bot
from telegram_bot.__main__ import identity, instance_lock

TOKEN = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_SYNTHETIC'


def update(event_id=1, user=101, text='Hello!', chat=None, **message_fields):
    return {'update_id': event_id, 'message': {'message_id': event_id,
            'from': {'id': user, 'is_bot': False},
            'chat': {'id': user if chat is None else chat, 'type': 'private'},
            'text': text, **message_fields}}


class ModelBackend:
    def __init__(self):
        self.calls = []
        self.error = None
        self.available = True

    def healthy(self):
        return self.available

    def infer(self, route, request):
        self.calls.append((route, request))
        if self.error:
            raise self.error
        return InferenceResult('Synthetic ' + route + ' response.', {
            'provider': 'custom' if route == 'local' else 'openai-codex',
            'wire_model': 'gemma4:e2b' if route == 'local' else 'gpt-5.6-sol',
            'endpoint_host': '127.0.0.1' if route == 'local' else 'chatgpt.com',
            'api_mode': 'chat_completions' if route == 'local' else 'codex_responses',
            'normalized_response_model': 'gemma4:e2b' if route == 'local' else 'gpt-5.6-sol',
        })


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.error = None
        self.batches = []
        self.polls = []
        self.sent_event = asyncio.Event()
        self.repolled = asyncio.Event()

    async def send(self, chat_id, text):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))
        self.sent_event.set()

    async def call(self, method, payload):
        self.polls.append(payload)
        if len(self.polls) > 1:
            self.repolled.set()
        if self.batches:
            value = self.batches.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        await asyncio.Event().wait()


class TelegramBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(TOKEN, frozenset({101, 202}), default_private=False)
        self.backend = ModelBackend()
        self.route_events, self.events = [], []
        self.router = Router(replace(Policy.load(), retries=0), self.backend, self.route_events.append)
        self.api = FakeAPI()
        self.bot = Bot(self.settings, self.api, self.router, self.events.append)

    async def test_authorized_simple_uses_router_local_and_same_chat(self):
        await self.bot.handle_update(update())
        self.assertEqual(self.backend.calls[0][0], 'local')
        self.assertEqual(self.api.sent, [(101, 'Synthetic local response.')])
        final = self.events[-1]
        self.assertTrue(final['success'])
        self.assertEqual(final['route_request_id'], self.route_events[-1]['request_id'])
        self.assertEqual(final['selected_model'], 'gemma4:e2b')
        self.assertEqual(final['execution']['wire_model'], 'gemma4:e2b')
        self.assertEqual(final['update_id'], 1)

    async def test_complex_uses_router_cloud(self):
        await self.bot.handle_update(update(text='Prove that the sum of even numbers is even.'))
        self.assertEqual(self.backend.calls[0][0], 'cloud')
        self.assertEqual(self.events[-1]['execution']['provider'], 'openai-codex')
        self.assertEqual(self.events[-1]['execution']['wire_model'], 'gpt-5.6-sol')

    async def test_unauthorized_group_and_spoofed_id_rejected(self):
        group = update(2)
        group['message']['chat']['type'] = 'group'
        for incoming in [update(user=999), group, update(3, chat=202), update(4, user=True)]:
            await self.bot.handle_update(incoming)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.api.sent, [])
        self.assertEqual(self.bot.sessions, {})
        self.assertTrue(all(not event['authorized'] for event in self.events))

    async def test_context_preserved_and_chats_isolated(self):
        await self.bot.handle_update(update(1, text='Translate synthetic apple into French.'))
        await self.bot.handle_update(update(2, user=202, text='Hello!'))
        await self.bot.handle_update(update(3, text='Rephrase that answer.'))
        self.assertEqual(self.backend.calls[1][1].history, [])
        self.assertEqual(self.backend.calls[2][1].history, [
            {'role': 'user', 'content': 'Translate synthetic apple into French.'},
            {'role': 'assistant', 'content': 'Synthetic local response.'}])
        self.assertEqual([chat for chat, _ in self.api.sent], [101, 202, 101])

    async def test_duplicates_do_not_replay_inference_or_reply(self):
        await asyncio.gather(self.bot.handle_update(update()), self.bot.handle_update(update()))
        await self.bot.handle_update(update())
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(len(self.api.sent), 1)
        self.assertEqual(sum(event['status'] == 'duplicate' for event in self.events), 2)

    async def test_invalid_and_oversized_messages(self):
        for i, text in enumerate(['', ' ' * 2, 'x' * 4097, '猫' * 4096, '\ud800', None, 17], 1):
            await self.bot.handle_update(update(i, text=text))
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(len(self.api.sent), 7)
        self.assertTrue(all(e['status'] in {'received', 'invalid_message'} for e in self.events))

    async def test_unsupported_update_and_identity_types(self):
        for incoming in [None, {'update_id': True}, {'update_id': 1, 'edited_message': {}},
                         {'update_id': 2, 'message': {'from': [], 'chat': {}}}]:
            await self.bot.handle_update(incoming)
        self.assertFalse(self.backend.calls)
        self.assertFalse(self.api.sent)

    async def test_forwarded_and_bot_messages_not_routed(self):
        await self.bot.handle_update(update(1, forward_origin={'type': 'user'}))
        bot = update(2)
        bot['message']['from']['is_bot'] = True
        await self.bot.handle_update(bot)
        self.assertEqual(self.backend.calls, [])

    async def test_privacy_is_sticky_and_new_clears_context(self):
        await self.bot.handle_update(update(1, text='[private] Prove a synthetic claim.'))
        await self.bot.handle_update(update(2, text='Prove another synthetic claim.'))
        self.assertEqual([route for route, _ in self.backend.calls], ['local', 'local'])
        self.assertTrue(self.backend.calls[-1][1].private)
        await self.bot.handle_update(update(3, text='/new'))
        await self.bot.handle_update(update(4, text='Prove an even number claim.'))
        self.assertEqual(self.backend.calls[-1][0], 'cloud')
        self.assertEqual(self.backend.calls[-1][1].history, [])

    async def test_private_command_and_model_switch_cannot_bypass(self):
        for i, text in enumerate(['/start', '/model gpt-5.6-sol', '/private'], 1):
            await self.bot.handle_update(update(i, text=text))
        self.assertEqual(self.backend.calls, [])
        await self.bot.handle_update(update(4, text='Prove a claim.'))
        self.assertEqual(self.backend.calls[0][0], 'local')

    async def test_history_bounded_and_privacy_survives_trimming(self):
        self.bot.settings = replace(self.settings, max_history_messages=2, max_history_bytes=300)
        await self.bot.handle_update(update(1, text='[private] Hello!'))
        for i in range(2, 8):
            await self.bot.handle_update(update(i, text='Hello!'))
        self.assertLessEqual(len(self.bot.sessions[101].history), 2)
        self.assertTrue(self.backend.calls[-1][1].private)
        self.assertTrue(all(route == 'local' for route, _ in self.backend.calls))

    async def test_model_failure_and_exception_do_not_crash_or_keep_context(self):
        self.backend.error = InferenceFailure('auth')
        await self.bot.handle_update(update(1))
        self.assertEqual(self.events[-1]['status'], 'model_failed')
        self.assertEqual(self.bot.sessions[101].history, [])
        with patch.object(self.router, 'run', side_effect=RuntimeError(TOKEN)):
            await self.bot.handle_update(update(2))
        self.assertNotIn(TOKEN, json.dumps(self.events + self.route_events + self.api.sent))
        self.backend.error = None
        await self.bot.handle_update(update(3))
        self.assertTrue(self.events[-1]['success'])

    async def test_private_local_unavailable_is_held(self):
        self.backend.available = False
        await self.bot.handle_update(update(text='[private] Hello!'))
        self.assertEqual(self.events[-1]['status'], 'held')
        self.assertEqual(self.backend.calls, [])
        self.assertIn('held', self.api.sent[-1][1])

    async def test_delivery_failure_is_not_replayed(self):
        self.api.error = TelegramError('forbidden')
        await self.bot.handle_update(update())
        await self.bot.handle_update(update())
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.bot.sessions[101].history, [])
        self.assertTrue(any(e['status'] == 'delivery_failed' for e in self.events))
        self.api.error = None
        await self.bot.handle_update(update(2))
        self.assertTrue(self.events[-1]['delivery_success'])

    async def test_audit_failure_before_dispatch_fails_closed(self):
        self.bot.audit = Mock(side_effect=OSError(TOKEN))
        with self.assertRaises(OSError):
            await self.bot.handle_update(update())
        self.assertFalse(self.backend.calls)
        self.assertFalse(self.api.sent)

    async def test_logs_do_not_contain_message_or_credentials(self):
        text = 'Translate synthetic-NEVER-IN-LOG into French.'
        with tempfile.TemporaryDirectory() as directory:
            self.bot.audit = JsonLog(Path(directory) / 'events.jsonl')
            self.router.log = JsonLog(Path(directory) / 'routes.jsonl')
            await self.bot.handle_update(update(text=text))
            logs = ''.join(p.read_text() for p in Path(directory).iterdir())
            for forbidden in [text, 'synthetic-NEVER-IN-LOG', TOKEN, 'Synthetic local response.']:
                self.assertNotIn(forbidden, logs)

    async def test_poll_offsets_dedup_and_shutdown(self):
        self.api.batches = [[update(), update()]]
        task = asyncio.create_task(self.bot.run())
        await asyncio.wait_for(self.api.sent_event.wait(), 3)
        await asyncio.wait_for(self.api.repolled.wait(), 3)
        self.bot.stop.set()
        await asyncio.wait_for(task, 3)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.bot.sessions, {})
        self.assertEqual(self.api.polls[0]['allowed_updates'], ['message'])
        self.assertTrue(any(p['offset'] == 2 for p in self.api.polls))

    async def test_poll_conflict_is_terminal(self):
        self.api.batches = [TelegramError('conflict')]
        with self.assertRaisesRegex(TelegramError, 'conflict'):
            await self.bot.run()
        self.assertEqual(self.events[-1]['status'], 'poll_failed')

    async def test_stopped_bot_does_not_start_model(self):
        self.bot.stop.set()
        await self.bot.handle_update(update())
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.events[-1]['status'], 'stopping')

    async def test_reply_verifier_requires_correlated_delivered_model_evidence(self):
        import copy
        from telegram_bot.verify_logs import verify
        await self.bot.handle_update(update(1))
        await self.bot.handle_update(update(2, text='/new'))
        await self.bot.handle_update(update(3, text='Prove the sum of even integers is even.'))
        result = verify(self.events, self.route_events)
        self.assertTrue(result['simple_local_reply_verified'])
        self.assertTrue(result['complex_cloud_reply_verified'])
        events = copy.deepcopy(self.events)
        for event in events:
            if event.get('selected_model') == 'gemma4:e2b':
                event['delivery_success'] = False
        self.assertFalse(verify(events, self.route_events)['simple_local_reply_verified'])
        self.assertFalse(verify(self.events, [])['complex_cloud_reply_verified'])

    async def test_reply_verifier_does_not_accept_failed_or_stale_run(self):
        from telegram_bot.verify_logs import verify
        await self.bot.handle_update(update())
        events = self.events + [{'status': 'ready'}]
        self.assertFalse(verify(events, self.route_events)['simple_local_reply_verified'])
        self.route_events[0]['success'] = False
        self.assertFalse(verify(self.events, self.route_events)['simple_local_reply_verified'])

    async def test_shutdown_drains_an_active_inference(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.infer
        def blocking(route, request):
            entered.set()
            release.wait(3)
            return original(route, request)
        self.backend.infer = blocking
        self.api.batches = [[update()]]
        task = asyncio.create_task(self.bot.run())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.bot.stop.set()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.wait_for(task, 3)
        self.assertEqual(self.api.sent, [(101, 'Synthetic local response.')])
        self.assertEqual(self.bot.sessions, {})

    async def test_same_chat_serialized_other_chat_has_no_shared_history(self):
        entered, release = threading.Event(), threading.Event()
        original = self.backend.infer
        def blocking(route, request):
            if request.prompt == 'Hello first!':
                entered.set()
                release.wait(3)
            return original(route, request)
        self.backend.infer = blocking
        first = asyncio.create_task(self.bot.handle_update(update(1, text='Hello first!')))
        await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(self.bot.handle_update(update(2, text='Rephrase that.')))
        other = asyncio.create_task(self.bot.handle_update(update(3, user=202)))
        try:
            await asyncio.wait_for(other, 2)
            self.assertEqual(self.backend.calls[0][1].history, [])
        finally:
            release.set()
            await asyncio.gather(first, second)
        followup = next(req for _, req in self.backend.calls if req.prompt == 'Rephrase that.')
        self.assertEqual(followup.history[0]['content'], 'Hello first!')


class Response(BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.code = status


class TelegramAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_error_never_exposes_token_or_url(self):
        api = TelegramAPI(TOKEN, retries=0)
        with patch('telegram_bot.api.build_opener') as opener:
            opener.return_value.open.side_effect = URLError('https://api.telegram.org/bot' + TOKEN)
            with self.assertRaises(TelegramError) as caught:
                await api.call('getMe')
        self.assertEqual(str(caught.exception), 'transport')
        self.assertNotIn(TOKEN, str(caught.exception))

    async def test_http_error_description_not_exposed(self):
        body = json.dumps({'ok': False, 'error_code': 401, 'description': TOKEN}).encode()
        with patch('telegram_bot.api.build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('https://x/' + TOKEN, 401, TOKEN, {}, BytesIO(body))
            with self.assertRaisesRegex(TelegramError, '^unauthorized_token$'):
                await TelegramAPI(TOKEN).call('getMe')

    async def test_rate_limit_retry_after_and_read_retry(self):
        sleep = AsyncMock()
        api = TelegramAPI(TOKEN, sleep=sleep)
        with patch.object(api, '_post', side_effect=[TelegramError('rate_limit', 3), {'message_id': 1}]):
            await api.call('sendMessage', {'chat_id': 101, 'text': 'Synthetic'})
        sleep.assert_awaited_once_with(3)
        with patch.object(api, '_post', side_effect=[TelegramError('transport'), []]) as post:
            self.assertEqual(await api.call('getUpdates'), [])
            self.assertEqual(post.call_count, 2)

    async def test_ambiguous_send_error_not_automatically_retried(self):
        api = TelegramAPI(TOKEN, sleep=AsyncMock())
        for kind in ['transport', 'server_error', 'forbidden', 'rate_limit_long']:
            with patch.object(api, '_post', side_effect=TelegramError(kind)) as post:
                with self.assertRaises(TelegramError):
                    await api.call('sendMessage', {'chat_id': 101, 'text': 'Synthetic'})
                self.assertEqual(post.call_count, 1)

    async def test_bounded_retries(self):
        api = TelegramAPI(TOKEN, retries=2, sleep=AsyncMock())
        with patch.object(api, '_post', side_effect=TelegramError('transport')) as post:
            with self.assertRaises(TelegramError):
                await api.call('getUpdates')
            self.assertEqual(post.call_count, 3)

    async def test_http_rate_limit_parameters_are_bounded(self):
        for delay, expected in [(2, 'rate_limit'), (61, 'rate_limit_long'), (None, 'rate_limit_long')]:
            body = json.dumps({'ok': False, 'error_code': 429, 'parameters': {'retry_after': delay},
                               'description': TOKEN}).encode()
            with patch('telegram_bot.api.build_opener') as opener:
                opener.return_value.open.return_value = Response(body, 429)
                with self.assertRaises(TelegramError) as caught:
                    TelegramAPI(TOKEN)._post('sendMessage', {}, 1)
                self.assertEqual(caught.exception.kind, expected)
                self.assertNotIn(TOKEN, str(caught.exception))

    async def test_safe_plain_text_chunking_and_chat_check(self):
        api = TelegramAPI(TOKEN)
        result = {'message_id': 7, 'chat': {'id': 101}}
        with patch.object(api, 'call', new=AsyncMock(return_value=result)) as call:
            await api.send(101, '🦉' * 2200)
            self.assertEqual(call.await_count, 3)
            self.assertEqual(''.join(c.args[1]['text'] for c in call.await_args_list), '🦉' * 2200)
            self.assertTrue(all('parse_mode' not in c.args[1] for c in call.await_args_list))
        with patch.object(api, 'call', new=AsyncMock(return_value={'message_id': 1, 'chat': {'id': 999}})):
            with self.assertRaisesRegex(TelegramError, 'invalid_response'):
                await api.send(101, 'Synthetic')

    async def test_malformed_oversized_redirect_and_server_responses(self):
        api = TelegramAPI(TOKEN, retries=0)
        for body, status, expected in [(b'bad', 200, 'invalid_response'), (b'[]', 200, 'invalid_response'),
                                       (b'x' * 2000001, 200, 'invalid_response'),
                                       (b'<html>', 503, 'server_error'),
                                       (b'{"ok": true, "result": []}', 200, 'invalid_response')]:
            with patch('telegram_bot.api.build_opener') as opener:
                opener.return_value.open.return_value = Response(body, status)
                with self.assertRaisesRegex(TelegramError, expected):
                    await api.call('getMe')
        from telegram_bot.api import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, None, None, 'https://attacker.invalid'))

    async def test_webhook_is_not_deleted_automatically(self):
        from telegram_bot.__main__ import serve
        with patch('telegram_bot.__main__.identity', new=AsyncMock(return_value={
            'bot_id': 123, 'username': 'synthetic_bot', 'webhook_configured': True
        })):
            with self.assertRaisesRegex(TelegramError, 'webhook_configured'):
                await serve(Settings(TOKEN, frozenset({101})))

    async def test_identity_only_returns_safe_fields(self):
        api = Mock()
        api.call = AsyncMock(side_effect=[{'id': 123, 'is_bot': True, 'username': 'synthetic_bot', 'description': TOKEN},
                                         {'url': '', 'last_error_message': TOKEN}])
        self.assertEqual(await identity(api), {'bot_id': 123, 'username': 'synthetic_bot', 'webhook_configured': False})


class TelegramConfigurationTests(unittest.TestCase):
    def test_secure_loading_fail_closed_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.env'
            path.write_text('TELEGRAM_BOT_TOKEN=' + TOKEN + '\n')
            with self.assertRaisesRegex(ValueError, 'ALLOWED_USER_IDS'):
                Settings.load(path, environ={})
            settings = Settings.load(path, environ={'TELEGRAM_ALLOWED_USER_IDS': '101,202', 'TELEGRAM_DEFAULT_PRIVATE': 'false'})
            self.assertEqual(settings.allowed_users, frozenset({101, 202}))
            self.assertFalse(settings.default_private)
            self.assertNotIn(TOKEN, repr(settings))
            self.assertTrue(Settings.load(path, environ={}, require_users=False).default_private)
            for raw in ['*', '-1', '0', '101,bad']:
                with self.assertRaises(ValueError):
                    Settings.load(path, environ={'TELEGRAM_ALLOWED_USER_IDS': raw})

    def test_token_and_environment_file_not_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.env'
            for content in ['TELEGRAM_BOT_TOKEN=$(cat secret)', 'UNKNOWN=value', 'badline']:
                path.write_text(content)
                with self.assertRaises(ValueError) as caught:
                    Settings.load(path, environ={})
                self.assertNotIn('$(cat secret)', str(caught.exception))

    def test_second_poller_instance_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'poller.lock'
            with instance_lock(path):
                with self.assertRaises(ValueError):
                    with instance_lock(path):
                        self.fail('Second instance acquired lock')


if __name__ == '__main__':
    unittest.main()
