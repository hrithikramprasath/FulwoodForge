"""Per-user sliding-window rate limiting (`REQ-SEC-03` / `VULN-03`).

Provides `KeyedSlidingWindow` (`user_generation_limiter` and `user_export_limiter`),
scoping rate limits by authenticated `user_id` with configurable per-call limit overrides
(`user.hourly_generation_limit` for generations, `20/min` for exports) so one user can
never exhaust the school-wide quota.

Unidirectional Dependency Law:
- Imports ONLY from Python standard library (zero `fastapi` or `starlette` imports).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable


def _int_env(name: str, default: int) -> int:
    """Positive int from the environment, falling back on anything unusable."""
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


class KeyedSlidingWindow:
    """Per-user (`int | str` key) sliding-window rate limiter (`REQ-SEC-03` / `VULN-03`).

    Ensures one user hitting their quota never blocks other users on the platform.
    Supports per-user custom limits (e.g. `user.hourly_generation_limit`).
    """

    def __init__(
        self,
        default_limit: int,
        seconds: float,
        what: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.default_limit = default_limit
        self.seconds = seconds
        self.what = what
        self._clock = clock
        self._buckets: dict[int | str, deque[float]] = {}
        self._lock = threading.Lock()

    def check_and_record(
        self,
        key: int | str,
        *,
        limit_override: int | None = None,
    ) -> tuple[bool, int]:
        """Check whether `key` is within quota and record a hit if allowed.

        Returns `(allowed, retry_after_seconds)`. When `allowed` is `True`, `retry_after_seconds` is `0`.
        """
        effective_limit = (
            limit_override if limit_override is not None else self.default_limit
        )
        if effective_limit <= 0:
            return False, max(1, int(self.seconds))

        now = self._clock()
        with self._lock:
            if len(self._buckets) > 10000:
                expired_keys = [
                    k
                    for k, b in self._buckets.items()
                    if not b or now - b[-1] >= self.seconds
                ]
                for k in expired_keys:
                    self._buckets.pop(k, None)

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket

            while bucket and now - bucket[0] >= self.seconds:
                bucket.popleft()

            if len(bucket) >= effective_limit:
                retry_after = max(1, int(self.seconds - (now - bucket[0])) + 1)
                return False, retry_after

            bucket.append(now)
            return True, 0

    def reset(self, key: int | str | None = None) -> None:
        """Clear rate-limit history for a single `key` or all keys."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


user_generation_limiter = KeyedSlidingWindow(
    _int_env("GENERATE_PER_HOUR", 30), 3600.0, "generation"
)
user_export_limiter = KeyedSlidingWindow(
    _int_env("EXPORT_PER_MINUTE", 20), 60.0, "export"
)
ip_rate_limiter = KeyedSlidingWindow(
    _int_env("IP_RATE_LIMIT_PER_MINUTE", 120), 60.0, "ip_request"
)
ip_registration_limiter = KeyedSlidingWindow(
    _int_env("IP_REGISTRATION_LIMIT_PER_15_MIN", 10), 900.0, "ip_registration"
)
