"""Wave 2 Verification Suite: MySQL 8.4 LTS DDL, AST SQL Injection Audit, and Async Repositories."""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.config import AppConfig
from app.core.uuid_v7 import generate_uuid7_bytes, uuid7_bytes_to_str
from app.db.errors import (
    DatabaseError,
    TransientDatabaseError,
    UniqueConstraintViolationError,
    classify_mysql_exception,
)
from app.db.migrator import MigrationRunner, split_sql_statements
from app.db.pool import AsyncDatabasePool
from app.repositories.artifacts_repo import ArtifactsRepository
from app.repositories.base import (
    KeysetCursor,
    decode_keyset_cursor,
    encode_keyset_cursor,
    ensure_utc_datetime,
    to_mysql_utc,
)
from app.repositories.generations_repo import GenerationsRepository
from app.repositories.idempotency_repo import IdempotencyRepository
from app.repositories.invite_codes_repo import InviteCodesRepository
from app.repositories.sessions_repo import SessionsRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.repositories.users_repo import UsersRepository
from app.schemas.admin import SystemSettingsUpdateRequest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BACKEND_ROOT / "migrations"
APP_DIR = BACKEND_ROOT / "app"
DB_DIR = APP_DIR / "db"
REPOS_DIR = APP_DIR / "repositories"


class FakeMySQLError(Exception):
    """Simulates an asyncmy MySQL driver exception with (errno, message) args."""


class RecordingCursor:
    """Deterministic in-memory cursor implementing AsyncCursorProtocol."""

    def __init__(
        self,
        *,
        fetchone_queue: list[tuple[Any, ...] | None] | None = None,
        fetchall_queue: list[list[tuple[Any, ...]]] | None = None,
        rowcount_queue: list[int] | None = None,
        lastrowid: int = 1,
        raise_on_execute: Exception | None = None,
    ) -> None:
        self.executed: list[tuple[str, tuple[Any, ...] | list[Any] | None]] = []
        self._fetchone_queue: list[tuple[Any, ...] | None] = list(fetchone_queue or [])
        self._fetchall_queue: list[list[tuple[Any, ...]]] = list(fetchall_queue or [])
        self._rowcount_queue: list[int] = list(rowcount_queue or [])
        self._current_rowcount: int = 1
        self._lastrowid: int = lastrowid
        self._raise_on_execute = raise_on_execute

    @property
    def lastrowid(self) -> int:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._current_rowcount

    async def execute(
        self,
        query: str,
        args: Any = None,
    ) -> int:
        self.executed.append((query, args))
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        if self._rowcount_queue:
            self._current_rowcount = self._rowcount_queue.pop(0)
        return self._current_rowcount

    async def fetchone(self) -> tuple[Any, ...] | None:
        if self._fetchone_queue:
            return self._fetchone_queue.pop(0)
        return None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        if self._fetchall_queue:
            return self._fetchall_queue.pop(0)
        return []

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


class RecordingConnection:
    """Deterministic in-memory connection implementing AsyncConnectionProtocol."""

    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor
        self.begin_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self) -> RecordingCursor:
        return self._cursor

    async def begin(self) -> None:
        self.begin_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


# ============================================================================
# 1. STATIC DDL INVARIANT AUDIT
# ============================================================================


