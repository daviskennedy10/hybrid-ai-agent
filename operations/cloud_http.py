"""Bounded HTTP servers. Request paths, bodies and headers are never logged."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


class LimitedServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, address, handler, limit=4):
        self.slots = threading.BoundedSemaphore(limit)
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        pass  # Never emit exception text containing upstream data or credentials.


class QuietHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def send_response(self, code, message=None):
        # BaseHTTPRequestHandler adds its implementation and Python version.
        # Keep Date/status behavior without sending a Server fingerprint.
        self.send_response_only(code, message)
        self.send_header('Date', self.date_time_string())

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.respond(code, b'Request rejected')

    def respond(self, status, body, content_type='text/plain; charset=utf-8', headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def body(self, maximum):
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
            raise ValueError('Explicit bounded body required')
        size = int(lengths[0])
        if not 0 < size <= maximum:
            raise ValueError('Body limit')
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError('Incomplete body')
        return body
