"""Generation and ad-hoc export endpoints (/api/generate and /api/export/{fmt})."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import Field

from app.api.deps import get_client_ip, get_lesson_service, require_active_user
from app.api.lessons import (
    acquire_export_slot,
    enforce_user_export_rate_limit,
    validate_export_format,
)
from app.schemas.content import (
    AnswerSection,
    GeneratedContent,
    GenerateRequest,
    Lesson,
)
from app.schemas.problem_details import InvalidParam
from app.services.auth_service import UserRow
from app.services.errors import ServiceError
from app.services.lesson_service import LessonService

router = APIRouter()


class ExportRequest(GeneratedContent):
    """Strict-bounded GeneratedContent payload for ad-hoc /api/export/{fmt} requests."""

    lessons: list[Lesson] = Field(min_length=1, max_length=20)
    teacher_answer_key: list[AnswerSection] | None = Field(
        default=None,
        max_length=50,
    )
    title: str = Field(min_length=1, max_length=300)
    subject: str = Field(max_length=100)
    year_group: str = Field(max_length=50)
    exam_board: str | None = Field(default=None, max_length=50)
    spec_reference: str | None = Field(default=None, max_length=200)
    audience_note: str = Field(max_length=1000)
    difficulty_note: str = Field(max_length=1000)


@router.post("/generate", response_model=GeneratedContent)
async def generate(
    request_body: GenerateRequest,
    response: Response,
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GeneratedContent:
    """Generate a curriculum-aligned lesson resource via LessonService."""
    normalized_idem: str | None = None
    if idempotency_key is not None:
        normalized_idem = idempotency_key.strip()
        if not (16 <= len(normalized_idem) <= 64):
            raise ServiceError(
                "Idempotency-Key header must be between 16 and 64 characters.",
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
    generation_uuid, content, cache_hit = await lesson_service.generate_lesson(
        actor,
        request_body,
        normalized_idem,
        client_ip,
    )
    response.headers["X-Generation-UUID"] = generation_uuid
    response.headers["X-Cache-Hit"] = "true" if cache_hit else "false"
    return content


@router.post("/export/{fmt}")
async def export_resource(
    fmt: str,
    export_req: ExportRequest,
    actor: Annotated[UserRow, Depends(require_active_user)],
    lesson_service: Annotated[LessonService, Depends(get_lesson_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> Response:
    """Export an ad-hoc GeneratedContent payload to DOCX, PPTX, or PDF."""
    validated_fmt = validate_export_format(fmt)
    enforce_user_export_rate_limit(actor.id)

    async with acquire_export_slot(timeout_seconds=15.0):
        filename, media_type, data = await lesson_service.export_ad_hoc(
            actor,
            validated_fmt,
            export_req,
            client_ip,
        )

    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
