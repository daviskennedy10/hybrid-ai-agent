"""Restricted grader UI using the same Router instance as Telegram.

HTTP Basic credentials travel only through platform HTTPS. No URL/query tokens,
CORS, arbitrary principals, model overrides, shell, or memory modifications.
"""
import asyncio
import base64
import hmac
import json
from pathlib import Path
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from operations.cloud_http import LimitedServer, QuietHandler
from operations.health import check
from operations.gateway_limits import AccessLimits
from router.core import Request

PAGE = b'''<!doctype html><meta charset="utf-8"><title>Assignment assistant</title>
<h1>Assignment assistant</h1><p>Synthetic assignment data only. Requests use the hybrid router.
This grader interface is stateless; persistent synthetic preferences remain available.</p>
<form id="f"><textarea id="q" maxlength="4000" required></textarea><button>Ask</button></form>
<pre id="out"></pre><script>
f.onsubmit=async(e)=>{e.preventDefault();out.textContent='Working...';try{
const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({text:q.value})});const d=await r.json();
out.textContent=d.response||'Request failed';}catch(e){out.textContent='Request failed';}};
</script>'''


def load(path, allowed):
    c = json.loads(Path(path).read_text())
    if (set(c) != {'username', 'password', 'principal_id'} or c['username'] != 'grader'
            or not isinstance(c['password'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', c['password'])
            or type(c['principal_id']) is not int or c['principal_id'] not in allowed):
        raise ValueError('Invalid restricted gateway configuration')
    return c


def same_origin(origin, host):
    """Validate serialized HTTPS origins; parsing failures are ordinary denials."""
    if not origin or not host:
        return False
    if any(ord(c) <= 32 or ord(c) >= 127 for c in origin + host):
        return False
    if any(c in host for c in '/?#@') or any(c in origin for c in '?#'):
        return False
    try:
        source, target = urlsplit(origin), urlsplit('https://' + host)
        for parsed in (source, target):
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.path
                    or parsed.username is not None or parsed.password is not None
                    or parsed.port == 0):
                return False
        return (source.hostname.lower(), source.port or 443) == (target.hostname.lower(), target.port or 443)
    except (ValueError, UnicodeError):
        return False


class Handler(QuietHandler):
    def event(self, authorized, status, result=None, started=None):
        audit=getattr(self.server,'audit',lambda event:None)
        audit({'timestamp':datetime.now(timezone.utc).isoformat(), 'message':'gateway_event',
               'authorized':authorized,'status':status,
               'route_request_id':(result or {}).get('request_id'),
               'route':(result or {}).get('route'),
               'selected_model':(result or {}).get('selected_model'),
               'success':(result or {}).get('success'),
               'latency_ms':round((time.monotonic()-started)*1000,2) if started is not None else None})

    def authorized(self):
        expected = 'Basic ' + base64.b64encode(
            ('grader:' + self.server.config['password']).encode()).decode()
        supplied = self.headers.get_all('Authorization', [])
        return len(supplied) == 1 and len(supplied[0]) <= 512 and hmac.compare_digest(supplied[0].encode(), expected.encode())

    def reject_auth(self):
        self.event(False,'rejected')
        return self.respond(401, b'Authentication required', headers={
            'WWW-Authenticate': 'Basic realm="Assignment assistant", charset="UTF-8"'})

    def authenticate(self):
        limits=getattr(self.server,'access_limits',None)
        status=limits.authenticate(self.authorized) if limits is not None else (200 if self.authorized() else 401)
        if status==200: return True
        if status==401: self.reject_auth()
        else:
            # Do not create a log for every denied flood request.
            self.respond(429,b'Try again later',headers={'Retry-After':'60'})
        return False

    def do_GET(self):
        if self.path == '/startupz':
            return self.respond(200, b'ready')
        if self.path == '/healthz':
            healthy = check(self.server.health_path)
            return self.respond(200 if healthy else 503, b'ok' if healthy else b'unhealthy')
        if not self.authenticate():
            return
        if self.path != '/':
            return self.respond(404, b'Not found')
        self.respond(200, PAGE, 'text/html; charset=utf-8', headers={
            'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'",
            'Referrer-Policy': 'no-referrer'})

    def do_POST(self):
        if not self.authenticate():
            return
        started=time.monotonic()
        self.event(True,'received')
        if self.path != '/ask':
            return self.respond(404, b'Not found')
        origins = self.headers.get_all('Origin', [])
        hosts = self.headers.get_all('Host', [])
        if origins and (len(origins) != 1 or len(hosts) != 1 or not same_origin(origins[0], hosts[0])):
            return self.respond(403, b'Cross-origin request rejected')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.respond(415, b'JSON required')
        try:
            data = json.loads(self.body(12000))
            text = data.get('text') if isinstance(data, dict) and set(data) == {'text'} else None
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise ValueError()
            text.encode('utf-8', errors='strict')
            if re.match(r'^\s*/(?:remember|forget)\b', text, re.I):
                return self.respond(403, b'Grader memory modifications disabled')
        except (ValueError, UnicodeError, RecursionError):
            return self.respond(400, b'Invalid message')
        if not self.server.inference_slots.acquire(False):
            return self.respond(429, b'Assistant busy')
        try:
            result = self.server.router.run(Request(text, principal_id=self.server.config['principal_id']))
            # Router owns privacy, tool authorization, memory and route evidence.
            safe = {key: result.get(key) for key in
                    ('response', 'success', 'request_id', 'route', 'selected_model','execution','attempts','skill_execution')}
            activity=getattr(self.server.router,'activity',None)
            if activity is not None:
                activity.record(self.server.config['principal_id'],'request_handled',result['request_id'])
            self.respond(200, json.dumps(safe).encode(), 'application/json')
            self.event(True,'responded',result,started)
        except Exception:
            self.respond(503, b'{"response":"Assistant unavailable"}', 'application/json')
        finally:
            self.server.inference_slots.release()


class Gateway:
    def __init__(self, router, config, health_path, port=8080):
        self.server = LimitedServer(('0.0.0.0', port), Handler)
        self.server.router, self.server.config = router, config
        self.server.health_path = health_path
        from router.core import JsonLog
        self.server.audit=JsonLog(Path(health_path).parent/'gateway.jsonl')
        self.server.inference_slots = threading.BoundedSemaphore(1)
        self.server.access_limits = AccessLimits()

    async def run(self, stop):
        task = asyncio.create_task(asyncio.to_thread(self.server.serve_forever, 0.2))
        waiter = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait((task, waiter), return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                await task
                raise RuntimeError('Gateway stopped')
        finally:
            stop.set()
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            await asyncio.to_thread(self.server.inference_slots.acquire)
            self.server.inference_slots.release()
            waiter.cancel()
            await asyncio.gather(task, waiter, return_exceptions=True)
