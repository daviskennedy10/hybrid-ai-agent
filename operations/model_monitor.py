"""Periodic application-path checks; never probe a private prompt through cloud fallback."""
import asyncio
from datetime import datetime, timezone
import time
from router.core import Request


class ModelMonitor:
    def __init__(self, router, slots, principal, log, interval=900, inference_interval=21600):
        self.router, self.slots, self.principal, self.log = router, slots, principal, log
        self.interval, self.inference_interval = interval, inference_interval
        self.last_inference = time.monotonic()

    async def sample(self):
        available = await asyncio.to_thread(self.router.backend.healthy)
        self.log({'timestamp': datetime.now(timezone.utc).isoformat(),
                  'message': 'private_inference_probe', 'success': bool(available),
                  'selected_model': self.router.policy.local_model})
        now = time.monotonic()
        if now - self.last_inference >= self.inference_interval:
            self.last_inference = now
            async with self.slots:
                # A real Hermes turn, private=True prevents cloud spillover if Mac
                # inference fails. Fixed synthetic text; response is discarded.
                await asyncio.to_thread(self.router.run, Request('Reply with only OK.', private=True,
                                                                  principal_id=self.principal))

    async def run(self, stop):
        try:
            while not stop.is_set():
                await self.sample()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval)
                except TimeoutError:
                    pass
        finally:
            stop.set()
