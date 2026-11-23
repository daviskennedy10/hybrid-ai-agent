"""Durable metadata-only outbox. Sending interrupted by a crash is uncertain, never replayed."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from functools import wraps


def serialized(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.barrier: return method(self,*args,**kwargs)
    return call


def durable(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.barrier:
            before=self.db.total_changes
            result=method(self,*args,**kwargs)
            if self.db.total_changes!=before: self.checkpoint()
            return result
    return call


def digest(*parts):
    return hashlib.sha256(json.dumps(parts,separators=(',',':'),sort_keys=True).encode()).hexdigest()


class State:
    def __init__(self,path):
        self.barrier=threading.RLock();self.checkpoint=lambda:None
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.parent.chmod(0o700)
        try:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        except FileExistsError: pass
        self.path.chmod(0o600)
        self.db=sqlite3.connect(self.path,timeout=5,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS notices(
                key TEXT PRIMARY KEY CHECK(length(key)=64),
                recipient TEXT NOT NULL CHECK(length(recipient)=64),
                kind TEXT NOT NULL CHECK(kind IN ('morning','evening','event','conflict','urgent')),
                origin TEXT NOT NULL CHECK(origin IN ('scheduler','manual')),
                due REAL NOT NULL, expires REAL NOT NULL, retry_at REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','building','sending','sent','uncertain','failed','expired')),
                attempts INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL, sent_at REAL);
            CREATE TABLE IF NOT EXISTS clocks(key TEXT PRIMARY KEY CHECK(length(key)=64), next_poll REAL NOT NULL);
        ''')
        self.db.commit()

    @contextmanager
    def exclusive(self):
        lock=self.path.parent/'scheduler.lock'
        with lock.open('a') as stream:
            lock.chmod(0o600)
            try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('A proactive scheduler already owns this state') from None
            try: yield
            finally: fcntl.flock(stream,fcntl.LOCK_UN)

    @durable
    def recover(self,now):
        with self.db:
            self.db.execute("UPDATE notices SET status='uncertain',updated=? WHERE status='sending'",(now,))
            self.db.execute("UPDATE notices SET status='pending',updated=? WHERE status='building'",(now,))
            self.db.execute("UPDATE notices SET status='expired',updated=? WHERE status='pending' AND expires<?",(now,now))

    @durable
    def expire(self,now):
        with self.db:
            self.db.execute("UPDATE notices SET status='expired',updated=? WHERE status='pending' AND expires<?",(now,now))

    @durable
    def enqueue(self,key,recipient,kind,origin,due,expires,now):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO notices VALUES(?,?,?,?,?,?,?,'pending',0,?,?,NULL)",
                            (key,recipient,kind,origin,due,expires,due,now,now))
        return self.get(key)

    @serialized
    def get(self,key):
        row=self.db.execute('SELECT * FROM notices WHERE key=?',(key,)).fetchone()
        return dict(row) if row else None

    @durable
    def begin(self,key,now):
        with self.db:
            cursor=self.db.execute("UPDATE notices SET status='building',attempts=attempts+1,updated=? WHERE key=? AND status='pending' AND retry_at<=? AND due<=? AND expires>=?",
                                   (now,key,now,now,now))
        return cursor.rowcount==1

    @durable
    def sending(self,key,now):
        with self.db:
            cursor=self.db.execute("UPDATE notices SET status='sending',updated=? WHERE key=? AND status='building'",(now,key))
        if cursor.rowcount!=1: raise RuntimeError('Invalid notification transition')

    @durable
    def finish(self,key,status,now,retry_at=None):
        if status not in {'pending','sent','uncertain','failed','expired'}: raise ValueError('Invalid status')
        with self.db:
            self.db.execute('UPDATE notices SET status=?,updated=?,retry_at=?,sent_at=? WHERE key=?',
                            (status,now,retry_at or now,now if status=='sent' else None,key))

    @serialized
    def pending_manual(self,recipient,now):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM notices WHERE recipient=? AND origin='manual' AND status='pending' AND retry_at<=? AND due<=? AND expires>=? ORDER BY due LIMIT 2",
            (recipient,now,now,now))]

    @serialized
    def poll_due(self,key,now):
        row=self.db.execute('SELECT next_poll FROM clocks WHERE key=?',(key,)).fetchone()
        return row is None or row[0]<=now

    @durable
    def polled(self,key,next_poll):
        with self.db:
            self.db.execute('INSERT INTO clocks VALUES(?,?) ON CONFLICT(key) DO UPDATE SET next_poll=excluded.next_poll',(key,next_poll))

    @serialized
    def counts(self,recipient):
        return dict(self.db.execute('SELECT status,count(*) FROM notices WHERE recipient=? GROUP BY status',(recipient,)).fetchall())

    def close(self): self.db.close()
