"""Global pytest fixtures and rate limiter resets for backend test suites."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from app.core.limits import (
    ip_rate_limiter,
    user_export_limiter,
    user_generation_limiter,
)


@pytest.fixture(autouse=True)
def reset_global_rate_limiters() -> Generator[None, None, None]:
    """Reset in-memory sliding window rate limiters before and after each test."""
    ip_rate_limiter.reset()
    user_generation_limiter.reset()
    user_export_limiter.reset()
    try:
        yield
    finally:
        ip_rate_limiter.reset()
        user_generation_limiter.reset()
        user_export_limiter.reset()
