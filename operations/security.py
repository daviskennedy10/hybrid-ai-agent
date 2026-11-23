"""Metadata-only file output and narrow worker environment. No secret discovery."""
import fcntl
import json
import math
import os
import re
import stat
import sys
import threading

STDOUT_LOCK=threading.Lock()
from datetime import datetime
from pathlib import Path

ENUMS={
 'message':{'hybrid_router_skill','hybrid_router_request','telegram_event','proactive_tool_read','proactive_delivery',
            'proactive_suppressed','proactive_started','proactive_stopped','service_heartbeat','isolation_report','hybrid_router_memory','proactive_trigger','private_inference_probe','gateway_event'},
 'route':{'local','cloud','held','tool','memory'},'initial_route':{'local','cloud','held','tool','memory'},
 'origin':{'manual','scheduler'},'quality':{'complete','partial'},
 'reason':{'privacy','private_context_exceeds_budget','long_context','complex_reasoning','simple_task','conservative_default','skill_request','memory_request'},
 'fallback':{'local_unavailable_to_cloud','local_failure_to_cloud','cloud_rate_limit_to_local','cloud_rate_limit_local_unavailable','cloud_rate_limit_local_ineligible'},
 'provider':{'custom','openai-codex','open_meteo','wttr'},
 'source':{'google_calendar_ical','gmail_imap_readonly','google_news_wikipedia','open_meteo','wttr'},
 'skill':{'calendar','email','web_search','weather'},
 'operation':{'day','upcoming','free','overlaps','unread','summarize','urgent','draft','send','blocked','search','current','forecast','read','write','forget','context'},
 'api_mode':{'chat_completions','codex_responses'},'severity':{'INFO','WARNING','ERROR'},'trigger_type':{'scheduled_heartbeat'},
 'endpoint_host':{'127.0.0.1','localhost','::1','chatgpt.com'},
 'status':{'success','failure','held','pending','building','sending','sent','uncertain','failed','expired',
           'invalid_update','duplicate','unsupported_update','invalid_identity','rejected','received','invalid_message',
           'conversation_reset','stopping','responded','skill_failed','model_failed','command','delivery_failed','poll_failed','ready','stopped','proactive_manual'},
 'failure':{'unavailable','timeout','rate_limit','server_error','auth','context_overflow','invalid_request','model_not_found',
            'unknown','worker_error','incomplete','not_configured','forbidden','invalid_response','unsupported_calendar','wrong_calendar',
            'recipient_blocked','sending_disabled','private_search_blocked','transport','unauthorized_token','conflict',
            'api_error','rate_limit_long','webhook_configured','invalid_message','internal_error','internal_delivery_error','preparation_failed'},
}
ENUMS['kind']=ENUMS['failure']|{'morning','evening','event','conflict','urgent'}
ENUMS['primary_failure']=ENUMS['failure'];ENUMS['notification_status']=ENUMS['status']
BOOLS={'success','private','authorized','delivery_success','model_success','tool_success','enabled','sending_enabled',
       'simulated_cloud_rate_limit','provider_fallback','tool_invoked','memory_success','telegram_poll_healthy','scheduler_healthy','scheduler_enabled'}
NUMBERS={'latency_ms','attempt','result_count','update_id','tool_successes','tool_failures','uptime_seconds',
         'poll_age_seconds','scheduler_age_seconds','rss_peak_bytes','memory_current_bytes','revision','record_count','matched_count','poll_seconds','free_slot_count'}
IDS={'request_id':32,'route_request_id':32,'run_id':32,'boot_id':32,'notification_key':64,'trigger_key':64,'state_digest':64,'principal_scope':64,'session_scope':64}
MODELS={'model','selected_model','wire_model','normalized_response_model'}


def metadata(event,depth=0):
    if depth>5 or not isinstance(event,dict): return {}
    safe={}
    for key,value in event.items():
        if key not in ENUMS and key not in BOOLS|NUMBERS|MODELS|set(IDS)|{
            'timestamp','scheduled_at','attempts','execution','skill_execution','memory_execution','provider_attempts','state_counts','providers_succeeded','source_request_ids','activity_counts'}: continue
        if value is None: safe[key]=None;continue
        if key in ENUMS: safe[key]=value if isinstance(value,str) and value in ENUMS[key] else '[redacted]'
        elif key in BOOLS: safe[key]=value if type(value) is bool else None
        elif key in NUMBERS:
            safe[key]=value if type(value) in {int,float} and 0<=value<2**53 and math.isfinite(value) else None
        elif key in IDS:
            safe[key]=value if isinstance(value,str) and re.fullmatch('[a-f0-9]{'+str(IDS[key])+'}',value) else None
        elif key in MODELS:
            safe[key]=value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',value) and not value.startswith(('sk-','eyJ')) else '[redacted]'
        elif key in {'timestamp','scheduled_at'}:
            try:
                if not isinstance(value,str) or len(value)>60 or datetime.fromisoformat(value).tzinfo is None: raise ValueError
                safe[key]=value
            except ValueError: safe[key]=None
        elif key in {'execution','skill_execution','memory_execution'}: safe[key]=metadata(value,depth+1)
        elif key in {'attempts','provider_attempts'}:
            safe[key]=[metadata(x,depth+1) for x in value[:20]] if isinstance(value,list) else []
        elif key=='state_counts':
            safe[key]={k:v for k,v in value.items() if k in {'pending','building','sending','sent','uncertain','failed','expired'} and type(v) is int and 0<=v<2**53} if isinstance(value,dict) else {}
        elif key=='activity_counts':
            from operations.activity import CATEGORIES
            safe[key]={k:v for k,v in value.items() if k in CATEGORIES|{'notifications_delivered'} and type(v) is int and 0<=v<2**53} if isinstance(value,dict) else None
        elif key=='source_request_ids':
            safe[key]=[v for v in value[:10] if isinstance(v,str) and re.fullmatch('[a-f0-9]{32}',v)] if isinstance(value,list) else []
        elif key=='providers_succeeded':
            safe[key]=[v for v in value[:5] if isinstance(v,str) and v in {'google_calendar_ical','gmail_imap_readonly','google_news','wikipedia','open_meteo','wttr'}] if isinstance(value,list) else []
    return safe


def append_json(path,event):
    """Owner-only, no-follow regular log, serialized appends across worker threads."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(metadata(event),sort_keys=True,allow_nan=False)+'\n').encode()
    if len(data)>65536: raise ValueError('Metadata event too large')
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('Regular log required')
        os.fchmod(fd,0o600)
        fcntl.flock(fd,fcntl.LOCK_EX)
        with os.fdopen(fd,'ab',closefd=False) as stream:
            stream.write(data);stream.flush()
    finally: os.close(fd)
    if os.environ.get('A3_LOG_STDOUT')=='1':
        with STDOUT_LOCK:
            sys.stdout.write(data.decode());sys.stdout.flush()


def worker_environment(source):
    # Model credentials are resolved from the existing read-only Hermes profile.
    # Do not forward bot/Google/cloud/Tailscale secrets or Python injection variables.
    allowed={'HOME','PATH','LANG','LC_ALL','LC_CTYPE','TMPDIR','TMP','TEMP',
             'SSL_CERT_FILE','SSL_CERT_DIR','HERMES_HOME','HERMES_PROFILE'}
    env={k:v for k,v in source.items() if k in allowed}
    env.update({'NO_PROXY':'*','no_proxy':'*','PYTHONDONTWRITEBYTECODE':'1'})
    return env
