"""Safe setup/probes. No account contents or credentials are printed by probes."""
import argparse
from datetime import datetime,timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from assignment_skills.config import Config,ROOT
from assignment_skills.dispatch import Dispatcher
from assignment_skills.common import SkillError
from router.core import JsonLog,Policy,Request,Router
from router.hermes_backend import HermesBackend


def fixtures(config):
    day=datetime.now(ZoneInfo(config.profile['timezone'])).date()+timedelta(days=1)
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//CSCI599 Stage4 Synthetic//EN',
           'X-WR-CALNAME:CSCI599 Stage4 Synthetic']
    for name,start,end in [('planning','100000','110000'),('overlap','103000','113000'),('review','140000','143000')]:
        lines += ['BEGIN:VEVENT','UID:stage4-'+name+'@assignment.invalid',
                  'DTSTAMP:'+datetime.now(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ'),
                  'DTSTART;TZID='+config.profile['timezone']+':'+day.strftime('%Y%m%d')+'T'+start,
                  'DTEND;TZID='+config.profile['timezone']+':'+day.strftime('%Y%m%d')+'T'+end,
                  'SUMMARY:Synthetic '+name,'END:VEVENT']
    lines += ['END:VCALENDAR','']
    path=ROOT/'secrets/stage4-calendar-fixture.ics'
    path.write_text('\r\n'.join(lines));path.chmod(0o600)
    return path


def main():
    parser=argparse.ArgumentParser(description='Assignment-only Stage 4 configuration and read-only probes')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--fixtures',action='store_true')
    parser.add_argument('--probe',choices=['calendar','email','web_search','weather','all'])
    args=parser.parse_args()
    try:
        config=Config.load()
        if args.fixtures:
            print('Import only into the dedicated calendar:',fixtures(config))
            return 0
        if args.probe:
            router=Router(Policy.load(),HermesBackend(Policy.load()),JsonLog(ROOT/'router/logs/stage4-probes.jsonl'),skill_dispatcher=Dispatcher(config))
            prompts={'calendar':"What's on my calendar tomorrow?",'email':'Summarize my unread emails.',
                     'web_search':'Search the web for recent artificial intelligence developments.','weather':"What's the weather forecast for Los Angeles?"}
            selected=prompts if args.probe=='all' else [args.probe]
            success=True
            for name in selected:
                result=router.run(Request(prompts[name],principal_id=min(config.allowed_user_ids)))
                print(json.dumps({k:result.get(k) for k in ['request_id','route','selected_model','success','failure','skill_execution','latency_ms']}))
                success=success and result['success']
            return 0 if success else 3
        ready={}
        for name,check in [('calendar',config.calendar_ready),('email',config.email_ready)]:
            try: check();ready[name]=True
            except SkillError: ready[name]=False
        print(json.dumps({'config_valid':True,'allowed_principal_count':len(config.allowed_user_ids),
                          'account_configured':ready,'email_sending_enabled':False,'weather_profile_fictitious':True}))
        return 0
    except (SkillError,ValueError,OSError):
        print('Stage 4 configuration or tool setup failed. See docs/STAGE4_SETUP.md; do not paste credentials.')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
