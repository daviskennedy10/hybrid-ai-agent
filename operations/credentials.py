"""Read-only expiry metadata. JWT payload is unverified and is not authentication proof."""
import base64
from datetime import datetime,timezone
import json
from pathlib import Path


def expiry_status(store,now=None):
    expiries=[];unknown=0
    def visit(value):
        nonlocal unknown
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'access_token','api_key'} and isinstance(item,str) and item:
                    try:
                        payload=item.split('.')[1]
                        exp=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))['exp']
                        if type(exp) not in {int,float} or not 0<exp<2**53: raise ValueError
                        expiries.append(exp)
                    except (ValueError,KeyError,IndexError,TypeError): unknown+=1
                else: visit(item)
        elif isinstance(value,list):
            for item in value: visit(item)
    visit(store)
    epoch=(now or datetime.now(timezone.utc)).timestamp()
    remaining=int(min(expiries)-epoch) if expiries else None
    return {'read_only':True,'jwt_expiry_metadata_available':bool(expiries),'unknown_token_expiries':unknown,
            'minimum_remaining_seconds':remaining,'all_observed_tokens_cover_48_hours':remaining is not None and remaining>=172800 and unknown==0,
            'jwt_signature_validated':False,'provider_acceptance_verified':False,'refresh_performed':False}


def main():
    try: result=expiry_status(json.loads((Path.home()/'.hermes/auth.json').read_text()))
    except (ValueError,OSError): print('Credential expiry metadata unavailable; no authentication was modified.');return 2
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__': raise SystemExit(main())
