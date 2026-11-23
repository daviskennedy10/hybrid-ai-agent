"""No-listener liveness evidence from the actual bot event loop, not a demo process."""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import resource
import sys
import time
import uuid


def atomic_json(path,event):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    # A unique O_EXCL temporary avoids following an existing symlink.
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(event,stream,sort_keys=True,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)


class Heartbeat:
    def __init__(self,bot,path,audit,interval=60,clock=time.monotonic):
        self.bot,self.path,self.audit,self.interval,self.clock=bot,Path(path),audit,interval,clock
        self.started=clock();self.boot_id=uuid.uuid4().hex

    def sample(self):
        now=self.clock()
        def age(value): return round(max(0,now-value),3) if value is not None else None
        poll_age=age(self.bot.last_poll_success)
        scheduler=self.bot.proactive
        scheduler_age=age(scheduler.last_progress) if scheduler is not None else None
        memory=None
        try:
            observed=int(Path('/sys/fs/cgroup/memory.current').read_text())
            if 0<=observed<2**53: memory=observed
        except (OSError,ValueError): pass
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        return {'timestamp':datetime.now(timezone.utc).isoformat(),'message':'service_heartbeat','boot_id':self.boot_id,
                'uptime_seconds':round(now-self.started,3),'poll_age_seconds':poll_age,
                'telegram_poll_healthy':poll_age is not None and poll_age<=90,
                'scheduler_enabled':scheduler is not None,'scheduler_age_seconds':scheduler_age,
                'scheduler_healthy':scheduler is None or (scheduler_age is not None and scheduler_age<=600),
                'rss_peak_bytes':rss,'memory_current_bytes':memory}

    async def run(self):
        while not self.bot.stop.is_set():
            event=self.sample();self.audit(event);atomic_json(self.path,event)
            try: await asyncio.wait_for(self.bot.stop.wait(),timeout=self.interval)
            except TimeoutError: pass


def check(path,max_age=150,now=None):
    try:
        d=json.loads(Path(path).read_text())
        observed=datetime.fromisoformat(d['timestamp'])
        now=now or datetime.now(timezone.utc)
        age=(now-observed).total_seconds()
        return 0<=age<=max_age and d['telegram_poll_healthy'] is True and d['scheduler_healthy'] is True
    except (OSError,ValueError,TypeError,KeyError): return False


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Read-only actual-bot heartbeat health check')
    parser.add_argument('--file',default=str(Path(__file__).resolve().parents[1]/'router/logs/health.json'))
    args=parser.parse_args()
    healthy=check(args.file)
    print(json.dumps({'healthy':healthy}))
    return 0 if healthy else 3


if __name__=='__main__': raise SystemExit(main())
