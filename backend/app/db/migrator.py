"""Reversible SQL migration runner for MySQL 8.4 LTS (`backend/app/db/migrator.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.logging`, `app.db.errors`,
`app.db.pool`, and Python standard library.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import log_event
from app.db.errors import DatabaseError, classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol

logger = logging.getLogger(__name__)

_CREATE_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""


def split_sql_statements(sql_text: str) -> list[str]:
    """Split a `.sql` migration file into executable SQL statements, stripping comment-only lines."""
    cleaned_lines: list[str] = []
    for raw_line in sql_text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("--"):
            continue
        cleaned_lines.append(raw_line)
    joined = "\n".join(cleaned_lines)
    statements: list[str] = []
    for part in joined.split(";"):
        stmt = part.strip()
        if stmt:
            statements.append(stmt)
    return statements


class MigrationRunner:
    """Executes reversible `*.up.sql` and `*.down.sql` migrations in deterministic lexical order."""

    def __init__(self, migrations_dir: Path | None = None) -> None:
        self._migrations_dir = migrations_dir or (
            Path(__file__).resolve().parents[2] / "migrations"
        )

    @property
    def migrations_dir(self) -> Path:
        return self._migrations_dir

    async def ensure_migrations_table(self, conn: AsyncConnectionProtocol) -> None:
        """Ensure the `schema_migrations` tracking table exists."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(_CREATE_MIGRATIONS_TABLE_SQL)
            await conn.commit()
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def list_applied_versions(self, conn: AsyncConnectionProtocol) -> list[str]:
        """Return all applied migration versions ordered ascending."""
        await self.ensure_migrations_table(conn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT version FROM schema_migrations ORDER BY version ASC"
                )
                rows = await cur.fetchall()
            return [str(row[0]) for row in rows]
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def apply_pending(self, conn: AsyncConnectionProtocol) -> list[str]:
        """Execute all unapplied `*.up.sql` files in lexical order."""
        applied = set(await self.list_applied_versions(conn))
        up_files = sorted(self._migrations_dir.glob("*.up.sql"))
        newly_applied: list[str] = []

        for up_file in up_files:
            version = up_file.name[: -len(".up.sql")]
            if version in applied:
                continue

            statements = split_sql_statements(up_file.read_text(encoding="utf-8"))
            now_utc = datetime.now(UTC).replace(tzinfo=None)
            try:
                await conn.begin()
                async with conn.cursor() as cur:
                    for stmt in statements:
                        await cur.execute(stmt)
                    await cur.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                        (version, now_utc),
                    )
                await conn.commit()
                newly_applied.append(version)
                log_event(
                    logger,
                    "DB_MIGRATION_APPLIED",
                    "Applied MySQL UP migration",
                    version=version,
                    statement_count=len(statements),
                )
            except Exception as exc:
                await conn.rollback()
                raise classify_mysql_exception(exc) from exc

        return newly_applied

    async def rollback_last(self, conn: AsyncConnectionProtocol) -> str | None:
        """Roll back the most recently applied migration using its paired `*.down.sql` script."""
        applied = await self.list_applied_versions(conn)
        if not applied:
            return None

        last_version = applied[-1]
        down_file = self._migrations_dir / f"{last_version}.down.sql"
        if not down_file.is_file():
            raise DatabaseError(
                f"Missing paired DOWN migration script: {down_file.name}"
            )

        statements = split_sql_statements(down_file.read_text(encoding="utf-8"))
        try:
            await conn.begin()
            async with conn.cursor() as cur:
                for stmt in statements:
                    await cur.execute(stmt)
                await cur.execute(
                    "DELETE FROM schema_migrations WHERE version = %s",
                    (last_version,),
                )
            await conn.commit()
            log_event(
                logger,
                "DB_MIGRATION_ROLLED_BACK",
                "Rolled back MySQL migration via DOWN script",
                version=last_version,
                statement_count=len(statements),
            )
            return last_version
        except Exception as exc:
            await conn.rollback()
            raise classify_mysql_exception(exc) from exc
