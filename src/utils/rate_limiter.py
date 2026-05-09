"""Token-bucket-ish per-user rate limiter (in-memory)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self, max_per_minute: int = 30) -> None:
        self.max_per_minute = max_per_minute
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - 60
        with self._lock:
            queue = self._timestamps[key]
            while queue and queue[0] < cutoff:
                queue.popleft()
            if len(queue) >= self.max_per_minute:
                return False
            queue.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._timestamps.clear()
            else:
                self._timestamps.pop(key, None)
