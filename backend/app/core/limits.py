"""Per-user and global rate limiting and concurrency controls (`REQ-SEC-03` / `VULN-03`).

Provides:
- `KeyedSlidingWindow`: Per-user (`user_id: int` or `str`) sliding-window rate limiter
  with configurable per-call limit override (`user.hourly_generation_limit` for generations,
  `20/min` for exports) so one user can never exhaust the school-wide quota.
- `SlidingWindow`, `generate_limit`, `export_limit`, `export_slot`: Preserved for
  backward-compatible endpoint/test callers until Wave 4 rewires HTTP dependencies.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status


def _int_env(name: str, default: int) -> int:
    """Positive int from the environment, falling back on anything unusable."""
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


class SlidingWindow:
    """Allow `limit` events per `seconds`, measured over a true sliding window."""

    def __init__(self, limit: int, seconds: float, what: str) -> None:
        self.limit = limit
        self.seconds = seconds
        self.what = what
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self) -> None:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] >= self.seconds:
                self._hits.popleft()
            if len(self._hits) >= self.limit:
                retry = max(1, int(self.seconds - (now - self._hits[0])) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many {self.what} requests — try again in {retry}s.",
                    headers={"Retry-After": str(retry)},
                )
            self._hits.append(now)


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

    def check(
        self,
        key: int | str,
        *,
        limit_override: int | None = None,
    ) -> None:
        """Check quota for `key` and raise `HTTPException(429)` if exceeded."""
        allowed, retry_after = self.check_and_record(key, limit_override=limit_override)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many {self.what} requests — try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

    def reset(self, key: int | str | None = None) -> None:
        """Clear rate-limit history for a single `key` or all keys."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


_generate = SlidingWindow(_int_env("GENERATE_PER_HOUR", 30), 3600.0, "generation")
_export = SlidingWindow(_int_env("EXPORT_PER_MINUTE", 20), 60.0, "export")

user_generation_limiter = KeyedSlidingWindow(
    _int_env("GENERATE_PER_HOUR", 30), 3600.0, "generation"
)
user_export_limiter = KeyedSlidingWindow(
    _int_env("EXPORT_PER_MINUTE", 20), 60.0, "export"
)

_EXPORT_SLOTS = threading.BoundedSemaphore(_int_env("EXPORT_CONCURRENCY", 2))
_EXPORT_WAIT_S = float(_int_env("EXPORT_QUEUE_WAIT_S", 30))


def generate_limit() -> None:
    """FastAPI dependency — bounds Gemini spend."""
    _generate.check()


def export_limit() -> None:
    """FastAPI dependency — bounds export request rate."""
    _export.check()


@contextmanager
def export_slot() -> Iterator[None]:
    """Hold one of the few concurrent-export slots, or 503 rather than OOM the box."""
    if not _EXPORT_SLOTS.acquire(timeout=_EXPORT_WAIT_S):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The server is busy building other files — try again in a moment.",
            headers={"Retry-After": "30"},
        )
    try:
        yield
    finally:
        _EXPORT_SLOTS.release()