def test_ddl_migrations_invariants() -> None:
    """Verify 0001_initial_schema.up.sql and .down.sql obey all MySQL 8.4 LTS physical invariants."""
    up_path = MIGRATIONS_DIR / "0001_initial_schema.up.sql"
    down_path = MIGRATIONS_DIR / "0001_initial_schema.down.sql"
    assert up_path.is_file(), "0001_initial_schema.up.sql must exist"
    assert down_path.is_file(), "0001_initial_schema.down.sql must exist"

    up_sql = up_path.read_text(encoding="utf-8")
    down_sql = down_path.read_text(encoding="utf-8")

    expected_tables = [
        "system_settings",
        "users",
        "user_sessions",
        "invite_codes",
        "lesson_generations",
        "artifacts",
        "idempotency_keys",
        "token_ledger",
        "audit_events",
    ]

    statements = split_sql_statements(up_sql)
    assert len(statements) == 9, (
        f"Expected 9 CREATE TABLE statements, found {len(statements)}"
    )

    for table_name, stmt in zip(expected_tables, statements, strict=True):
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in stmt
        assert (
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci" in stmt
        )
        assert "id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY" in stmt

    # BINARY(16) uuid_v7 on all entity tables carrying public UUIDv7 identifiers
    tables_with_uuid_v7 = [
        "users",
        "user_sessions",
        "invite_codes",
        "lesson_generations",
        "artifacts",
        "token_ledger",
        "audit_events",
    ]
    for stmt, table_name in zip(statements, expected_tables, strict=True):
        if table_name in tables_with_uuid_v7:
            assert "uuid_v7 BINARY(16) NOT NULL UNIQUE" in stmt

    # Zero UUIDv4 VARCHAR(36)/CHAR(36) PKs or FLOAT/DOUBLE types
    assert re.search(r"\b(FLOAT|DOUBLE)\b", up_sql, flags=re.IGNORECASE) is None
    assert (
        re.search(r"\b(VARCHAR|CHAR)\s*\(\s*36\s*\)", up_sql, flags=re.IGNORECASE)
        is None
    )
    assert "estimated_cost_usd DECIMAL(19,4) NOT NULL DEFAULT 0.0000" in up_sql

    # Required Foreign Keys
    expected_fks = [
        "CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
        "CONSTRAINT fk_invites_creator FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT",
        "CONSTRAINT fk_generations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
        "CONSTRAINT fk_artifacts_generation FOREIGN KEY (generation_id) REFERENCES lesson_generations(id) ON DELETE CASCADE",
        "CONSTRAINT fk_artifacts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
        "CONSTRAINT fk_idempotency_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
        "CONSTRAINT fk_ledger_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
        "CONSTRAINT fk_ledger_generation FOREIGN KEY (generation_id) REFERENCES lesson_generations(id) ON DELETE SET NULL",
        "CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL",
        "CONSTRAINT fk_audit_target FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL",
    ]
    for fk in expected_fks:
        assert fk in up_sql, f"Missing foreign key definition: {fk}"

    # Required Composite & Keyset Indexes
    expected_indexes = [
        "INDEX idx_users_status_created (status, created_at DESC, id DESC)",
        "INDEX idx_sessions_user_active (user_id, revoked_at, expires_at)",
        "INDEX idx_invites_active_created (is_active, created_at DESC, id DESC)",
        "INDEX idx_generations_user_hash_created (user_id, request_hash, created_at DESC)",
        "INDEX idx_generations_user_keyset (user_id, is_archived, created_at DESC, id DESC)",
        "INDEX idx_artifacts_gen_format (generation_id, format)",
        "UNIQUE KEY uq_idempotency_user_key (user_id, idempotency_key)",
        "INDEX idx_idempotency_expires (expires_at)",
        "INDEX idx_ledger_user_created (user_id, created_at DESC, id DESC)",
        "INDEX idx_ledger_created_keyset (created_at DESC, id DESC)",
        "INDEX idx_audit_keyset (created_at DESC, id DESC)",
        "INDEX idx_audit_actor_keyset (actor_user_id, created_at DESC, id DESC)",
        "INDEX idx_audit_type_keyset (event_type, created_at DESC, id DESC)",
        "INDEX idx_audit_type_ip_created (event_type, ip_address, created_at DESC)",
    ]
    for idx in expected_indexes:
        assert idx in up_sql, f"Missing composite index: {idx}"

    # Verify down.sql drops all 9 tables in exact reverse FK dependency order
    down_statements = split_sql_statements(down_sql)
    expected_reverse = list(reversed(expected_tables))
    assert len(down_statements) == 9
    for table_name, stmt in zip(expected_reverse, down_statements, strict=True):
        assert stmt == f"DROP TABLE IF EXISTS {table_name}"


# ============================================================================
# 2. STATIC AST SQL INJECTION & UNIDIRECTIONAL IMPORT SCAN
# ============================================================================


