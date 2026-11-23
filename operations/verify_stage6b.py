"""Metadata-only Stage 6B live evidence; never reads memory values or account bodies."""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
from proactive.config import ROOT


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []


def verify(events,routes,notifications):
    by_id={r.get('request_id'):r for r in routes}
    proofs=[]
    for e in events:
        r=by_id.get(e.get('route_request_id'),{});m=r.get('memory_execution') or {}
        if (e.get('authorized') is True and e.get('success') is True and e.get('delivery_success') is True
                and r.get('success') is True and r.get('route')=='memory' and e.get('memory_success') is True
                and m.get('record_count',0)>0 and m.get('state_digest')):
            proofs.append({'request_id':r['request_id'],'boot_id':e.get('boot_id'),'timestamp':e['timestamp'],
                           'operation':m['operation'],'revision':m['revision'],'record_count':m['record_count'],
                           'principal_scope':m.get('principal_scope'),'matched_count':m.get('matched_count',0),'state_digest':m['state_digest'],'reply_delivered':True})
    writes=[p for p in proofs if p['operation']=='write'];reads=[p for p in proofs if p['operation']=='read' and p['matched_count']>0]
    resets=[e for e in events if e.get('status')=='conversation_reset' and e.get('authorized') is True and e.get('success') is True]
    after_new=any(w['state_digest']==r['state_digest'] and w.get('principal_scope')==r.get('principal_scope')==reset.get('session_scope') and w['timestamp']<reset['timestamp']<r['timestamp']
                  and w['boot_id']==r['boot_id']==reset.get('boot_id') for w in writes for r in reads for reset in resets)
    after_restart=any(w['state_digest']==r['state_digest'] and w.get('principal_scope')==r.get('principal_scope') and w['timestamp']<r['timestamp'] and w['boot_id']!=r['boot_id']
                      and w['boot_id'] and r['boot_id'] for w in writes for r in reads)
    model_context=False
    for e in events:
        r=by_id.get(e.get('route_request_id'),{});m=r.get('memory_execution') or {}
        attempts=r.get('attempts') or [];execution=attempts[-1].get('execution') if attempts else None
        if (e.get('authorized') is True and e.get('delivery_success') is True and e.get('model_success') is True
                and r.get('success') is True and r.get('route') in {'local','cloud'} and isinstance(execution,dict)
                and execution.get('wire_model')==r.get('selected_model') and m.get('operation')=='context'
                and any(w['state_digest']==m.get('state_digest') and w.get('principal_scope')==m.get('principal_scope') for w in writes)): model_context=True
    briefing=False;recap=False
    for n in notifications:
        if n.get('message')!='proactive_delivery' or n.get('success') is not True or n.get('status')!='sent': continue
        source=[by_id.get(k,{}) for k in n.get('source_request_ids',[])]
        if not source or not all(r.get('route')=='tool' and r.get('success') is True for r in source): continue
        if n.get('kind')=='morning' and n.get('quality')=='complete' and n.get('free_slot_count') is not None:
            briefing=any((r.get('skill_execution') or {}).get('skill')=='calendar' and r['skill_execution']['operation']=='free' for r in source)
        counts=n.get('activity_counts')
        if n.get('kind')=='evening' and isinstance(counts,dict) and counts.get('request_handled',0)>0 and counts.get('tool_success',0)>0: recap=True
    checks={'memory_write_delivered':bool(writes),'recall_after_new':after_new,'recall_after_process_restart':after_restart,
            'real_model_context_reply':model_context,'briefing_real_free_slots':briefing,'recap_verified_activity':recap}
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'local_telegram_verified':all(checks.values()),
            'memory_proofs':proofs,'gcs_live_verified':False,'publication_verified':False,'user_receipt_confirmation_required':True}
