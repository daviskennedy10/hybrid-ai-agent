"""Start only the assignment-owned routed Telegram ingress, never stock Hermes gateway."""
import argparse
import asyncio
from contextlib import contextmanager, nullcontext
import fcntl
import json
import os
import signal

from router.core import JsonLog, Policy, Router
from router.hermes_backend import HermesBackend
from telegram_bot.api import TelegramAPI, TelegramError
from telegram_bot.config import ROOT, Settings
from telegram_bot.service import Bot


@contextmanager
def instance_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another assignment Telegram poller is running') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


async def identity(api):
    import re
    me = await api.call('getMe')
    webhook = await api.call('getWebhookInfo')
    username = me.get('username')
    if (type(me.get('id')) is not int or me.get('is_bot') is not True
            or not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,64}', username)
            or not isinstance(webhook.get('url'), str)):
        raise TelegramError('invalid_response')
    return {'bot_id': me['id'], 'username': username, 'webhook_configured': bool(webhook['url'])}


async def serve(settings, *, check=False, run_seconds=None):
    api = TelegramAPI(settings.token, retries=settings.api_retries)
    info = await identity(api)
    if check:
        print(json.dumps({**info, 'allowlist_configured': bool(settings.allowed_users)}))
        return
    if info['webhook_configured']:
        raise TelegramError('webhook_configured')
    policy = Policy.load()
    from assignment_skills.config import Config
    from assignment_skills.dispatch import Dispatcher
    skills = Dispatcher(Config.load(fallback_users=settings.allowed_users))
    from proactive.config import Schedule
    schedule=Schedule.load();schedule.authorize(settings.allowed_users,skills.config)
    from persistence.runtime import configured
    runtime=configured(ROOT / 'router/logs',settings.allowed_users & skills.config.allowed_user_ids,ROOT / 'secrets/persistence.json')
    router = Router(policy, HermesBackend(policy), JsonLog(ROOT / 'router/logs/telegram-routing.jsonl'), skill_dispatcher=skills,memory_store=runtime.memory,activity=runtime.activity)
    bot = Bot(settings, api, router, JsonLog(ROOT / 'router/logs/telegram.jsonl'))
    from proactive.engine import Engine
    from proactive.state import State
    state=None
    try:
        state = State(runtime.scheduler_path)
        runtime.bind_state(state)
    except Exception:
        try: runtime.close()
        finally:
            if state is not None: state.close()
        raise
    if schedule.enabled:
        bot.proactive = Engine(schedule,state,router,api,JsonLog(ROOT / 'router/logs/proactive.jsonl'),
                               settings.allowed_users,stop=bot.stop,slots=bot.slots)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, bot.stop.set)
    timer = loop.call_later(run_seconds, bot.stop.set) if run_seconds else None
    bot.event(None, False, 'ready')
    print(json.dumps({'status': 'ready', 'username': info['username'],
                      'allowed_user_count': len(settings.allowed_users),
                      'default_private': settings.default_private,'proactive_enabled': schedule.enabled}), flush=True)
    try:
        with state.exclusive() if state else nullcontext():
            from operations.health import Heartbeat
            from operations.audit import isolation_report
            from operations.health import atomic_json
            atomic_json(ROOT / 'router/logs/startup-isolation.json',isolation_report())
            monitor = Heartbeat(bot,ROOT / 'router/logs/health.json',JsonLog(ROOT / 'router/logs/operations.jsonl'))
            services=[]
            if os.environ.get('A3_GATEWAY_CONFIG'):
                from operations.gateway import Gateway, load
                config=load(os.environ['A3_GATEWAY_CONFIG'],settings.allowed_users & skills.config.allowed_user_ids)
                services.append(Gateway(router,config,ROOT / 'router/logs/health.json',int(os.environ.get('PORT','8080'))))
            if os.environ.get('A3_CLOUD_EVIDENCE')=='1':
                if runtime.coordinator is None: raise ValueError('Durable evidence requires GCS runtime')
                from operations.cloud_evidence import Evidence
                services.append(Evidence(ROOT / 'router/logs',runtime.coordinator.objects))
                from operations.model_monitor import ModelMonitor
                services.append(ModelMonitor(router,bot.slots,schedule.recipient_user_id,
                                             JsonLog(ROOT / 'router/logs/operations.jsonl')))
            await supervise(bot,monitor,runtime,services)

    finally:
        if timer:
            timer.cancel()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        try: runtime.close()
        finally: state.close()
        bot.event(None, False, 'stopped')


async def supervise(bot,monitor=None,runtime=None,services=()):
    tasks=[asyncio.create_task(bot.run())]
    tasks.extend(asyncio.create_task(service.run(bot.stop)) for service in services)
    if runtime is not None:
        tasks.append(asyncio.create_task(runtime.run(bot.stop)))
    if monitor is not None:
        tasks.append(asyncio.create_task(monitor.run()))
    if bot.proactive is not None:
        tasks.append(asyncio.create_task(bot.proactive.run()))
    try:
        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        bot.stop.set()
        results=await asyncio.gather(*tasks,return_exceptions=True)
        for result in results:
            if isinstance(result,BaseException): raise result
    finally:
        bot.stop.set()
        # Drain bounded in-flight calls rather than replay an interrupted send.
        await asyncio.gather(*tasks,return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description='Assignment Telegram ingress with routed skills and optional proactive scheduling')
    parser.add_argument('--env-file', default=str(ROOT / 'secrets/telegram.env'))
    parser.add_argument('--check', action='store_true', help='Read-only getMe/webhook check; no message polling or inference')
    parser.add_argument('--run-seconds', type=int, help='Stop accepting new work after this many seconds; drain active inference')
    args = parser.parse_args()
    try:
        if args.run_seconds is not None and not 1 <= args.run_seconds <= 86400:
            raise ValueError('run-seconds must be 1–86400')
        settings = Settings.load(args.env_file, require_users=not args.check)
        if args.check:
            asyncio.run(serve(settings, check=True))
        else:
            with instance_lock(ROOT / 'router/logs/telegram.lock'):
                asyncio.run(serve(settings, run_seconds=args.run_seconds))
        return 0
    except TelegramError as exc:
        # kind is our closed classification, never Telegram's description or token URL.
        print(json.dumps({'status': 'telegram_error', 'failure': exc.kind}), flush=True)
        return 3
    except (ValueError, OSError):
        print('Telegram configuration/log error. Set TELEGRAM_ALLOWED_USER_IDS, validate the token file, and ensure one writable poller.', flush=True)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('Telegram service stopped after an internal error. No provider fallback was attempted.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
