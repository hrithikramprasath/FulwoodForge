"""Parameterized MySQL 8.4 repository for `user_sessions` (`backend/app/repositories/sessions_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.uuid_v7`, `app.db`,
`app.repositories.base`, `app.repositories.users_repo`, and Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.uuid_v7 import generate_uuid7_bytes, uuid7_bytes_to_str
from app.db.errors import classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import ensure_utc_datetime, to_mysql_utc
from app.repositories.users_repo import UserRow, hydrate_user_row

_INSERT_SESSION_SQL = """
INSERT INTO user_sessions (
    uuid_v7,
    user_id,
    token_sha256,
    ip_address,
    user_agent,
    expires_at
) VALUES (%s, %s, %s, %s, %s, %s)
"""

_SELECT_ACTIVE_SESSION_WITH_USER_SQL = """
SELECT
    s.id,
    s.uuid_v7,
    s.user_id,
    s.token_sha256,
    s.ip_address,
    s.user_agent,
    s.expires_at,
    s.revoked_at,
    s.created_at,
    u.id,
    u.uuid_v7,
    u.username,
    u.display_name,
    u.password_hash,
    u.role,
    u.status,
    u.must_change_password,
    u.hourly_generation_limit,
    u.last_login_at,
    u.created_at,
    u.updated_at
FROM user_sessions s
INNER JOIN users u ON u.id = s.user_id
WHERE s.token_sha256 = %s
  AND s.revoked_at IS NULL
  AND s.expires_at > %s
LIMIT 1
"""

_EXTEND_SESSION_SQL = """
UPDATE user_sessions
SET expires_at = %s
WHERE id = %s AND revoked_at IS NULL
"""

_REVOKE_BY_TOKEN_HASH_SQL = """
UPDATE user_sessions
SET revoked_at = %s
WHERE token_sha256 = %s AND revoked_at IS NULL
"""

_REVOKE_ALL_FOR_USER_SQL = """
UPDATE user_sessions
SET revoked_at = %s
WHERE user_id = %s AND revoked_at IS NULL
"""

_REVOKE_OTHER_SESSIONS_FOR_USER_SQL = """
UPDATE user_sessions
SET revoked_at = %s
WHERE user_id = %s
  AND id != %s
  AND revoked_at IS NULL
"""


@dataclass(frozen=True, slots=True)
class SessionRow:
    """Domain row representing a record in `user_sessions`."""

    id: int
    uuid_v7: bytes
    user_id: int
    token_sha256: bytes
    ip_address: str
    user_agent: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime

    @property
    def uuid_str(self) -> str:
        return uuid7_bytes_to_str(self.uuid_v7)


def _hydrate_session_row(row: tuple[Any, ...]) -> SessionRow:
    (
        session_id,
        uuid_raw,
        user_id,
        token_sha256,
        ip_address,
        user_agent,
        expires_at,
        revoked_at,
        created_at,
    ) = row
    return SessionRow(
        id=int(session_id),
        uuid_v7=bytes(uuid_raw),
        user_id=int(user_id),
        token_sha256=bytes(token_sha256),
        ip_address=str(ip_address),
        user_agent=str(user_agent),
        expires_at=ensure_utc_datetime(expires_at),
        revoked_at=(
            ensure_utc_datetime(revoked_at)
            if isinstance(revoked_at, datetime)
            else None
        ),
        created_at=ensure_utc_datetime(created_at),
    )


class SessionsRepository:
    """Parameterized data access layer for `user_sessions` (`REQ-AUTH-05`, `REQ-ADMIN-02`)."""

    async def create_session(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        token_sha256: bytes,
        ip_address: str,
        user_agent: str,
        expires_at: datetime,
    ) -> bytes:
        """Insert a session row and return its 16-byte `uuid_v7`."""
        session_uuid = generate_uuid7_bytes()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_SESSION_SQL,
                    (
                        session_uuid,
                        int(user_id),
                        bytes(token_sha256),
                        ip_address[:45],
                        user_agent[:255],
                        to_mysql_utc(expires_at),
                    ),
                )
            return session_uuid
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_active_session_with_user(
        self,
        conn: AsyncConnectionProtocol,
        token_sha256: bytes,
        now_utc: datetime,
    ) -> tuple[SessionRow, UserRow] | None:
        """Fetch an active, non-revoked session and its joined `UserRow` in a single query."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _SELECT_ACTIVE_SESSION_WITH_USER_SQL,
                    (bytes(token_sha256), to_mysql_utc(now_utc)),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                session_row = _hydrate_session_row(row[:9])
                user_row = hydrate_user_row(row[9:21])
                return session_row, user_row
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def extend_session(
        self,
        conn: AsyncConnectionProtocol,
        session_id: int,
        new_expires_at: datetime,
    ) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _EXTEND_SESSION_SQL,
                    (to_mysql_utc(new_expires_at), int(session_id)),
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def revoke_session_by_token_hash(
        self,
        conn: AsyncConnectionProtocol,
        token_sha256: bytes,
        now_utc: datetime,
    ) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _REVOKE_BY_TOKEN_HASH_SQL,
                    (to_mysql_utc(now_utc), bytes(token_sha256)),
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def revoke_all_for_user(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        now_utc: datetime,
    ) -> int:
        """Revoke all active sessions for `user_id` and return the number of revoked sessions."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _REVOKE_ALL_FOR_USER_SQL,
                    (to_mysql_utc(now_utc), int(user_id)),
                )
                return max(0, int(cur.rowcount))
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def revoke_other_sessions_for_user(
        self,
        conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        keep_session_id: int,
        now_utc: datetime,
    ) -> int:
        """Revoke all active sessions for `user_id` except `keep_session_id` (REQ-AUTH-06)."""
        now_str = to_mysql_utc(now_utc)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _REVOKE_OTHER_SESSIONS_FOR_USER_SQL,
                    (now_str, int(user_id), int(keep_session_id)),
                )
                return max(0, int(cur.rowcount or 0))
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