def test_ast_unidirectional_imports_and_zero_dynamic_sql() -> None:
    """Verify db/ and repositories/ have zero upward imports and zero dynamic SQL formatting in execute()."""
    target_files = sorted(list(DB_DIR.glob("*.py")) + list(REPOS_DIR.glob("*.py")))
    assert len(target_files) >= 14

    forbidden_import_prefixes = ("app.services", "app.api", "app.main", "fastapi")

    for py_file in target_files:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in forbidden_import_prefixes:
                        assert not alias.name.startswith(prefix), (
                            f"Forbidden import '{alias.name}' in {py_file}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for prefix in forbidden_import_prefixes:
                    assert not module_name.startswith(prefix), (
                        f"Forbidden from-import '{module_name}' in {py_file}"
                    )

            # Inspect all .execute(...) calls to ensure zero f-strings, .format(), or % formatting
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
            ):
                first_arg = node.args[0]
                assert not isinstance(first_arg, ast.JoinedStr), (
                    f"f-string inside execute() in {py_file}:{node.lineno}"
                )
                assert not (
                    isinstance(first_arg, ast.BinOp)
                    and isinstance(first_arg.op, ast.Mod)
                ), f"% string formatting inside execute() in {py_file}:{node.lineno}"
                assert not (
                    isinstance(first_arg, ast.Call)
                    and isinstance(first_arg.func, ast.Attribute)
                    and first_arg.func.attr == "format"
                ), f".format() call inside execute() in {py_file}:{node.lineno}"


def test_sqlite_cache_db_completely_eliminated() -> None:
    """Verify cache_db.py is deleted and zero references to sqlite3/cache_db/_warn_once remain in app/."""
    cache_db_path = APP_DIR / "services" / "cache_db.py"
    assert not cache_db_path.exists(), (
        "backend/app/services/cache_db.py must be deleted"
    )

    forbidden_tokens = ("sqlite3", "cache_db", "cache.db", "_warn_once")
    for py_file in APP_DIR.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in content, (
                f"Forbidden legacy SQLite token '{token}' in {py_file}"
            )


# ============================================================================
# 3. ERROR CLASSIFICATION, POOL & MIGRATOR UNIT TESTS
# ============================================================================


def test_mysql_error_classification() -> None:
    """Verify MySQL errno 1213/1205/40001 -> TransientDatabaseError and 1062 -> UniqueConstraintViolationError."""
    deadlock_err = classify_mysql_exception(
        FakeMySQLError(1213, "Deadlock found when trying to get lock")
    )
    assert isinstance(deadlock_err, TransientDatabaseError)
    assert deadlock_err.errno == 1213

    lock_timeout_err = classify_mysql_exception(
        FakeMySQLError(1205, "Lock wait timeout exceeded")
    )
    assert isinstance(lock_timeout_err, TransientDatabaseError)
    assert lock_timeout_err.errno == 1205

    sqlstate_err = classify_mysql_exception(
        FakeMySQLError(9999, "SQLSTATE 40001 serialization failure")
    )
    assert isinstance(sqlstate_err, TransientDatabaseError)

    dup_err = classify_mysql_exception(
        FakeMySQLError(1062, "Duplicate entry 'foo' for key 'users.username'")
    )
    assert isinstance(dup_err, UniqueConstraintViolationError)
    assert dup_err.errno == 1062

    generic_err = classify_mysql_exception(
        FakeMySQLError(1045, "Access denied for user")
    )
    assert isinstance(generic_err, DatabaseError)
    assert not isinstance(generic_err, TransientDatabaseError)
    assert not isinstance(generic_err, UniqueConstraintViolationError)

    passthrough = classify_mysql_exception(dup_err)
    assert passthrough is dup_err


