"""Wave 4 Verification Suite: RFC 9457 Runtime, Controllers & Docker Compose MySQL 8.4 Stack.

Covers all Wave 4 verification requirements:
1. Unidirectional Import Audit across `backend/app/api/**` and `backend/app/main.py`
2. Strict CSP Header (`REQ-SEC-01`), `X-Request-ID` Propagation & Structured JSON Logging (`REQ-LOG-01`)
3. 100% IETF RFC 9457 `application/problem+json` Error Responses (`401`, `403`, `404`, `405`, `413`, `422`, `429`, `500`, `503`)
4. Native Auth HTTP Lifecycle (`/api/auth/register`, `/api/auth/login`, `/api/auth/status`, `/api/auth/change-password`, `/api/auth/logout`)
5. Generation, Idempotency-Key Replay, Saved Lesson Library CRUD, IDOR `403` & Export (`/api/generate`, `/api/lessons/*`, `/api/export/{fmt}`)
6. Admin Control Center HTTP Lifecycle (`/api/admin/overview`, `/users`, `/reset-password`, `/revoke-sessions`, `/invite-codes`, `/settings`, `/audit-events`, `/token-ledger`)
7. Liveness & Readiness Probes (`/health/live`, `/health/ready`) and SIGTERM Draining (`503 application/problem+json`)
8. Docker Compose MySQL 8.4 Stack & `deploy/` OAuth2-Proxy Elimination Invariants
"""

from __future__ import annotations

import ast
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import MagicMock

import yaml
from app.api.deps import RuntimeContainer
from app.core.config import AppConfig
from app.core.hardening import STRICT_CSP
from app.core.limits import user_export_limiter, user_generation_limiter
from app.core.logging import StructuredJsonFormatter
from app.core.uuid_v7 import (
    generate_uuid7_bytes,
    generate_uuid7_str,
    uuid7_bytes_to_str,
)
from app.db.errors import UniqueConstraintViolationError
from app.db.pool import AsyncConnectionProtocol, AsyncDatabasePool
from app.main import app
from app.repositories.artifacts_repo import ArtifactRow, ArtifactsRepository
from app.repositories.base import (
    KeysetCursor,
    KeysetPage,
    encode_keyset_cursor,
)
from app.repositories.generations_repo import GenerationRow, GenerationsRepository
from app.repositories.idempotency_repo import IdempotencyRecord, IdempotencyRepository
from app.repositories.invite_codes_repo import InviteCodeRow, InviteCodesRepository
from app.repositories.sessions_repo import SessionRow, SessionsRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.repositories.users_repo import UserRow, UsersRepository
from app.schemas.admin import (
    AdminOverviewMetricsDTO,
    AdminUserUpdateRequest,
    AuditEventDTO,
    InviteCodeDTO,
    SystemSettingsDTO,
    SystemSettingsUpdateRequest,
    TokenLedgerEntryDTO,
)
from app.schemas.auth import UserRole, UserStatus
from app.schemas.content import (
    ALL_SUBJECTS,
    AnswerSection,
    CoachPoint,
    DinQuestion,
    DinTask,
    ExamQuestion,
    Feedback,
    GeneratedContent,
    GenerateRequest,
    Keyword,
    Lesson,
    Misconception,
    ModelStep,
    Overview,
    RevisionResource,
    Spelling,
    Task,
    TaskTypeLiteral,
    YouDo,
)
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService
from app.services.gemini import GenerationResult as GeminiGenerationResult
from app.services.lesson_service import LessonService
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
APP_DIR = BACKEND_ROOT / "app"
API_DIR = APP_DIR / "api"
MAIN_FILE = APP_DIR / "main.py"


def make_test_config(*, cookie_secure: bool = True) -> AppConfig:
    """Create a deterministic AppConfig for HTTP integration testing."""
    return AppConfig(
        app_env="development",
        gemini_api_key="test-gemini-api-key-wave4",
        gemini_model="gemini-2.5-flash",
        mysql_host="127.0.0.1",
        mysql_port=3306,
        mysql_user="fulwood_app",
        mysql_password="fulwood-password-12345",
        mysql_database="fulwood_forge",
        mysql_pool_min=1,
        mysql_pool_max=5,
        session_cookie_name="ff_session",
        session_cookie_secure=cookie_secure,
        session_ttl_hours=24,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="BootstrapAdminPassword!2026",
        artifacts_dir=Path("/tmp/fulwood-artifacts-wave4"),
    )


def make_sample_generated_content(
    *,
    title: str = "Quadratic Equations and Graphs",
    task_type: TaskTypeLiteral = "worksheet",
    subject: str = "Maths",
) -> GeneratedContent:
    """Build a valid GeneratedContent payload for tests."""
    return GeneratedContent(
        title=title,
        task_type=task_type,
        subject=subject,
        year_group="10",
        exam_board="Edexcel",
        spec_reference="A18: Solve quadratic equations algebraically",
        audience_note="Year 10 · Higher · Edexcel",
        difficulty_note="Pitched for Higher tier Year 10 students.",
        overview=Overview(
            summary="Solve quadratic equations using $x^2 + bx + c = 0$.",
            objectives=[
                "Factorise expressions of the form $x^2 + bx + c$",
                "Apply the quadratic formula accurately",
            ],
            assessment_focus="Method marks for clear algebraic substitution.",
        ),
        lessons=[
            Lesson(
                title="Lesson 1: Factorising and Formula",
                lesson_focus="Connecting roots to parabola intercepts.",
                objectives=[
                    "Identify coefficients $a$, $b$, and $c$",
                    "Check solutions by substitution",
                ],
                din=DinTask(
                    instruction="Complete quietly in your exercise book.",
                    recall_focus="Expanding brackets $(x + 3)(x - 2)$",
                    questions=[
                        DinQuestion(
                            number=1,
                            type="short",
                            prompt_md="Expand $(x + 4)(x - 1)$.",
                            answer_md="$x^2 + 3x - 4$",
                        )
                    ],
                ),
                key_spellings=[
                    Spelling(
                        word="quadratic",
                        phonetic="kwod-RAT-ik",
                        meaning="A polynomial of degree two.",
                        dont_confuse="quartic",
                    )
                ],
                keyword_bank=[
                    Keyword(
                        term="Root",
                        definition="A value of $x$ for which $f(x) = 0$.",
                        tier=3,
                    )
                ],
                i_do=[
                    ModelStep(
                        label="Step 1 — Rearrange to standard form",
                        body_md="Ensure the equation equals zero: $x^2 - 5x + 6 = 0$.",
                        examiner_tip="Set the right-hand side to $0$ before factorising.",
                    )
                ],
                misconceptions=[
                    Misconception(
                        misconception="Keeping the bracket sign as the root.",
                        correction="Solve $x - 3 = 0$ to obtain $x = 3$.",
                        exam_reference="Edexcel Examiner Report Q14",
                    )
                ],
                we_do=[
                    Task(
                        prompt_md="Solve $x^2 + 7x + 12 = 0$ by factorising.",
                        scaffold="Find two numbers that multiply to $12$ and add to $7$.",
                    )
                ],
                you_do=YouDo(
                    success_criteria=["Rearrange first", "Write both roots clearly"],
                    tasks=[
                        Task(
                            prompt_md="BRONZE: Solve $x^2 + 5x + 6 = 0$.",
                            scaffold="Factor pairs of $6$: $(1, 6)$ and $(2, 3)$.",
                        )
                    ],
                ),
                exam_language_coach=[
                    CoachPoint(
                        point="State exact solutions when asked for roots.",
                        allow="$x = 2, x = 3$",
                        do_not_accept="Leaving the expression factorised without solving.",
                    )
                ],
                exam_questions=[
                    ExamQuestion(
                        prompt_md="Solve $2x^2 + 5x - 3 = 0$.",
                        marks=3,
                        answer_md="**M1** $(2x - 1)(x + 3) = 0$ **A1** $x = 0.5, -3$",
                    )
                ],
                exit_ticket=["Solve $x^2 - 4x + 3 = 0$."],
                exit_ticket_answers=["$x = 1$ or $x = 3$"],
                feedback=Feedback(
                    even_better_if="Check signs when expanding negative brackets.",
                    improvement_prompt="Substitute your roots back into the original equation.",
                ),
                revision_resources=[
                    RevisionResource(
                        name="CorbettMaths Quadratics",
                        url="https://corbettmaths.com",
                        purpose="Video walkthroughs and practice questions.",
                    )
                ],
            )
        ],
        teacher_answer_key=[
            AnswerSection(
                heading="Lesson 1 — Worked Solutions",
                markdown_body="1. **WE DO:** $x = -3$ or $x = -4$.",
            )
        ],
    )


