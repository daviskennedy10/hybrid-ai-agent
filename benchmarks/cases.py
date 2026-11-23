from dataclasses import dataclass
import json
import math
from router.core import Policy,Request,decide


@dataclass(frozen=True)
class Case:
    identifier:str
    prompt:str
    expected:dict
    route:str
    reason:str


def cases():
    simple=Case('simple',
        'What is 17 + 25? Also identify the capital of France and compare 3/4 with 2/3. '
        'Return only JSON: sum (integer), capital (string), greater_fraction (string), '
        'cross_products (array [3*3, 2*4]). No tools or external lookup are needed.',
        {'sum':42,'capital':'Paris','greater_fraction':'3/4','cross_products':[9,8]},'local','simple_task')
    planning=Case('planning',
        'Analyze this public synthetic project. Unlimited workers, zero transfer time, tasks cannot be interrupted. '
        'A takes 2 minutes and has no prerequisites. B takes 3 after A. C takes 4 after A. D takes 2 after B. '
        'E takes 1 after both C and D. Find the earliest feasible schedule and critical path. '
        'Return only JSON: makespan (minutes), critical_path (ordered array of task letters), '
        'starts (object mapping every task to its earliest start minute), slack_C (minutes).',
        {'makespan':8,'critical_path':['A','B','D','E'],'starts':{'A':0,'B':2,'C':2,'D':5,'E':7},'slack_C':1},
        'cloud','complex_reasoning')
    rows=[f'Record R{i:04d} | units={(i%9)+1} | batch=public-demo | note=generated filler for context retrieval, not account data.' for i in range(280)]
    # 280 public rows keep the request above the production long-context threshold
    # and below the local counterfactual safety budget. Oracle is computed exactly.
    context=Case('context',
        'PUBLIC SYNTHETIC BENCHMARK LEDGER. No real accounts, people or sensitive data. Initial status: queued.\n'
        +'\n'.join(rows)+'\nFINAL UPDATE: status=ready.\n'
        +'Extract the ledger facts. Return only JSON: row_count, total_units, R0217_units, final_status. '
         'Count only Record rows; use the final update for status.',
        {'row_count':280,'total_units':sum((i%9)+1 for i in range(280)),
         'R0217_units':(217%9)+1,'final_status':'ready'},'cloud','long_context')
    return (simple,planning,context)


def score(case,text):
    """Four binary, type-aware checks; formatting is reported separately.

    A malformed/non-JSON answer earns 0/4 under this preregistered strict task.
    Human review may explain substantive correctness but must not replace the
    original score or silently change criteria after seeing results.
    """
    try:answer=json.loads(text)
    except (ValueError,TypeError):answer=None
    checks={key:isinstance(answer,dict) and key in answer and strict_equal(answer[key],value)
            for key,value in case.expected.items()}
    return {'points':sum(checks.values()),'maximum':4,'criteria':checks,
            'valid_json_object':isinstance(answer,dict)}


def strict_equal(actual,expected):
    if type(expected) in {int,float}:
        return type(actual) in {int,float} and math.isfinite(actual) and actual==expected
    if isinstance(expected,str):return isinstance(actual,str) and actual.strip().casefold()==expected.casefold()
    if type(actual)!=type(expected):return False
    if isinstance(expected,dict):return actual.keys()==expected.keys() and all(strict_equal(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):return len(actual)==len(expected) and all(strict_equal(a,b) for a,b in zip(actual,expected))
    return actual==expected


def check_route_safety(case,route,policy):
    if route not in {'local','cloud'}:raise ValueError('Explicit model route required')
    request=Request(case.prompt)
    decision=decide(request,policy)
    if route=='cloud' and decision.private:raise ValueError('Private cases cannot be sent to cloud')
    if route=='local' and len(request.text.encode())>policy.local_input_bytes:raise ValueError('Local input budget exceeded')
    return decision
