"""Parameterized MySQL 8.4 repository for `users` (`backend/app/repositories/users_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.uuid_v7`, `app.db`,
`app.repositories.base`, `app.schemas`, and Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from app.core.uuid_v7 import generate_uuid7_bytes, uuid7_bytes_to_str
from app.db.errors import DatabaseError, classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import (
    KeysetCursor,
    KeysetPage,
    ensure_utc_datetime,
    to_mysql_utc,
)
from app.schemas.admin import AdminUserUpdateRequest
from app.schemas.auth import AuthenticatedUserDTO, UserRole, UserStatus

_INSERT_USER_SQL = """
INSERT INTO users (
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_SELECT_BY_ID_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
WHERE id = %s
LIMIT 1
"""

_SELECT_BY_UUID_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
WHERE uuid_v7 = %s
LIMIT 1
"""

_SELECT_BY_USERNAME_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
WHERE username = %s
LIMIT 1
"""

_COUNT_ADMINS_SQL = "SELECT COUNT(*) FROM users WHERE role = 'ADMIN'"

_UPDATE_PASSWORD_SQL = """
UPDATE users
SET password_hash = %s, must_change_password = 0, updated_at = UTC_TIMESTAMP(6)
WHERE id = %s
"""

_SET_TEMP_PASSWORD_SQL = """
UPDATE users
SET password_hash = %s, must_change_password = 1, updated_at = UTC_TIMESTAMP(6)
WHERE id = %s
"""

_UPDATE_USER_ADMIN_SQL = """
UPDATE users
SET role = %s, status = %s, hourly_generation_limit = %s, updated_at = UTC_TIMESTAMP(6)
WHERE id = %s
"""

_TOUCH_LAST_LOGIN_SQL = """
UPDATE users
SET last_login_at = %s, updated_at = UTC_TIMESTAMP(6)
WHERE id = %s
"""

_LIST_USERS_FIRST_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
ORDER BY created_at DESC, id DESC
LIMIT %s
"""

_LIST_USERS_CURSOR_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
WHERE (created_at, id) < (%s, %s)
ORDER BY created_at DESC, id DESC
LIMIT %s
"""

_LIST_USERS_STATUS_FIRST_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
WHERE status = %s
ORDER BY created_at DESC, id DESC
LIMIT %s
"""

_LIST_USERS_STATUS_CURSOR_SQL = """
SELECT
    id,
    uuid_v7,
    username,
    display_name,
    password_hash,
    role,
    status,
    must_change_password,
    hourly_generation_limit,
    last_login_at,
    created_at,
    updated_at
FROM users
WHERE status = %s
  AND (created_at, id) < (%s, %s)
ORDER BY created_at DESC, id DESC
LIMIT %s
"""


@dataclass(frozen=True, slots=True)
class UserRow:
    """Domain row representing a record in `users`."""

    id: int
    uuid_v7: bytes
    username: str
    display_name: str
    password_hash: str
    role: UserRole
    status: UserStatus
    must_change_password: bool
    hourly_generation_limit: int
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def uuid_str(self) -> str:
        return uuid7_bytes_to_str(self.uuid_v7)

    def to_dto(self) -> AuthenticatedUserDTO:
        return AuthenticatedUserDTO(
            uuid=self.uuid_str,
            username=self.username,
            display_name=self.display_name,
            role=self.role,
            status=self.status,
            must_change_password=self.must_change_password,
            hourly_generation_limit=self.hourly_generation_limit,
            created_at=self.created_at,
            last_login_at=self.last_login_at,
        )


def hydrate_user_row(row: tuple[Any, ...]) -> UserRow:
    """Hydrate a 12-column `users` tuple into a `UserRow`."""
    (
        user_id,
        uuid_raw,
        username,
        display_name,
        password_hash,
        role,
        status,
        must_change,
        hourly_limit,
        last_login_at,
        created_at,
        updated_at,
    ) = row
    return UserRow(
        id=int(user_id),
        uuid_v7=bytes(uuid_raw),
        username=str(username),
        display_name=str(display_name),
        password_hash=str(password_hash),
        role=cast(UserRole, str(role)),
        status=cast(UserStatus, str(status)),
        must_change_password=bool(must_change),
        hourly_generation_limit=int(hourly_limit),
        last_login_at=(
            ensure_utc_datetime(last_login_at)
            if isinstance(last_login_at, datetime)
            else None
        ),
        created_at=ensure_utc_datetime(created_at),
        updated_at=ensure_utc_datetime(updated_at),
    )


class UsersRepository:
    """Parameterized data access layer for `users` (`REQ-AUTH-01`..`06`, `REQ-ADMIN-01`..`02`)."""

    async def create_user(
        self,
        conn: AsyncConnectionProtocol,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: UserRole = "USER",
        status: UserStatus = "ACTIVE",
        must_change_password: bool = False,
        hourly_generation_limit: int = 30,
        uuid_bytes: bytes | None = None,
    ) -> UserRow:
        """Insert a new user with a `BINARY(16)` `UUIDv7` identifier and return the hydrated `UserRow`."""
        resolved_uuid = uuid_bytes or generate_uuid7_bytes()
        normalized_username = username.strip().lower()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_USER_SQL,
                    (
                        resolved_uuid,
                        normalized_username,
                        display_name.strip(),
                        password_hash,
                        role,
                        status,
                        1 if must_change_password else 0,
                        int(hourly_generation_limit),
                    ),
                )
                await cur.execute(_SELECT_BY_UUID_SQL, (resolved_uuid,))
                row = await cur.fetchone()
                if row is None:
                    raise DatabaseError("Failed to fetch newly created user row")
                return hydrate_user_row(row)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_by_id(
        self, conn: AsyncConnectionProtocol, user_id: int
    ) -> UserRow | None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT_BY_ID_SQL, (int(user_id),))
                row = await cur.fetchone()
                return hydrate_user_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_by_uuid(
        self, conn: AsyncConnectionProtocol, uuid_bytes: bytes
    ) -> UserRow | None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT_BY_UUID_SQL, (bytes(uuid_bytes),))
                row = await cur.fetchone()
                return hydrate_user_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_by_username(
        self, conn: AsyncConnectionProtocol, username: str
    ) -> UserRow | None:
        normalized = username.strip().lower()
        try:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT_BY_USERNAME_SQL, (normalized,))
                row = await cur.fetchone()
                return hydrate_user_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def count_admins(self, conn: AsyncConnectionProtocol) -> int:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_COUNT_ADMINS_SQL)
                row = await cur.fetchone()
                return int(row[0]) if row is not None else 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def update_password(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        new_password_hash: str,
    ) -> None:
        """Update a user's password hash and clear `must_change_password = 0`."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _UPDATE_PASSWORD_SQL, (new_password_hash, int(user_id))
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def set_temporary_password(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        temp_password_hash: str,
    ) -> None:
        """Set a one-time temporary password hash and enforce `must_change_password = 1` (`REQ-ADMIN-02`)."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _SET_TEMP_PASSWORD_SQL, (temp_password_hash, int(user_id))
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def update_user_admin(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        update: AdminUserUpdateRequest,
    ) -> UserRow | None:
        """Update a user's `role`, `status`, or `hourly_generation_limit` and return the updated `UserRow`."""
        existing = await self.get_by_id(conn, user_id)
        if existing is None:
            return None

        new_role = update.role if update.role is not None else existing.role
        new_status = update.status if update.status is not None else existing.status
        new_limit = (
            update.hourly_generation_limit
            if update.hourly_generation_limit is not None
            else existing.hourly_generation_limit
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _UPDATE_USER_ADMIN_SQL,
                    (new_role, new_status, int(new_limit), int(user_id)),
                )
            return await self.get_by_id(conn, user_id)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def touch_last_login(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        login_at: datetime,
    ) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _TOUCH_LAST_LOGIN_SQL,
                    (to_mysql_utc(login_at), int(user_id)),
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def list_users_keyset(
        self,
        conn: AsyncConnectionProtocol,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
        status: UserStatus | None = None,
    ) -> KeysetPage[UserRow]:
        """List users ordered by `(created_at DESC, id DESC)` using composite keyset pagination."""
        clamped_limit = max(1, min(int(limit), 200))
        fetch_limit = clamped_limit + 1
        try:
            async with conn.cursor() as cur:
                if status is None and cursor is None:
                    await cur.execute(_LIST_USERS_FIRST_SQL, (fetch_limit,))
                elif status is None and cursor is not None:
                    await cur.execute(
                        _LIST_USERS_CURSOR_SQL,
                        (to_mysql_utc(cursor.created_at), int(cursor.id), fetch_limit),
                    )
                elif status is not None and cursor is None:
                    await cur.execute(
                        _LIST_USERS_STATUS_FIRST_SQL, (status, fetch_limit)
                    )
                else:
                    assert cursor is not None
                    await cur.execute(
                        _LIST_USERS_STATUS_CURSOR_SQL,
                        (
                            status,
                            to_mysql_utc(cursor.created_at),
                            int(cursor.id),
                            fetch_limit,
                        ),
                    )
                raw_rows = await cur.fetchall()

            hydrated = [hydrate_user_row(r) for r in raw_rows]
            has_more = len(hydrated) > clamped_limit
            items = hydrated[:clamped_limit]
            next_cursor = (
                KeysetCursor(created_at=items[-1].created_at, id=items[-1].id).encode()
                if has_more and items
                else None
            )
            return KeysetPage(items=items, next_cursor=next_cursor)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
