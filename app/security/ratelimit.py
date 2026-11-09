"""Request caps for the routes that create data or spend model calls (SE-05).

Each capped route has one RateLimiter: at most `per_client` calls per client and `total` calls in
all within a sliding window. The client is the first X-Forwarded-For address (Render's proxy sets
it), which a caller can fake; the total cap bounds the damage either way. In-process state: a
restart forgets it, and with one web instance that is the whole picture.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(
        self,
        *,
        per_client: int,
        total: int,
        window_s: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_client = per_client
        self.total = total
        self.window_s = window_s
        self.now = now
        self._lock = threading.Lock()
        self._all: deque[float] = deque()
        self._by_client: dict[str, deque[float]] = {}

    def allow(self, client: str) -> bool:
        """True and counted, or False and not counted."""
        with self._lock:
            t = self.now()
            self._forget_before(t - self.window_s)
            mine = self._by_client.get(client)
            if len(self._all) >= self.total or (mine is not None and len(mine) >= self.per_client):
                return False
            self._all.append(t)
            self._by_client.setdefault(client, deque()).append(t)
            return True

    def _forget_before(self, cutoff: float) -> None:
        while self._all and self._all[0] <= cutoff:
            self._all.popleft()
        for client in list(self._by_client):
            calls = self._by_client[client]
            while calls and calls[0] <= cutoff:
                calls.popleft()
            if not calls:
                del self._by_client[client]


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


def limited(limiter: RateLimiter) -> Callable[[Request], None]:
    """A route dependency: 429 with Retry-After once the caller or the route is over its cap."""

    def check(request: Request) -> None:
        if not limiter.allow(client_key(request)):
            raise HTTPException(
                status_code=429,
                detail="Too many requests from here just now. Try again in a few minutes.",
                headers={"Retry-After": str(int(limiter.window_s))},
            )

    return check
