"""Constant-memory, process-wide gateway limits; never retain IPs or credentials."""
from collections import deque
import threading
import time


class AccessLimits:
    def __init__(self, clock=time.monotonic, window=60, max_failures=10, max_requests=30):
        self.clock, self.window = clock, window
        self.max_failures, self.max_requests = max_failures, max_requests
        self.failures, self.requests = deque(), deque()
        self.lock = threading.Lock()

    def authenticate(self, verify):
        with self.lock:
            now = self.clock()
            for queue in (self.failures, self.requests):
                while queue and queue[0] <= now-self.window:
                    queue.popleft()
            # Always check the bounded credential first: anonymous failures must
            # not consume the legitimate user's budget. Constant-time comparison
            # still runs on denied attempts; connection concurrency is bounded by
            # LimitedServer. Saturated failures neither grow state nor emit logs.
            if not verify():
                if len(self.failures) >= self.max_failures:
                    return 429
                self.failures.append(now)
                return 401
            if len(self.requests) >= self.max_requests:
                return 429
            self.requests.append(now)
            return 200
