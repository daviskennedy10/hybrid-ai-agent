"""Public-provider diagnostics only; no account secrets or response bodies are logged."""
from datetime import datetime,timezone
import json
import argparse
import os
import socket
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,build_opener,ProxyHandler
from assignment_skills.config import Config,ROOT
from assignment_skills.common import NoRedirect
from assignment_skills.weather import Weather


def probe(name,url,config,capture=False):
    started=time.monotonic()
    report={'provider':name,'http_received':False,'json_received':False,'validation_passed':False}
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(Request(url,headers={'User-Agent':'CSCI599Assignment4/1.0'}),timeout=8) as response:
            report['http_status']=response.code
            body=response.read(2_000_001)
        report['http_received']=True
        if len(body)>2_000_000: raise ValueError
        data=json.loads(body)
        report['json_received']=isinstance(data,dict)
        if capture and isinstance(data,dict):
            path=ROOT/('secrets/stage4-weather-'+name+'.json')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as stream: json.dump(data,stream)
            path.chmod(0o600)
            report['public_response_captured']=True
        class Captured:
            def json(self,_): return data
        try:
            adapter=Weather(config,Captured())
            if name=='open_meteo': adapter._open_meteo()
            else: adapter._wttr('forecast')
            report['validation_passed']=True
        except Exception as error:
            report['validation_failure_kind']=getattr(error,'kind','invalid_response')
    except HTTPError as error:
        report.update(failure_phase='http',http_status=error.code)
    except URLError as error:
        reason=error.reason
        report.update(failure_phase='transport',reason_type=type(reason).__name__,errno=getattr(reason,'errno',None))
    except (TimeoutError,socket.timeout) as error:
        report.update(failure_phase='transport',reason_type=type(error).__name__)
    except (ValueError,TypeError,OSError) as error:
        report.update(failure_phase='parse_or_transport',reason_type=type(error).__name__)
    report['latency_ms']=round((time.monotonic()-started)*1000,2)
    return report


def main():
    parser=argparse.ArgumentParser(description='Independent public weather transport and schema checks')
    parser.add_argument('--capture',action='store_true',help='Save bounded public weather JSON in ignored owner-only files for local debugging; never account data')
    parser.add_argument('--provider',choices=['all','open_meteo','wttr'],default='all',help='Probe and validate a provider independently')
    args=parser.parse_args()
    try:
        config=Config.load();p=config.profile
        primary='https://api.open-meteo.com/v1/forecast?'+urlencode({
            'latitude':p['latitude'],'longitude':p['longitude'],'timezone':p['timezone'],
            'current':'temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m',
            'daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code',
            'forecast_days':3,'temperature_unit':'celsius','wind_speed_unit':'kmh'})
        fallback='https://wttr.in/'+str(p['latitude'])+','+str(p['longitude'])+'?format=j1'
        targets={'open_meteo':primary,'wttr':fallback}
        names=list(targets) if args.provider=='all' else [args.provider]
        report={'timestamp':datetime.now(timezone.utc).isoformat(),
                'probes':[probe(name,targets[name],config,args.capture) for name in names]}
        path=ROOT/'router/logs/stage4-weather-diagnostic.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        return 0 if any(p['validation_passed'] for p in report['probes']) else 3
    except Exception:
        print('Weather diagnostic setup failed; no credentials displayed.');return 2


if __name__=='__main__':
    raise SystemExit(main())
