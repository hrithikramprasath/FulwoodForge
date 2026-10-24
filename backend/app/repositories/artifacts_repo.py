"""Parameterized MySQL 8.4 repository for `artifacts` (`backend/app/repositories/artifacts_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.uuid_v7`, `app.db`,
`app.repositories.base`, and Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.uuid_v7 import generate_uuid7_bytes, uuid7_bytes_to_str
from app.db.errors import DatabaseError, classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import ensure_utc_datetime

_SELECT_BY_GEN_AND_FORMAT_SQL = """
SELECT
    id,
    uuid_v7,
    generation_id,
    user_id,
    format,
    file_path,
    sha256_checksum,
    byte_size,
    created_at
FROM artifacts
WHERE generation_id = %s AND format = %s
ORDER BY id DESC
LIMIT 1
"""

_UPDATE_ARTIFACT_SQL = """
UPDATE artifacts
SET file_path = %s,
    sha256_checksum = %s,
    byte_size = %s
WHERE id = %s
"""

_INSERT_ARTIFACT_SQL = """
INSERT INTO artifacts (
    uuid_v7,
    generation_id,
    user_id,
    format,
    file_path,
    sha256_checksum,
    byte_size
) VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


@dataclass(frozen=True, slots=True)
class ArtifactRow:
    """Domain row representing an exported document artifact in `artifacts`."""

    id: int
    uuid_v7: bytes
    generation_id: int
    user_id: int
    format: str
    file_path: str | None
    sha256_checksum: bytes
    byte_size: int
    created_at: datetime

    @property
    def uuid_str(self) -> str:
        return uuid7_bytes_to_str(self.uuid_v7)


def _hydrate_artifact_row(row: tuple[Any, ...]) -> ArtifactRow:
    (
        artifact_id,
        uuid_raw,
        generation_id,
        user_id,
        fmt,
        file_path,
        sha256_checksum,
        byte_size,
        created_at,
    ) = row
    return ArtifactRow(
        id=int(artifact_id),
        uuid_v7=bytes(uuid_raw),
        generation_id=int(generation_id),
        user_id=int(user_id),
        format=str(fmt),
        file_path=str(file_path) if file_path is not None else None,
        sha256_checksum=bytes(sha256_checksum),
        byte_size=int(byte_size),
        created_at=ensure_utc_datetime(created_at),
    )


class ArtifactsRepository:
    """Parameterized data access layer for `artifacts`."""

    async def get_by_generation_and_format(
        self,
        conn: AsyncConnectionProtocol,
        generation_id: int,
        fmt: str,
    ) -> ArtifactRow | None:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _SELECT_BY_GEN_AND_FORMAT_SQL,
                    (int(generation_id), fmt),
                )
                row = await cur.fetchone()
                return _hydrate_artifact_row(row) if row is not None else None
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def upsert_artifact(
        self,
        conn: AsyncConnectionProtocol,
        generation_id: int,
        user_id: int,
        fmt: str,
        file_path: str | None,
        sha256_checksum: bytes,
        byte_size: int,
    ) -> ArtifactRow:
        """Insert or update an artifact metadata record for `(generation_id, fmt)`."""
        existing = await self.get_by_generation_and_format(conn, generation_id, fmt)
        try:
            async with conn.cursor() as cur:
                if existing is not None:
                    await cur.execute(
                        _UPDATE_ARTIFACT_SQL,
                        (
                            file_path,
                            bytes(sha256_checksum),
                            int(byte_size),
                            existing.id,
                        ),
                    )
                else:
                    artifact_uuid = generate_uuid7_bytes()
                    await cur.execute(
                        _INSERT_ARTIFACT_SQL,
                        (
                            artifact_uuid,
                            int(generation_id),
                            int(user_id),
                            fmt,
                            file_path,
                            bytes(sha256_checksum),
                            int(byte_size),
                        ),
                    )
                await cur.execute(
                    _SELECT_BY_GEN_AND_FORMAT_SQL,
                    (int(generation_id), fmt),
                )
                row = await cur.fetchone()
                if row is None:
                    raise DatabaseError("Failed to fetch upserted artifact row")
                return _hydrate_artifact_row(row)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
