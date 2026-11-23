import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
from operations.health import atomic_json

MAX_OBJECT=16*1024*1024


class PersistenceFault(RuntimeError): pass
class Missing(PersistenceFault): pass
class Conflict(PersistenceFault): pass


class LocalObjects:
    """Process-safe local object-store fixture; never advertised as GCS verification."""
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.root.chmod(0o700)

    def path(self,key): return self.root/hashlib.sha256(key.encode()).hexdigest()

    @contextmanager
    def locked(self):
        fd=os.open(self.root/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try: fcntl.flock(fd,fcntl.LOCK_EX);yield
        finally: os.close(fd)

    def get(self,key):
        with self.locked():
            try: d=json.loads(self.path(key).read_text())
            except FileNotFoundError: raise Missing('Object missing') from None
            except (ValueError,OSError): raise PersistenceFault('Invalid object') from None
            return base64.b64decode(d['data'],validate=True),int(d['generation'])

    def put(self,key,body,expected):
        if len(body)>MAX_OBJECT: raise PersistenceFault('Object exceeds bound')
        with self.locked():
            try: previous=json.loads(self.path(key).read_text())['generation']
            except FileNotFoundError: previous=0
            if previous!=expected: raise Conflict('Generation conflict')
            generation=previous+1
            atomic_json(self.path(key),{'generation':generation,'data':base64.b64encode(body).decode()})
            return generation


class GCSObjects:
    """Official client, ambient workload identity, finite requests, conditional writes."""
    def __init__(self,bucket,client=None,prefix='csci599-a3'):
        if not isinstance(bucket,str) or not __import__('re').fullmatch(r'[a-z0-9][a-z0-9._-]{2,62}',bucket): raise ValueError('Invalid bucket')
        if not __import__('re').fullmatch(r'[a-z0-9_-]{1,48}',prefix): raise ValueError('Invalid namespace')
        if client is None:
            from google.cloud import storage
            client=storage.Client()
        self.bucket=client.bucket(bucket);self.prefix=prefix

    def get(self,key):
        try:
            blob=self.bucket.blob(self.prefix+'/'+key)
            blob.reload(timeout=10,retry=None)
            generation=int(blob.generation)
            if blob.size is None or int(blob.size)>MAX_OBJECT: raise PersistenceFault('Object exceeds bound')
            body=blob.download_as_bytes(if_generation_match=generation,timeout=10,retry=None)
            if len(body)>MAX_OBJECT: raise PersistenceFault('Object exceeds bound')
            return body,generation
        except PersistenceFault: raise
        except Exception as exc:
            code=getattr(exc,'code',None)
            if code==404: raise Missing('Object missing') from None
            if code in {409,412}: raise Conflict('Generation conflict') from None
            raise PersistenceFault('Cloud object read failed') from None

    def put(self,key,body,expected):
        if len(body)>MAX_OBJECT: raise PersistenceFault('Object exceeds bound')
        try:
            blob=self.bucket.blob(self.prefix+'/'+key)
            blob.upload_from_string(body,content_type='application/json',if_generation_match=expected,timeout=10,retry=None)
            return int(blob.generation)
        except Exception as exc:
            if getattr(exc,'code',None) in {409,412}: raise Conflict('Generation conflict') from None
            raise PersistenceFault('Cloud object write failed') from None
