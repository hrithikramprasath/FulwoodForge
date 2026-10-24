"""Lesson generation, library management, and export service (`backend/app/services/lesson_service.py`).

Enforces:
- Cache lookup BEFORE rate-limit quota check (`REQ-SEC-03` / `VULN-03`)
- `Idempotency-Key` concurrency lock & replay (`REQ-API-02`)
- Pure-Python `calculate_slide_count` in `< 1ms` (`REQ-SEC-02` / `VULN-02`)
- `SystemSettings` maintenance mode and subject/task_type allowlists (`REQ-ADMIN-02`)
- Object-Level Authorization (IDOR defense) on all saved lessons and artifacts (`403 PermissionDeniedError`)
- Append-only `token_ledger` (`DECIMAL(19,4)`) and `audit_events` telemetry (`REQ-LOG-02`, `REQ-LOG-03`)

Unidirectional Dependency Law: Imports ONLY from `app.schemas`, `app.core`, `app.db`,
`app.repositories`, `app.services.errors`, `app.services.export`, `app.services.gemini`,
`app.services.pptx_writer`, and Python standard library. ZERO imports from `app.api`, `app.main`, or `fastapi`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import overload

from app.core.config import AppConfig
from app.core.processes import run_worker_async
from app.core.uuid_v7 import uuid7_str_to_bytes
from app.db.pool import AsyncConnectionProtocol, AsyncDatabasePool
from app.repositories.artifacts_repo import ArtifactRow, ArtifactsRepository
from app.repositories.base import KeysetCursor, KeysetPage
from app.repositories.generations_repo import GenerationRow, GenerationsRepository
from app.repositories.idempotency_repo import IdempotencyRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.repositories.users_repo import UserRow
from app.schemas.content import GeneratedContent, GenerateRequest
from app.schemas.problem_details import InvalidParam
from app.services.errors import (
    ConflictError,
    MaintenanceModeError,
    PermissionDeniedError,
    RateLimitExceededError,
    ResourceNotFoundError,
    ServiceError,
    UpstreamGenerationError,
)
from app.services.export import MEDIA_TYPES, _slug
from app.services.gemini import GenerationResult, generate_with_usage_async
from app.services.pptx_writer import calculate_slide_count

logger = logging.getLogger(__name__)

CACHE_WINDOW_MINUTES: int = 15
QUOTA_WINDOW_HOURS: int = 1
IDEMPOTENCY_TTL_HOURS: int = 24

_FOUR_DP = Decimal("0.0001")
# Estimated Gemini 2.5 Flash pricing per 1M tokens ($0.15 prompt / $0.60 candidate)
_PROMPT_COST_PER_TOKEN = Decimal("0.00000015")
_CANDIDATE_COST_PER_TOKEN = Decimal("0.00000060")

AsyncGeneratorCallable = Callable[[GenerateRequest], Awaitable[GenerationResult]]
AsyncExporterCallable = Callable[[GeneratedContent, str], Awaitable[bytes]]


def compute_generation_request_hash(user_id: int, req: GenerateRequest) -> bytes:
    """Compute a deterministic 32-byte SHA-256 request digest scoped to `(user_id, payload)`."""
    payload = {
        "user_id": int(user_id),
        "subject": req.subject,
        "year_group": req.year_group,
        "ability": req.ability,
        "exam_board": req.exam_board,
        "topic": req.topic.strip().lower(),
        "task_type": req.task_type,
        "question_count": req.question_count,
        "slide_count": req.slide_count,
        "booklet_length": req.booklet_length,
        "include_answer_key": bool(req.include_answer_key),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def estimate_generation_cost_usd(
    prompt_tokens: int,
    candidate_tokens: int,
) -> Decimal:
    """Calculate `DECIMAL(19,4)` USD cost for a Gemini generation call."""
    raw = (
        Decimal(max(0, prompt_tokens)) * _PROMPT_COST_PER_TOKEN
        + Decimal(max(0, candidate_tokens)) * _CANDIDATE_COST_PER_TOKEN
    )
    if raw > Decimal(0) and raw < _FOUR_DP:
        return _FOUR_DP
    return raw.quantize(_FOUR_DP, rounding=ROUND_HALF_UP)


async def _default_export_artifact(content: GeneratedContent, fmt: str) -> bytes:
    """Default isolated worker export for `docx`, `pptx`, `pdf`, `answers-docx`, `answers-pdf`."""
    payload_bytes = json.dumps(
        {"content": content.model_dump(mode="json"), "format": fmt},
        separators=(",", ":"),
    ).encode("utf-8")
    return await run_worker_async("export", payload_bytes, timeout=180.0)


def format_export_filename(title: str, fmt: str) -> tuple[str, str]:
    """Validate `fmt` against `MEDIA_TYPES` and return `(filename, media_type)`."""
    if fmt not in MEDIA_TYPES:
        raise ServiceError(
            f"Unsupported export format: .{fmt}",
            problem_type="urn:fulwoodforge:error:validation-failed",
            title="Request Validation Failed",
            status_code=422,
            invalid_params=[
                InvalidParam(
                    name="fmt",
                    reason=f"Unsupported export format '{fmt}'. Must be one of {sorted(MEDIA_TYPES)}.",
                )
            ],
        )
    suffix = "-answers" if fmt.startswith("answers-") else ""
    ext = fmt.split("-")[-1]
    filename = f"{_slug(title)}{suffix}.{ext}"
    return filename, MEDIA_TYPES[fmt]


def _assert_lesson_access(actor: UserRow, row: GenerationRow) -> None:
    """Enforce Object-Level Authorization (IDOR defense): owner or ADMIN only."""
    if row.user_id != actor.id and actor.role != "ADMIN":
        raise PermissionDeniedError(
            "You do not have permission to access another teacher's lesson resource."
        )


def _parse_lesson_uuid(lesson_uuid: str) -> bytes:
    try:
        return uuid7_str_to_bytes(lesson_uuid)
    except ValueError as exc:
        raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' was not found.") from exc


def _parse_keyset_cursor_or_422(cursor: str | None) -> KeysetCursor | None:
    if not cursor:
        return None
    try:
        return KeysetCursor.decode(cursor)
    except ValueError as exc:
        raise ServiceError(
            "Invalid keyset pagination cursor.",
            problem_type="urn:fulwoodforge:error:validation-failed",
            title="Request Validation Failed",
            status_code=422,
            invalid_params=[
                InvalidParam(
                    name="cursor",
                    reason="Malformed keyset pagination cursor.",
                )
            ],
        ) from exc


class LessonService:
    """Domain orchestration service for lesson generation, library CRUD, and exports."""

    def __init__(
        self,
        pool: AsyncDatabasePool | None = None,
        config: AppConfig | None = None,
        *,
        generations_repo: GenerationsRepository | None = None,
        artifacts_repo: ArtifactsRepository | None = None,
        idempotency_repo: IdempotencyRepository | None = None,
        settings_repo: SystemSettingsRepository | None = None,
        telemetry_repo: TelemetryRepository | None = None,
        generator_fn: AsyncGeneratorCallable | None = None,
        exporter_fn: AsyncExporterCallable | None = None,
    ) -> None:
        self._pool = pool
        self._config = config
        self._generations_repo = generations_repo or GenerationsRepository()
        self._artifacts_repo = artifacts_repo or ArtifactsRepository()
        self._idempotency_repo = idempotency_repo or IdempotencyRepository()
        self._settings_repo = settings_repo or SystemSettingsRepository()
        self._telemetry_repo = telemetry_repo or TelemetryRepository()
        self._generator_fn: AsyncGeneratorCallable = (
            generator_fn or generate_with_usage_async
        )
        self._exporter_fn: AsyncExporterCallable = (
            exporter_fn or _default_export_artifact
        )
        self._artifact_bytes_cache: dict[tuple[int, str], tuple[bytes, bytes]] = {}

    def _require_pool(self) -> AsyncDatabasePool:
        if self._pool is None:
            raise RuntimeError(
                "LessonService requires an AsyncDatabasePool when called without an explicit connection."
            )
        return self._pool

    @overload
    async def generate_lesson(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        req: GenerateRequest,
        /,
        *,
        idempotency_key: str | None = None,
        refresh: bool = False,
        ip_address: str = "127.0.0.1",
        now_utc: datetime | None = None,
    ) -> tuple[str, GeneratedContent, bool]: ...

    @overload
    async def generate_lesson(
        self,
        actor: UserRow,
        req: GenerateRequest,
        idempotency_key: str | None = None,
        ip_address: str = "127.0.0.1",
        /,
        *,
        refresh: bool = False,
        now_utc: datetime | None = None,
    ) -> tuple[str, GeneratedContent, bool]: ...

    async def generate_lesson(
        self,
        first_arg: AsyncConnectionProtocol | UserRow,
        second_arg: UserRow | GenerateRequest,
        third_arg: GenerateRequest | str | None = None,
        fourth_arg: str = "127.0.0.1",
        /,
        *,
        idempotency_key: str | None = None,
        refresh: bool = False,
        ip_address: str = "127.0.0.1",
        now_utc: datetime | None = None,
    ) -> tuple[str, GeneratedContent, bool]:
        """Generate or retrieve a cached lesson resource (`REQ-SEC-03`, `REQ-API-02`).

        Supports both `(conn, actor, req, ...)` and `(actor, req, idempotency_key, ip_address, ...)`.
        Returns `(generation_uuid, content, cache_hit)`.
        """
        if isinstance(first_arg, UserRow):
            if not isinstance(second_arg, GenerateRequest):
                raise TypeError("Expected GenerateRequest as second argument.")
            resolved_idem = third_arg if isinstance(third_arg, str) else idempotency_key
            resolved_ip = fourth_arg if fourth_arg != "127.0.0.1" else ip_address
            pool = self._require_pool()
            async with pool.transaction() as conn:
                return await self._generate_lesson_on_conn(
                    conn,
                    first_arg,
                    second_arg,
                    idempotency_key=resolved_idem,
                    refresh=refresh,
                    ip_address=resolved_ip,
                    now_utc=now_utc,
                )

        if not isinstance(second_arg, UserRow) or not isinstance(
            third_arg, GenerateRequest
        ):
            raise TypeError("Expected (conn, actor, req) arguments.")
        return await self._generate_lesson_on_conn(
            first_arg,
            second_arg,
            third_arg,
            idempotency_key=idempotency_key,
            refresh=refresh,
            ip_address=ip_address,
            now_utc=now_utc,
        )

    async def _generate_lesson_on_conn(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        req: GenerateRequest,
        *,
        idempotency_key: str | None = None,
        refresh: bool = False,
        ip_address: str = "127.0.0.1",
        now_utc: datetime | None = None,
    ) -> tuple[str, GeneratedContent, bool]:
        current_time = now_utc or datetime.now(UTC)

        # 1. Enforce SystemSettings (maintenance mode + subject/task_type allowlists)
        settings = await self._settings_repo.get_or_init_settings(conn)
        if settings.maintenance_mode and actor.role != "ADMIN":
            raise MaintenanceModeError()
        if req.subject not in settings.allowed_subjects:
            raise PermissionDeniedError(
                f"Subject '{req.subject}' is currently disabled by system settings."
            )
        if req.task_type not in settings.allowed_task_types:
            raise PermissionDeniedError(
                f"Task type '{req.task_type}' is currently disabled by system settings."
            )

        # 2. Compute deterministic 32-byte request hash
        request_hash = compute_generation_request_hash(actor.id, req)

        # 3. Optional Idempotency-Key lock & deduplication (`REQ-API-02`)
        idem_record_id: int | None = None
        if idempotency_key is not None and idempotency_key.strip():
            cleaned_key = idempotency_key.strip()
            if len(cleaned_key) > 64:
                raise ServiceError(
                    "Idempotency-Key header must be at most 64 characters.",
                    problem_type="urn:fulwoodforge:error:validation-failed",
                    title="Request Validation Failed",
                    status_code=422,
                    invalid_params=[
                        InvalidParam(
                            name="Idempotency-Key",
                            reason="Idempotency-Key must be between 16 and 64 characters.",
                        )
                    ],
                )
            record = await self._idempotency_repo.lock_or_create_key(
                conn,
                user_id=actor.id,
                idempotency_key=cleaned_key,
                request_hash=request_hash,
                expires_at=current_time + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
            )
            if not record.is_newly_created:
                if record.request_hash != request_hash:
                    raise ConflictError(
                        "Idempotency-Key was already used with a different request payload."
                    )
                if record.status == "IN_PROGRESS":
                    raise ConflictError(
                        "A generation request with this Idempotency-Key is currently in progress."
                    )
                if (
                    record.status == "COMPLETED"
                    and record.response_body_json is not None
                ):
                    cached_payload = json.loads(record.response_body_json)
                    replayed_content = GeneratedContent.model_validate(
                        cached_payload["content"]
                    )
                    return (
                        str(cached_payload["generation_uuid"]),
                        replayed_content,
                        True,
                    )
            idem_record_id = record.id

        # 4. Cache Lookup BEFORE Rate-Limit Quota Check (`REQ-SEC-03` / `VULN-03`)
        if not refresh:
            cached_row = await self._generations_repo.find_recent_by_hash(
                conn,
                user_id=actor.id,
                request_hash=request_hash,
                since_utc=current_time - timedelta(minutes=CACHE_WINDOW_MINUTES),
            )
            if cached_row is not None:
                cached_content = GeneratedContent.model_validate_json(
                    cached_row.content_json
                )
                await self._telemetry_repo.insert_token_ledger(
                    conn,
                    user_id=actor.id,
                    generation_id=cached_row.id,
                    model_name="cache",
                    prompt_tokens=0,
                    candidate_tokens=0,
                    total_tokens=0,
                    estimated_cost_usd=Decimal("0.0000"),
                    latency_ms=0,
                    cache_hit=True,
                    status="CACHE_HIT",
                )
                await self._telemetry_repo.insert_audit_event(
                    conn,
                    actor_user_id=actor.id,
                    target_user_id=None,
                    event_type="LESSON_GENERATED",
                    ip_address=ip_address,
                    metadata={
                        "generation_uuid": cached_row.uuid_str,
                        "subject": req.subject,
                        "task_type": req.task_type,
                        "cache_hit": True,
                    },
                )
                if idem_record_id is not None:
                    serialized = json.dumps(
                        {
                            "generation_uuid": cached_row.uuid_str,
                            "content": cached_content.model_dump(mode="json"),
                            "cache_hit": True,
                        },
                        separators=(",", ":"),
                    )
                    await self._idempotency_repo.complete_key(
                        conn,
                        record_id=idem_record_id,
                        response_status=200,
                        response_body_json=serialized,
                    )
                return (cached_row.uuid_str, cached_content, True)

        # 5. Per-User Hourly Generation Quota Check ONLY on Cache Miss (`REQ-SEC-03`)
        recent_count = await self._generations_repo.count_user_generations_since(
            conn,
            user_id=actor.id,
            since_utc=current_time - timedelta(hours=QUOTA_WINDOW_HOURS),
        )
        if recent_count >= actor.hourly_generation_limit:
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=None,
                event_type="QUOTA_EXCEEDED",
                ip_address=ip_address,
                metadata={
                    "recent_count": recent_count,
                    "hourly_generation_limit": actor.hourly_generation_limit,
                },
            )
            if idem_record_id is not None:
                await self._idempotency_repo.fail_key(conn, idem_record_id)
            await conn.commit()
            raise RateLimitExceededError(
                f"Hourly generation quota ({actor.hourly_generation_limit}/hr) exceeded.",
                retry_after_seconds=3600,
            )

        # 6. Execute Upstream Generation
        active_model_name = (
            self._config.gemini_model if self._config else "gemini-3.5-flash-lite"
        )
        try:
            gen_result = await self._generator_fn(req)
        except UpstreamGenerationError:
            await self._telemetry_repo.insert_token_ledger(
                conn,
                user_id=actor.id,
                generation_id=None,
                model_name=active_model_name,
                prompt_tokens=0,
                candidate_tokens=0,
                total_tokens=0,
                estimated_cost_usd=Decimal("0.0000"),
                latency_ms=0,
                cache_hit=False,
                status="UPSTREAM_ERROR",
            )
            if idem_record_id is not None:
                await self._idempotency_repo.fail_key(conn, idem_record_id)
            await conn.commit()
            raise

        # 7. Calculate slide count in pure Python (`< 1ms`, `REQ-SEC-02` / `VULN-02`)
        slide_count = calculate_slide_count(gen_result.content)
        content_json_str = gen_result.content.model_dump_json()

        created_row = await self._generations_repo.create_generation(
            conn,
            user_id=actor.id,
            request_hash=request_hash,
            subject=req.subject,
            year_group=req.year_group,
            tier=req.ability,
            exam_board=req.exam_board,
            topic=req.topic,
            task_type=req.task_type,
            title=gen_result.content.title,
            content_json=content_json_str,
            slide_count=slide_count,
        )

        estimated_cost = estimate_generation_cost_usd(
            gen_result.prompt_tokens,
            gen_result.candidate_tokens,
        )
        await self._telemetry_repo.insert_token_ledger(
            conn,
            user_id=actor.id,
            generation_id=created_row.id,
            model_name=gen_result.model_name,
            prompt_tokens=gen_result.prompt_tokens,
            candidate_tokens=gen_result.candidate_tokens,
            total_tokens=gen_result.total_tokens,
            estimated_cost_usd=estimated_cost,
            latency_ms=gen_result.latency_ms,
            cache_hit=False,
            status="SUCCESS",
        )
        await self._telemetry_repo.insert_audit_event(
            conn,
            actor_user_id=actor.id,
            target_user_id=None,
            event_type="LESSON_GENERATED",
            ip_address=ip_address,
            metadata={
                "generation_uuid": created_row.uuid_str,
                "subject": req.subject,
                "task_type": req.task_type,
                "slide_count": slide_count,
                "cache_hit": False,
            },
        )

        if idem_record_id is not None:
            serialized = json.dumps(
                {
                    "generation_uuid": created_row.uuid_str,
                    "content": gen_result.content.model_dump(mode="json"),
                    "cache_hit": False,
                },
                separators=(",", ":"),
            )
            await self._idempotency_repo.complete_key(
                conn,
                record_id=idem_record_id,
                response_status=200,
                response_body_json=serialized,
            )

        return (created_row.uuid_str, gen_result.content, False)

    @overload
    async def get_lesson(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        /,
    ) -> tuple[GenerationRow, GeneratedContent]: ...

    @overload
    async def get_lesson(
        self,
        actor: UserRow,
        lesson_uuid: str,
        /,
    ) -> tuple[GenerationRow, GeneratedContent]: ...

    async def get_lesson(
        self,
        first_arg: AsyncConnectionProtocol | UserRow,
        second_arg: UserRow | str,
        third_arg: str | None = None,
        /,
    ) -> tuple[GenerationRow, GeneratedContent]:
        """Fetch a saved lesson by UUID enforcing Object-Level Authorization (`403` on IDOR)."""
        if isinstance(first_arg, UserRow):
            if not isinstance(second_arg, str):
                raise TypeError("Expected lesson_uuid string as second argument.")
            pool = self._require_pool()
            async with pool.connection() as conn:
                return await self._get_lesson_on_conn(conn, first_arg, second_arg)

        if not isinstance(second_arg, UserRow) or not isinstance(third_arg, str):
            raise TypeError("Expected (conn, actor, lesson_uuid) arguments.")
        return await self._get_lesson_on_conn(first_arg, second_arg, third_arg)

    async def _get_lesson_on_conn(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
    ) -> tuple[GenerationRow, GeneratedContent]:
        uuid_bytes = _parse_lesson_uuid(lesson_uuid)
        row = await self._generations_repo.get_by_uuid(conn, uuid_bytes)
        if row is None or row.is_archived:
            raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' was not found.")
        _assert_lesson_access(actor, row)
        content = GeneratedContent.model_validate_json(row.content_json)
        return (row, content)

    @overload
    async def list_lessons(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        /,
        *,
        cursor: str | None = None,
        limit: int = 25,
        subject: str | None = None,
        task_type: str | None = None,
    ) -> KeysetPage[GenerationRow]: ...

    @overload
    async def list_lessons(
        self,
        actor: UserRow,
        /,
        *,
        cursor: str | None = None,
        limit: int = 25,
        subject: str | None = None,
        task_type: str | None = None,
    ) -> KeysetPage[GenerationRow]: ...

    async def list_lessons(
        self,
        first_arg: AsyncConnectionProtocol | UserRow,
        second_arg: UserRow | None = None,
        /,
        *,
        cursor: str | None = None,
        limit: int = 25,
        subject: str | None = None,
        task_type: str | None = None,
    ) -> KeysetPage[GenerationRow]:
        """List non-archived lessons owned by `actor` using keyset pagination."""
        parsed_cursor = _parse_keyset_cursor_or_422(cursor)
        if isinstance(first_arg, UserRow):
            pool = self._require_pool()
            async with pool.connection() as conn:
                return await self._generations_repo.list_for_user_keyset(
                    conn,
                    user_id=first_arg.id,
                    cursor=parsed_cursor,
                    limit=limit,
                    subject=subject,
                    task_type=task_type,
                )

        if not isinstance(second_arg, UserRow):
            raise TypeError("Expected (conn, actor) arguments.")
        return await self._generations_repo.list_for_user_keyset(
            first_arg,
            user_id=second_arg.id,
            cursor=parsed_cursor,
            limit=limit,
            subject=subject,
            task_type=task_type,
        )

    @overload
    async def update_lesson_content(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        updated_content: GeneratedContent,
        /,
        *,
        title: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[GenerationRow, GeneratedContent]: ...

    @overload
    async def update_lesson_content(
        self,
        actor: UserRow,
        lesson_uuid: str,
        updated_content: GeneratedContent,
        /,
        *,
        title: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[GenerationRow, GeneratedContent]: ...

    async def update_lesson_content(
        self,
        first_arg: AsyncConnectionProtocol | UserRow,
        second_arg: UserRow | str,
        third_arg: str | GeneratedContent,
        fourth_arg: GeneratedContent | None = None,
        /,
        *,
        title: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[GenerationRow, GeneratedContent]:
        """Update saved lesson content enforcing Object-Level Authorization (`403` on IDOR)."""
        if isinstance(first_arg, UserRow):
            if not isinstance(second_arg, str) or not isinstance(
                third_arg, GeneratedContent
            ):
                raise TypeError(
                    "Expected (actor, lesson_uuid, updated_content) arguments."
                )
            pool = self._require_pool()
            async with pool.transaction() as conn:
                return await self._update_lesson_content_on_conn(
                    conn,
                    first_arg,
                    second_arg,
                    third_arg,
                    title=title,
                    ip_address=ip_address,
                )

        if (
            not isinstance(second_arg, UserRow)
            or not isinstance(third_arg, str)
            or not isinstance(fourth_arg, GeneratedContent)
        ):
            raise TypeError(
                "Expected (conn, actor, lesson_uuid, updated_content) arguments."
            )
        return await self._update_lesson_content_on_conn(
            first_arg,
            second_arg,
            third_arg,
            fourth_arg,
            title=title,
            ip_address=ip_address,
        )

    async def _update_lesson_content_on_conn(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        updated_content: GeneratedContent,
        *,
        title: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[GenerationRow, GeneratedContent]:
        uuid_bytes = _parse_lesson_uuid(lesson_uuid)
        row = await self._generations_repo.get_by_uuid(conn, uuid_bytes)
        if row is None or row.is_archived:
            raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' was not found.")
        _assert_lesson_access(actor, row)

        if title is not None and title.strip():
            updated_content = updated_content.model_copy(
                update={"title": title.strip()}
            )
        slide_count = calculate_slide_count(updated_content)
        content_json_str = updated_content.model_dump_json()

        updated = await self._generations_repo.update_content(
            conn,
            generation_id=row.id,
            user_id=row.user_id,
            title=updated_content.title,
            content_json=content_json_str,
            slide_count=slide_count,
        )
        if not updated:
            raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' could not be updated.")

        # Invalidate any cached compiled artifacts for this lesson generation
        for key in [k for k in self._artifact_bytes_cache if k[0] == row.id]:
            self._artifact_bytes_cache.pop(key, None)

        await self._telemetry_repo.insert_audit_event(
            conn,
            actor_user_id=actor.id,
            target_user_id=row.user_id if row.user_id != actor.id else None,
            event_type="LESSON_UPDATED",
            ip_address=ip_address,
            metadata={
                "generation_uuid": row.uuid_str,
                "slide_count": slide_count,
            },
        )
        refreshed = await self._generations_repo.get_by_uuid(conn, uuid_bytes)
        if refreshed is None:
            raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' was not found.")
        return (refreshed, updated_content)

    @overload
    async def archive_lesson(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        /,
        *,
        ip_address: str = "127.0.0.1",
    ) -> None: ...

    @overload
    async def archive_lesson(
        self,
        actor: UserRow,
        lesson_uuid: str,
        /,
        *,
        ip_address: str = "127.0.0.1",
    ) -> None: ...

    async def archive_lesson(
        self,
        first_arg: AsyncConnectionProtocol | UserRow,
        second_arg: UserRow | str,
        third_arg: str | None = None,
        /,
        *,
        ip_address: str = "127.0.0.1",
    ) -> None:
        """Soft-archive a saved lesson enforcing Object-Level Authorization (`403` on IDOR)."""
        if isinstance(first_arg, UserRow):
            if not isinstance(second_arg, str):
                raise TypeError("Expected (actor, lesson_uuid) arguments.")
            pool = self._require_pool()
            async with pool.transaction() as conn:
                await self._archive_lesson_on_conn(
                    conn, first_arg, second_arg, ip_address=ip_address
                )
            return

        if not isinstance(second_arg, UserRow) or not isinstance(third_arg, str):
            raise TypeError("Expected (conn, actor, lesson_uuid) arguments.")
        await self._archive_lesson_on_conn(
            first_arg, second_arg, third_arg, ip_address=ip_address
        )

    async def _archive_lesson_on_conn(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        *,
        ip_address: str = "127.0.0.1",
    ) -> None:
        uuid_bytes = _parse_lesson_uuid(lesson_uuid)
        row = await self._generations_repo.get_by_uuid(conn, uuid_bytes)
        if row is None or row.is_archived:
            raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' was not found.")
        _assert_lesson_access(actor, row)

        archived = await self._generations_repo.archive_generation(
            conn,
            generation_id=row.id,
            user_id=row.user_id,
        )
        if not archived:
            raise ResourceNotFoundError(f"Lesson '{lesson_uuid}' was not found.")

        for key in [k for k in self._artifact_bytes_cache if k[0] == row.id]:
            self._artifact_bytes_cache.pop(key, None)

        await self._telemetry_repo.insert_audit_event(
            conn,
            actor_user_id=actor.id,
            target_user_id=row.user_id if row.user_id != actor.id else None,
            event_type="LESSON_ARCHIVED",
            ip_address=ip_address,
            metadata={"generation_uuid": row.uuid_str},
        )

    @overload
    async def export_lesson(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        fmt: str,
        /,
        *,
        file_path: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[ArtifactRow, bytes]: ...

    @overload
    async def export_lesson(
        self,
        actor: UserRow,
        lesson_uuid: str,
        fmt: str,
        /,
        *,
        file_path: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[ArtifactRow, bytes]: ...

    async def export_lesson(
        self,
        first_arg: AsyncConnectionProtocol | UserRow,
        second_arg: UserRow | str,
        third_arg: str,
        fourth_arg: str | None = None,
        /,
        *,
        file_path: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[ArtifactRow, bytes]:
        """Export a saved lesson to `fmt` and record SHA-256 artifact metadata (`403` on IDOR)."""
        if isinstance(first_arg, UserRow):
            if not isinstance(second_arg, str):
                raise TypeError("Expected (actor, lesson_uuid, fmt) arguments.")
            pool = self._require_pool()
            async with pool.transaction() as conn:
                return await self._export_lesson_on_conn(
                    conn,
                    first_arg,
                    second_arg,
                    third_arg,
                    file_path=file_path,
                    ip_address=ip_address,
                )

        if not isinstance(second_arg, UserRow) or not isinstance(fourth_arg, str):
            raise TypeError("Expected (conn, actor, lesson_uuid, fmt) arguments.")
        return await self._export_lesson_on_conn(
            first_arg,
            second_arg,
            third_arg,
            fourth_arg,
            file_path=file_path,
            ip_address=ip_address,
        )

    async def _export_lesson_on_conn(
        self,
        conn: AsyncConnectionProtocol,
        actor: UserRow,
        lesson_uuid: str,
        fmt: str,
        *,
        file_path: str | None = None,
        ip_address: str = "127.0.0.1",
    ) -> tuple[ArtifactRow, bytes]:
        format_export_filename("resource", fmt)
        row, content = await self._get_lesson_on_conn(conn, actor, lesson_uuid)
        content_digest = hashlib.sha256(row.content_json.encode("utf-8")).digest()

        existing_artifact = await self._artifacts_repo.get_by_generation_and_format(
            conn, row.id, fmt
        )
        cached_entry = self._artifact_bytes_cache.get((row.id, fmt))
        if (
            existing_artifact is not None
            and cached_entry is not None
            and cached_entry[0] == content_digest
        ):
            artifact_bytes = cached_entry[1]
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=row.user_id if row.user_id != actor.id else None,
                event_type="LESSON_EXPORTED",
                ip_address=ip_address,
                metadata={
                    "generation_uuid": row.uuid_str,
                    "artifact_uuid": existing_artifact.uuid_str,
                    "format": fmt,
                    "byte_size": len(artifact_bytes),
                    "cache_hit": True,
                },
            )
            return (existing_artifact, artifact_bytes)

        if (
            existing_artifact is not None
            and existing_artifact.file_path
            and Path(existing_artifact.file_path).is_file()
        ):
            disk_bytes = Path(existing_artifact.file_path).read_bytes()
            if hashlib.sha256(disk_bytes).digest() == existing_artifact.sha256_checksum:
                self._artifact_bytes_cache[(row.id, fmt)] = (content_digest, disk_bytes)
                await self._telemetry_repo.insert_audit_event(
                    conn,
                    actor_user_id=actor.id,
                    target_user_id=row.user_id if row.user_id != actor.id else None,
                    event_type="LESSON_EXPORTED",
                    ip_address=ip_address,
                    metadata={
                        "generation_uuid": row.uuid_str,
                        "artifact_uuid": existing_artifact.uuid_str,
                        "format": fmt,
                        "byte_size": len(disk_bytes),
                        "cache_hit": True,
                    },
                )
                return (existing_artifact, disk_bytes)

        try:
            artifact_bytes = await self._exporter_fn(content, fmt)
        except TimeoutError as exc:
            raise UpstreamGenerationError(
                "Document export timed out. Please try again with a smaller resource.",
                status_code=503,
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise UpstreamGenerationError(
                f"Failed to compile .{fmt} artifact.",
                status_code=502,
            ) from exc

        sha256_checksum = hashlib.sha256(artifact_bytes).digest()
        self._artifact_bytes_cache[(row.id, fmt)] = (content_digest, artifact_bytes)

        artifact_row = await self._artifacts_repo.upsert_artifact(
            conn,
            generation_id=row.id,
            user_id=row.user_id,
            fmt=fmt,
            file_path=file_path,
            sha256_checksum=sha256_checksum,
            byte_size=len(artifact_bytes),
        )
        await self._telemetry_repo.insert_audit_event(
            conn,
            actor_user_id=actor.id,
            target_user_id=row.user_id if row.user_id != actor.id else None,
            event_type="LESSON_EXPORTED",
            ip_address=ip_address,
            metadata={
                "generation_uuid": row.uuid_str,
                "artifact_uuid": artifact_row.uuid_str,
                "format": fmt,
                "byte_size": len(artifact_bytes),
                "cache_hit": False,
            },
        )
        return (artifact_row, artifact_bytes)

    async def export_ad_hoc(
        self,
        actor: UserRow,
        fmt: str,
        content: GeneratedContent,
        ip_address: str = "127.0.0.1",
    ) -> tuple[str, str, bytes]:
        """Compile an ad-hoc `GeneratedContent` payload to `.docx`, `.pptx`, or `.pdf` (`POST /api/export/{fmt}`).

        Returns `(filename, media_type, artifact_bytes)`.
        """
        filename, media_type = format_export_filename(content.title, fmt)
        try:
            artifact_bytes = await self._exporter_fn(content, fmt)
        except TimeoutError as exc:
            raise UpstreamGenerationError(
                "Document export timed out. Please try again with a smaller resource.",
                status_code=503,
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise UpstreamGenerationError(
                f"Failed to compile .{fmt} artifact.",
                status_code=502,
            ) from exc

        if self._pool is not None:
            async with self._pool.transaction() as conn:
                await self._telemetry_repo.insert_audit_event(
                    conn,
                    actor_user_id=actor.id,
                    target_user_id=None,
                    event_type="LESSON_EXPORTED",
                    ip_address=ip_address,
                    metadata={
                        "format": fmt,
                        "title": content.title,
                        "byte_size": len(artifact_bytes),
                        "ad_hoc": True,
                    },
                )
        return filename, media_type, artifact_bytes

    async def purge_expired_idempotency_keys(self) -> int:
        """Delete expired idempotency_keys rows via the configured connection pool."""
        if self._pool is None:
            return 0
        async with self._pool.transaction() as conn:
            return await self._idempotency_repo.delete_expired(conn, datetime.now(UTC))


__all__ = [
    "CACHE_WINDOW_MINUTES",
    "IDEMPOTENCY_TTL_HOURS",
    "QUOTA_WINDOW_HOURS",
    "ArtifactRow",
    "AsyncExporterCallable",
    "AsyncGeneratorCallable",
    "GenerationRow",
    "KeysetPage",
    "LessonService",
    "compute_generation_request_hash",
    "estimate_generation_cost_usd",
    "format_export_filename",
]
