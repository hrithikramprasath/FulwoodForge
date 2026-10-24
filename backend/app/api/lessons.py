"""Saved lesson library CRUD and artifact export router (/api/lessons)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_client_ip, get_lesson_service, require_active_user
from app.core.limits import user_export_limiter
from app.schemas.content import (
    GeneratedContent,
    SubjectLiteral,
    TaskTypeLiteral,
)
from app.schemas.problem_details import InvalidParam
from app.services.auth_service import UserRow
from app.services.errors import RateLimitExceededError, ServiceError
from app.services.lesson_service import GenerationRow, LessonService

router = APIRouter(prefix="/api/lessons", tags=["lessons"])

ExportFormatLiteral = Literal["docx", "pptx", "pdf", "answers-docx", "answers-pdf"]

_ASYNC_EXPORT_SEMAPHORE = asyncio.Semaphore(2)

_EXPORT_MIME_EXTENSION: dict[str, tuple[str, str]] = {
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "answers-docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
    "pdf": ("application/pdf", "pdf"),
    "answers-pdf": ("application/pdf", "pdf"),
}


@asynccontextmanager
async def acquire_export_slot(timeout_seconds: float = 15.0) -> AsyncIterator[None]:
    """Acquire a bounded non-blocking export worker slot or raise 503 ServiceError."""
    try:
        async with asyncio.timeout(timeout_seconds):
            await _ASYNC_EXPORT_SEMAPHORE.acquire()
    except TimeoutError as exc:
        raise ServiceError(
            "Export workers are at capacity. Try again in a moment.",
            problem_type="urn:fulwoodforge:error:service-unavailable",
            title="Service Unavailable",
            status_code=503,
            headers={"Retry-After": "15"},
        ) from exc
    try:
        yield
    finally:
        _ASYNC_EXPORT_SEMAPHORE.release()


def safe_export_filename(title: str, fmt: str, ext: str) -> str:
    """Build an ASCII-safe Content-Disposition filename for exported artifacts."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-")[:60] or "resource"
    suffix = "-ANSWER-KEY" if fmt.startswith("answers-") else ""
    return f"{slug}{suffix}.{ext}"


def validate_export_format(fmt: str) -> ExportFormatLiteral:
    """Validate that fmt is one of the 5 supported export format literals."""
    if fmt not in _EXPORT_MIME_EXTENSION:
        raise ServiceError(
            f"Unsupported export format '{fmt}'.",
            problem_type="urn:fulwoodforge:error:validation-failed",
            title="Request Validation Failed",
            status_code=422,
            invalid_params=[
                InvalidParam(
                    name="fmt",
                    reason="Must be one of: docx, pptx, pdf, answers-docx, answers-pdf.",
                )
            ],
        )
    return cast(ExportFormatLiteral, fmt)


def enforce_user_export_rate_limit(user_id: int) -> None:
    """Enforce the 20/minute per-user export rate limit (REQ-SEC-03)."""
    allowed, retry_after = user_export_limiter.check_and_record(str(user_id))
    if not allowed:
        raise RateLimitExceededError(
            "Export rate limit exceeded (maximum 20 exports per minute).",
            retry_after_seconds=retry_after,
        )


class SavedLessonSummaryDTO(BaseModel):
    """Summary metadata for a saved lesson generation in a teacher's library."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    title: str
    task_type: TaskTypeLiteral
    subject: str
    year_group: str
    tier: str | None = None
    exam_board: str | None = None
    topic: str
    slide_count: int = Field(ge=0)
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class SavedLessonDetailDTO(SavedLessonSummaryDTO):
    """Full saved lesson record including the validated GeneratedContent payload."""

    content: GeneratedContent


class SavedLessonPageResponse(BaseModel):
    """Keyset-paginated page of SavedLessonSummaryDTO items."""

    model_config = ConfigDict(extra="forbid")

    items: list[SavedLessonSummaryDTO]
    next_cursor: str | None = None
    has_more: bool


class SavedLessonUpdateRequest(BaseModel):
    """Request body for inline updates to a saved lesson's title and/or content."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: GeneratedContent | None = None


