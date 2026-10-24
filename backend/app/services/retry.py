"""Deterministic 3-Attempt Exponential Backoff Retry Helpers (`REQ-SEC-07` / `VULN-07`).

Enforces:
- Maximum 3 attempts total (`RETRY_DELAYS_SECONDS = (0.1, 0.2, 0.4)` -> `100ms`, `200ms`, `400ms`).
- Retries ONLY on transient infrastructure/upstream failures (`TransientDatabaseError`,
  network timeouts, or upstream HTTP `429`, `500`, `502`, `503`, `504`).
- ZERO retries on client/validation/domain errors (`400`, `401`, `403`, `404`, `409`, `422`,
  `ValidationError`, `ValueError`, or `ServiceError`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from pydantic import ValidationError

from app.db.errors import TransientDatabaseError
from app.services.errors import ServiceError

T = TypeVar("T")

RETRY_DELAYS_SECONDS: tuple[float, float, float] = (0.1, 0.2, 0.4)
MAX_RETRY_ATTEMPTS: int = 3

_TRANSIENT_HTTP_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_NON_RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset(
    {400, 401, 403, 404, 405, 409, 422}
)


def extract_status_code(exc: BaseException) -> int | None:
    """Extract an integer HTTP status code from an exception if present."""
    for attr in ("status_code", "code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and not isinstance(val, bool):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def is_transient_error(exc: Exception) -> bool:
    """Return `True` strictly for transient errors; `False` for `4xx`, validation, or domain errors."""
    if isinstance(
        exc,
        (
            ServiceError,
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ),
    ):
        return False

    if isinstance(exc, TransientDatabaseError):
        return True

    status_code = extract_status_code(exc)
    if status_code is not None:
        if status_code in _NON_RETRYABLE_HTTP_STATUS_CODES:
            return False
        if status_code in _TRANSIENT_HTTP_STATUS_CODES:
            return True
        if 400 <= status_code < 500:
            return False
        if 500 <= status_code < 600:
            return True

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    exc_type_name = type(exc).__name__.lower()
    if "timeout" in exc_type_name or "connection" in exc_type_name:
        return True

    msg = str(exc).upper()
    for non_retryable_token in (
        "400",
        "401",
        "403",
        "404",
        "422",
        "INVALID_ARGUMENT",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "NOT_FOUND",
    ):
        if non_retryable_token in msg:
            return False

    return any(
        transient_token in msg
        for transient_token in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "UNAVAILABLE",
            "RESOURCE_EXHAUSTED",
            "DEADLINE_EXCEEDED",
            "INTERNAL",
        )
    )


def retry_sync(
    operation: Callable[[], T],
    *,
    delays: Sequence[float] = RETRY_DELAYS_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    should_retry: Callable[[Exception], bool] = is_transient_error,
) -> T:
    """Execute a synchronous callable up to `len(delays)` (3) attempts with deterministic backoff."""
    last_exc: Exception | None = None
    max_attempts = len(delays)
    for attempt_idx in range(max_attempts):
        try:
            return operation()
        except Exception as exc:
            last_exc = exc
            if not should_retry(exc):
                raise
            sleep_fn(float(delays[attempt_idx]))
    assert last_exc is not None
    raise last_exc


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    delays: Sequence[float] = RETRY_DELAYS_SECONDS,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    should_retry: Callable[[Exception], bool] = is_transient_error,
) -> T:
    """Execute an async callable up to `len(delays)` (3) attempts with deterministic backoff."""
    last_exc: Exception | None = None
    max_attempts = len(delays)
    for attempt_idx in range(max_attempts):
        try:
            return await operation()
        except Exception as exc:
            last_exc = exc
            if not should_retry(exc):
                raise
            await sleep_fn(float(delays[attempt_idx]))
    assert last_exc is not None
    raise last_exc