@pytest.mark.asyncio
async def test_async_database_pool_lifecycle_and_transactions() -> None:
    """Verify AsyncDatabasePool connection, transaction commit/rollback, and ping."""
    cursor = RecordingCursor(fetchone_queue=[(1,)])
    conn = RecordingConnection(cursor)

    class FakeDriverPoolContext:
        async def __aenter__(self) -> RecordingConnection:
            return conn

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            return None

    fake_driver_pool = MagicMock()
    fake_driver_pool.acquire.return_value = FakeDriverPoolContext()
    fake_driver_pool.wait_closed = AsyncMock()

    config = AppConfig(
        app_env="test",
        mysql_host="localhost",
        mysql_port=3306,
        mysql_user="forge",
        mysql_password="test-mysql-password",
        mysql_database="fulwood_forge",
        mysql_pool_min=1,
        mysql_pool_max=5,
        gemini_api_key="test-gemini-key",
        gemini_model="gemini-2.5-flash",
        session_cookie_name="ff_session",
        session_cookie_secure=False,
        session_ttl_hours=12,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="AdminPassword123!",
        artifacts_dir=BACKEND_ROOT / "artifacts",
    )

    pool = AsyncDatabasePool(config)
    with patch(
        "app.db.pool.asyncmy.create_pool", AsyncMock(return_value=fake_driver_pool)
    ):
        await pool.initialize()

    assert pool.is_initialized is True
    assert await pool.ping() is True

    # Transaction commit
    async with pool.transaction() as tx_conn, tx_conn.cursor() as cur:
        await cur.execute("SELECT 1")
    assert conn.begin_calls == 1
    assert conn.commit_calls == 1
    assert conn.rollback_calls == 0

    # Transaction rollback + error classification on MySQL deadlock
    with pytest.raises(TransientDatabaseError):
        async with pool.transaction():
            raise FakeMySQLError(1213, "Deadlock found")
    assert conn.rollback_calls == 1

    # Non-database domain exceptions roll back transaction and propagate untouched
    class DomainTestError(Exception):
        pass

    with pytest.raises(DomainTestError, match="upstream 502"):
        async with pool.transaction():
            raise DomainTestError("upstream 502")
    assert conn.rollback_calls == 2

    await pool.close()
    assert pool.is_initialized is False
    fake_driver_pool.close.assert_called_once()


@pytest.mark.asyncio
async def test_migration_runner_apply_and_rollback() -> None:
    """Verify MigrationRunner applies pending migrations and rolls back the last applied version."""
    cursor = RecordingCursor(
        fetchall_queue=[[], [("0001_initial_schema",)]],
    )
    conn = RecordingConnection(cursor)
    runner = MigrationRunner(migrations_dir=MIGRATIONS_DIR)

    applied = await runner.apply_pending(conn)
    assert applied == ["0001_initial_schema"]
    assert any("INSERT INTO schema_migrations" in q for q, _ in cursor.executed)

    rolled_back = await runner.rollback_last(conn)
    assert rolled_back == "0001_initial_schema"
    assert any(
        "DELETE FROM schema_migrations WHERE version = %s" in q
        for q, _ in cursor.executed
    )


# ============================================================================
# 4. REPOSITORY CONTRACT & UNIT TESTS
# ============================================================================


def test_keyset_cursor_round_trip_and_utc_helpers() -> None:
    """Verify keyset cursor base64 encoding/decoding and UTC normalization."""
    now = datetime(2026, 10, 17, 9, 0, 0, 123456, tzinfo=UTC)
    cursor = KeysetCursor(created_at=now, id=42)
    token = encode_keyset_cursor(cursor)
    decoded = decode_keyset_cursor(token)
    assert decoded == cursor
    assert decoded.created_at.tzinfo == UTC
    assert to_mysql_utc(now).tzinfo is None

    with pytest.raises(ValueError, match="Malformed keyset pagination cursor"):
        decode_keyset_cursor("not-valid-base64!!!")


@pytest.mark.asyncio
async def test_system_settings_repository() -> None:
    """Verify SystemSettingsRepository get_or_init_settings and update_settings."""
    now_naive = datetime(2026, 10, 17, 9, 0, 0)
    row_invite = (
        1,
        "INVITE_ONLY",
        30,
        0,
        '["Biology","Chemistry"]',
        '["worksheet","powerpoint"]',
        None,
        now_naive,
    )
    row_open = (
        1,
        "OPEN",
        45,
        1,
        '["Biology"]',
        '["worksheet"]',
        99,
        now_naive,
    )
    cursor = RecordingCursor(
        fetchone_queue=[
            row_invite,  # get_or_init_settings
            row_invite,  # update_settings -> get_or_init_settings
            row_invite,  # update_settings -> SELECT existing id
            row_open,  # update_settings -> SELECT updated row
        ]
    )
    conn = RecordingConnection(cursor)
    repo = SystemSettingsRepository()

    settings = await repo.get_or_init_settings(conn)
    assert settings.registration_mode == "INVITE_ONLY"
    assert settings.default_hourly_quota == 30
    assert settings.updated_at.tzinfo == UTC

    updated = await repo.update_settings(
        conn,
        SystemSettingsUpdateRequest(
            registration_mode="OPEN",
            default_hourly_quota=45,
            maintenance_mode=True,
            allowed_subjects=["Biology"],
            allowed_task_types=["worksheet"],
        ),
        updated_by_user_id=99,
    )
    assert updated.registration_mode == "OPEN"
    assert updated.default_hourly_quota == 45
    assert updated.maintenance_mode is True


