"""Local operator configuration/check only. The Telegram process owns all execution."""
import argparse
from dataclasses import asdict,replace
from datetime import datetime,timedelta,timezone
import json
import os
import uuid
from assignment_skills.config import Config
from assignment_skills.common import SkillError
from proactive.config import PATH,Schedule
from telegram_bot.config import Settings


def configure(path=PATH):
    settings=Settings.load()
    skills=Config.load(fallback_users=settings.allowed_users)
    if path.exists(): schedule=Schedule.load(path)
    else:
        users=settings.allowed_users & skills.allowed_user_ids
        if len(users)!=1: raise ValueError('Configure one explicit notification recipient in secrets/schedule.json')
        schedule=Schedule(enabled=True,recipient_user_id=next(iter(users)))
    schedule.authorize(settings.allowed_users,skills)
    return schedule


def save(schedule,path=PATH):
    # Atomic operator-owned metadata file; no token or account credentials.
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump({'version':1,**asdict(schedule)},stream,indent=2);stream.write('\n')
    os.replace(temporary,path)


def main():
    parser=argparse.ArgumentParser(description='Configure/check Stage 5; execution stays inside python -m telegram_bot')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--configure',action='store_true',help='Create authorized defaults if configuration is absent')
    group.add_argument('--check',action='store_true',help='Validate local configuration, no network or delivery')
    group.add_argument('--arm-live-test',action='store_true',help='Arm two real scheduled briefings and a 5-minute actual-data alert window')
    parser.add_argument('--after-seconds',type=int,default=45)
    args=parser.parse_args()
    try:
        if not 15<=args.after_seconds<=120: raise ValueError('after-seconds must be 15–120')
        schedule=configure()
        if args.arm_live_test:
            if not schedule.enabled: raise ValueError('Enable proactive scheduling before arming a live test')
            now=datetime.now(timezone.utc)
            schedule=replace(schedule,test_jobs=tuple({'id':uuid.uuid4().hex,'kind':kind,
                'due_at':(now+timedelta(seconds=args.after_seconds*(i+1))).isoformat()}
                for i,kind in enumerate(('morning','evening'))),verification={
                'until':(now+timedelta(minutes=5)).isoformat(),'lead_minutes':2880,'poll_seconds':10})
            schedule.validate();save(schedule)
        elif args.configure: save(schedule)
        elif not PATH.exists(): raise ValueError('Run --configure first')
        print(json.dumps({'status':'configured' if args.configure else 'live_test_armed' if args.arm_live_test else 'valid',
                          'enabled':schedule.enabled,'timezone':schedule.timezone,'morning':schedule.morning_time,
                          'evening':schedule.evening_time,'controlled_jobs':len(schedule.test_jobs),
                          'temporary_window_seconds':300 if args.arm_live_test else None}))
        return 0
    except (ValueError,OSError,SkillError):
        print('Invalid proactive configuration. Check docs/STAGE5.md; no notification was sent.')
        return 2


if __name__=='__main__': raise SystemExit(main())
