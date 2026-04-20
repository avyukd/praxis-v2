from __future__ import annotations

import asyncio


class RateBucket:
    """Simple token bucket for newswire politeness. Shared across pollers."""

    def __init__(self, tokens_per_sec: float = 5.0) -> None:
        self.interval = 1.0 / tokens_per_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def consume(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self.interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = loop.time()


NEWSWIRE_RATE = RateBucket(tokens_per_sec=5.0)
