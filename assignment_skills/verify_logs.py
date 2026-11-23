"""Metadata-only proof of real tool reads and Telegram delivery; no account contents."""
import json
import re
from datetime import datetime,timezone
from pathlib import Path
from assignment_skills.config import ROOT


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


def verify(events,routes):
    start=max((i for i,e in enumerate(events) if e.get('status')=='ready'),default=-1)
    by_request={r.get('request_id'):r for r in routes if isinstance(r.get('request_id'),str)}
    names={'calendar':'google_calendar_ical','email':'gmail_imap_readonly','web_search':'google_news_wikipedia','weather':'open_meteo'}
    proofs=[]
    for event in events[start+1:]:
        request_id=event.get('route_request_id')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id): continue
        route=by_request.get(request_id,{})
        tool=route.get('skill_execution')
        if (not isinstance(tool,dict) or tool.get('skill') not in names or tool!=event.get('skill_execution')
                or event.get('authorized') is not True or event.get('success') is not True
                or event.get('delivery_success') is not True or event.get('tool_success') is not True
                or event.get('model_success') is not False or event.get('status')!='responded'
                or route.get('route')!='tool' or route.get('selected_model') is not None
                or route.get('success') is not True or tool.get('success') is not True
                or tool.get('source') not in ({'open_meteo','wttr'} if tool['skill']=='weather' else {names[tool['skill']]}) or tool.get('sending_enabled') is not False
                or type(tool.get('result_count')) is not int or tool['result_count']<=0
                or type(event.get('update_id')) is not int): continue
        proofs.append({'update_id':event['update_id'],'request_id':request_id,'skill':tool['skill'],
                       'operation':tool['operation'],'source':tool['source'],'result_count':tool['result_count'],
                       'providers_succeeded':tool.get('providers_succeeded',[]),'reply_delivered':True,
                       'latency_ms':event['latency_ms']})
    return {'verified_skills':{name:any(p['skill']==name for p in proofs) for name in names},
            'verified_operations':{name:sorted({p['operation'] for p in proofs if p['skill']==name}) for name in names},
            'proofs':proofs,'email_sending_enabled':False}
