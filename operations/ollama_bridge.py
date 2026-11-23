"""Fixed-target TLS bridge over tailscaled's userspace HTTP CONNECT proxy.

Keep the verified Router loopback URL unchanged. No ambient proxies, redirects,
client-supplied destinations, credentials or arbitrary native Ollama operations.
"""
import argparse
import os
import re
import http.client
import ssl
from operations.cloud_http import LimitedServer, QuietHandler

TARGET = os.environ.get('HYBRID_INFERENCE_HOST', 'unconfigured.invalid')
ALLOWED = {'GET': {'/v1/models', '/api/version'},
           'POST': {'/v1/chat/completions'}}


def connection(host=TARGET):
    if host != TARGET or not re.fullmatch(r'[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.ts\.net', host):
        raise ValueError('Unapproved inference destination')
    # HTTPSConnection performs CONNECT before TLS, using the tunnel host for
    # certificate hostname validation/SNI. Tailscale resolves the private name.
    conn = http.client.HTTPSConnection('127.0.0.1', 1055, timeout=180,
                                      context=ssl.create_default_context())
    conn.set_tunnel(host, 443)
    return conn


class Bridge(QuietHandler):
    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        if self.path not in ALLOWED.get(self.command, set()):
            return self.respond(403, b'Operation blocked')
        if self.headers.get('Upgrade') or self.headers.get('Expect'):
            return self.respond(400, b'Unsupported request')
        try:
            body = self.body(2 * 1024 * 1024) if self.command == 'POST' else None
        except ValueError:
            return self.respond(413, b'Invalid request size')
        conn = None
        started = False
        try:
            conn = self.server.connect()
            # Never forward local Authorization, cookies, proxy or Host headers.
            conn.request(self.command, self.path, body=body,
                         headers={'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
            upstream = conn.getresponse()
            if not 200 <= upstream.status < 300:
                return self.respond(503, b'Private inference unavailable')
            kind = upstream.getheader('Content-Type', '').split(';')[0].strip()
            if kind not in {'application/json', 'text/event-stream', 'application/x-ndjson'}:
                return self.respond(502, b'Invalid inference response')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Connection', 'close')
            self.end_headers()
            started = True
            size = 0
            while chunk := upstream.read1(16384):
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise ValueError('Response limit')
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            if not started:
                self.respond(503, b'Private inference unavailable')
        finally:
            self.close_connection = True
            if conn is not None:
                conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()
    server = LimitedServer(('127.0.0.1', 11434), Bridge, limit=4)
    server.connect = connection
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
