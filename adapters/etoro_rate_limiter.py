from __future__ import annotations

import asyncio
from contextlib import suppress

_RATE_LIMIT_CAPACITY = 20
_RATE_LIMIT_REFILL_INTERVAL = 3.0

class _RateLimiter:
    """Async token bucket: 20 cap, 1 token / 3 s.

    CLOSE requests queue and wait; OPEN requests fail-fast if no token.
    """

    def __init__(
        self,
        capacity: int = _RATE_LIMIT_CAPACITY,
        refill_interval: float = _RATE_LIMIT_REFILL_INTERVAL,
    ) -> None:
        self._capacity = capacity
        self._tokens = capacity
        self._refill_interval = refill_interval
        self._lock = asyncio.Lock()
        self._close_queue: asyncio.PriorityQueue[tuple[int, int, asyncio.Future[bool]]] = (
            asyncio.PriorityQueue()
        )
        self._seq = 0
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.ensure_future(self._refill_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _refill_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refill_interval)
            await self._tick()

    async def _tick(self) -> None:
        """One refill cycle: add a token if below capacity, drain CLOSE queue."""
        futures_to_resolve: list[asyncio.Future[bool]] = []
        async with self._lock:
            if self._tokens < self._capacity:
                self._tokens += 1
            while not self._close_queue.empty() and self._tokens > 0:
                _, _, future = self._close_queue.get_nowait()
                if not future.done():
                    self._tokens -= 1
                    futures_to_resolve.append(future)
        for future in futures_to_resolve:
            if not future.done():
                future.set_result(True)

    async def acquire(self, priority: str) -> bool:
        """Acquire a token.

        Returns True when the token is granted.
        For CLOSE: queues and awaits until granted (never dropped).
        For OPEN / LIMIT: returns False immediately when capacity is 0.
        """
        if priority == "CLOSE":
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            async with self._lock:
                if self._tokens > 0:
                    self._tokens -= 1
                    return True
                self._seq += 1
                self._close_queue.put_nowait((0, self._seq, future))
            return await future
        else:
            async with self._lock:
                if self._tokens > 0:
                    self._tokens -= 1
                    return True
            return False

    @property
    def tokens(self) -> int:
        return self._tokens