@pytest.mark.asyncio
async def test_users_and_sessions_repositories() -> None:
    """Verify UsersRepository and SessionsRepository BINARY(16) UUIDv7 hydration and duplicate handling."""
    user_uuid = generate_uuid7_bytes()
    session_uuid = generate_uuid7_bytes()
    now_naive = datetime(2026, 10, 17, 9, 0, 0)
    expires_naive = datetime(2026, 10, 17, 21, 0, 0)

    user_tuple = (
        10,
        user_uuid,
        "mrs_smith",
        "Mrs Smith",
        "$argon2id$v=19$m=65536,t=3,p=4$hash",
        "USER",
        "ACTIVE",
        0,
        30,
        None,
        now_naive,
        now_naive,
    )

    cursor = RecordingCursor(
        lastrowid=10,
        fetchone_queue=[
            user_tuple,  # create_user -> select by uuid
            user_tuple,  # get_by_uuid
            (
                55,
                session_uuid,
                10,
                b"x" * 32,
                "127.0.0.1",
                "pytest-agent",
                expires_naive,
                None,
                now_naive,
                *user_tuple,
            ),
        ],
    )
    conn = RecordingConnection(cursor)
    users_repo = UsersRepository()
    sessions_repo = SessionsRepository()

    created_user = await users_repo.create_user(
        conn,
        username="Mrs_Smith",
        display_name="Mrs Smith",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
        role="USER",
        uuid_bytes=user_uuid,
    )
    assert created_user.id == 10
    assert created_user.uuid_str == uuid7_bytes_to_str(user_uuid)
    assert created_user.username == "mrs_smith"

    fetched_by_uuid = await users_repo.get_by_uuid(conn, user_uuid)
    assert fetched_by_uuid is not None
    assert fetched_by_uuid.uuid_v7 == user_uuid

    created_session_uuid = await sessions_repo.create_session(
        conn,
        user_id=10,
        token_sha256=b"x" * 32,
        ip_address="127.0.0.1",
        user_agent="pytest-agent",
        expires_at=datetime(2026, 10, 17, 21, 0, 0, tzinfo=UTC),
    )
    assert len(created_session_uuid) == 16

    active = await sessions_repo.get_active_session_with_user(
        conn,
        token_sha256=b"x" * 32,
        now_utc=datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC),
    )
    assert active is not None
    active_session, active_user = active
    assert active_session.id == 55
    assert active_session.uuid_str == uuid7_bytes_to_str(session_uuid)
    assert active_user.id == 10

    # Verify duplicate username raises UniqueConstraintViolationError
    dup_cursor = RecordingCursor(
        raise_on_execute=FakeMySQLError(
            1062, "Duplicate entry 'mrs_smith' for key 'users.username'"
        )
    )
    dup_conn = RecordingConnection(dup_cursor)
    with pytest.raises(UniqueConstraintViolationError):
        await users_repo.create_user(
            dup_conn,
            username="mrs_smith",
            display_name="Duplicate",
            password_hash="hash",
        )


