"""Parameterized MySQL 8.4 repository for `idempotency_keys` (`backend/app/repositories/idempotency_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.db`, `app.repositories.base`,
and Python standard library (`REQ-API-02`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from app.db.errors import DatabaseError, classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import ensure_utc_datetime, to_mysql_utc

IdempotencyStatus = Literal["IN_PROGRESS", "COMPLETED", "FAILED"]

_SELECT_FOR_UPDATE_SQL = """
SELECT
    id,
    user_id,
    idempotency_key,
    request_hash,
    status,
    response_status,
    response_body_json,
    expires_at,
    created_at
FROM idempotency_keys
WHERE user_id = %s AND idempotency_key = %s
FOR UPDATE
"""

_INSERT_KEY_SQL = """
INSERT INTO idempotency_keys (
    user_id,
    idempotency_key,
    request_hash,
    status,
    expires_at
) VALUES (%s, %s, %s, 'IN_PROGRESS', %s)
"""

_COMPLETE_KEY_SQL = """
UPDATE idempotency_keys
SET status = 'COMPLETED',
    response_status = %s,
    response_body_json = %s
WHERE id = %s
"""

_FAIL_KEY_SQL = """
UPDATE idempotency_keys
SET status = 'FAILED'
WHERE id = %s
"""

_DELETE_EXPIRED_SQL = """
DELETE FROM idempotency_keys
WHERE expires_at <= %s
"""


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Domain row representing an entry in `idempotency_keys`."""

    id: int
    user_id: int
    idempotency_key: str
    request_hash: bytes
    status: IdempotencyStatus
    response_status: int | None
    response_body_json: str | None
    expires_at: datetime
    created_at: datetime
    is_newly_created: bool = False


def _hydrate_idempotency_row(
    row: tuple[Any, ...], *, is_newly_created: bool
) -> IdempotencyRecord:
    (
        record_id,
        user_id,
        idempotency_key,
        request_hash,
        status,
        response_status,
        response_body_json,
        expires_at,
        created_at,
    ) = row
    body_str: str | None = None
    if response_body_json is not None:
        body_str = (
            response_body_json.decode("utf-8")
            if isinstance(response_body_json, (bytes, bytearray))
            else str(response_body_json)
        )
    return IdempotencyRecord(
        id=int(record_id),
        user_id=int(user_id),
        idempotency_key=str(idempotency_key),
        request_hash=bytes(request_hash),
        status=cast(IdempotencyStatus, str(status)),
        response_status=int(response_status) if response_status is not None else None,
        response_body_json=body_str,
        expires_at=ensure_utc_datetime(expires_at),
        created_at=ensure_utc_datetime(created_at),
        is_newly_created=is_newly_created,
    )


class IdempotencyRepository:
    """Parameterized data access layer for `idempotency_keys` (`REQ-API-02`)."""

    async def lock_or_create_key(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        idempotency_key: str,
        request_hash: bytes,
        expires_at: datetime,
    ) -> IdempotencyRecord:
        """Lock an existing `(user_id, idempotency_key)` row via `SELECT ... FOR UPDATE`, or insert a new `IN_PROGRESS` row."""
        cleaned_key = idempotency_key.strip()[:64]
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _SELECT_FOR_UPDATE_SQL,
                    (int(user_id), cleaned_key),
                )
                existing = await cur.fetchone()
                if existing is not None:
                    return _hydrate_idempotency_row(existing, is_newly_created=False)

                await cur.execute(
                    _INSERT_KEY_SQL,
                    (
                        int(user_id),
                        cleaned_key,
                        bytes(request_hash),
                        to_mysql_utc(expires_at),
                    ),
                )
                await cur.execute(
                    _SELECT_FOR_UPDATE_SQL,
                    (int(user_id), cleaned_key),
                )
                created = await cur.fetchone()
                if created is None:
                    raise DatabaseError(
                        "Failed to lock newly created idempotency_key row"
                    )
                return _hydrate_idempotency_row(created, is_newly_created=True)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def complete_key(
        self,
        conn: AsyncConnectionProtocol,
        record_id: int,
        response_status: int,
        response_body_json: str,
    ) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _COMPLETE_KEY_SQL,
                    (int(response_status), response_body_json, int(record_id)),
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def fail_key(self, conn: AsyncConnectionProtocol, record_id: int) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_FAIL_KEY_SQL, (int(record_id),))
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def delete_expired(
        self, conn: AsyncConnectionProtocol, now_utc: datetime
    ) -> int:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_DELETE_EXPIRED_SQL, (to_mysql_utc(now_utc),))
                return max(0, int(cur.rowcount))
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
