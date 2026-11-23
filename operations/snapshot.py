"""Consistent local SQLite backup, not an unsafe copy of an active WAL file."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def snapshot(source,destination):
    source,destination=Path(source),Path(destination)
    if source.is_symlink() or not source.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError('Use a regular source and a new destination')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name('.snapshot-'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(temporary)) as dst:
            tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables!={'notices','clocks'}: raise ValueError('Expected only scheduling metadata')
            deadline=time.monotonic()+30
            def progress(status,remaining,total):
                if time.monotonic()>deadline: raise TimeoutError('Snapshot deadline exceeded')
            src.backup(dst,pages=128,progress=progress)
            if dst.execute('PRAGMA integrity_check').fetchone()!=('ok',): raise ValueError('Snapshot integrity failure')
        # Hard link publishes atomically and refuses to overwrite any existing path.
        os.link(temporary,destination)
        return {'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'bytes':destination.stat().st_size,
                'sqlite_integrity_verified':True,'cloud_upload_performed':False}
    finally: temporary.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    try: print(json.dumps(snapshot(args.source,args.output)));return 0
    except (ValueError,OSError,sqlite3.Error): print('Snapshot failed; existing files were not overwritten.');return 2


if __name__=='__main__': raise SystemExit(main())
