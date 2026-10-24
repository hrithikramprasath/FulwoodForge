"""Admin Control Center, Invite Code, System Settings & Telemetry Schemas.

Covers `REQ-ADMIN-01`..`02` and `REQ-LOG-02`..`03`.
All monetary/token cost fields strictly use `Decimal` quantized to 4 decimal places
(`DECIMAL(19,4) NOT NULL` in MySQL 8.4) and reject `float` inputs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.auth import RegistrationMode, UserRole, UserStatus
from app.schemas.content import SubjectLiteral, TaskTypeLiteral

_FOUR_DP = Decimal("0.0001")


def _quantize_decimal_4dp(value: object) -> Decimal:
    """Reject binary floats and quantize Decimal/string/int values to 4 decimal places."""
    if isinstance(value, float):
        raise PydanticCustomError(
            "float_forbidden",
            "Float values are forbidden for cost/rate accounting; use Decimal or str.",
        )
    if isinstance(value, (Decimal, int, str)):
        return Decimal(value).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
    raise PydanticCustomError(
        "decimal_type_error",
        "Cannot convert value to Decimal(19,4).",
    )


class AdminUserUpdateRequest(BaseModel):
    """Payload for `PATCH /api/admin/users/{uuid}` (`REQ-ADMIN-02`)."""

    model_config = ConfigDict(extra="forbid")

    role: UserRole | None = None
    status: UserStatus | None = None
    hourly_generation_limit: int | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _ensure_at_least_one_field(self) -> AdminUserUpdateRequest:
        if (
            self.role is None
            and self.status is None
            and self.hourly_generation_limit is None
        ):
            raise ValueError("At least one field must be provided")
        return self


class AdminPasswordResetResponse(BaseModel):
    """One-time temporary password response returned by `POST /api/admin/users/{uuid}/reset-password`."""

    model_config = ConfigDict(extra="forbid")

    user_uuid: str
    username: str
    temporary_password: str = Field(min_length=10, max_length=128)
    must_change_password: bool = True
    sessions_revoked: int = Field(ge=0)


class InviteCodeCreateRequest(BaseModel):
    """Payload for `POST /api/admin/invite-codes` (`REQ-AUTH-04`, `REQ-ADMIN-02`)."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, pattern=r"^[A-Z0-9_-]{6,32}$")
    max_uses: int = Field(default=10, ge=1, le=10000)
    expires_in_hours: int | None = Field(default=168, ge=1, le=8760)


class InviteCodeDTO(BaseModel):
    """Invite code record displayed in the Admin Control Center."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    code: str
    max_uses: int = Field(ge=1)
    uses_count: int = Field(ge=0)
    expires_at: datetime | None = None
    is_active: bool
    created_at: datetime


class SystemSettingsUpdateRequest(BaseModel):
    """Payload for `PUT /api/admin/settings` (`REQ-ADMIN-02`)."""

    model_config = ConfigDict(extra="forbid")

    registration_mode: RegistrationMode
    default_hourly_quota: int = Field(ge=1, le=1000)
    maintenance_mode: bool
    allowed_subjects: list[SubjectLiteral] = Field(min_length=1)
    allowed_task_types: list[TaskTypeLiteral] = Field(min_length=1)


class SystemSettingsDTO(SystemSettingsUpdateRequest):
    """Persisted system settings state."""

    updated_at: datetime


class TokenLedgerEntryDTO(BaseModel):
    """Immutable Gemini token & cost accounting row (`REQ-LOG-03`)."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    user_uuid: str
    username: str
    generation_uuid: str | None = None
    model_name: str
    prompt_tokens: int = Field(ge=0)
    candidate_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: Decimal
    latency_ms: int = Field(ge=0)
    cache_hit: bool
    status: Literal["SUCCESS", "CACHE_HIT", "UPSTREAM_ERROR"]
    created_at: datetime

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def _validate_cost_decimal(cls, v: object) -> Decimal:
        return _quantize_decimal_4dp(v)


class AuditEventDTO(BaseModel):
    """Immutable relational audit trail row (`REQ-LOG-02`)."""

    model_config = ConfigDict(extra="forbid")

    uuid: str
    actor_username: str | None = None
    target_username: str | None = None
    event_type: str
    ip_address: str
    metadata: dict[str, Any]
    created_at: datetime


class AdminOverviewMetricsDTO(BaseModel):
    """Top-level telemetry bar metrics for `/admin` (`REQ-ADMIN-02`)."""

    model_config = ConfigDict(extra="forbid")

    total_users: int = Field(ge=0)
    active_users: int = Field(ge=0)
    pending_users: int = Field(ge=0)
    active_sessions: int = Field(ge=0)
    generations_24h: int = Field(ge=0)
    cache_hit_rate_24h: Decimal
    total_tokens_24h: int = Field(ge=0)
    total_cost_usd_24h: Decimal
    cumulative_cost_usd: Decimal

    @field_validator(
        "cache_hit_rate_24h", "total_cost_usd_24h", "cumulative_cost_usd", mode="before"
    )
    @classmethod
    def _validate_decimals(cls, v: object) -> Decimal:
        return _quantize_decimal_4dp(v)
