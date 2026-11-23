"""Offline candidate scanner. No secret values or matching snippets are printed.

Scans all non-runtime candidate files, staged blobs and all reachable Git history.
Known private-value scanning is an additional local-only preparation check.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
IGNORED={'.git','.venv','hermes-agent','__pycache__','.pytest_cache'}
FORBIDDEN_PARTS={'secrets','logs','runtime','state','.hermes','.codex','.tailscale','node_modules'}
PATTERNS={
 'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'telegram_token':rb'\b[0-9]{8,}:[A-Za-z0-9_-]{25,}',
 'tailscale_key':rb'tskey-(?:auth|api)-[A-Za-z0-9_-]{20,}',
 'api_key':rb'\bsk-[A-Za-z0-9_-]{24,}',
 'github_token':rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
 'google_api_key':rb'\bAIza[A-Za-z0-9_-]{25,}',
 'jwt':rb'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}',
 'absolute_home':rb'/Users/[A-Za-z0-9_.-]+/',
 'tailnet_identity':rb'[a-zA-Z0-9-]+\.tail[0-9a-z]+\.ts\.net',
 'deployed_run_url':rb'https://[a-zA-Z0-9-]+\.[a-z0-9-]+\.run\.app',
}

def file_bytes(root=ROOT):
    out={}
    for p in root.rglob('*'):
        parts=p.relative_to(root).parts
        if parts[:2]==('reports','local'):continue
        if any(x in IGNORED for x in parts):continue
        if p.is_symlink():raise ValueError('Candidate symlink requires review')
        if not p.is_file():continue
        name=p.relative_to(root).as_posix()
        if any(x in FORBIDDEN_PARTS for x in parts) or p.suffix in {'.key','.pem','.sqlite','.sqlite3','.db','.mp4','.zip','.pdf'}:
            raise ValueError('Private/generated material in candidate')
        if p.stat().st_size>2*1024*1024:raise ValueError('Unexpected large public file')
        out[name]=p.read_bytes()
    return out

def findings(entries,review):
    approved={(r['file'],r['kind'],r['sha256']) for r in review.get('fixtures',[])}
    hits=[];fixtures=[]
    for name,body in entries.items():
        for kind,pattern in PATTERNS.items():
            if re.search(pattern,body):
                item={'file':name,'kind':kind}
                if (name.rsplit('@',1)[0],kind,hashlib.sha256(body).hexdigest()) in approved:fixtures.append(item)
                else:hits.append(item)
    return hits,fixtures

def git_scan():
    git=ROOT/'.git'
    if not git.exists():return {'present':False,'staged_blobs':0,'history_blobs':0,'secret_pattern_findings':0}
    def run(*args):return subprocess.check_output(['git','-C',str(ROOT),*args],stderr=subprocess.DEVNULL)
    staged={}
    for record in run('ls-files','-s','-z').split(b'\0'):
        if not record:continue
        head,name=record.split(b'\t',1);oid=head.split()[1].decode()
        decoded=name.decode()
        if any(x in IGNORED|FORBIDDEN_PARTS for x in Path(decoded).parts) or Path(decoded).parts[:2]==('reports','local'):raise ValueError('Ignored/private material staged in Git')
        staged[decoded]=run('cat-file','blob',oid)
    # Approved fixture exceptions apply only to exact current staged bytes.
    review=json.loads((ROOT/'scripts/scanner-fixtures.json').read_text())
    stage_hits,_=findings(staged,review)
    listed=run('rev-list','--objects','--all').splitlines();history={}
    for line in listed:
        oid,_,name=line.partition(b' ')
        if not name:continue
        if run('cat-file','-t',oid.decode()).strip()==b'blob':
            history[name.decode(errors='replace')+'@'+oid.decode()]=run('cat-file','blob',oid.decode())
    # Historical fixture waivers require the same approved path and exact blob hash.
    history_hits,_=findings(history,review)
    return {'present':True,'staged_blobs':len(staged),'history_blobs':len(history),
            'secret_pattern_findings':len(stage_hits)+len(history_hits)}

def main():
    entries=file_bytes();review=json.loads((ROOT/'scripts/scanner-fixtures.json').read_text())
    hits,fixtures=findings(entries,review);git=git_scan()
    report={'scanner':'stdlib bounded pattern/path + staged/history scan','candidate_files':len(entries),
            'unreviewed_pattern_findings':hits,'reviewed_synthetic_fixture_findings':fixtures,'git':git,
            'semantic_privacy_certified':False,'external_gitleaks_or_trufflehog_executed':False}
    print(json.dumps(report,indent=2))
    return 1 if hits or git['secret_pattern_findings'] else 0

if __name__=='__main__':raise SystemExit(main())
