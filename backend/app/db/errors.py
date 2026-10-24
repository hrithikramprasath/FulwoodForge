"""MySQL 8.4 LTS exception hierarchy and structured error classification (`backend/app/db/errors.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.logging` and standard library.
Never silences database errors (remediating `VULN-10`).
"""

from __future__ import annotations

import logging

from app.core.logging import log_event

logger = logging.getLogger(__name__)

TRANSIENT_ERRNOS: frozenset[int] = frozenset({1205, 1213, 2003, 2006, 2013})
UNIQUE_VIOLATION_ERRNO: int = 1062
DEADLOCK_SQLSTATE: str = "40001"


class DatabaseError(Exception):
    """Base exception for all MySQL 8.4 data access failures."""

    def __init__(
        self,
        message: str,
        *,
        errno: int | None = None,
        sqlstate: str | None = None,
    ) -> None:
        super().__init__(message)
        self.errno = errno
        self.sqlstate = sqlstate


class TransientDatabaseError(DatabaseError):
    """Raised on retryable MySQL deadlocks (1213 / 40001), lock wait timeouts (1205), or connection drops (2003, 2006, 2013)."""


class UniqueConstraintViolationError(DatabaseError):
    """Raised on MySQL duplicate key violation (`errno == 1062`)."""


def _extract_errno_and_sqlstate(exc: BaseException) -> tuple[int | None, str | None]:
    errno_attr = getattr(exc, "errno", None)
    sqlstate_attr = getattr(exc, "sqlstate", None)
    errno: int | None = errno_attr if isinstance(errno_attr, int) else None
    sqlstate: str | None = sqlstate_attr if isinstance(sqlstate_attr, str) else None

    args: tuple[object, ...] = exc.args
    if errno is None and len(args) >= 1 and isinstance(args[0], int):
        errno = args[0]
    if sqlstate is None and len(args) >= 2 and isinstance(args[1], str):
        candidate = args[1]
        if candidate.startswith("40001") or "40001" in candidate:
            sqlstate = DEADLOCK_SQLSTATE
    return errno, sqlstate


def is_mysql_driver_exception(exc: BaseException) -> bool:
    """Return True if `exc` is a `DatabaseError`, an `asyncmy` driver error, or carries a MySQL errno/sqlstate."""
    if isinstance(exc, DatabaseError):
        return True
    errno, sqlstate = _extract_errno_and_sqlstate(exc)
    if errno is not None or sqlstate is not None:
        return True
    module_name = type(exc).__module__
    return module_name.startswith(("asyncmy", "pymysql", "MySQLdb"))


def classify_mysql_exception(exc: Exception) -> DatabaseError:
    """Translate raw MySQL driver exceptions into typed `DatabaseError` subclasses and emit structured JSON logs."""
    if isinstance(exc, DatabaseError):
        return exc

    errno, sqlstate = _extract_errno_and_sqlstate(exc)
    msg = str(exc) or exc.__class__.__name__

    if errno == UNIQUE_VIOLATION_ERRNO:
        log_event(
            logger,
            "DB_UNIQUE_VIOLATION",
            "MySQL unique constraint violation",
            level=logging.WARNING,
            errno=errno,
            sqlstate=sqlstate,
            error_type=type(exc).__name__,
        )
        return UniqueConstraintViolationError(msg, errno=errno, sqlstate=sqlstate)

    if (errno is not None and errno in TRANSIENT_ERRNOS) or (
        sqlstate == DEADLOCK_SQLSTATE
    ):
        log_event(
            logger,
            "DB_TRANSIENT_ERROR",
            "Transient MySQL error (deadlock, lock wait timeout, or connection loss)",
            level=logging.WARNING,
            errno=errno,
            sqlstate=sqlstate,
            error_type=type(exc).__name__,
        )
        return TransientDatabaseError(msg, errno=errno, sqlstate=sqlstate)

    log_event(
        logger,
        "DB_ERROR",
        "MySQL operational or programming error",
        level=logging.ERROR,
        errno=errno,
        sqlstate=sqlstate,
        error_type=type(exc).__name__,
    )
    return DatabaseError(msg, errno=errno, sqlstate=sqlstate)
