"""Parameterized MySQL 8.4 repository for `lesson_generations` (`backend/app/repositories/generations_repo.py`).

Replaces ephemeral local cache storage (`REQ-SEC-10` / `VULN-10`).
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
from app.schemas.content import TaskTypeLiteral

_FIND_RECENT_BY_HASH_SQL = """
SELECT
    id,
    uuid_v7,
    user_id,
    request_hash,
    subject,
    year_group,
    tier,
    exam_board,
    topic,
    task_type,
    title,
    content_json,
    slide_count,
    is_archived,
    created_at,
    updated_at
FROM lesson_generations
WHERE user_id = %s
  AND request_hash = %s
  AND is_archived = 0
  AND created_at >= %s
ORDER BY created_at DESC, id DESC
LIMIT 1
"""

_COUNT_USER_GENERATIONS_SINCE_SQL = """
SELECT COUNT(*)
FROM lesson_generations
WHERE user_id = %s
  AND created_at >= %s
"""

_INSERT_GENERATION_SQL = """
INSERT INTO lesson_generations (
    uuid_v7,
    user_id,
    request_hash,
    subject,
    year_group,
    tier,
    exam_board,
    topic,
    task_type,
    title,
    content_json,
    slide_count,
    is_archived
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
"""

_SELECT_BY_UUID_SQL = """
SELECT
    id,
    uuid_v7,
    user_id,
    request_hash,
    subject,
    year_group,
    tier,
    exam_board,
    topic,
    task_type,
    title,
    content_json,
    slide_count,
    is_archived,
    created_at,
    updated_at
FROM lesson_generations
WHERE uuid_v7 = %s
LIMIT 1
"""

_UPDATE_CONTENT_SQL = """
UPDATE lesson_generations
SET title = %s,
    content_json = %s,
    slide_count = %s,
    updated_at = UTC_TIMESTAMP(6)
WHERE id = %s AND user_id = %s
"""

_ARCHIVE_GENERATION_SQL = """
UPDATE lesson_generations
SET is_archived = 1,
    updated_at = UTC_TIMESTAMP(6)
WHERE id = %s AND user_id = %s AND is_archived = 0
"""

_LIST_FOR_USER_FIRST_SQL = """
SELECT
    id,
    uuid_v7,
    user_id,
    request_hash,
    subject,
    year_group,
    tier,
    exam_board,
    topic,
    task_type,
    title,
    content_json,
    slide_count,
    is_archived,
    created_at,
    updated_at
FROM lesson_generations
WHERE user_id = %s
  AND is_archived = 0
  AND (%s IS NULL OR subject = %s)
  AND (%s IS NULL OR task_type = %s)
ORDER BY created_at DESC, id DESC
LIMIT %s
"""

_LIST_FOR_USER_CURSOR_SQL = """
SELECT
    id,
    uuid_v7,
    user_id,
    request_hash,
    subject,
    year_group,
    tier,
    exam_board,
    topic,
    task_type,
    title,
    content_json,
    slide_count,
    is_archived,
    created_at,
    updated_at
FROM lesson_generations
WHERE user_id = %s
  AND is_archived = 0
  AND (%s IS NULL OR subject = %s)
  AND (%s IS NULL OR task_type = %s)
  AND (created_at, id) < (%s, %s)
ORDER BY created_at DESC, id DESC
LIMIT %s
"""


@dataclass(frozen=True, slots=True)
class GenerationRow:
    """Domain row representing a record in `lesson_generations`."""

    id: int
    uuid_v7: bytes
    user_id: int
    request_hash: bytes
    subject: str
    year_group: str
    tier: str | None
    exam_board: str | None
    topic: str
    task_type: TaskTypeLiteral
    title: str
    content_json: str
    slide_count: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    @property
    def uuid_str(self) -> str:
        return uuid7_bytes_to_str(self.uuid_v7)


def _hydrate_generation_row(row: tuple[Any, ...]) -> GenerationRow:
    (
        gen_id,
        uuid_raw,
        user_id,
        request_hash,
        subject,
        year_group,
        tier,
        exam_board,
        topic,
        task_type,
        title,
        content_json,
        slide_count,
        is_archived,
        created_at,
        updated_at,
    ) = row
    content_str = (
        content_json.decode("utf-8")
        if isinstance(content_json, (bytes, bytearray))
        else str(content_json)
    )
    return GenerationRow(
        id=int(gen_id),
        uuid_v7=bytes(uuid_raw),
        user_id=int(user_id),
        request_hash=bytes(request_hash),
        subject=str(subject),
        year_group=str(year_group),
        tier=str(tier) if tier is not None else None,
        exam_board=str(exam_board) if exam_board is not None else None,
        topic=str(topic),
        task_type=cast(TaskTypeLiteral, str(task_type)),
        title=str(title),
        content_json=content_str,
        slide_count=int(slide_count),
        is_archived=bool(is_archived),
        created_at=ensure_utc_datetime(created_at),
        updated_at=ensure_utc_datetime(updated_at),
    )


class GenerationsRepository:
    """Parameterized data access layer for `lesson_generations` (`REQ-SEC-03`, `REQ-SEC-10`)."""

    async def find_recent_by_hash(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        request_hash: bytes,
        since_utc: datetime,
    ) -> GenerationRow | None:
        """Look up a non-archived generation matching `(user_id, request_hash)` created at or after `since_utc`."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _FIND_RECENT_BY_HASH_SQL,
                    (int(user_id), bytes(request_hash), to_mysql_utc(since_utc)),
                )
                row = await cur.fetchone()
                return _hydrate_generation_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def count_user_generations_since(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        since_utc: datetime,
    ) -> int:
        """Count generations for `user_id` created at or after `since_utc` (`REQ-SEC-03` hourly quota check)."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _COUNT_USER_GENERATIONS_SINCE_SQL,
                    (int(user_id), to_mysql_utc(since_utc)),
                )
                row = await cur.fetchone()
                return int(row[0]) if row is not None else 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def create_generation(
        self,
        conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        request_hash: bytes,
        subject: str,
        year_group: str,
        tier: str | None,
        exam_board: str | None,
        topic: str,
        task_type: TaskTypeLiteral,
        title: str,
        content_json: str,
        slide_count: int = 0,
        uuid_bytes: bytes | None = None,
    ) -> GenerationRow:
        """Persist a newly generated resource and return its hydrated `GenerationRow`."""
        resolved_uuid = uuid_bytes or generate_uuid7_bytes()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_GENERATION_SQL,
                    (
                        resolved_uuid,
                        int(user_id),
                        bytes(request_hash),
                        subject,
                        year_group,
                        tier,
                        exam_board,
                        topic,
                        task_type,
                        title,
                        content_json,
                        int(slide_count),
                    ),
                )
                await cur.execute(_SELECT_BY_UUID_SQL, (resolved_uuid,))
                row = await cur.fetchone()
                if row is None:
                    raise DatabaseError("Failed to fetch newly created generation row")
                return _hydrate_generation_row(row)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_by_uuid(
        self, conn: AsyncConnectionProtocol, uuid_bytes: bytes
    ) -> GenerationRow | None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT_BY_UUID_SQL, (bytes(uuid_bytes),))
                row = await cur.fetchone()
                return _hydrate_generation_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def update_content(
        self,
        conn: AsyncConnectionProtocol,
        generation_id: int,
        user_id: int,
        title: str,
        content_json: str,
        slide_count: int,
    ) -> bool:
        """Update lesson content enforcing strict owner isolation (`WHERE id = %s AND user_id = %s`)."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _UPDATE_CONTENT_SQL,
                    (
                        title,
                        content_json,
                        int(slide_count),
                        int(generation_id),
                        int(user_id),
                    ),
                )
                return int(cur.rowcount) > 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def archive_generation(
        self,
        conn: AsyncConnectionProtocol,
        generation_id: int,
        user_id: int,
    ) -> bool:
        """Soft-archive a lesson generation enforcing owner isolation (`WHERE id = %s AND user_id = %s`)."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _ARCHIVE_GENERATION_SQL,
                    (int(generation_id), int(user_id)),
                )
                return int(cur.rowcount) > 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def list_for_user_keyset(
        self,
        conn: AsyncConnectionProtocol,
        user_id: int,
        cursor: KeysetCursor | None = None,
        limit: int = 25,
        subject: str | None = None,
        task_type: str | None = None,
    ) -> KeysetPage[GenerationRow]:
        """List non-archived generations for `user_id` ordered by `(created_at DESC, id DESC)`."""
        clamped_limit = max(1, min(int(limit), 100))
        fetch_limit = clamped_limit + 1
        try:
            async with conn.cursor() as cur:
                if cursor is None:
                    await cur.execute(
                        _LIST_FOR_USER_FIRST_SQL,
                        (
                            int(user_id),
                            subject,
                            subject,
                            task_type,
                            task_type,
                            fetch_limit,
                        ),
                    )
                else:
                    await cur.execute(
                        _LIST_FOR_USER_CURSOR_SQL,
                        (
                            int(user_id),
                            subject,
                            subject,
                            task_type,
                            task_type,
                            to_mysql_utc(cursor.created_at),
                            int(cursor.id),
                            fetch_limit,
                        ),
                    )
                raw_rows = await cur.fetchall()

            hydrated = [_hydrate_generation_row(r) for r in raw_rows]
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
