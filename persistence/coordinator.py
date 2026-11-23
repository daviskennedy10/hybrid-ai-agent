"""Single-writer lease + generation-fenced immutable snapshot publication."""
from contextlib import contextmanager
import hashlib
import json
import threading
import time
import uuid
from persistence.objects import Missing,PersistenceFault,Conflict
from persistence.bundle import unpack


class Coordinator:
    def __init__(self,objects,clock=time.time,lease_seconds=300):
        if not 120<=lease_seconds<=3600: raise ValueError('Invalid lease interval')
        self.objects,self.clock,self.lease_seconds=objects,clock,lease_seconds
        self.owner=uuid.uuid4().hex;self.lock=threading.RLock();self.poisoned=False;self.generation=None;self.head=None;self.export=None

    def _read(self,body):
        try:
            d=json.loads(body)
            if (set(d)!={'version','owner','until','snapshot','sha256'} or d['version']!=1
                    or not isinstance(d['owner'],str) or not __import__('re').fullmatch('[a-f0-9]{32}',d['owner'])
                    or type(d['until']) not in {int,float} or not 0<=d['until']<2**53): raise ValueError
            if d['snapshot'] is None and d['sha256'] is not None: raise ValueError
            if d['snapshot'] is not None and (d['snapshot']!='snapshots/'+d['sha256'] or not __import__('re').fullmatch('[a-f0-9]{64}',d['sha256'])): raise ValueError
            return d
        except (ValueError,TypeError,KeyError): raise PersistenceFault('Invalid persistence manifest') from None

    def acquire(self,bootstrap=False):
        with self.lock:
            try:
                raw,generation=self.objects.get('head');head=self._read(raw)
                if bootstrap: raise Conflict('Bootstrap refuses an existing namespace')
                if head['until']>self.clock(): raise Conflict('Another runtime holds the lease')
            except Missing:
                if not bootstrap: raise PersistenceFault('No snapshot: explicit initial bootstrap required') from None
                generation=0;head={'version':1,'owner':self.owner,'until':0,'snapshot':None,'sha256':None}
            head={**head,'owner':self.owner,'until':self.clock()+self.lease_seconds}
            self.generation=self.objects.put('head',json.dumps(head).encode(),generation);self.head=head
            if head['snapshot'] is None:
                if not bootstrap: raise PersistenceFault('Initial snapshot not published')
                return None
            body,_=self.objects.get(head['snapshot'])
            if hashlib.sha256(body).hexdigest()!=head['sha256']: raise PersistenceFault('Snapshot checksum mismatch')
            unpack(body);return body

    def ensure(self):
        if self.poisoned or self.head is None or self.head['until']<=self.clock():
            self.poisoned=True;raise PersistenceFault('Durability lease unavailable')

    def _publish_head(self,head):
        self.ensure()
        try:
            generation=self.objects.put('head',json.dumps(head,sort_keys=True).encode(),self.generation)
            self.generation=generation;self.head=head
            self.ensure()
        except Exception:
            self.poisoned=True;raise PersistenceFault('Durability generation fence failed') from None

    def renew(self):
        with self.lock:
            self.ensure();self._publish_head({**self.head,'until':self.clock()+self.lease_seconds})

    def checkpoint(self):
        with self.lock:
            self.ensure()
            try:
                body=self.export();sha=hashlib.sha256(body).hexdigest();key='snapshots/'+sha
                try: self.objects.put(key,body,0)
                except Conflict:
                    existing,_=self.objects.get(key)
                    if existing!=body: raise PersistenceFault('Immutable object mismatch')
                self._publish_head({**self.head,'snapshot':key,'sha256':sha,'until':self.clock()+self.lease_seconds})
            except Exception:
                self.poisoned=True;raise PersistenceFault('Durable snapshot publication failed') from None

    def release(self):
        with self.lock:
            self.ensure()
            try:
                self.head={**self.head,'until':0}
                self.generation=self.objects.put('head',json.dumps(self.head).encode(),self.generation)
            finally: self.poisoned=True
