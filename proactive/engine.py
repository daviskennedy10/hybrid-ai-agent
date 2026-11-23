"""Single-process scheduler; read-only skills and a metadata-only durable outbox."""
import asyncio
from datetime import datetime,timedelta,timezone
import time
import uuid
from zoneinfo import ZoneInfo

from proactive.config import aware,local_due
from proactive.content import Collector
from proactive.state import digest
from telegram_bot.api import TelegramError

UTC=timezone.utc


class Engine:
    def __init__(self,config,state,router,api,audit,telegram_users,*,stop=None,slots=None,now=None):
        config.authorize(telegram_users,router.skill_dispatcher.config)
        if not config.enabled: raise ValueError('Proactive scheduler is disabled')
        self.config,self.state,self.api,self.log=config,state,api,audit
        self.now=now or (lambda:datetime.now(UTC))
        self.stop=stop if stop is not None else asyncio.Event()
        self.slots=slots if slots is not None else asyncio.Semaphore(1)
        self.lock=asyncio.Lock()
        self.last_progress=time.monotonic()
        self.zone=ZoneInfo(config.timezone)
        self.recipient=digest('recipient',config.recipient_user_id)
        skills=router.skill_dispatcher.config
        self.scope=digest(self.recipient,skills.calendar_ics_url,skills.email_username)
        self.run_id=uuid.uuid4().hex
        self.collector=Collector(router,config.recipient_user_id,self.audit)

    def audit(self,event):
        self.log({'timestamp':self.now().isoformat(),'run_id':self.run_id,**event})

    def recover(self):
        self.state.recover(self.now().timestamp())
        self.audit({'message':'proactive_started','enabled':True,'state_counts':self.state.counts(self.recipient)})

    def status(self,alerts=False):
        c=self.config
        if alerts:
            lead,poll=c.effective(self.now())
            return (f'Alerts enabled: calendar lead {lead} minutes; conflicts within {c.conflict_horizon_hours} hours; '
                    f'urgent unread synthetic email. Clock-aligned scheduled heartbeat every {poll} seconds. Durable deduplication; '
                    f'ledger counts: {self.state.counts(self.recipient)}. Email sending is disabled.')
        return (f'Morning {c.morning_time}; recap {c.evening_time}; timezone {c.timezone}. '
                f'Catch-up window {c.catchup_minutes} minutes on the same local date. '
                'One bot process must remain running. /briefing now and /recap now use the same read-only pipeline.')

    def daily_jobs(self,now):
        day=now.astimezone(self.zone).date()
        jobs=[]
        for kind,hhmm in [('morning',self.config.morning_time),('evening',self.config.evening_time)]:
            due=local_due(day,hhmm,self.zone)
            expires=min(due+timedelta(minutes=self.config.catchup_minutes),local_due(day+timedelta(days=1),'00:00',self.zone))
            if due<=now:
                jobs.append((digest(self.scope,kind,str(day),self.config.timezone),kind,due,expires))
        for job in self.config.test_jobs:
            due=aware(job['due_at'])
            if due<=now:
                jobs.append((digest(self.scope,'controlled_test',job['id']),job['kind'],due,due+timedelta(minutes=2)))
        return jobs

    async def enqueue(self,key,kind,origin,due,expires):
        return await self._state('enqueue',key,self.recipient,kind,origin,due.timestamp(),expires.timestamp(),self.now().timestamp())

    async def _state(self,method,*args):
        call=getattr(self.state,method)
        if getattr(self.state,'cloud_backed',False): return await asyncio.to_thread(call,*args)
        return call(*args)

    async def execute(self,key,kind,origin,build):
        """A single HTML message is the delivery unit. Never replay ambiguous sends."""
        started=time.monotonic();now=self.now().timestamp()
        row=(await self._state('get',key))
        if row['attempts']>=self.config.max_delivery_attempts and row['status']=='pending':
            (await self._state('finish',key,'failed',now))
        if self.stop.is_set() or not (await self._state('begin',key,now)):
            current=(await self._state('get',key))
            self.audit({'message':'proactive_suppressed','notification_key':key,'kind':kind,'origin':origin,'status':current['status']})
            return current['status']
        quality={}
        failure=None
        try:
            async with self.slots:
                body,quality=await build()
                if self.stop.is_set():
                    (await self._state('finish',key,'pending',self.now().timestamp()));return 'pending'
                (await self._state('sending',key,self.now().timestamp()))
                try:
                    await self.api.send(self.config.recipient_user_id,body)
                except TelegramError as exc:
                    failure=exc.kind
                    # Telegram explicitly rejected 429: safe bounded retry. Transport/5xx
                    # can mean accepted but acknowledgement lost: suppress replay.
                    status='pending' if exc.kind=='rate_limit' else ('failed' if exc.kind in {
                        'forbidden','unauthorized_token','api_error','rate_limit_long'} else 'uncertain')
                    delay=max(self.config.retry_seconds,exc.retry_after or 0)
                except Exception:
                    status,failure,delay='uncertain','internal_delivery_error',0
                else:
                    status,delay='sent',0
        except Exception:
            # Preparation never sent anything; safe to retry with fresh skill outputs.
            status,failure,delay='pending','preparation_failed',self.config.retry_seconds
        attempts=(await self._state('get',key))['attempts']
        if status=='pending' and attempts>=self.config.max_delivery_attempts: status='failed'
        retry_at=self.now().timestamp()+min(600,delay*2**max(0,attempts-1))
        (await self._state('finish',key,status,self.now().timestamp(),retry_at))
        self.audit({'message':'proactive_delivery','notification_key':key,'kind':kind,'origin':origin,
                    'success':status=='sent','status':status,'failure':failure,'attempt':attempts,
                    'latency_ms':round((time.monotonic()-started)*1000,2),**quality})
        return status

    async def tick(self):
        async with self.lock:
            if self.stop.is_set(): return
            now=self.now();(await self._state('expire',now.timestamp()))
            for key,kind,due,expires in self.daily_jobs(now):
                await self.enqueue(key,kind,'scheduler',due,expires)
                if expires<now:
                    if (await self._state('get',key))['status']=='pending': (await self._state('finish',key,'expired',now.timestamp()))
                    continue
                await self.execute(key,kind,'scheduler',lambda k=key,t=kind:self.collector.assemble(t,self.now(),self.zone,k,'scheduler'))
            for row in (await self._state('pending_manual',self.recipient,now.timestamp())):
                key,kind=row['key'],row['kind']
                await self.execute(key,kind,'manual',lambda k=key,t=kind:self.collector.assemble(t,self.now(),self.zone,k,'manual'))
            if self.stop.is_set(): return
            poll_key=digest(self.scope,'alert_poll');lead,poll=self.config.effective(now)
            if not (await self._state('poll_due',poll_key,now.timestamp())): return
            # Persist throttle before service reads, so a failed service/restart cannot hammer it.
            slot=int(now.timestamp()//poll)
            scheduled_at=datetime.fromtimestamp(slot*poll,UTC)
            trigger_key=digest(self.scope,'scheduled_heartbeat',poll,slot)
            (await self._state('polled',poll_key,(slot+1)*poll))
            self.audit({'message':'proactive_trigger','trigger_type':'scheduled_heartbeat','trigger_key':trigger_key,
                        'scheduled_at':scheduled_at.isoformat(),'origin':'scheduler','poll_seconds':poll})
            async with self.slots:
                candidates=await self.collector.alerts(now,self.zone,lead,self.config.conflict_horizon_hours,trigger_key)
            sent=0
            for item in candidates:
                key=digest(self.scope,item['identity']);kind=item['kind']
                row=await self.enqueue(key,kind,'scheduler',now,item['expires'])
                if row['status'] in {'sent','uncertain','failed','expired'}:
                    self.audit({'message':'proactive_suppressed','notification_key':key,'kind':kind,'origin':'scheduler','status':row['status']})
                    continue
                if sent>=self.config.max_notifications_per_poll: continue
                before=(await self._state('get',key))['attempts']
                async def body(value=item['body'],ids=item['source_request_ids'],trigger=trigger_key):
                    return value,{'quality':'complete','source_request_ids':ids,'trigger_key':trigger}
                await self.execute(key,kind,'scheduler',body)
                if (await self._state('get',key))['attempts']>before: sent+=1

    async def manual(self,principal,kind,event_id):
        if principal!=self.config.recipient_user_id or type(principal) is not int or kind not in {'morning','evening'}:
            return 'rejected'
        async with self.lock:
            now=self.now();key=digest(self.scope,'manual',kind,event_id)
            await self.enqueue(key,kind,'manual',now,now+timedelta(minutes=5))
            return await self.execute(key,kind,'manual',lambda:self.collector.assemble(kind,self.now(),self.zone,key,'manual'))

    async def run(self):
        if getattr(self.state,'cloud_backed',False): await asyncio.to_thread(self.recover)
        else: self.recover()
        try:
            while not self.stop.is_set():
                await self.tick()
                self.last_progress=time.monotonic()
                try: await asyncio.wait_for(self.stop.wait(),timeout=self.config.tick_seconds)
                except TimeoutError: pass
        finally:
            self.stop.set()
            self.audit({'message':'proactive_stopped','state_counts':await self._state('counts',self.recipient)})
