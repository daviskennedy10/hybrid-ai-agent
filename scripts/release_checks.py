"""Independent secret/advisory checks; never commits, pushes or starts services.

Requires public registry access. Scans an exact staged snapshot, with credential
verification disabled. Raw tool output stays in ignored owner-only local files.
Findings are never automatically waived. Exit 0 requires all four tools/checks
to complete and report zero findings, not merely a successful scanner process.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def classify(kind,code,raw):
    try:
        data=json.loads(raw)
        if kind=='secrets':
            results=data['results']
            if not isinstance(results,dict):raise ValueError()
            count=sum(len(rows) for rows in results.values())
        else:
            rows=data.get('dependencies') if isinstance(data,dict) else data
            if not isinstance(rows,list):raise ValueError()
            if any(row.get('skip_reason') for row in rows):raise ValueError()
            count=sum(len(row['vulns']) for row in rows)
        return {'completed':code in {0,1},'finding_count':count,
                'passed':code==0 and count==0,'exit_code':code}
    except (ValueError,KeyError,TypeError,AttributeError):
        return {'completed':False,'finding_count':None,'passed':False,'exit_code':code}


def main():
    if not shutil.which('uvx'):raise ValueError('uvx required')
    private=ROOT/'reports/local'/('release-checks-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    private.mkdir(parents=True,mode=0o700)
    os.chmod(private,0o700)
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).split(b'\0')
    if not any(names):raise ValueError('No staged public source')
    with tempfile.TemporaryDirectory(prefix='hybrid-release-scan-') as tmp:
        snapshot=Path(tmp)/'source';snapshot.mkdir()
        subprocess.run(['git','checkout-index','--all','--prefix='+str(snapshot)+'/'],cwd=ROOT,check=True,capture_output=True)
        optional=[]
        inventory=json.loads((snapshot/'reports/optional-hermes-dependencies.json').read_text())
        for row in inventory['packages']:
            if row['name'].lower().replace('_','-')=='hermes-agent':continue
            if not re.fullmatch(r'[A-Za-z0-9_.-]+',row['name']) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+',row['version']):raise ValueError('Invalid package metadata')
            optional.append(row['name']+'=='+row['version'])
        requirements=Path(tmp)/'optional-hermes.txt';requirements.write_text('\n'.join(optional)+'\n')
        jobs={
            'independent_secrets':['uvx','--from','detect-secrets','detect-secrets','scan','--all-files','--no-verify',str(snapshot)],
            'core_advisories':['uvx','pip-audit','--disable-pip','--require-hashes','-r',str(snapshot/'requirements.lock'),'--format','json'],
            'optional_hermes_advisories':['uvx','pip-audit','--disable-pip','--no-deps','-r',str(requirements),'--format','json'],
            'optional_gcs_advisories':['uvx','pip-audit','-r',str(snapshot/'persistence/requirements-cloud.txt'),'--format','json'],
        }
        def run(item):
            name,command=item
            try:
                result=subprocess.run(command,cwd=snapshot,capture_output=True,timeout=240)
                for suffix,body in [('json',result.stdout),('stderr.txt',result.stderr)]:
                    with os.fdopen(os.open(private/(name+'.'+suffix),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:f.write(body)
                return name,classify('secrets' if name=='independent_secrets' else 'advisories',result.returncode,result.stdout)
            except Exception:
                return name,{'completed':False,'finding_count':None,'passed':False,'exit_code':None}
        with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(run,jobs.items()))
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'checks':results,
            'secret_value_network_verification_disabled':True,
            'optional_inventory_framework_itself_excluded':'Local pinned Hermes source is not a PyPI release; its dependencies were enumerated separately.',
            'optional_gcs_scope':'Resolve the optional pinned GCS SDK and its transitive dependencies in the auditor; no account or bucket access.',
            'raw_output_directory':str(private.relative_to(ROOT)),
            'publication_authorized':False,'committed_or_pushed':False}
    (private/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if all(x['passed'] for x in results.values()) else 1


if __name__=='__main__':raise SystemExit(main())
