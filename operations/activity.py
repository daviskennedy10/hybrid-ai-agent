"""Restart-safe assistant activity, without prompt/response/user/account content."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

CATEGORIES={'request_handled','reply_delivered','tool_success','tool_failure','tool_rejected','model_success','model_failure','memory_write','memory_read'}


class Activity:
    def __init__(self,path,barrier=None,checkpoint=None,now=None,notices_path=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.path.parent.chmod(0o700)
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=5);self.path.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS activity(key TEXT PRIMARY KEY, principal TEXT NOT NULL, category TEXT NOT NULL, at REAL NOT NULL)');self.db.commit()
        self.barrier=barrier or threading.RLock();self.checkpoint=checkpoint or (lambda:None)
        self.now=now or (lambda:datetime.now(timezone.utc));self.notices_path=Path(notices_path) if notices_path else None

    @staticmethod
    def scope(principal): return hashlib.sha256(('telegram:'+str(principal)).encode()).hexdigest()

    def record(self,principal,category,event_id):
        if type(principal) is not int or principal<=0 or category not in CATEGORIES: raise ValueError('Invalid activity metadata')
        scope=self.scope(principal);key=hashlib.sha256((scope+category+str(event_id)).encode()).hexdigest()
        with self.barrier:
            with self.db:
                cursor=self.db.execute('INSERT OR IGNORE INTO activity VALUES(?,?,?,?)',(key,scope,category,self.now().timestamp()))
            if cursor.rowcount: self.checkpoint()

    def summary(self,principal,day,zone):
        start=datetime.combine(day,datetime.min.time(),zone).timestamp()
        stop=datetime.combine(day+timedelta(days=1),datetime.min.time(),zone).timestamp()
        with self.barrier:
            result={k:0 for k in CATEGORIES}
            result.update(dict(self.db.execute('SELECT category,count(*) FROM activity WHERE principal=? AND at>=? AND at<? GROUP BY category',(self.scope(principal),start,stop))))
            result['notifications_delivered']=0
            if self.notices_path and self.notices_path.exists():
                from contextlib import closing
                recipient=hashlib.sha256(json.dumps(('recipient',principal),separators=(',',':'),sort_keys=True).encode()).hexdigest()
                with closing(sqlite3.connect(self.notices_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    result['notifications_delivered']=db.execute("SELECT count(*) FROM notices WHERE recipient=? AND status='sent' AND sent_at>=? AND sent_at<?",(recipient,start,stop)).fetchone()[0]
            return result

    def close(self): self.db.close()
