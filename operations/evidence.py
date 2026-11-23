"""Conservative sampled-continuity check. Does not turn local logs into cloud proof."""
from datetime import datetime
import json
from pathlib import Path
import re


def continuity(events,required_seconds=48*3600,max_gap_seconds=150):
    longest=0;start=last=None;boot=None;previous_uptime=None;accepted=0;unhealthy=0
    for e in events:
        if not isinstance(e,dict):
            unhealthy+=1;start=last=boot=previous_uptime=None;continue
        if e.get('message')!='service_heartbeat': continue
        try:
            t=datetime.fromisoformat(e['timestamp'])
            identity=e['boot_id'];uptime=e['uptime_seconds']
            if (t.tzinfo is None or not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{32}',identity)
                    or type(uptime) not in {int,float} or not 0<=uptime<2**53
                    or e.get('telegram_poll_healthy') is not True or e.get('scheduler_healthy') is not True): raise ValueError
        except (ValueError,KeyError,TypeError):
            unhealthy+=1;start=last=boot=previous_uptime=None;continue
        accepted+=1
        gap=(t-last).total_seconds() if last else None
        if (identity!=boot or gap is None or not 0<gap<=max_gap_seconds
                or previous_uptime is None or abs((uptime-previous_uptime)-gap)>5): start=t
        longest=max(longest,(t-start).total_seconds())
        last,boot,previous_uptime=t,identity,uptime
    return {'required_seconds':required_seconds,'max_allowed_sample_gap_seconds':max_gap_seconds,
            'healthy_heartbeats':accepted,'unhealthy_or_invalid_heartbeats':unhealthy,
            'longest_healthy_same_boot_span_seconds':longest,
            'sampled_48_hour_continuity_verified':longest>=required_seconds,
            'managed_restart_aggregation_supported':False,'cloud_storage_persistence_verified':False,
            'full_course_compliance_claimed':False}


def main():
    root=Path(__file__).resolve().parents[1]
    path=root/'router/logs/operations.jsonl'
    try:
        events=[json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        result=continuity(events)
        print(json.dumps(result,indent=2));return 0 if result['sampled_48_hour_continuity_verified'] else 3
    except (ValueError,TypeError,OSError):
        print('Operational evidence unavailable or malformed.');return 2


if __name__=='__main__': raise SystemExit(main())
