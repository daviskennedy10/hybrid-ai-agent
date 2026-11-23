"""Offline replay of six genuine synthetic outcomes, never model inference."""
import hashlib
import json
from pathlib import Path
from benchmarks.cases import cases,score

ROOT=Path(__file__).resolve().parent

def verify():
    raw=(ROOT/'results/2026-11-19.jsonl').read_bytes()
    manifest=json.loads((ROOT/'results/manifest.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=manifest['results_sha256']:raise ValueError('Benchmark data changed')
    rows=[json.loads(line) for line in raw.splitlines() if line]
    expected={(c.identifier,r) for c in cases() for r in ('local','cloud')}
    if len(rows)!=6 or {(r['category'],r['forced_backend']) for r in rows}!=expected:raise ValueError('Expected six distinct cells')
    output=[]
    for row in rows:
        case=next(c for c in cases() if c.identifier==row['category'])
        local=row['forced_backend']=='local';execution=row['execution']
        if (row['success'] is not True or row['fallback_performed'] is not False
            or execution['wire_model']!=('gemma4:e2b' if local else 'gpt-5.6-sol')
            or execution['provider']!=('custom' if local else 'openai-codex')
            or row['quality']!=score(case,row['response'])
            or row['user_prompt_utf8_bytes']!=len(case.prompt.encode())
            or row['isolated_turn_wall_ms']<=0):raise ValueError('Invalid recorded result')
        output.append({'category':row['category'],'model':execution['wire_model'],
                       'seconds':round(row['isolated_turn_wall_ms']/1000,3),'strict_score':row['quality']['points']})
    return output

if __name__=='__main__':print(json.dumps({'new_inference_performed':False,'verified_results':verify()},indent=2))
