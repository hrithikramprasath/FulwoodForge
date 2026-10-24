"""Parameterized MySQL 8.4 repository for `invite_codes` (`backend/app/repositories/invite_codes_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.uuid_v7`, `app.db`,
`app.repositories.base`, `app.schemas`, and Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.uuid_v7 import generate_uuid7_bytes, uuid7_bytes_to_str
from app.db.errors import DatabaseError, classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import (
    KeysetCursor,
    KeysetPage,
    ensure_utc_datetime,
    to_mysql_utc,
)
from app.schemas.admin import InviteCodeDTO

_INSERT_INVITE_SQL = """
INSERT INTO invite_codes (
    uuid_v7,
    code,
    max_uses,
    uses_count,
    expires_at,
    created_by_user_id,
    is_active
) VALUES (%s, %s, %s, 0, %s, %s, 1)
"""

_SELECT_BY_UUID_SQL = """
SELECT
    id,
    uuid_v7,
    code,
    max_uses,
    uses_count,
    expires_at,
    created_by_user_id,
    is_active,
    created_at
FROM invite_codes
WHERE uuid_v7 = %s
LIMIT 1
"""

_SELECT_ACTIVE_FOR_UPDATE_SQL = """
SELECT
    id,
    uuid_v7,
    code,
    max_uses,
    uses_count,
    expires_at,
    created_by_user_id,
    is_active,
    created_at
FROM invite_codes
WHERE code = %s
  AND is_active = 1
  AND uses_count < max_uses
  AND (expires_at IS NULL OR expires_at > %s)
FOR UPDATE
"""

_INCREMENT_USES_SQL = """
UPDATE invite_codes
SET uses_count = uses_count + 1
WHERE id = %s
"""

_DEACTIVATE_BY_UUID_SQL = """
UPDATE invite_codes
SET is_active = 0
WHERE uuid_v7 = %s AND is_active = 1
"""

_LIST_INVITES_FIRST_SQL = """
SELECT
    id,
    uuid_v7,
    code,
    max_uses,
    uses_count,
    expires_at,
    created_by_user_id,
    is_active,
    created_at
FROM invite_codes
ORDER BY created_at DESC, id DESC
LIMIT %s
"""

_LIST_INVITES_CURSOR_SQL = """
SELECT
    id,
    uuid_v7,
    code,
    max_uses,
    uses_count,
    expires_at,
    created_by_user_id,
    is_active,
    created_at
FROM invite_codes
WHERE (created_at, id) < (%s, %s)
ORDER BY created_at DESC, id DESC
LIMIT %s
"""


@dataclass(frozen=True, slots=True)
class InviteCodeRow:
    """Domain row representing a record in `invite_codes`."""

    id: int
    uuid_v7: bytes
    code: str
    max_uses: int
    uses_count: int
    expires_at: datetime | None
    created_by_user_id: int
    is_active: bool
    created_at: datetime

    @property
    def uuid_str(self) -> str:
        return uuid7_bytes_to_str(self.uuid_v7)

    def to_dto(self) -> InviteCodeDTO:
        return InviteCodeDTO(
            uuid=self.uuid_str,
            code=self.code,
            max_uses=self.max_uses,
            uses_count=self.uses_count,
            expires_at=self.expires_at,
            is_active=self.is_active,
            created_at=self.created_at,
        )


def _hydrate_invite_row(row: tuple[Any, ...]) -> InviteCodeRow:
    (
        invite_id,
        uuid_raw,
        code,
        max_uses,
        uses_count,
        expires_at,
        created_by_user_id,
        is_active,
        created_at,
    ) = row
    return InviteCodeRow(
        id=int(invite_id),
        uuid_v7=bytes(uuid_raw),
        code=str(code),
        max_uses=int(max_uses),
        uses_count=int(uses_count),
        expires_at=(
            ensure_utc_datetime(expires_at)
            if isinstance(expires_at, datetime)
            else None
        ),
        created_by_user_id=int(created_by_user_id),
        is_active=bool(is_active),
        created_at=ensure_utc_datetime(created_at),
    )


class InviteCodesRepository:
    """Parameterized data access layer for `invite_codes` (`REQ-AUTH-04`, `REQ-ADMIN-02`)."""

    async def create_invite_code(
        self,
        conn: AsyncConnectionProtocol,
        *,
        code: str,
        max_uses: int,
        expires_at: datetime | None,
        created_by_user_id: int,
        uuid_bytes: bytes | None = None,
    ) -> InviteCodeDTO:
        """Insert a new invite code and return its `InviteCodeDTO`."""
        resolved_uuid = uuid_bytes or generate_uuid7_bytes()
        normalized_code = code.strip().upper()
        mysql_expires = to_mysql_utc(expires_at) if expires_at is not None else None
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_INVITE_SQL,
                    (
                        resolved_uuid,
                        normalized_code,
                        int(max_uses),
                        mysql_expires,
                        int(created_by_user_id),
                    ),
                )
                await cur.execute(_SELECT_BY_UUID_SQL, (resolved_uuid,))
                row = await cur.fetchone()
                if row is None:
                    raise DatabaseError(
                        "Failed to retrieve newly created invite_code row"
                    )
                return _hydrate_invite_row(row).to_dto()
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_active_for_update(
        self,
        conn: AsyncConnectionProtocol,
        code: str,
        now_utc: datetime,
    ) -> InviteCodeRow | None:
        """Lock and fetch an active, un-expired invite code with remaining capacity via `SELECT ... FOR UPDATE`."""
        normalized_code = code.strip().upper()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _SELECT_ACTIVE_FOR_UPDATE_SQL,
                    (normalized_code, to_mysql_utc(now_utc)),
                )
                row = await cur.fetchone()
                return _hydrate_invite_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def increment_uses(
        self, conn: AsyncConnectionProtocol, invite_id: int
    ) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_INCREMENT_USES_SQL, (int(invite_id),))
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def deactivate_by_uuid(
        self, conn: AsyncConnectionProtocol, uuid_bytes: bytes
    ) -> bool:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_DEACTIVATE_BY_UUID_SQL, (bytes(uuid_bytes),))
                return int(cur.rowcount) > 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def list_invite_codes_keyset(
        self,
        conn: AsyncConnectionProtocol,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
    ) -> KeysetPage[InviteCodeDTO]:
        """List invite codes ordered by `(created_at DESC, id DESC)` using composite keyset pagination."""
        clamped_limit = max(1, min(int(limit), 200))
        fetch_limit = clamped_limit + 1
        try:
            async with conn.cursor() as cur:
                if cursor is None:
                    await cur.execute(_LIST_INVITES_FIRST_SQL, (fetch_limit,))
                else:
                    await cur.execute(
                        _LIST_INVITES_CURSOR_SQL,
                        (to_mysql_utc(cursor.created_at), int(cursor.id), fetch_limit),
                    )
                raw_rows = await cur.fetchall()

            hydrated = [_hydrate_invite_row(r) for r in raw_rows]
            has_more = len(hydrated) > clamped_limit
            trimmed = hydrated[:clamped_limit]
            next_cursor = (
                KeysetCursor(
                    created_at=trimmed[-1].created_at, id=trimmed[-1].id
                ).encode()
                if has_more and trimmed
                else None
            )
            return KeysetPage(
                items=[r.to_dto() for r in trimmed],
                next_cursor=next_cursor,
            )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
