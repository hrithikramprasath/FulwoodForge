"""Native Username + Password Authentication & Session Schemas (`REQ-AUTH-01`..`06`).

Leaf schema module: imports only from sibling `app.schemas.content` and Python stdlib.
Zero email fields or external OAuth headers are permitted (`NG-01`, `REQ-AUTH-01`).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.content import SubjectLiteral, TaskTypeLiteral

UserRole = Literal["USER", "ADMIN"]
UserStatus = Literal["ACTIVE", "PENDING", "SUSPENDED"]
RegistrationMode = Literal["INVITE_ONLY", "ADMIN_APPROVAL", "OPEN"]

USERNAME_REGEX = r"^[a-zA-Z0-9_.-]{3,32}$"
USERNAME_PATTERN = re.compile(USERNAME_REGEX)


def validate_username_format(raw: str) -> tuple[str, str]:
    """Validate a raw username string and return `(normalized_lowercase, display_name)`.

    Raises `ValueError` if the username contains `@` (email format), whitespace, or
    falls outside `^[a-zA-Z0-9_.-]{3,32}$`.
    """
    cleaned = raw.strip()
    if not USERNAME_PATTERN.match(cleaned):
        raise ValueError(
            "Username must be 3 to 32 characters and contain only letters, numbers, "
            "dots (.), underscores (_), or hyphens (-); email addresses are not permitted."
        )
    return cleaned.lower(), cleaned


class LoginRequest(BaseModel):
    """Credentials posted to `POST /api/auth/login`."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, v: str) -> str:
        normalized, _ = validate_username_format(v)
        return normalized


class RegisterRequest(BaseModel):
    """Account registration payload posted to `POST /api/auth/register`.

    Preserves the user's original casing in `display_name` while normalizing
    `username` to canonical lowercase (`REQ-AUTH-01`).
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(default="", max_length=64)
    password: str = Field(min_length=10, max_length=128)
    invite_code: str | None = Field(default=None, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def _extract_display_and_normalize_username(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw_username = data.get("username")
            if isinstance(raw_username, str):
                normalized, cased = validate_username_format(raw_username)
                explicit_display = data.get("display_name")
                display = (
                    explicit_display.strip()
                    if isinstance(explicit_display, str) and explicit_display.strip()
                    else cased
                )
                return {
                    **data,
                    "username": normalized,
                    "display_name": display,
                }
        return data

    @field_validator("invite_code")
    @classmethod
    def _clean_invite_code(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return v.strip().upper()


class ChangePasswordRequest(BaseModel):
    """Payload posted to `POST /api/auth/change-password` (`REQ-AUTH-06`)."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)

    @model_validator(mode="after")
    def _ensure_password_rotated(self) -> ChangePasswordRequest:
        if self.new_password == self.current_password:
            raise ValueError("New password must differ from the current password.")
        return self


class AuthenticatedUserDTO(BaseModel):
    """Publicly safe representation of an authenticated user (`REQ-AUTH-05`)."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    username: str
    display_name: str
    role: UserRole
    status: UserStatus
    must_change_password: bool
    hourly_generation_limit: int = Field(ge=0)
    created_at: datetime
    last_login_at: datetime | None = None


class AuthStatusResponse(BaseModel):
    """Bootstrapped session and platform visibility state returned by `GET /api/auth/status`."""

    model_config = ConfigDict(extra="forbid")

    authenticated: bool
    user: AuthenticatedUserDTO | None = None
    registration_mode: RegistrationMode
    maintenance_mode: bool
    allowed_subjects: list[SubjectLiteral]
    allowed_task_types: list[TaskTypeLiteral]