# ============================================================================
# IN-MEMORY STATEFUL REPOSITORY & CONTAINER HARNESS FOR HTTP INTEGRATION TESTS
# ============================================================================


@dataclass
class InMemoryStore:
    """Shared relational state for fast, isolated HTTP integration tests."""

    settings: SystemSettingsDTO
    users: dict[int, UserRow]
    sessions: dict[int, SessionRow]
    invites: dict[int, InviteCodeRow]
    generations: dict[int, GenerationRow]
    artifacts: dict[tuple[int, str], ArtifactRow]
    idempotency: dict[tuple[int, str], IdempotencyRecord]
    audit_events: list[tuple[int, int | None, int | None, AuditEventDTO]]
    token_ledger: list[tuple[int, int, TokenLedgerEntryDTO]]
    next_id: int = 1
    db_healthy: bool = True

    def allocate_id(self) -> int:
        val = self.next_id
        self.next_id += 1
        return val


class FakeDatabasePool:
    """In-memory DatabaseLifecycleProtocol + AsyncDatabasePool replacement."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self._conn = MagicMock(spec=AsyncConnectionProtocol)
        self.closed = False

    async def ping(self) -> bool:
        return self._store.db_healthy and not self.closed

    async def close(self) -> None:
        self.closed = True

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnectionProtocol]:
        yield self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnectionProtocol]:
        yield self._conn


def _paginate_rows(
    rows: list[Any],
    cursor: KeysetCursor | None,
    limit: int,
    get_ts_id: Any,
) -> tuple[list[Any], str | None]:
    ordered = sorted(rows, key=lambda r: get_ts_id(r), reverse=True)
    if cursor is not None:
        c_ts = cursor.created_at
        c_id = cursor.id
        ordered = [
            r
            for r in ordered
            if (get_ts_id(r)[0] < c_ts)
            or (get_ts_id(r)[0] == c_ts and get_ts_id(r)[1] < c_id)
        ]
    sliced = ordered[: limit + 1]
    has_more = len(sliced) > limit
    items = sliced[:limit]
    next_cursor: str | None = None
    if has_more and items:
        last_ts, last_id = get_ts_id(items[-1])
        next_cursor = encode_keyset_cursor(
            KeysetCursor(created_at=last_ts, id=int(last_id))
        )
    return items, next_cursor


class InMemorySettingsRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def get_or_init_settings(
        self, _conn: AsyncConnectionProtocol
    ) -> SystemSettingsDTO:
        return self._store.settings

    async def update_settings(
        self,
        _conn: AsyncConnectionProtocol,
        req: SystemSettingsUpdateRequest,
        updated_by_user_id: int | None = None,
    ) -> SystemSettingsDTO:
        _ = updated_by_user_id
        self._store.settings = SystemSettingsDTO(
            registration_mode=req.registration_mode,
            default_hourly_quota=req.default_hourly_quota,
            maintenance_mode=req.maintenance_mode,
            allowed_subjects=list(req.allowed_subjects),
            allowed_task_types=list(req.allowed_task_types),
            updated_at=datetime.now(UTC),
        )
        return self._store.settings


class InMemoryUsersRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def count_admins(self, _conn: AsyncConnectionProtocol) -> int:
        return sum(
            1
            for u in self._store.users.values()
            if u.role == "ADMIN" and u.status == "ACTIVE"
        )

    async def get_by_username(
        self, _conn: AsyncConnectionProtocol, username: str
    ) -> UserRow | None:
        norm = username.strip().lower()
        for u in self._store.users.values():
            if u.username == norm:
                return u
        return None

    async def get_by_id(
        self, _conn: AsyncConnectionProtocol, user_id: int
    ) -> UserRow | None:
        return self._store.users.get(user_id)

    async def get_by_uuid(
        self, _conn: AsyncConnectionProtocol, uuid_v7: bytes
    ) -> UserRow | None:
        for u in self._store.users.values():
            if u.uuid_v7 == uuid_v7:
                return u
        return None

    async def create_user(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: UserRole = "USER",
        status: UserStatus = "ACTIVE",
        must_change_password: bool = False,
        hourly_generation_limit: int = 30,
        uuid_v7: bytes | None = None,
    ) -> UserRow:
        norm = username.strip().lower()
        for existing in self._store.users.values():
            if existing.username == norm:
                raise UniqueConstraintViolationError(f"Duplicate username {norm}")
        now = datetime.now(UTC)
        uid = self._store.allocate_id()
        row = UserRow(
            id=uid,
            uuid_v7=uuid_v7 or generate_uuid7_bytes(),
            username=norm,
            display_name=display_name or username,
            password_hash=password_hash,
            role=role,
            status=status,
            must_change_password=must_change_password,
            hourly_generation_limit=hourly_generation_limit,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        self._store.users[uid] = row
        return row

    async def touch_last_login(
        self,
        _conn: AsyncConnectionProtocol,
        user_id: int,
        login_time_utc: datetime | None = None,
    ) -> None:
        row = self._store.users.get(user_id)
        if row is not None:
            now = login_time_utc or datetime.now(UTC)
            self._store.users[user_id] = replace(row, last_login_at=now, updated_at=now)

    async def update_password(
        self,
        _conn: AsyncConnectionProtocol,
        user_id: int,
        new_password_hash: str,
    ) -> None:
        row = self._store.users.get(user_id)
        if row is not None:
            self._store.users[user_id] = replace(
                row,
                password_hash=new_password_hash,
                must_change_password=False,
                updated_at=datetime.now(UTC),
            )

    async def set_temporary_password(
        self,
        _conn: AsyncConnectionProtocol,
        user_id: int,
        temp_password_hash: str,
    ) -> None:
        row = self._store.users.get(user_id)
        if row is not None:
            self._store.users[user_id] = replace(
                row,
                password_hash=temp_password_hash,
                must_change_password=True,
                updated_at=datetime.now(UTC),
            )

    async def update_user_admin(
        self,
        _conn: AsyncConnectionProtocol,
        user_id: int,
        req: AdminUserUpdateRequest,
    ) -> UserRow | None:
        row = self._store.users.get(user_id)
        if row is None:
            return None
        updated = replace(
            row,
            role=req.role if req.role is not None else row.role,
            status=req.status if req.status is not None else row.status,
            hourly_generation_limit=(
                req.hourly_generation_limit
                if req.hourly_generation_limit is not None
                else row.hourly_generation_limit
            ),
            updated_at=datetime.now(UTC),
        )
        self._store.users[user_id] = updated
        return updated

    async def list_users_keyset(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
        status: UserStatus | None = None,
    ) -> KeysetPage[UserRow]:
        rows = [
            u
            for u in self._store.users.values()
            if status is None or u.status == status
        ]
        items, next_cursor = _paginate_rows(
            rows, cursor, limit, lambda r: (r.created_at, r.id)
        )
        return KeysetPage(items=items, next_cursor=next_cursor)


class InMemorySessionsRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def create_session(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        token_sha256: bytes,
        ip_address: str,
        user_agent: str,
        expires_at: datetime,
        uuid_v7: bytes | None = None,
    ) -> SessionRow:
        sid = self._store.allocate_id()
        row = SessionRow(
            id=sid,
            uuid_v7=uuid_v7 or generate_uuid7_bytes(),
            user_id=user_id,
            token_sha256=token_sha256,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
            revoked_at=None,
            created_at=datetime.now(UTC),
        )
        self._store.sessions[sid] = row
        return row

    async def get_active_session_with_user(
        self,
        _conn: AsyncConnectionProtocol,
        token_sha256: bytes,
        now_utc: datetime | None = None,
    ) -> tuple[SessionRow, UserRow] | None:
        now = now_utc or datetime.now(UTC)
        for s in self._store.sessions.values():
            if (
                s.token_sha256 == token_sha256
                and s.revoked_at is None
                and s.expires_at > now
            ):
                user = self._store.users.get(s.user_id)
                if user is not None:
                    return s, user
        return None

    async def extend_session(
        self,
        _conn: AsyncConnectionProtocol,
        session_id: int,
        new_expires_at: datetime,
    ) -> None:
        s = self._store.sessions.get(session_id)
        if s is not None:
            self._store.sessions[session_id] = replace(s, expires_at=new_expires_at)

    async def revoke_session_by_token_hash(
        self,
        _conn: AsyncConnectionProtocol,
        token_sha256: bytes,
        revoked_at_utc: datetime | None = None,
    ) -> bool:
        now = revoked_at_utc or datetime.now(UTC)
        for sid, s in list(self._store.sessions.items()):
            if s.token_sha256 == token_sha256 and s.revoked_at is None:
                self._store.sessions[sid] = replace(s, revoked_at=now)
                return True
        return False

    async def revoke_all_for_user(
        self,
        _conn: AsyncConnectionProtocol,
        user_id: int,
        revoked_at_utc: datetime | None = None,
    ) -> int:
        now = revoked_at_utc or datetime.now(UTC)
        count = 0
        for sid, s in list(self._store.sessions.items()):
            if s.user_id == user_id and s.revoked_at is None:
                self._store.sessions[sid] = replace(s, revoked_at=now)
                count += 1
        return count


class InMemoryInviteCodesRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def create_invite_code(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        code: str,
        max_uses: int,
        expires_at: datetime | None,
        created_by_user_id: int,
        uuid_v7: bytes | None = None,
    ) -> InviteCodeDTO:
        norm = code.strip().upper()
        for existing in self._store.invites.values():
            if existing.code == norm:
                raise UniqueConstraintViolationError(f"Duplicate invite code {norm}")
        iid = self._store.allocate_id()
        row = InviteCodeRow(
            id=iid,
            uuid_v7=uuid_v7 or generate_uuid7_bytes(),
            code=norm,
            max_uses=max_uses,
            uses_count=0,
            expires_at=expires_at,
            created_by_user_id=created_by_user_id,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        self._store.invites[iid] = row
        return row.to_dto()

    async def get_active_for_update(
        self,
        _conn: AsyncConnectionProtocol,
        code: str,
        now_utc: datetime | None = None,
    ) -> InviteCodeRow | None:
        now = now_utc or datetime.now(UTC)
        norm = code.strip().upper()
        for row in self._store.invites.values():
            if (
                row.code == norm
                and row.is_active
                and row.uses_count < row.max_uses
                and (row.expires_at is None or row.expires_at > now)
            ):
                return row
        return None

    async def increment_uses(
        self, _conn: AsyncConnectionProtocol, invite_id: int
    ) -> None:
        row = self._store.invites.get(invite_id)
        if row is not None:
            self._store.invites[invite_id] = replace(row, uses_count=row.uses_count + 1)

    async def deactivate_by_uuid(
        self, _conn: AsyncConnectionProtocol, uuid_v7: bytes
    ) -> bool:
        for iid, row in list(self._store.invites.items()):
            if row.uuid_v7 == uuid_v7 and row.is_active:
                self._store.invites[iid] = replace(row, is_active=False)
                return True
        return False

    async def list_invite_codes_keyset(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
    ) -> KeysetPage[InviteCodeDTO]:
        rows = list(self._store.invites.values())
        items, next_cursor = _paginate_rows(
            rows, cursor, limit, lambda r: (r.created_at, r.id)
        )
        return KeysetPage(
            items=[r.to_dto() for r in items],
            next_cursor=next_cursor,
        )


class InMemoryGenerationsRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def find_recent_by_hash(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        request_hash: bytes,
        since_utc: datetime,
    ) -> GenerationRow | None:
        candidates = [
            g
            for g in self._store.generations.values()
            if g.user_id == user_id
            and g.request_hash == request_hash
            and not g.is_archived
            and g.created_at >= since_utc
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda g: (g.created_at, g.id), reverse=True)
        return candidates[0]

    async def count_user_generations_since(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        since_utc: datetime,
    ) -> int:
        return sum(
            1
            for g in self._store.generations.values()
            if g.user_id == user_id and g.created_at >= since_utc
        )

    async def create_generation(
        self,
        _conn: AsyncConnectionProtocol,
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
        slide_count: int,
        uuid_v7: bytes | None = None,
    ) -> GenerationRow:
        gid = self._store.allocate_id()
        now = datetime.now(UTC)
        row = GenerationRow(
            id=gid,
            uuid_v7=uuid_v7 or generate_uuid7_bytes(),
            user_id=user_id,
            request_hash=request_hash,
            subject=subject,
            year_group=year_group,
            tier=tier,
            exam_board=exam_board,
            topic=topic,
            task_type=task_type,
            title=title,
            content_json=content_json,
            slide_count=slide_count,
            is_archived=False,
            created_at=now,
            updated_at=now,
        )
        self._store.generations[gid] = row
        return row

    async def get_by_uuid(
        self, _conn: AsyncConnectionProtocol, uuid_v7: bytes
    ) -> GenerationRow | None:
        for g in self._store.generations.values():
            if g.uuid_v7 == uuid_v7:
                return g
        return None

    async def list_for_user_keyset(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        cursor: KeysetCursor | None = None,
        limit: int = 25,
        subject: str | None = None,
        task_type: str | None = None,
    ) -> KeysetPage[GenerationRow]:
        rows = [
            g
            for g in self._store.generations.values()
            if g.user_id == user_id
            and not g.is_archived
            and (subject is None or g.subject == subject)
            and (task_type is None or g.task_type == task_type)
        ]
        items, next_cursor = _paginate_rows(
            rows, cursor, limit, lambda r: (r.created_at, r.id)
        )
        return KeysetPage(items=items, next_cursor=next_cursor)

    async def update_content(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        generation_id: int,
        user_id: int,
        title: str,
        content_json: str,
        slide_count: int,
    ) -> bool:
        row = self._store.generations.get(generation_id)
        if row is None or row.user_id != user_id:
            return False
        self._store.generations[generation_id] = replace(
            row,
            title=title,
            content_json=content_json,
            slide_count=slide_count,
            updated_at=datetime.now(UTC),
        )
        return True

    async def archive_generation(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        generation_id: int,
        user_id: int,
    ) -> bool:
        row = self._store.generations.get(generation_id)
        if row is None or row.user_id != user_id or row.is_archived:
            return False
        self._store.generations[generation_id] = replace(
            row,
            is_archived=True,
            updated_at=datetime.now(UTC),
        )
        return True


class InMemoryArtifactsRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def get_by_generation_and_format(
        self,
        _conn: AsyncConnectionProtocol,
        generation_id: int,
        fmt: str,
    ) -> ArtifactRow | None:
        return self._store.artifacts.get((generation_id, fmt))

    async def upsert_artifact(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        generation_id: int,
        user_id: int,
        fmt: str,
        file_path: str | None,
        sha256_checksum: bytes,
        byte_size: int,
        uuid_v7: bytes | None = None,
    ) -> ArtifactRow:
        existing = self._store.artifacts.get((generation_id, fmt))
        aid = existing.id if existing is not None else self._store.allocate_id()
        row = ArtifactRow(
            id=aid,
            uuid_v7=existing.uuid_v7
            if existing is not None
            else (uuid_v7 or generate_uuid7_bytes()),
            generation_id=generation_id,
            user_id=user_id,
            format=cast(
                Literal["docx", "pptx", "pdf", "answers-docx", "answers-pdf"], fmt
            ),
            file_path=file_path,
            sha256_checksum=sha256_checksum,
            byte_size=byte_size,
            created_at=datetime.now(UTC),
        )
        self._store.artifacts[(generation_id, fmt)] = row
        return row


class InMemoryIdempotencyRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def lock_or_create_key(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        idempotency_key: str,
        request_hash: bytes,
        expires_at: datetime,
        now_utc: datetime | None = None,
    ) -> IdempotencyRecord:
        now = now_utc or datetime.now(UTC)
        existing = self._store.idempotency.get((user_id, idempotency_key))
        if existing is not None and existing.expires_at > now:
            return replace(existing, is_newly_created=False)
        rid = self._store.allocate_id()
        rec = IdempotencyRecord(
            id=rid,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="IN_PROGRESS",
            response_status=None,
            response_body_json=None,
            expires_at=expires_at,
            created_at=now,
            is_newly_created=True,
        )
        self._store.idempotency[(user_id, idempotency_key)] = rec
        return rec

    async def complete_key(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        record_id: int,
        response_status: int,
        response_body_json: str,
    ) -> None:
        for k, rec in list(self._store.idempotency.items()):
            if rec.id == record_id:
                self._store.idempotency[k] = replace(
                    rec,
                    status="COMPLETED",
                    response_status=response_status,
                    response_body_json=response_body_json,
                )

    async def fail_key(self, _conn: AsyncConnectionProtocol, record_id: int) -> None:
        for k, rec in list(self._store.idempotency.items()):
            if rec.id == record_id:
                self._store.idempotency[k] = replace(rec, status="FAILED")

    async def delete_expired(
        self, _conn: AsyncConnectionProtocol, now_utc: datetime
    ) -> int:
        deleted = 0
        for k, rec in list(self._store.idempotency.items()):
            if rec.expires_at <= now_utc:
                self._store.idempotency.pop(k, None)
                deleted += 1
        return deleted


class InMemoryTelemetryRepo:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    async def insert_audit_event(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        actor_user_id: int | None,
        target_user_id: int | None,
        event_type: str,
        ip_address: str,
        metadata: dict[str, Any],
        uuid_v7: bytes | None = None,
    ) -> AuditEventDTO:
        eid = self._store.allocate_id()
        actor = (
            self._store.users.get(actor_user_id) if actor_user_id is not None else None
        )
        target = (
            self._store.users.get(target_user_id)
            if target_user_id is not None
            else None
        )
        dto = AuditEventDTO(
            uuid=uuid7_bytes_to_str(uuid_v7 or generate_uuid7_bytes()),
            actor_username=actor.username if actor else None,
            target_username=target.username if target else None,
            event_type=event_type,
            ip_address=ip_address,
            metadata=dict(metadata),
            created_at=datetime.now(UTC),
        )
        self._store.audit_events.append((eid, actor_user_id, target_user_id, dto))
        return dto

    async def count_failed_logins_since(
        self,
        _conn: AsyncConnectionProtocol,
        username: str,
        ip_address: str,
        since_utc: datetime,
    ) -> int:
        norm = username.strip().lower()
        return sum(
            1
            for _, _, _, ev in self._store.audit_events
            if ev.event_type == "AUTH_LOGIN_FAILED"
            and ev.ip_address == ip_address
            and ev.created_at >= since_utc
            and ev.metadata.get("username") == norm
        )

    async def insert_token_ledger(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        generation_id: int | None,
        model_name: str,
        prompt_tokens: int,
        candidate_tokens: int,
        total_tokens: int,
        estimated_cost_usd: Decimal,
        latency_ms: int,
        cache_hit: bool,
        status: Literal["SUCCESS", "CACHE_HIT", "UPSTREAM_ERROR"],
        uuid_v7: bytes | None = None,
    ) -> TokenLedgerEntryDTO:
        lid = self._store.allocate_id()
        user = self._store.users[user_id]
        gen = (
            self._store.generations.get(generation_id)
            if generation_id is not None
            else None
        )
        dto = TokenLedgerEntryDTO(
            uuid=uuid7_bytes_to_str(uuid_v7 or generate_uuid7_bytes()),
            user_uuid=user.uuid_str,
            username=user.username,
            generation_uuid=gen.uuid_str if gen else None,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            candidate_tokens=candidate_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            status=status,
            created_at=datetime.now(UTC),
        )
        self._store.token_ledger.append((lid, user_id, dto))
        return dto

    async def get_admin_overview_metrics(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        now_utc: datetime | None = None,
    ) -> AdminOverviewMetricsDTO:
        now = now_utc or datetime.now(UTC)
        total_users = len(self._store.users)
        active_users = sum(
            1 for u in self._store.users.values() if u.status == "ACTIVE"
        )
        pending_users = sum(
            1 for u in self._store.users.values() if u.status == "PENDING"
        )
        active_sessions = sum(
            1
            for s in self._store.sessions.values()
            if s.revoked_at is None and s.expires_at > now
        )
        total_entries = len(self._store.token_ledger)
        cache_hits = sum(1 for _, _, e in self._store.token_ledger if e.cache_hit)
        hit_rate = (
            (Decimal(cache_hits) / Decimal(total_entries)).quantize(Decimal("0.0001"))
            if total_entries > 0
            else Decimal("0.0000")
        )
        total_tokens = sum(e.total_tokens for _, _, e in self._store.token_ledger)
        total_cost = sum(
            (e.estimated_cost_usd for _, _, e in self._store.token_ledger),
            Decimal("0.0000"),
        )
        return AdminOverviewMetricsDTO(
            total_users=total_users,
            active_users=active_users,
            pending_users=pending_users,
            active_sessions=active_sessions,
            generations_24h=len(self._store.generations),
            cache_hit_rate_24h=hit_rate,
            total_tokens_24h=total_tokens,
            total_cost_usd_24h=total_cost,
            cumulative_cost_usd=total_cost,
        )

    async def list_audit_events_keyset(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
        event_type: str | None = None,
        actor_user_id: int | None = None,
    ) -> KeysetPage[AuditEventDTO]:
        filtered = [
            (eid, ev)
            for eid, a_id, _, ev in self._store.audit_events
            if (event_type is None or ev.event_type == event_type)
            and (actor_user_id is None or a_id == actor_user_id)
        ]
        items, next_cursor = _paginate_rows(
            filtered, cursor, limit, lambda pair: (pair[1].created_at, pair[0])
        )
        return KeysetPage(
            items=[pair[1] for pair in items],
            next_cursor=next_cursor,
        )

    async def list_token_ledger_keyset(
        self,
        _conn: AsyncConnectionProtocol,
        *,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
        user_id: int | None = None,
    ) -> KeysetPage[TokenLedgerEntryDTO]:
        filtered = [
            (lid, entry)
            for lid, u_id, entry in self._store.token_ledger
            if user_id is None or u_id == user_id
        ]
        items, next_cursor = _paginate_rows(
            filtered, cursor, limit, lambda pair: (pair[1].created_at, pair[0])
        )
        return KeysetPage(
            items=[pair[1] for pair in items],
            next_cursor=next_cursor,
        )


def build_in_memory_runtime(
    *,
    cookie_secure: bool = True,
    registration_mode: Literal["INVITE_ONLY", "ADMIN_APPROVAL", "OPEN"] = "INVITE_ONLY",
    generator_fn: Any = None,
    exporter_fn: Any = None,
) -> tuple[RuntimeContainer, InMemoryStore]:
    """Create a wired RuntimeContainer + InMemoryStore for HTTP tests."""
    user_export_limiter.reset()
    user_generation_limiter.reset()

    config = make_test_config(cookie_secure=cookie_secure)
    store = InMemoryStore(
        settings=SystemSettingsDTO(
            registration_mode=registration_mode,
            default_hourly_quota=30,
            maintenance_mode=False,
            allowed_subjects=list(ALL_SUBJECTS),
            allowed_task_types=["worksheet", "powerpoint", "booklet"],
            updated_at=datetime.now(UTC),
        ),
        users={},
        sessions={},
        invites={},
        generations={},
        artifacts={},
        idempotency={},
        audit_events=[],
        token_ledger=[],
    )
    fake_pool = FakeDatabasePool(store)
    pool_typed = cast(AsyncDatabasePool, fake_pool)

    settings_repo = cast(SystemSettingsRepository, InMemorySettingsRepo(store))
    users_repo = cast(UsersRepository, InMemoryUsersRepo(store))
    sessions_repo = cast(SessionsRepository, InMemorySessionsRepo(store))
    invite_repo = cast(InviteCodesRepository, InMemoryInviteCodesRepo(store))
    generations_repo = cast(GenerationsRepository, InMemoryGenerationsRepo(store))
    artifacts_repo = cast(ArtifactsRepository, InMemoryArtifactsRepo(store))
    idempotency_repo = cast(IdempotencyRepository, InMemoryIdempotencyRepo(store))
    telemetry_repo = cast(TelemetryRepository, InMemoryTelemetryRepo(store))

    async def _default_generator(req: GenerateRequest) -> GeminiGenerationResult:
        content = make_sample_generated_content(
            title=f"{req.subject}: {req.topic}",
            task_type=req.task_type,
            subject=req.subject,
        )
        return GeminiGenerationResult(
            content=content,
            model_name="gemini-2.5-flash",
            prompt_tokens=420,
            candidate_tokens=880,
            total_tokens=1300,
            latency_ms=115,
        )

    async def _default_exporter(_content: GeneratedContent, fmt: str) -> bytes:
        if fmt.endswith("pdf"):
            return b"%PDF-1.7-fake-compiled-pdf"
        return b"PK\x03\x04-fake-compiled-office-archive"

    auth_service = AuthService(
        pool=pool_typed,
        config=config,
        users_repo=users_repo,
        sessions_repo=sessions_repo,
        invite_repo=invite_repo,
        settings_repo=settings_repo,
        telemetry_repo=telemetry_repo,
    )
    admin_service = AdminService(
        pool=pool_typed,
        config=config,
        users_repo=users_repo,
        sessions_repo=sessions_repo,
        invite_repo=invite_repo,
        settings_repo=settings_repo,
        telemetry_repo=telemetry_repo,
    )
    lesson_service = LessonService(
        pool=pool_typed,
        config=config,
        generations_repo=generations_repo,
        artifacts_repo=artifacts_repo,
        idempotency_repo=idempotency_repo,
        settings_repo=settings_repo,
        telemetry_repo=telemetry_repo,
        generator_fn=generator_fn or _default_generator,
        exporter_fn=exporter_fn or _default_exporter,
    )
    container = RuntimeContainer(
        config=config,
        pool=fake_pool,
        auth_service=auth_service,
        admin_service=admin_service,
        lesson_service=lesson_service,
    )
    return container, store


# ============================================================================
# 1. UNIDIRECTIONAL IMPORT AUDIT (`backend/app/api/**` & `backend/app/main.py`)
# ============================================================================


def test_unidirectional_import_audit_controllers_and_main() -> None:
    """Verify controllers and main.py never import app.repositories, google.genai, docx, pptx, or sqlite3."""
    forbidden_prefixes = (
        "app.repositories",
        "google",
        "google.genai",
        "docx",
        "pptx",
        "sqlite3",
    )
    api_forbidden_prefixes = (*forbidden_prefixes, "app.db")

    violations: list[str] = []
    files_to_scan = [*sorted(API_DIR.rglob("*.py")), MAIN_FILE]

    for py_file in files_to_scan:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        rel = py_file.relative_to(BACKEND_ROOT)
        active_forbidden = (
            api_forbidden_prefixes if py_file != MAIN_FILE else forbidden_prefixes
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == mod or alias.name.startswith(f"{mod}.")
                        for mod in active_forbidden
                    ):
                        violations.append(f"{rel}:{node.lineno} imports {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and any(
                    node.module == mod or node.module.startswith(f"{mod}.")
                    for mod in active_forbidden
                )
            ):
                violations.append(f"{rel}:{node.lineno} imports from {node.module}")

    assert violations == [], f"Controller/Runtime import violations: {violations}"


# ============================================================================
# 2. STRICT CSP HEADER, X-REQUEST-ID PROPAGATION & STRUCTURED JSON LOGGING
# ============================================================================


def test_strict_csp_and_request_id_propagation_with_structured_json_logs() -> None:
    """Verify strict CSP header, UUIDv7 X-Request-ID propagation, and single-line http_request JSON logs."""
    container, _ = build_in_memory_runtime()
    app.state.container = container

    captured_records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    handler = _ListHandler()
    formatter = StructuredJsonFormatter()
    hardening_logger = logging.getLogger("app.core.hardening")
    hardening_logger.addHandler(handler)
    hardening_logger.setLevel(logging.INFO)

    try:
        with TestClient(app, base_url="https://testserver") as client:
            custom_req_id = generate_uuid7_str()
            resp = client.get(
                "/api/auth/status",
                headers={"X-Request-ID": custom_req_id},
            )
            assert resp.status_code == 200
            assert resp.headers.get("content-security-policy") == STRICT_CSP
            assert resp.headers.get("x-request-id") == custom_req_id
            assert resp.headers.get("x-content-type-options") == "nosniff"
            assert resp.headers.get("x-frame-options") == "DENY"

            # Invalid X-Request-ID (not UUIDv7) is replaced with a fresh UUIDv7
            resp_fresh = client.get(
                "/api/auth/status",
                headers={"X-Request-ID": "not-a-valid-uuidv7"},
            )
            assert resp_fresh.status_code == 200
            generated_id = resp_fresh.headers.get("x-request-id", "")
            assert generated_id != "not-a-valid-uuidv7"
            assert len(generated_id) == 36
    finally:
        hardening_logger.removeHandler(handler)

    http_logs = [
        json.loads(formatter.format(r))
        for r in captured_records
        if getattr(r, "structured_fields", {}).get("event") == "http_request"
    ]
    assert len(http_logs) >= 2
    first_log = http_logs[0]
    assert first_log["event"] == "http_request"
    assert first_log["request_id"] == custom_req_id
    assert first_log["path"] == "/api/auth/status"
    assert first_log["method"] == "GET"
    assert first_log["status_code"] == 200
    assert isinstance(first_log["duration_ms"], (int, float))


# ============================================================================
# 3. FULL AUTH LIFECYCLE (`INVITE_ONLY`, `ADMIN_APPROVAL`, `OPEN`, LOGIN, LOGOUT)
# ============================================================================


def test_auth_registration_modes_login_cookie_attributes_and_logout() -> None:
    """Verify /api/auth/register across INVITE_ONLY, ADMIN_APPROVAL, OPEN, /login cookie attributes, and /logout."""
    container, store = build_in_memory_runtime(
        cookie_secure=True, registration_mode="INVITE_ONLY"
    )
    app.state.container = container

    with TestClient(app, base_url="https://testserver") as client:
        # 1. Initial status is unauthenticated + INVITE_ONLY
        status_r = client.get("/api/auth/status")
        assert status_r.status_code == 200
        assert status_r.json()["authenticated"] is False
        assert status_r.json()["registration_mode"] == "INVITE_ONLY"

        # 2. Registering in INVITE_ONLY without an invite code fails with 422 problem+json
        no_invite = client.post(
            "/api/auth/register",
            json={
                "username": "Teacher_Alice",
                "password": "StrongPassword!2026",
            },
        )
        assert no_invite.status_code == 422
        assert no_invite.headers["content-type"].startswith("application/problem+json")
        assert no_invite.json()["type"] == "urn:fulwoodforge:error:invalid-invite-code"

        # 3. Login as bootstrap admin and create an invite code
        admin_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "BootstrapAdminPassword!2026",
            },
        )
        assert admin_login.status_code == 200
        set_cookie_hdr = admin_login.headers.get("set-cookie", "")
        assert "ff_session=" in set_cookie_hdr
        assert "HttpOnly" in set_cookie_hdr
        assert "Secure" in set_cookie_hdr
        assert "SameSite=lax" in set_cookie_hdr

        invite_r = client.post(
            "/api/admin/invite-codes",
            json={"code": "FF-MATHS-2026", "max_uses": 2, "expires_in_hours": 48},
        )
        assert invite_r.status_code == 201
        invite_code = invite_r.json()["code"]
        assert invite_code == "FF-MATHS-2026"

        # Logout admin before registering Teacher_Alice
        logout_admin = client.post("/api/auth/logout")
        assert logout_admin.status_code == 204

        # 4. Register Teacher_Alice with valid invite code -> 201 Created + authenticated
        reg_alice = client.post(
            "/api/auth/register",
            json={
                "username": "Teacher_Alice",
                "password": "StrongPassword!2026",
                "invite_code": "FF-MATHS-2026",
            },
        )
        assert reg_alice.status_code == 201
        alice_body = reg_alice.json()
        assert alice_body["authenticated"] is True
        assert alice_body["user"]["username"] == "teacher_alice"
        assert alice_body["user"]["display_name"] == "Teacher_Alice"
        assert "ff_session=" in reg_alice.headers.get("set-cookie", "")

        # Status reflects authenticated Teacher_Alice
        authed_status = client.get("/api/auth/status")
        assert authed_status.status_code == 200
        assert authed_status.json()["authenticated"] is True
        assert authed_status.json()["user"]["username"] == "teacher_alice"

        # Logout Alice
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/status").json()["authenticated"] is False

        # 5. Switch to ADMIN_APPROVAL mode -> registration returns 202 Accepted (PENDING)
        store.settings = store.settings.model_copy(
            update={"registration_mode": "ADMIN_APPROVAL"}
        )
        reg_bob = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_bob",
                "password": "StrongPassword!2026",
            },
        )
        assert reg_bob.status_code == 202
        bob_body = reg_bob.json()
        assert bob_body["authenticated"] is False
        assert bob_body["user"]["status"] == "PENDING"

        # Bob cannot log in while PENDING -> 403 application/problem+json
        bob_login = client.post(
            "/api/auth/login",
            json={
                "username": "teacher_bob",
                "password": "StrongPassword!2026",
            },
        )
        assert bob_login.status_code == 403
        assert bob_login.headers["content-type"].startswith("application/problem+json")
        assert bob_login.json()["type"] == "urn:fulwoodforge:error:account-pending"

        # 6. Switch to OPEN mode -> registration returns 201 Created without invite code
        store.settings = store.settings.model_copy(update={"registration_mode": "OPEN"})
        reg_claire = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_claire",
                "password": "StrongPassword!2026",
            },
        )
        assert reg_claire.status_code == 201
        assert reg_claire.json()["authenticated"] is True
        assert reg_claire.json()["user"]["status"] == "ACTIVE"


# ============================================================================
# 4. ADMIN PASSWORD RESET, FORCED PASSWORD CHANGE GATE (403) & SESSION REVOCATION
# ============================================================================


def test_admin_lifecycle_password_reset_forced_password_change_and_telemetry() -> None:
    """Verify /api/admin/* endpoints, temporary password reset revoking sessions, and 403 forced password change gate."""
    container, _ = build_in_memory_runtime(cookie_secure=True, registration_mode="OPEN")
    app.state.container = container

    with (
        TestClient(app, base_url="https://testserver") as teacher_client,
        TestClient(app, base_url="https://testserver") as admin_client,
    ):
        # 1. Register teacher
        reg = teacher_client.post(
            "/api/auth/register",
            json={"username": "teacher_dan", "password": "InitialPassword!2026"},
        )
        assert reg.status_code == 201
        dan_uuid = reg.json()["user"]["uuid"]

        # Teacher is non-admin -> /api/admin/overview returns 403 application/problem+json
        forb = teacher_client.get("/api/admin/overview")
        assert forb.status_code == 403
        assert forb.headers["content-type"].startswith("application/problem+json")
        assert forb.json()["type"] == "urn:fulwoodforge:error:forbidden"

        # 2. Admin signs in
        adm_login = admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "BootstrapAdminPassword!2026"},
        )
        assert adm_login.status_code == 200

        # Admin fetches overview & user list
        overview = admin_client.get("/api/admin/overview")
        assert overview.status_code == 200
        assert overview.json()["total_users"] == 2

        users_page = admin_client.get("/api/admin/users")
        assert users_page.status_code == 200
        assert len(users_page.json()["items"]) == 2

        # Admin updates Dan's hourly quota
        patch_user = admin_client.patch(
            f"/api/admin/users/{dan_uuid}",
            json={"hourly_generation_limit": 45},
        )
        assert patch_user.status_code == 200
        assert patch_user.json()["hourly_generation_limit"] == 45

        # 3. Admin resets Dan's password -> revokes Dan's active session & sets must_change_password=True
        reset_resp = admin_client.post(f"/api/admin/users/{dan_uuid}/reset-password")
        assert reset_resp.status_code == 200
        reset_data = reset_resp.json()
        temp_password = reset_data["temporary_password"]
        assert reset_data["must_change_password"] is True
        assert reset_data["sessions_revoked"] >= 1

        # Dan's existing cookie is now revoked -> 401
        old_session_check = teacher_client.get("/api/lessons")
        assert old_session_check.status_code == 401
        assert old_session_check.headers["content-type"].startswith(
            "application/problem+json"
        )

        # 4. Dan logs in with the temporary password
        relogin = teacher_client.post(
            "/api/auth/login",
            json={"username": "teacher_dan", "password": temp_password},
        )
        assert relogin.status_code == 200
        assert relogin.json()["must_change_password"] is True

        # While must_change_password is True, /api/generate, /api/lessons, /api/export/* return 403
        blocked_gen = teacher_client.post(
            "/api/generate",
            json={
                "subject": "Maths",
                "year_group": "10",
                "ability": "high",
                "exam_board": "Edexcel",
                "topic": "Quadratics",
                "task_type": "worksheet",
            },
        )
        assert blocked_gen.status_code == 403
        assert blocked_gen.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert (
            blocked_gen.json()["type"]
            == "urn:fulwoodforge:error:password-change-required"
        )

        blocked_lessons = teacher_client.get("/api/lessons")
        assert blocked_lessons.status_code == 403
        assert (
            blocked_lessons.json()["type"]
            == "urn:fulwoodforge:error:password-change-required"
        )

        # 5. Dan completes POST /api/auth/change-password
        changed = teacher_client.post(
            "/api/auth/change-password",
            json={
                "current_password": temp_password,
                "new_password": "UpdatedPermanentPass!2026",
            },
        )
        assert changed.status_code == 200
        assert changed.json()["must_change_password"] is False

        # Dan can now access /api/lessons
        now_allowed = teacher_client.get("/api/lessons")
        assert now_allowed.status_code == 200

        # 6. Admin system settings GET/PUT, invite deactivation, session revoke, audit events & token ledger
        get_settings = admin_client.get("/api/admin/settings")
        assert get_settings.status_code == 200
        put_settings = admin_client.put(
            "/api/admin/settings",
            json={
                "registration_mode": "INVITE_ONLY",
                "default_hourly_quota": 25,
                "maintenance_mode": False,
                "allowed_subjects": ["Maths", "Science"],
                "allowed_task_types": ["worksheet", "powerpoint"],
            },
        )
        assert put_settings.status_code == 200
        assert put_settings.json()["default_hourly_quota"] == 25

        inv = admin_client.post(
            "/api/admin/invite-codes",
            json={"code": "FF-TEMP-9999", "max_uses": 5, "expires_in_hours": 24},
        )
        assert inv.status_code == 201
        inv_uuid = inv.json()["uuid"]
        assert admin_client.get("/api/admin/invite-codes").status_code == 200
        assert (
            admin_client.delete(f"/api/admin/invite-codes/{inv_uuid}").status_code
            == 204
        )

        revoke_r = admin_client.post(f"/api/admin/users/{dan_uuid}/revoke-sessions")
        assert revoke_r.status_code == 200
        assert revoke_r.json()["sessions_revoked"] >= 1

        audits = admin_client.get("/api/admin/audit-events")
        assert audits.status_code == 200
        assert len(audits.json()["items"]) >= 4

        ledger = admin_client.get("/api/admin/token-ledger")
        assert ledger.status_code == 200


# ============================================================================
# 5. GENERATION, IDEMPOTENCY REPLAY, LESSONS CRUD, IDOR 403 & EXPORT
# ============================================================================


def test_generate_idempotency_lessons_crud_idor_403_and_export() -> None:
    """Verify /api/generate with Idempotency-Key, /api/lessons CRUD, IDOR 403, and /api/export/{fmt}."""
    gen_calls = 0

    async def _counting_generator(req: GenerateRequest) -> GeminiGenerationResult:
        nonlocal gen_calls
        gen_calls += 1
        return GeminiGenerationResult(
            content=make_sample_generated_content(
                title=f"Generated: {req.topic}",
                task_type=req.task_type,
                subject=req.subject,
            ),
            model_name="gemini-2.5-flash",
            prompt_tokens=300,
            candidate_tokens=600,
            total_tokens=900,
            latency_ms=95,
        )

    export_calls = 0

    async def _counting_exporter(_content: GeneratedContent, fmt: str) -> bytes:
        nonlocal export_calls
        export_calls += 1
        return b"%PDF-1.7-artifact" if fmt.endswith("pdf") else b"PK\x03\x04-artifact"

    container, _ = build_in_memory_runtime(
        cookie_secure=True,
        registration_mode="OPEN",
        generator_fn=_counting_generator,
        exporter_fn=_counting_exporter,
    )
    app.state.container = container

    with (
        TestClient(app, base_url="https://testserver") as teacher_a,
        TestClient(app, base_url="https://testserver") as teacher_b,
    ):
        assert (
            teacher_a.post(
                "/api/auth/register",
                json={"username": "teacher_a", "password": "ValidPassword!2026"},
            ).status_code
            == 201
        )
        assert (
            teacher_b.post(
                "/api/auth/register",
                json={"username": "teacher_b", "password": "ValidPassword!2026"},
            ).status_code
            == 201
        )

        gen_payload = {
            "subject": "Maths",
            "year_group": "10",
            "ability": "high",
            "exam_board": "Edexcel",
            "topic": "Simultaneous Equations",
            "task_type": "worksheet",
        }
        idem_key = "idem-wave4-key-0001"

        # 1. First POST /api/generate with Idempotency-Key
        gen_1 = teacher_a.post(
            "/api/generate",
            json=gen_payload,
            headers={"Idempotency-Key": idem_key},
        )
        assert gen_1.status_code == 200
        lesson_uuid = gen_1.headers["x-generation-uuid"]
        assert gen_1.headers["x-cache-hit"] == "false"
        assert gen_calls == 1

        # 2. Replay with same Idempotency-Key and same payload -> returns cached result without calling Gemini
        gen_2 = teacher_a.post(
            "/api/generate",
            json=gen_payload,
            headers={"Idempotency-Key": idem_key},
        )
        assert gen_2.status_code == 200
        assert gen_2.headers["x-generation-uuid"] == lesson_uuid
        assert gen_2.headers["x-cache-hit"] == "true"
        assert gen_calls == 1

        # 3. Replay with same Idempotency-Key but different payload -> 409 Conflict application/problem+json
        gen_conflict = teacher_a.post(
            "/api/generate",
            json={**gen_payload, "topic": "Different Topic"},
            headers={"Idempotency-Key": idem_key},
        )
        assert gen_conflict.status_code == 409
        assert gen_conflict.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert gen_conflict.json()["type"] == "urn:fulwoodforge:error:conflict"

        # 4. Short Idempotency-Key (< 16 chars) -> 422 application/problem+json
        short_idem = teacher_a.post(
            "/api/generate",
            json=gen_payload,
            headers={"Idempotency-Key": "too-short"},
        )
        assert short_idem.status_code == 422
        assert short_idem.headers["content-type"].startswith("application/problem+json")

        # 5. Teacher A lists lessons -> sees the saved lesson
        list_a = teacher_a.get("/api/lessons")
        assert list_a.status_code == 200
        assert len(list_a.json()["items"]) == 1
        assert list_a.json()["items"][0]["uuid"] == lesson_uuid

        # 6. Teacher A fetches and updates the lesson title inline
        detail_a = teacher_a.get(f"/api/lessons/{lesson_uuid}")
        assert detail_a.status_code == 200
        assert (
            detail_a.json()["content"]["title"] == "Generated: Simultaneous Equations"
        )

        patched_a = teacher_a.patch(
            f"/api/lessons/{lesson_uuid}",
            json={"title": "Updated Simultaneous Equations Worksheet"},
        )
        assert patched_a.status_code == 200
        assert patched_a.json()["title"] == "Updated Simultaneous Equations Worksheet"

        # 7. Teacher B attempts IDOR on Teacher A's lesson -> 403 application/problem+json on GET, PATCH, DELETE, EXPORT
        for resp in (
            teacher_b.get(f"/api/lessons/{lesson_uuid}"),
            teacher_b.patch(f"/api/lessons/{lesson_uuid}", json={"title": "Hijacked"}),
            teacher_b.delete(f"/api/lessons/{lesson_uuid}"),
            teacher_b.post(f"/api/lessons/{lesson_uuid}/export/docx"),
        ):
            assert resp.status_code == 403
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["type"] == "urn:fulwoodforge:error:forbidden"

        # 8. Teacher A exports saved lesson -> compiles on first call, serves cached artifact on second call
        exp_1 = teacher_a.post(f"/api/lessons/{lesson_uuid}/export/docx")
        assert exp_1.status_code == 200
        assert exp_1.content.startswith(b"PK")
        assert "attachment; filename=" in exp_1.headers["content-disposition"]
        assert exp_1.headers["x-content-type-options"] == "nosniff"
        assert export_calls == 1

        exp_2 = teacher_a.post(f"/api/lessons/{lesson_uuid}/export/docx")
        assert exp_2.status_code == 200
        assert exp_2.content == exp_1.content
        assert export_calls == 1  # Served from compiled artifact cache!

        # 9. Ad-hoc POST /api/export/pdf
        ad_hoc = teacher_a.post(
            "/api/export/pdf",
            json=make_sample_generated_content().model_dump(mode="json"),
        )
        assert ad_hoc.status_code == 200
        assert ad_hoc.content.startswith(b"%PDF")
        assert ad_hoc.headers["content-type"] == "application/pdf"

        # 10. Teacher A archives (DELETE) the lesson -> 204 No Content, subsequent GET returns 404
        del_r = teacher_a.delete(f"/api/lessons/{lesson_uuid}")
        assert del_r.status_code == 204
        not_found = teacher_a.get(f"/api/lessons/{lesson_uuid}")
        assert not_found.status_code == 404
        assert not_found.headers["content-type"].startswith("application/problem+json")


# ============================================================================
# 6. RFC 9457 PROBLEM DETAILS ACROSS ALL 4xx / 5xx STATUS CODES
# ============================================================================


def test_rfc9457_problem_details_across_401_403_404_405_413_422_429_and_503() -> None:
    """Verify 100% of 4xx/5xx error responses emit Content-Type: application/problem+json."""
    container, store = build_in_memory_runtime(
        cookie_secure=True, registration_mode="OPEN"
    )
    app.state.container = container

    with TestClient(app, base_url="https://testserver") as client:
        # 401 Unauthenticated
        r_401 = client.get("/api/lessons")
        assert r_401.status_code == 401
        assert r_401.headers["content-type"].startswith("application/problem+json")
        assert r_401.json()["status"] == 401

        # Register teacher
        client.post(
            "/api/auth/register",
            json={"username": "teacher_err", "password": "ValidPassword!2026"},
        )

        # 403 Forbidden (non-admin accessing /api/admin/overview)
        r_403 = client.get("/api/admin/overview")
        assert r_403.status_code == 403
        assert r_403.headers["content-type"].startswith("application/problem+json")

        # 404 Not Found
        r_404 = client.get(f"/api/lessons/{generate_uuid7_str()}")
        assert r_404.status_code == 404
        assert r_404.headers["content-type"].startswith("application/problem+json")
        assert r_404.json()["type"] == "urn:fulwoodforge:error:not-found"

        # 405 Method Not Allowed
        r_405 = client.get("/api/generate")
        assert r_405.status_code == 405
        assert r_405.headers["content-type"].startswith("application/problem+json")
        assert r_405.json()["type"] == "urn:fulwoodforge:error:method-not-allowed"

        # 413 Payload Too Large (> 5 MiB Content-Length)
        r_413 = client.post(
            "/api/generate",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(6 * 1024 * 1024),
            },
        )
        assert r_413.status_code == 413
        assert r_413.headers["content-type"].startswith("application/problem+json")
        assert r_413.json()["type"] == "urn:fulwoodforge:error:payload-too-large"
        assert r_413.json()["title"] == "Payload Too Large"

        # 422 Validation Failed with invalid-params
        r_422 = client.post(
            "/api/generate",
            json={
                "subject": "InvalidSubject",
                "year_group": "10",
                "ability": "high",
                "topic": "x",
                "task_type": "worksheet",
            },
        )
        assert r_422.status_code == 422
        assert r_422.headers["content-type"].startswith("application/problem+json")
        body_422 = r_422.json()
        assert body_422["type"] == "urn:fulwoodforge:error:validation-failed"
        assert isinstance(body_422.get("invalid-params"), list)
        assert len(body_422["invalid-params"]) >= 1

        # 429 Rate Limit Exceeded with Retry-After header (20/min export limit)
        sample_payload = make_sample_generated_content().model_dump(mode="json")
        for _ in range(20):
            ok_exp = client.post("/api/export/docx", json=sample_payload)
            assert ok_exp.status_code == 200
        r_429 = client.post("/api/export/docx", json=sample_payload)
        assert r_429.status_code == 429
        assert r_429.headers["content-type"].startswith("application/problem+json")
        assert "retry-after" in r_429.headers
        assert r_429.json()["type"] == "urn:fulwoodforge:error:rate-limit-exceeded"

        # 503 Maintenance Mode
        store.settings = store.settings.model_copy(update={"maintenance_mode": True})
        r_503 = client.post(
            "/api/generate",
            json={
                "subject": "Maths",
                "year_group": "10",
                "ability": "high",
                "exam_board": "Edexcel",
                "topic": "Trigonometry",
                "task_type": "worksheet",
            },
        )
        assert r_503.status_code == 503
        assert r_503.headers["content-type"].startswith("application/problem+json")
        assert r_503.json()["type"] == "urn:fulwoodforge:error:maintenance-mode"


# ============================================================================
# 7. HEALTH & READINESS PROBES (`/health/live`, `/health/ready`) & DRAINING
# ============================================================================


def test_health_live_and_ready_probes_and_draining_503() -> None:
    """Verify /health/live and /health/ready return 200 when healthy and 503 problem+json when DB down or draining."""
    container, store = build_in_memory_runtime()
    app.state.container = container

    with TestClient(app, base_url="https://testserver") as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "live"}

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "database": "connected",
            "pandoc": "available",
        }

        # Simulate DB pool unhealthy -> 503 application/problem+json
        store.db_healthy = False
        unhealthy_db = client.get("/health/ready")
        assert unhealthy_db.status_code == 503
        assert unhealthy_db.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert unhealthy_db.json()["status"] == 503

        # Restore DB, simulate SIGTERM draining -> 503 application/problem+json
        store.db_healthy = True
        app.state.is_draining = True
        try:
            draining_resp = client.get("/health/ready")
            assert draining_resp.status_code == 503
            assert draining_resp.headers["content-type"].startswith(
                "application/problem+json"
            )
        finally:
            app.state.is_draining = False


# ============================================================================
# 8. DOCKER COMPOSE MYSQL 8.4 STACK & DEPLOY OAUTH2-PROXY ELIMINATION
# ============================================================================


def test_docker_compose_mysql_84_and_oauth2_proxy_removed() -> None:
    """Verify docker-compose.yml defines mysql:8.4, removes oauth2-proxy, and aligns with Dockerfile runtime."""
    compose_path = REPO_ROOT / "docker-compose.yml"
    compose_raw = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_raw)
    services = compose.get("services", {})

    dockerfile_path = REPO_ROOT / "Dockerfile"
    dockerfile_raw = dockerfile_path.read_text(encoding="utf-8")

    assert "oauth2-proxy" not in services
    assert "mysql" in services
    assert "app" in services

    mysql_svc = services["mysql"]
    assert mysql_svc["image"] == "mysql:8.4"
    assert "--character-set-server=utf8mb4" in mysql_svc["command"]
    assert "--collation-server=utf8mb4_0900_ai_ci" in mysql_svc["command"]
    assert "mysql_data:/var/lib/mysql" in mysql_svc["volumes"]

    app_svc = services["app"]
    assert app_svc["depends_on"]["mysql"]["condition"] == "service_healthy"
    assert not (REPO_ROOT / "deploy" / "oauth2-proxy.yaml").exists()

    # Wave 6.1 container alignment invariants
    assert "pandoc" in dockerfile_raw
    assert "REQUIRE_PROXY_AUTH" not in dockerfile_raw
    assert "REQUIRE_PROXY_AUTH" not in compose_raw
    assert "PORT=8000" in dockerfile_raw
    assert "EXPOSE 8000" in dockerfile_raw
    assert app_svc["environment"]["PORT"] == "8000"
    assert any(":8000:8000" in p for p in app_svc["ports"])
    assert "HOME=/home/appuser" in dockerfile_raw
    assert any(t.startswith("/home/appuser:") for t in app_svc["tmpfs"])
    assert "ARTIFACTS_DIR=/tmp/artifacts" in dockerfile_raw
    assert app_svc["environment"]["ARTIFACTS_DIR"] == "/tmp/artifacts"
    assert 'PYTHONPATH="/app/backend"' in dockerfile_raw
    assert "mkdir -p /tmp/artifacts" in dockerfile_raw
    assert (REPO_ROOT / "deploy" / "chrome-seccomp.json").is_file()
    seccomp_raw = (REPO_ROOT / "deploy" / "chrome-seccomp.json").read_text(
        encoding="utf-8"
    )
    assert '"openat2"' in seccomp_raw
    assert '"clone3"' in seccomp_raw
    assert "./deploy/chrome-seccomp.json" in compose_raw
    assert "seccomp-chrome.json" not in compose_raw
    assert "SYS_CHROOT" in app_svc.get("cap_add", [])