def _row_to_summary(row: GenerationRow) -> SavedLessonSummaryDTO:
    return SavedLessonSummaryDTO(
        uuid=row.uuid_str,
        title=row.title,
        task_type=row.task_type,
        subject=row.subject,
        year_group=row.year_group,
        tier=row.tier,
        exam_board=row.exam_board,
        topic=row.topic,
        slide_count=row.slide_count,
        is_archived=row.is_archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_detail(
    row: GenerationRow, content: GeneratedContent
) -> SavedLessonDetailDTO:
    return SavedLessonDetailDTO(
        uuid=row.uuid_str,
        title=row.title,
        task_type=row.task_type,
        subject=row.subject,
        year_group=row.year_group,
        tier=row.tier,
        exam_board=row.exam_board,
        topic=row.topic,
        slide_count=row.slide_count,
        is_archived=row.is_archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
        content=content,
    )


@router.get("", response_model=SavedLessonPageResponse)
async def list_lessons(
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    subject: Annotated[SubjectLiteral | None, Query()] = None,
    task_type: Annotated[
        Literal["worksheet", "powerpoint", "booklet"] | None,
        Query(),
    ] = None,
) -> SavedLessonPageResponse:
    """Return a keyset-paginated list of the authenticated teacher's saved lessons."""
    page = await lesson_service.list_lessons(
        actor,
        cursor=cursor,
        limit=limit,
        subject=subject,
        task_type=task_type,
    )
    return SavedLessonPageResponse(
        items=[_row_to_summary(row) for row in page.items],
        next_cursor=page.next_cursor,
        has_more=page.next_cursor is not None,
    )


@router.get("/{lesson_uuid}", response_model=SavedLessonDetailDTO)
async def get_lesson(
    lesson_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
) -> SavedLessonDetailDTO:
    """Fetch a single saved lesson record and its GeneratedContent (enforcing IDOR checks)."""
    row, content = await lesson_service.get_lesson(actor, lesson_uuid)
    return _row_to_detail(row, content)


@router.patch("/{lesson_uuid}", response_model=SavedLessonDetailDTO)
async def update_lesson(
    lesson_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    payload: SavedLessonUpdateRequest,
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> SavedLessonDetailDTO:
    """Update a saved lesson's title and/or edited GeneratedContent sections inline."""
    if payload.title is None and payload.content is None:
        raise ServiceError(
            "At least one of 'title' or 'content' must be provided.",
            problem_type="urn:fulwoodforge:error:validation-failed",
            title="Request Validation Failed",
            status_code=422,
            invalid_params=[
                InvalidParam(
                    name="title",
                    reason="Provide 'title', 'content', or both.",
                )
            ],
        )
    content_to_save = payload.content
    if content_to_save is None:
        _, existing_content = await lesson_service.get_lesson(actor, lesson_uuid)
        content_to_save = (
            existing_content.model_copy(update={"title": payload.title})
            if payload.title is not None
            else existing_content
        )
    row, updated_content = await lesson_service.update_lesson_content(
        actor,
        lesson_uuid,
        content_to_save,
        title=payload.title,
        ip_address=client_ip,
    )
    return _row_to_detail(row, updated_content)


@router.delete("/{lesson_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(
    lesson_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> Response:
    """Archive a saved lesson (enforcing IDOR checks) and return 204 No Content."""
    await lesson_service.archive_lesson(actor, lesson_uuid, ip_address=client_ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{lesson_uuid}/export/{fmt}")
async def export_saved_lesson(
    lesson_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    fmt: str,
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> Response:
    """Export a saved lesson to DOCX, PPTX, or PDF, serving cached artifact bytes when available."""
    validated_fmt = validate_export_format(fmt)
    enforce_user_export_rate_limit(actor.id)

    row, _ = await lesson_service.get_lesson(actor, lesson_uuid)
    async with acquire_export_slot(timeout_seconds=15.0):
        artifact, data = await lesson_service.export_lesson(
            actor,
            lesson_uuid,
            validated_fmt,
            ip_address=client_ip,
        )

    mime, ext = _EXPORT_MIME_EXTENSION[validated_fmt]
    filename = safe_export_filename(row.title, validated_fmt, ext)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-Artifact-UUID": artifact.uuid_str,
        },
    )
