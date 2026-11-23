"""Restore before opening live stores; local mode preserves existing scheduler path."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import uuid
from agent_memory.store import Store
from operations.activity import Activity
from persistence.bundle import database,pack,restore
from persistence.coordinator import Coordinator
from persistence.objects import GCSObjects,PersistenceFault


class Runtime:
    def __init__(self,root,allowed,coordinator=None,bootstrap=False,seed=None):
        if not allowed or len(allowed)>64 or any(type(x) is not int or x<=0 for x in allowed): raise PersistenceFault('Invalid runtime principals')
        self.root=Path(root);self.coordinator=coordinator
        self.barrier=coordinator.lock if coordinator else threading.RLock()
        self.mode='gcs' if coordinator else 'local';self.restored=False
        if seed is not None:
            from persistence.bundle import unpack
            unpack(seed)
        if coordinator:
            body=coordinator.acquire(bootstrap=bootstrap)
            live=self.root/'durable-runs'/uuid.uuid4().hex
            if body is not None or seed is not None: restore(body if body is not None else seed,live);self.restored=True
            else: live.mkdir(parents=True,mode=0o700)
            self.memory_path=live/'memory';self.scheduler_path=live/'scheduler.sqlite3';self.activity_path=live/'activity.sqlite3'
        else:
            self.memory_path=self.root/'memory';self.scheduler_path=self.root/'proactive-state/state.sqlite3';self.activity_path=self.root/'activity/activity.sqlite3'
        self.memory=Store(self.memory_path,allowed,self.barrier,self.checkpoint,coordinator.renew if coordinator else None)
        self.activity=Activity(self.activity_path,self.barrier,self.checkpoint,notices_path=self.scheduler_path)
        if coordinator: coordinator.export=self.export
        self.state=None

    def bind_state(self,state):
        self.state=state
        state.barrier=self.barrier;state.checkpoint=self.checkpoint;state.cloud_backed=self.coordinator is not None
        if self.coordinator: self.checkpoint()

    def export(self):
        files={'scheduler.sqlite3':database(self.scheduler_path),'activity.sqlite3':database(self.activity_path)}
        files.update(self.memory.export());return pack(files)

    def checkpoint(self):
        if self.coordinator: self.coordinator.checkpoint()

    async def run(self,stop):
        if not self.coordinator:
            await stop.wait();return
        try:
            while not stop.is_set():
                try: await asyncio.wait_for(stop.wait(),timeout=30)
                except TimeoutError: await asyncio.to_thread(self.coordinator.renew)
        finally: stop.set()

    def close(self):
        try:
            if self.coordinator and not self.coordinator.poisoned:
                if self.state is not None: self.checkpoint()
                self.coordinator.release()
        finally: self.activity.close()


def configured(root,allowed,path):
    path=Path(path)
    if not path.exists():
        if os.environ.get('A3_REQUIRE_GCS')=='1': raise PersistenceFault('Cloud startup requires explicit GCS configuration')
        return Runtime(root,allowed)
    try:
        c=json.loads(path.read_text())
        if set(c)-{'seed_file'}!={'version','mode','bucket','namespace','lease_seconds','bootstrap'} or c['version']!=1 or c['mode']!='gcs' or type(c['bootstrap']) is not bool: raise ValueError
        seed=None
        if c['bootstrap']:
            seed=Path(c['seed_file']).read_bytes()
            from persistence.bundle import unpack
            unpack(seed)
        objects=GCSObjects(c['bucket'],prefix=c['namespace'])
        return Runtime(root,allowed,Coordinator(objects,lease_seconds=c['lease_seconds']),bootstrap=c['bootstrap'],seed=seed)
    except PersistenceFault: raise
    except Exception: raise PersistenceFault('Invalid or unavailable cloud persistence configuration') from None
