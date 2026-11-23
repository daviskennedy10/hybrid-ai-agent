"""Run synthetic tests in a neutral home with network denied, including children."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix="hybrid-tests-") as temp:
        neutral=Path(temp)
        # sitecustomize also applies to subprocess fixture interpreters.
        (neutral/"sitecustomize.py").write_text("import socket\ndef deny(*a,**k): raise PermissionError('Offline tests deny network')\nsocket.socket.connect=deny\nsocket.socket.connect_ex=deny\nsocket.create_connection=deny\nsocket.getaddrinfo=deny\n")
        env={k:v for k,v in os.environ.items() if k in {"PATH","LANG","LC_ALL","TMPDIR","SYSTEMROOT"}}
        env.update(HOME=temp,HERMES_HOME=str(neutral/"hermes"),PYTHONPATH=str(neutral)+os.pathsep+str(ROOT),PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-v"],cwd=ROOT,env=env).returncode

if __name__=="__main__": raise SystemExit(main())
