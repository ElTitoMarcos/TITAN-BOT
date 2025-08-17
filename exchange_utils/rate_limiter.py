import threading
import time
from typing import Optional


class RateLimiter:
    """Simple token bucket rate limiter.

    Args:
        capacity: Maximum number of tokens per period.
        per: Period in seconds for full refill (default 60s).
    """

    def __init__(self, capacity: int, per: float = 60.0) -> None:
        self.capacity = float(capacity)
        self.per = float(per)
        self.tokens = float(capacity)
        self.lock = threading.Lock()
        self.timestamp = time.monotonic()

    def _refill(self, now: float) -> None:
        rate = self.capacity / self.per
        elapsed = now - self.timestamp
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * rate)
        self.timestamp = now

    def acquire(self, weight: float) -> None:
        """Acquire tokens for a request of given *weight*.

        Blocks until enough tokens are available."""
        weight = float(weight)
        rate = self.capacity / self.per
        while True:
            with self.lock:
                now = time.monotonic()
                self._refill(now)
                if self.tokens >= weight:
                    self.tokens -= weight
                    return
                deficit = weight - self.tokens
                wait_for = deficit / rate
            time.sleep(wait_for)
