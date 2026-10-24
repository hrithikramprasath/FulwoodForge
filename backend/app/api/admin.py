"""Admin Control Center router (/api/admin)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_admin_service, get_client_ip, require_admin_user
from app.schemas.admin import (
    AdminOverviewMetricsDTO,
    AdminPasswordResetResponse,
    AdminUserUpdateRequest,
    AuditEventDTO,
    InviteCodeCreateRequest,
    InviteCodeDTO,
    SystemSettingsDTO,
    SystemSettingsUpdateRequest,
    TokenLedgerEntryDTO,
)
from app.schemas.auth import AuthenticatedUserDTO, UserStatus
from app.services.admin_service import AdminService
from app.services.auth_service import UserRow

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminUserPageResponse(BaseModel):
    """Keyset-paginated page of AuthenticatedUserDTO records."""

    model_config = ConfigDict(extra="forbid")

    items: list[AuthenticatedUserDTO]
    next_cursor: str | None = None
    has_more: bool


class InviteCodePageResponse(BaseModel):
    """Keyset-paginated page of InviteCodeDTO records."""

    model_config = ConfigDict(extra="forbid")

    items: list[InviteCodeDTO]
    next_cursor: str | None = None
    has_more: bool


class AuditEventPageResponse(BaseModel):
    """Keyset-paginated page of AuditEventDTO records."""

    model_config = ConfigDict(extra="forbid")

    items: list[AuditEventDTO]
    next_cursor: str | None = None
    has_more: bool


class TokenLedgerPageResponse(BaseModel):
    """Keyset-paginated page of TokenLedgerEntryDTO records."""

    model_config = ConfigDict(extra="forbid")

    items: list[TokenLedgerEntryDTO]
    next_cursor: str | None = None
    has_more: bool


class AdminSessionRevokeResponse(BaseModel):
    """Response payload for POST /api/admin/users/{user_uuid}/revoke-sessions."""

    model_config = ConfigDict(extra="forbid")

    user_uuid: str
    sessions_revoked: int = Field(ge=0)


@router.get("/overview", response_model=AdminOverviewMetricsDTO)
async def get_admin_overview(
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
) -> AdminOverviewMetricsDTO:
    """Return aggregated KPIs for the Admin Control Center overview tab."""
    return await admin_service.get_overview_metrics(admin)


@router.get("/users", response_model=AdminUserPageResponse)
async def list_users(
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    status: Annotated[UserStatus | None, Query()] = None,
) -> AdminUserPageResponse:
    """Return a keyset-paginated list of registered user accounts."""
    page = await admin_service.list_users(
        admin,
        cursor=cursor,
        limit=limit,
        status=status,
    )
    return AdminUserPageResponse(
        items=list(page.items),
        next_cursor=page.next_cursor,
        has_more=page.next_cursor is not None,
    )


@router.patch("/users/{user_uuid}", response_model=AuthenticatedUserDTO)
async def update_user(
    user_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    payload: AdminUserUpdateRequest,
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> AuthenticatedUserDTO:
    """Update a user's status, role, or hourly generation quota."""
    return await admin_service.update_user(
        admin,
        user_uuid,
        payload,
        ip_address=client_ip,
    )


@router.post(
    "/users/{user_uuid}/reset-password", response_model=AdminPasswordResetResponse
)
async def reset_user_password(
    user_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> AdminPasswordResetResponse:
    """Issue a one-time temporary password, set must_change_password=True, and revoke sessions."""
    return await admin_service.reset_user_password(
        admin,
        user_uuid,
        ip_address=client_ip,
    )


@router.post(
    "/users/{user_uuid}/revoke-sessions", response_model=AdminSessionRevokeResponse
)
async def revoke_user_sessions(
    user_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> AdminSessionRevokeResponse:
    """Revoke all active sessions for the specified user."""
    revoked = await admin_service.revoke_user_sessions(
        admin,
        user_uuid,
        ip_address=client_ip,
    )
    return AdminSessionRevokeResponse(user_uuid=user_uuid, sessions_revoked=revoked)


@router.get("/invite-codes", response_model=InviteCodePageResponse)
async def list_invite_codes(
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> InviteCodePageResponse:
    """Return a keyset-paginated list of invite codes."""
    page = await admin_service.list_invite_codes(
        admin,
        cursor=cursor,
        limit=limit,
    )
    return InviteCodePageResponse(
        items=list(page.items),
        next_cursor=page.next_cursor,
        has_more=page.next_cursor is not None,
    )


@router.post(
    "/invite-codes",
    response_model=InviteCodeDTO,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite_code(
    payload: InviteCodeCreateRequest,
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> InviteCodeDTO:
    """Create a new invite code for INVITE_ONLY registration."""
    return await admin_service.create_invite_code(
        admin,
        payload,
        ip_address=client_ip,
    )


@router.delete("/invite-codes/{invite_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_invite_code(
    invite_uuid: Annotated[str, Path(min_length=32, max_length=36)],
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> Response:
    """Deactivate an invite code and return 204 No Content."""
    await admin_service.deactivate_invite_code(
        admin,
        invite_uuid,
        ip_address=client_ip,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings", response_model=SystemSettingsDTO)
async def get_system_settings(
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
) -> SystemSettingsDTO:
    """Return global system settings."""
    return await admin_service.get_system_settings(admin)


@router.put("/settings", response_model=SystemSettingsDTO)
async def update_system_settings(
    payload: SystemSettingsUpdateRequest,
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> SystemSettingsDTO:
    """Update global system settings (registration mode, quotas, maintenance mode, subjects)."""
    return await admin_service.update_system_settings(
        admin,
        payload,
        ip_address=client_ip,
    )


@router.get("/audit-events", response_model=AuditEventPageResponse)
async def list_audit_events(
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    actor_user_uuid: Annotated[str | None, Query(max_length=36)] = None,
) -> AuditEventPageResponse:
    """Return a keyset-paginated list of immutable audit events."""
    page = await admin_service.list_audit_events(
        admin,
        cursor=cursor,
        limit=limit,
        event_type=event_type,
        actor_user_uuid=actor_user_uuid,
    )
    return AuditEventPageResponse(
        items=list(page.items),
        next_cursor=page.next_cursor,
        has_more=page.next_cursor is not None,
    )


@router.get("/token-ledger", response_model=TokenLedgerPageResponse)
async def list_token_ledger(
    admin: Annotated[UserRow, Depends(require_admin_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    user_uuid: Annotated[str | None, Query(max_length=36)] = None,
) -> TokenLedgerPageResponse:
    """Return a keyset-paginated list of Gemini token and cost ledger rows."""
    page = await admin_service.list_token_ledger(
        admin,
        cursor=cursor,
        limit=limit,
        user_uuid=user_uuid,
    )
    return TokenLedgerPageResponse(
        items=list(page.items),
        next_cursor=page.next_cursor,
        has_more=page.next_cursor is not None,
    )
