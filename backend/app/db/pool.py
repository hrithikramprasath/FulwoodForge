"""Bounded async MySQL 8.4 connection pool and connection/cursor protocols (`backend/app/db/pool.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.config`, `app.core.logging`,
`app.db.errors`, `asyncmy`, and Python standard library.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

import asyncmy

from app.core.config import AppConfig
from app.core.logging import log_event
from app.db.errors import (
    DatabaseError,
    classify_mysql_exception,
    is_mysql_driver_exception,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class AsyncCursorProtocol(Protocol):
    """Protocol for an async database cursor supporting parameterized `%s` execution."""

    @property
    def lastrowid(self) -> int: ...

    @property
    def rowcount(self) -> int: ...

    async def execute(self, query: str, args: Sequence[Any] | None = None) -> int: ...

    async def fetchone(self) -> tuple[Any, ...] | None: ...

    async def fetchall(self) -> Sequence[tuple[Any, ...]]: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class AsyncConnectionProtocol(Protocol):
    """Protocol for an async database connection."""

    def cursor(self) -> AsyncCursorProtocol: ...

    async def begin(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class AsyncDatabasePool:
    """Manages a bounded `asyncmy` connection pool for MySQL 8.4 LTS."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._pool: Any | None = None

    @property
    def is_initialized(self) -> bool:
        return self._pool is not None

    async def initialize(
        self,
        *,
        max_attempts: int = 1,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        """Create the bounded `asyncmy` connection pool if not already initialized."""
        if self._pool is not None:
            return
        attempts = max(1, max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                self._pool = await asyncmy.create_pool(
                    host=self._config.mysql_host,
                    port=self._config.mysql_port,
                    user=self._config.mysql_user,
                    password=self._config.mysql_password,
                    db=self._config.mysql_database,
                    minsize=self._config.mysql_pool_min,
                    maxsize=self._config.mysql_pool_max,
                    autocommit=False,
                    charset="utf8mb4",
                )
                log_event(
                    logger,
                    "DB_POOL_INITIALIZED",
                    "MySQL 8.4 async connection pool initialized",
                    mysql_host=self._config.mysql_host,
                    mysql_port=self._config.mysql_port,
                    mysql_database=self._config.mysql_database,
                    minsize=self._config.mysql_pool_min,
                    maxsize=self._config.mysql_pool_max,
                )
                return
            except Exception as exc:
                self._pool = None
                db_err = classify_mysql_exception(exc)
                if attempt >= attempts:
                    raise db_err from exc
                log_event(
                    logger,
                    "DB_POOL_INIT_RETRY",
                    "MySQL 8.4 connection pool not ready yet; retrying",
                    level=logging.WARNING,
                    attempt=attempt,
                    max_attempts=attempts,
                    error_class=type(exc).__name__,
                )
                await asyncio.sleep(retry_delay_seconds)

    async def close(self) -> None:
        """Gracefully close and await termination of all pooled connections."""
        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        pool.close()
        await pool.wait_closed()
        log_event(
            logger,
            "DB_POOL_CLOSED",
            "MySQL 8.4 async connection pool closed",
        )

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnectionProtocol]:
        """Acquire a connection from the pool and translate any driver errors."""
        if self._pool is None:
            raise DatabaseError("AsyncDatabasePool has not been initialized")
        try:
            async with self._pool.acquire() as conn:
                yield conn
        except DatabaseError:
            raise
        except Exception as exc:
            if is_mysql_driver_exception(exc):
                raise classify_mysql_exception(exc) from exc
            raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnectionProtocol]:
        """Acquire a connection, start an explicit transaction, commit on success, or rollback on failure."""
        async with self.connection() as conn:
            try:
                await conn.begin()
                yield conn
                await conn.commit()
            except DatabaseError:
                await conn.rollback()
                raise
            except Exception as exc:
                await conn.rollback()
                if is_mysql_driver_exception(exc):
                    raise classify_mysql_exception(exc) from exc
                raise

    async def ping(self) -> bool:
        """Execute `SELECT 1` for `/health/ready` readiness verification (`REQ-API-03`)."""
        if self._pool is None:
            return False
        try:
            async with self.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT 1")
                row = await cur.fetchone()
                return row is not None and int(row[0]) == 1
        except DatabaseError:
            return False
