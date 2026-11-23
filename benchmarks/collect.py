"""Optional six-turn collector. Default prints a plan and performs no requests.

Independent private profile, explicit cost/load approval and maximum six turns.
New outputs are ignored local files. Historical results are never overwritten.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time
from benchmarks.cases import cases,score,check_route_safety
from router.core import Policy,Request
from router.hermes_backend import HermesBackend

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--confirm-independent-profile-and-inference-costs',action='store_true')
    args=p.parse_args()
    if not args.execute:
        print(json.dumps({'actual_inference':False,'planned_turns':6,'cloud_turns':3,'local_turns':3,'tasks':[c.identifier for c in cases()]}));return 0
    profile=ROOT/'secrets/benchmark-hermes'
    if not args.confirm_independent_profile_and_inference_costs or not (profile/'config.yaml').is_file() or not (profile/'auth.json').is_file():
        raise ValueError('Create and authenticate a NEW ignored secrets/benchmark-hermes profile; explicitly approve six turns first')
    if profile.is_symlink() or any(x.is_symlink() for x in profile.rglob('*')):raise ValueError('Independent regular profile required')
    os.environ['HERMES_HOME']=str(profile)
    policy=Policy.load();backend=HermesBackend(policy)
    output=ROOT/'reports/local';output.mkdir(parents=True,exist_ok=True)
    target=output/('benchmark-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.jsonl')
    with target.open('x') as stream:
        for case in cases():
            for route in ('local','cloud'):
                natural=check_route_safety(case,route,policy);started=time.monotonic()
                row={'category':case.identifier,'forced_backend':route,'natural_route':natural.route,
                     'normalized_sdk_usage':None,'usage_note':'This portable collector does not instrument SDK token usage.'}
                try:
                    result=backend.infer(route,Request(case.prompt))
                    row.update(success=True,response=result.response,execution=result.execution,quality=score(case,result.response))
                except Exception:
                    row.update(success=False,failure='inference_failed',response=None,quality=None)
                row['wall_ms']=(time.monotonic()-started)*1000
                stream.write(json.dumps(row)+'\n');stream.flush()
    print('Saved six-turn results to ignored local reports; no historical data modified.')
    return 0

if __name__=='__main__':raise SystemExit(main())
