"""Whitelisted, checksummed snapshots. Never archive a home, secrets or raw logs."""
import base64
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from agent_memory.store import decode,encode,SOUL,USER
from operations.activity import CATEGORIES
from operations.health import atomic_json
from persistence.objects import PersistenceFault,MAX_OBJECT

HEX=re.compile('[a-f0-9]{64}')


def database(path):
    with tempfile.TemporaryDirectory() as tmp:
        target=Path(tmp)/'copy.db'
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            deadline=time.monotonic()+30
            def progress(*args):
                if time.monotonic()>deadline: raise PersistenceFault('Snapshot deadline')
            src.backup(dst,pages=128,progress=progress)
            dst.execute('PRAGMA journal_mode=DELETE')
            dst.execute('VACUUM')
        return target.read_bytes()


def validate_db(raw,activity=False):
    if len(raw)>8*1024*1024: raise PersistenceFault('Database exceeds bound')
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'validate.db';p.write_bytes(raw)
        try:
            with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA query_only=ON')
                if db.execute('PRAGMA integrity_check').fetchone()!=('ok',): raise ValueError
                schema=list(db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
                expected={'activity'} if activity else {'notices','clocks'}
                if {name for typ,name in schema if typ=='table'}!=expected or any(typ!='table' for typ,name in schema): raise ValueError
                if activity:
                    if [r[1] for r in db.execute('PRAGMA table_info(activity)')]!=['key','principal','category','at']: raise ValueError
                    for key,principal,category,at in db.execute('SELECT * FROM activity'):
                        if not HEX.fullmatch(key) or not HEX.fullmatch(principal) or category not in CATEGORIES or not number(at): raise ValueError
                else:
                    if [r[1] for r in db.execute('PRAGMA table_info(notices)')]!=['key','recipient','kind','origin','due','expires','retry_at','status','attempts','created','updated','sent_at']: raise ValueError
                    for row in db.execute('SELECT * FROM notices'):
                        key,recipient,kind,origin,due,expires,retry,status,attempts,created,updated,sent=row
                        if (not HEX.fullmatch(key) or not HEX.fullmatch(recipient) or kind not in {'morning','evening','event','conflict','urgent'}
                            or origin not in {'manual','scheduler'} or status not in {'pending','building','sending','sent','uncertain','failed','expired'}
                            or type(attempts) is not int or not 0<=attempts<=100 or not all(number(x) for x in [due,expires,retry,created,updated]) or (sent is not None and not number(sent))): raise ValueError
                    if [r[1] for r in db.execute('PRAGMA table_info(clocks)')]!=['key','next_poll']: raise ValueError
                    for key,at in db.execute('SELECT * FROM clocks'):
                        if not HEX.fullmatch(key) or not number(at): raise ValueError
        except (sqlite3.Error,ValueError,TypeError): raise PersistenceFault('Invalid metadata database') from None


def number(value): return type(value) in {int,float} and 0<=value<2**53 and math.isfinite(value)


def pack(files):
    validate_files(files)
    data={'version':1,'files':{key:{'sha256':hashlib.sha256(value).hexdigest(),'data':base64.b64encode(value).decode()} for key,value in sorted(files.items())}}
    body=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    if len(body)>MAX_OBJECT: raise PersistenceFault('Snapshot exceeds bound')
    return body


def unpack(body):
    try:
        if len(body)>MAX_OBJECT: raise ValueError
        d=json.loads(body)
        if set(d)!={'version','files'} or d['version']!=1 or not isinstance(d['files'],dict): raise ValueError
        files={}
        for key,value in d['files'].items():
            if set(value)!={'sha256','data'}: raise ValueError
            raw=base64.b64decode(value['data'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=value['sha256']: raise ValueError
            files[key]=raw
        validate_files(files);return files
    except (ValueError,TypeError,KeyError,UnicodeError): raise PersistenceFault('Invalid snapshot') from None


def validate_files(files):
    if not {'activity.sqlite3','scheduler.sqlite3'}<=set(files) or len(files)>194: raise PersistenceFault('Incomplete snapshot')
    validate_db(files['activity.sqlite3'],activity=True);validate_db(files['scheduler.sqlite3'])
    profiles={}
    for key,raw in files.items():
        if key in {'activity.sqlite3','scheduler.sqlite3'}: continue
        match=re.fullmatch(r'memory/([a-f0-9]{64})/(SOUL.md|memories/USER.md|memories/MEMORY.md)',key)
        if not match: raise PersistenceFault('Unapproved snapshot path')
        profiles.setdefault(match[1],{})[match[2]]=raw
    for items in profiles.values():
        if set(items)!={'SOUL.md','memories/USER.md','memories/MEMORY.md'}: raise PersistenceFault('Incomplete memory profile')
        if items['SOUL.md']!=SOUL.encode() or items['memories/USER.md']!=USER.encode(): raise PersistenceFault('Unapproved persona')
        try: decode(items['memories/MEMORY.md'])
        except (ValueError,UnicodeError): raise PersistenceFault('Unsafe memory') from None


def restore(body,destination):
    """Validate everything first; atomically publish a fresh boot tree, never overwrite."""
    import os,uuid,shutil
    files=unpack(body);destination=Path(destination)
    if destination.exists() or destination.is_symlink(): raise PersistenceFault('Restore destination must be new')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=destination.with_name('.restore-'+uuid.uuid4().hex);stage.mkdir(mode=0o700)
    try:
        for name,raw in files.items():
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as stream: stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.rename(stage,destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