@pytest.mark.asyncio
async def test_invite_codes_repository_for_update() -> None:
    """Verify InviteCodesRepository executes SELECT ... FOR UPDATE and increments uses."""
    invite_uuid = generate_uuid7_bytes()
    now_naive = datetime(2026, 10, 17, 9, 0, 0)
    expires_naive = datetime(2026, 10, 24, 9, 0, 0)
    invite_tuple = (
        3,
        invite_uuid,
        "FULWOOD-MATHS-2026",
        10,
        2,
        expires_naive,
        1,
        1,
        now_naive,
    )
    cursor = RecordingCursor(
        lastrowid=3,
        fetchone_queue=[invite_tuple, invite_tuple],
        rowcount_queue=[1, 1, 1],
    )
    conn = RecordingConnection(cursor)
    repo = InviteCodesRepository()

    created_dto = await repo.create_invite_code(
        conn,
        code="fulwood-maths-2026",
        max_uses=10,
        expires_at=datetime(2026, 10, 24, 9, 0, 0, tzinfo=UTC),
        created_by_user_id=1,
        uuid_bytes=invite_uuid,
    )
    assert created_dto.uuid == uuid7_bytes_to_str(invite_uuid)
    assert created_dto.code == "FULWOOD-MATHS-2026"

    locked = await repo.get_active_for_update(
        conn,
        code="FULWOOD-MATHS-2026",
        now_utc=datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC),
    )
    assert locked is not None
    assert locked.id == 3
    assert any("FOR UPDATE" in q for q, _ in cursor.executed)

    await repo.increment_uses(conn, locked.id)
    assert any("SET uses_count = uses_count + 1" in q for q, _ in cursor.executed)


@pytest.mark.asyncio
async def test_generations_and_artifacts_repositories_with_keyset_pagination() -> None:
    """Verify GenerationsRepository and ArtifactsRepository CRUD and keyset pagination."""
    gen_uuid_1 = generate_uuid7_bytes()
    gen_uuid_2 = generate_uuid7_bytes()
    art_uuid = generate_uuid7_bytes()
    t1 = datetime(2026, 10, 17, 9, 2, 0)
    t2 = datetime(2026, 10, 17, 9, 1, 0)

    gen_row_1 = (
        102,
        gen_uuid_1,
        10,
        b"h" * 32,
        "Biology",
        "Year 10",
        "Higher",
        "AQA",
        "Photosynthesis",
        "powerpoint",
        "Photosynthesis Lesson",
        '{"slides":[]}',
        8,
        0,
        t1,
        t1,
    )
    gen_row_2 = (
        101,
        gen_uuid_2,
        10,
        b"g" * 32,
        "Biology",
        "Year 10",
        "Higher",
        "AQA",
        "Mitosis",
        "worksheet",
        "Mitosis Worksheet",
        '{"questions":[]}',
        0,
        0,
        t2,
        t2,
    )
    art_row = (
        201,
        art_uuid,
        102,
        10,
        "pptx",
        "10/102/photosynthesis.pptx",
        b"a" * 32,
        4096,
        t1,
    )

    cursor = RecordingCursor(
        lastrowid=102,
        fetchone_queue=[
            gen_row_1,  # create_generation -> select by uuid
            None,  # upsert_artifact -> get_by_generation_and_format (none yet)
            art_row,  # upsert_artifact -> select after insert
        ],
        fetchall_queue=[
            [gen_row_1, gen_row_2],  # limit=1 returns 2 rows to trigger next_cursor
        ],
    )
    conn = RecordingConnection(cursor)
    gen_repo = GenerationsRepository()
    art_repo = ArtifactsRepository()

    created_gen = await gen_repo.create_generation(
        conn,
        user_id=10,
        request_hash=b"h" * 32,
        subject="Biology",
        year_group="Year 10",
        tier="Higher",
        exam_board="AQA",
        topic="Photosynthesis",
        task_type="powerpoint",
        title="Photosynthesis Lesson",
        content_json='{"slides":[]}',
        slide_count=8,
        uuid_bytes=gen_uuid_1,
    )
    assert created_gen.id == 102
    assert created_gen.uuid_str == uuid7_bytes_to_str(gen_uuid_1)

    page = await gen_repo.list_for_user_keyset(
        conn,
        user_id=10,
        cursor=KeysetCursor(
            created_at=ensure_utc_datetime(t1) + timedelta(minutes=1), id=999
        ),
        limit=1,
    )
    assert len(page.items) == 1
    assert page.next_cursor is not None
    assert any("(created_at, id) < (%s, %s)" in q for q, _ in cursor.executed)

    created_art = await art_repo.upsert_artifact(
        conn,
        generation_id=102,
        user_id=10,
        fmt="pptx",
        file_path="10/102/photosynthesis.pptx",
        sha256_checksum=b"a" * 32,
        byte_size=4096,
    )
    assert created_art.id == 201
    assert created_art.uuid_str == uuid7_bytes_to_str(art_uuid)


