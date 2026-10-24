"""Boundary-guard regressions for `app.schemas.content` (migrated from an embedded main block)."""

from __future__ import annotations

from typing import Any

import pytest
from app.schemas.content import _MAX_STR, GeneratedContent, TeacherRequest
from pydantic import BaseModel, ValidationError

_OK_LESSON: dict[str, Any] = {
    "title": "L",
    "lesson_focus": "f",
    "objectives": ["o"],
}
_OK_CONTENT: dict[str, Any] = {
    "title": "T",
    "task_type": "worksheet",
    "subject": "Maths",
    "year_group": "9",
    "audience_note": "a",
    "difficulty_note": "d",
    "overview": {"summary": "s", "objectives": ["o"], "assessment_focus": "a"},
    "lessons": [_OK_LESSON],
}
_OK_REQUEST: dict[str, Any] = {
    "task_type": "worksheet",
    "subject": "Maths",
    "year_group": "9",
    "topic": "photosynthesis",
    "ability": "mixed",
}


def _assert_rejects(model: type[BaseModel], payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_normal_content_and_request_pass() -> None:
    GeneratedContent.model_validate(_OK_CONTENT)
    TeacherRequest.model_validate(_OK_REQUEST)


def test_oversized_scalar_field_is_rejected() -> None:
    _assert_rejects(GeneratedContent, {**_OK_CONTENT, "title": "A" * (_MAX_STR + 1)})


def test_oversized_nested_lesson_field_is_rejected() -> None:
    lesson = {**_OK_LESSON, "lesson_focus": "Z" * (_MAX_STR + 1)}
    _assert_rejects(GeneratedContent, {**_OK_CONTENT, "lessons": [lesson]})


def test_oversized_topic_is_rejected_at_input_boundary() -> None:
    _assert_rejects(TeacherRequest, {**_OK_REQUEST, "topic": "A" * 1001})
