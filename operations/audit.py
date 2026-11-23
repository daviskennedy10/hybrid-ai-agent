"""Timestamped runtime evidence. Reports observations, never assumes container isolation."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import platform


def isolation_report(proc=Path('/proc'),marker=Path('/.dockerenv')):
    values={}
    try:
        for line in (proc/'self/status').read_text().splitlines():
            key,_,value=line.partition(':')
            if key in {'CapEff','NoNewPrivs','Seccomp'}: values[key]=value.strip()
    except OSError: pass
    cap=values.get('CapEff')
    try: capabilities_zero=int(cap,16)==0 if cap is not None else None
    except ValueError: capabilities_zero=None
    return {'timestamp':datetime.now(timezone.utc).isoformat(),'platform':platform.system(),
            'uid':os.geteuid(),'gid':os.getegid(),'non_root':os.geteuid()!=0,
            'container_marker_present':marker.exists(),'effective_capabilities_zero':capabilities_zero,
            'no_new_privileges':{'0':False,'1':True}.get(values.get('NoNewPrivs')),
            'seccomp_filter':{'0':False,'1':False,'2':True}.get(values.get('Seccomp')),
            'container_isolation_demonstrated':False,
            'note':'Runtime observations require matching deployed configuration and negative tests; a marker alone proves no boundary.'}


def main():
    print(json.dumps(isolation_report(),indent=2))
    return 0


if __name__=='__main__': raise SystemExit(main())