@pytest.mark.asyncio
async def test_idempotency_and_telemetry_repositories() -> None:
    """Verify IdempotencyRepository SELECT ... FOR UPDATE locking and TelemetryRepository DECIMAL(19,4)."""
    ledger_uuid = generate_uuid7_bytes()
    audit_uuid = generate_uuid7_bytes()
    now_utc = datetime(2026, 10, 17, 9, 0, 0, tzinfo=UTC)
    future_naive = datetime(2026, 10, 18, 9, 0, 0)
    now_naive = datetime(2026, 10, 17, 9, 0, 0)

    idem_tuple = (
        77,
        10,
        "idem-key-001",
        b"r" * 32,
        "IN_PROGRESS",
        None,
        None,
        future_naive,
        now_naive,
    )

    overview_tuple = (
        12,  # total_users
        10,  # active_users
        2,  # pending_users
        5,  # active_sessions
        40,  # generations_24h
        50,  # ledger_calls_24h
        15,  # cache_hits_24h
        120_000,  # total_tokens_24h
        Decimal("0.4500"),  # total_cost_usd_24h
        Decimal("12.3450"),  # cumulative_cost_usd
    )

    cursor = RecordingCursor(
        lastrowid=77,
        fetchone_queue=[
            None,  # first lock_or_create_key -> SELECT FOR UPDATE returns None
            idem_tuple,  # first lock_or_create_key -> SELECT FOR UPDATE after INSERT
            idem_tuple,  # second lock_or_create_key -> existing row
            overview_tuple,  # get_admin_overview_metrics
        ],
    )
    conn = RecordingConnection(cursor)
    idem_repo = IdempotencyRepository()
    telemetry_repo = TelemetryRepository()

    first_record = await idem_repo.lock_or_create_key(
        conn,
        user_id=10,
        idempotency_key="idem-key-001",
        request_hash=b"r" * 32,
        expires_at=now_utc + timedelta(hours=24),
    )
    assert first_record.is_newly_created is True
    assert first_record.id == 77
    assert any("FOR UPDATE" in q for q, _ in cursor.executed)

    replay_record = await idem_repo.lock_or_create_key(
        conn,
        user_id=10,
        idempotency_key="idem-key-001",
        request_hash=b"r" * 32,
        expires_at=now_utc + timedelta(hours=24),
    )
    assert replay_record.is_newly_created is False
    assert replay_record.id == 77

    await idem_repo.complete_key(
        conn,
        record_id=77,
        response_status=200,
        response_body_json='{"status":"ok"}',
    )

    inserted_ledger_uuid = await telemetry_repo.insert_token_ledger(
        conn,
        user_id=10,
        generation_id=102,
        model_name="gemini-2.5-flash",
        prompt_tokens=1000,
        candidate_tokens=500,
        total_tokens=1500,
        estimated_cost_usd=Decimal("0.01256"),
        latency_ms=850,
        cache_hit=False,
        status="SUCCESS",
        uuid_bytes=ledger_uuid,
    )
    assert inserted_ledger_uuid == ledger_uuid

    inserted_audit_uuid = await telemetry_repo.insert_audit_event(
        conn,
        actor_user_id=10,
        target_user_id=None,
        event_type="LOGIN_SUCCESS",
        ip_address="127.0.0.1",
        metadata={"username": "mrs_smith"},
        uuid_bytes=audit_uuid,
    )
    assert inserted_audit_uuid == audit_uuid

    overview = await telemetry_repo.get_admin_overview_metrics(conn, now_utc=now_utc)
    assert overview.total_users == 12
    assert overview.cache_hit_rate_24h == Decimal("0.3000")
    assert overview.total_cost_usd_24h == Decimal("0.4500")
    assert overview.cumulative_cost_usd == Decimal("12.3450")
