"""MySQL 8.4 LTS database connection pool, error classification, and migration runner."""

from __future__ import annotations

from app.db.errors import (
    DEADLOCK_SQLSTATE,
    TRANSIENT_ERRNOS,
    UNIQUE_VIOLATION_ERRNO,
    DatabaseError,
    TransientDatabaseError,
    UniqueConstraintViolationError,
    classify_mysql_exception,
)
from app.db.migrator import MigrationRunner, split_sql_statements
from app.db.pool import (
    AsyncConnectionProtocol,
    AsyncCursorProtocol,
    AsyncDatabasePool,
)

__all__ = [
    "DEADLOCK_SQLSTATE",
    "TRANSIENT_ERRNOS",
    "UNIQUE_VIOLATION_ERRNO",
    "AsyncConnectionProtocol",
    "AsyncCursorProtocol",
    "AsyncDatabasePool",
    "DatabaseError",
    "MigrationRunner",
    "TransientDatabaseError",
    "UniqueConstraintViolationError",
    "classify_mysql_exception",
    "split_sql_statements",
]
