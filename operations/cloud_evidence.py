"""Durable metadata-only heartbeat/log batches, independent of snapshot retention."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from operations.security import metadata


class Evidence:
    def __init__(self, root, objects, interval=60):
        self.root, self.objects, self.interval = Path(root), objects, interval
        self.boot = uuid.uuid4().hex
        self.offsets = {}
        self.sequence = 0

    def upload(self):
        # Only explicit application logs; never arbitrary workspace/profile files.
        for name in ('operations.jsonl', 'telegram-routing.jsonl', 'telegram.jsonl', 'proactive.jsonl', 'gateway.jsonl'):
            path = self.root / name
            if not path.exists():
                continue
            start = self.offsets.get(name, 0)
            with path.open('rb') as stream:
                stream.seek(start)
                lines = stream.readlines(1024 * 1024)
            if not lines:
                continue
            events = []
            consumed = 0
            for line in lines:
                if not line.endswith(b'\n'):
                    break
                consumed += len(line)
                events.append(metadata(json.loads(line)))
            if not consumed:
                continue
            body = b''.join((json.dumps(event, sort_keys=True) + '\n').encode() for event in events)
            digest = hashlib.sha256(body).hexdigest()
            key = f'evidence/{self.boot}/{name}/{start:016d}-{digest}.jsonl'
            # A transport-ambiguous successful upload is retried by read/checksum,
            # never by overwriting a prior immutable batch.
            from persistence.objects import Conflict
            try:
                self.objects.put(key, body, 0)
            except Conflict:
                observed, _ = self.objects.get(key)
                if observed != body:
                    raise ValueError('Evidence checksum mismatch')
            self.offsets[name] = start + consumed
        isolation = self.root / 'startup-isolation.json'
        if isolation.exists() and not self.sequence:
            report = json.loads(isolation.read_text())
            safe = {k: v for k, v in report.items() if k in {
                'timestamp', 'platform', 'uid', 'gid', 'non_root', 'container_marker_present',
                'effective_capabilities_zero', 'no_new_privileges', 'seccomp_filter',
                'container_isolation_demonstrated'}}
            from persistence.objects import Conflict
            body=json.dumps(safe).encode()
            key=f'evidence/{self.boot}/isolation.json'
            try: self.objects.put(key,body,0)
            except Conflict:
                observed,_=self.objects.get(key)
                if observed!=body: raise ValueError('Isolation evidence mismatch')
            self.sequence += 1

    async def run(self, stop):
        failures = 0
        try:
            while not stop.is_set():
                try:
                    await asyncio.to_thread(self.upload)
                    failures = 0
                except Exception:
                    failures += 1
                    if failures >= 3:
                        raise RuntimeError('Durable evidence upload unavailable') from None
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval)
                except TimeoutError:
                    pass
        finally:
            stop.set()
            # Best effort final metadata; failure cannot mask original error.
            try:
                await asyncio.to_thread(self.upload)
            except Exception:
                pass
