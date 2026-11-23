"""Join API-accepted deliveries to successful routed skill reads; export metadata only."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
import re
from proactive.config import ROOT


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def valid_id(value,length):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{'+str(length)+'}',value) is not None


def verify(events,routes):
    by_request={r.get('request_id'):r for r in routes if valid_id(r.get('request_id'),32)}
    proofs=[];successes=[]
    for event in events:
        if (event.get('message')!='proactive_delivery' or event.get('success') is not True or event.get('status')!='sent'
                or not valid_id(event.get('notification_key'),64) or not valid_id(event.get('run_id'),32)
                or event.get('origin') not in {'scheduler','manual'}): continue
        successes.append(event['notification_key'])
        ids=event.get('source_request_ids');kind=event.get('kind')
        expected={'morning':['calendar','email','weather'],'evening':['calendar','email','calendar'],
                  'event':['calendar'],'conflict':['calendar'],'urgent':['email']}.get(kind)
        if kind=='morning' and isinstance(ids,list) and len(ids)==4: expected=['calendar','email','calendar','weather']
        if not expected or not isinstance(ids,list) or len(ids)!=len(expected): continue
        tools=[]
        for request_id,name in zip(ids,expected):
            r=by_request.get(request_id,{});tool=r.get('skill_execution') or {}
            allowed_sources={'calendar':{'google_calendar_ical'},'email':{'gmail_imap_readonly'},'weather':{'open_meteo','wttr'}}
            if (r.get('route')!='tool' or r.get('success') is not True or r.get('private') is not True
                    or r.get('selected_model') is not None or tool.get('skill')!=name or tool.get('success') is not True
                    or tool.get('sending_enabled') is not False or tool.get('source') not in allowed_sources[name]): break
            tools.append({'request_id':request_id,'skill':name,'operation':tool.get('operation'),
                          'source':tool['source'],'result_count':tool.get('result_count')})
        else:
            if event.get('quality')!='complete': continue
            proofs.append({'notification_key':event['notification_key'],'run_id':event['run_id'],'kind':kind,
                           'origin':event['origin'],'telegram_api_accepted':True,'sources':tools,
                           'latency_ms':event.get('latency_ms'),'timestamp':event.get('timestamp')})
    sent_runs={p['notification_key']:p['run_id'] for p in proofs}
    suppressions=[e for e in events if e.get('message')=='proactive_suppressed' and e.get('status')=='sent'
                  and e.get('notification_key') in sent_runs and valid_id(e.get('run_id'),32)]
    duplicate_keys=[key for key,n in Counter(successes).items() if n>1]
    checks={'scheduler_'+kind:any(p['kind']==kind and p['origin']=='scheduler' for p in proofs)
            for kind in ['morning','evening','event','conflict','urgent']}
    checks.update({'manual_'+kind:any(p['kind']==kind and p['origin']=='manual' for p in proofs) for kind in ['morning','evening']})
    checks.update({'repeated_poll_deduplication':bool(suppressions),
                   'restart_deduplication':any(e['run_id']!=sent_runs[e['notification_key']] for e in suppressions),
                   'no_duplicate_accepted_deliveries':not duplicate_keys})
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'verified':all(checks.values()),
            'proofs':proofs,'suppression_count':len(suppressions),'duplicate_success_count':len(duplicate_keys),
            'email_sending_enabled':False,'user_receipt_confirmation_required':True}
