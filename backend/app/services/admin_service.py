"""Admin Control Center, Account Lifecycle, Invite Code & Telemetry Service (`REQ-ADMIN-01`..`02`).

Unidirectional Dependency Law:
- Imports ONLY from `app.core`, `app.db`, `app.repositories`, `app.schemas`,
  `app.services.auth_service`, `app.services.errors`, and Python standard library.
- ZERO imports from `fastapi`, `starlette`, `app.api`, or `app.main`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import string
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.core.config import AppConfig
from app.core.uuid_v7 import uuid7_str_to_bytes
from app.db.errors import UniqueConstraintViolationError
from app.db.pool import AsyncDatabasePool
from app.repositories.base import KeysetCursor, KeysetPage, decode_keyset_cursor
from app.repositories.invite_codes_repo import InviteCodesRepository
from app.repositories.sessions_repo import SessionsRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.repositories.users_repo import UserRow, UsersRepository
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
from app.schemas.problem_details import InvalidParam
from app.services.auth_service import hash_password
from app.services.errors import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ServiceError,
)

logger = logging.getLogger(__name__)

_TEMP_PASSWORD_ALPHABET: str = (
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%&*"
)
_INVITE_CODE_ALPHABET: str = string.ascii_uppercase + string.digits


def _generate_temporary_password(length: int = 16) -> str:
    """Generate a high-entropy 16-character one-time temporary password (`REQ-ADMIN-02.1`)."""
    clamped_len = max(12, min(length, 64))
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(clamped_len))


def _generate_invite_code() -> str:
    """Generate a readable uppercase invite code (`FF-XXXX-XXXX`)."""
    part1 = "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(4))
    part2 = "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(4))
    return f"FF-{part1}-{part2}"


def _parse_uuid_or_404(raw_uuid: str, resource_label: str = "Resource") -> bytes:
    try:
        return uuid7_str_to_bytes(raw_uuid)
    except ValueError as exc:
        raise ResourceNotFoundError(
            f"{resource_label} '{raw_uuid}' was not found."
        ) from exc


def _decode_cursor_or_422(cursor: str | None) -> KeysetCursor | None:
    if not cursor:
        return None
    try:
        return decode_keyset_cursor(cursor)
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


class AdminService:
    """Transport-agnostic service for `/admin` account recovery, RBAC, invites, settings, and telemetry."""

    def __init__(
        self,
        pool: AsyncDatabasePool,
        config: AppConfig,
        *,
        users_repo: UsersRepository | None = None,
        sessions_repo: SessionsRepository | None = None,
        invite_repo: InviteCodesRepository | None = None,
        settings_repo: SystemSettingsRepository | None = None,
        telemetry_repo: TelemetryRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pool = pool
        self._config = config
        self._users_repo = users_repo or UsersRepository()
        self._sessions_repo = sessions_repo or SessionsRepository()
        self._invite_repo = invite_repo or InviteCodesRepository()
        self._settings_repo = settings_repo or SystemSettingsRepository()
        self._telemetry_repo = telemetry_repo or TelemetryRepository()
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _require_admin(actor: UserRow) -> None:
        """Enforce that `actor` holds an active `ADMIN` role (`REQ-ADMIN-02`)."""
        if actor.role != "ADMIN" or actor.status != "ACTIVE":
            raise PermissionDeniedError(
                "Administrator privileges are required to perform this action."
            )

    async def ensure_bootstrap_admin(self) -> UserRow | None:
        """Initialize system settings and seed the initial `ADMIN` account on startup if zero admins exist (`REQ-ADMIN-01`)."""
        async with self._pool.transaction() as conn:
            settings = await self._settings_repo.get_or_init_settings(conn)
            admin_count = await self._users_repo.count_admins(conn)
            if admin_count > 0:
                return None
            password_hash = await asyncio.to_thread(
                hash_password, self._config.bootstrap_admin_password
            )
            admin_row = await self._users_repo.create_user(
                conn,
                username=self._config.bootstrap_admin_username,
                display_name=self._config.bootstrap_admin_username,
                password_hash=password_hash,
                role="ADMIN",
                status="ACTIVE",
                must_change_password=False,
                hourly_generation_limit=max(100, settings.default_hourly_quota),
            )
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=admin_row.id,
                target_user_id=admin_row.id,
                event_type="BOOTSTRAP_ADMIN_CREATED",
                ip_address="127.0.0.1",
                metadata={"username": admin_row.username},
            )

        logger.info(
            "Bootstrap ADMIN account created: username=%s",
            admin_row.username,
        )
        return admin_row

    async def reset_user_password(
        self,
        actor: UserRow,
        target_user_uuid: str,
        ip_address: str = "127.0.0.1",
    ) -> AdminPasswordResetResponse:
        """Issue a one-time temporary password, set `must_change_password=1`, and revoke all active sessions."""
        self._require_admin(actor)
        target_uuid_bytes = _parse_uuid_or_404(target_user_uuid, "User")
        now_utc = self._clock()
        temp_password = _generate_temporary_password(16)
        temp_hash = await asyncio.to_thread(hash_password, temp_password)

        async with self._pool.transaction() as conn:
            target_user = await self._users_repo.get_by_uuid(conn, target_uuid_bytes)
            if target_user is None:
                raise ResourceNotFoundError(f"User '{target_user_uuid}' was not found.")

            await self._users_repo.set_temporary_password(
                conn, target_user.id, temp_hash
            )
            sessions_revoked = await self._sessions_repo.revoke_all_for_user(
                conn, target_user.id, now_utc
            )
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=target_user.id,
                event_type="ADMIN_PASSWORD_RESET",
                ip_address=ip_address,
                metadata={
                    "target_username": target_user.username,
                    "sessions_revoked": sessions_revoked,
                },
            )

        logger.info(
            "Admin %s reset password for user %s (sessions_revoked=%d)",
            actor.username,
            target_user.username,
            sessions_revoked,
        )
        return AdminPasswordResetResponse(
            user_uuid=target_user.uuid_str,
            username=target_user.username,
            temporary_password=temp_password,
            must_change_password=True,
            sessions_revoked=sessions_revoked,
        )

    async def update_user(
        self,
        actor: UserRow,
        target_user_uuid: str,
        req: AdminUserUpdateRequest,
        ip_address: str = "127.0.0.1",
    ) -> AuthenticatedUserDTO:
        """Update user `role`, `status`, or `hourly_generation_limit`, guarding the last active admin."""
        self._require_admin(actor)
        target_uuid_bytes = _parse_uuid_or_404(target_user_uuid, "User")
        now_utc = self._clock()

        async with self._pool.transaction() as conn:
            target_user = await self._users_repo.get_by_uuid(conn, target_uuid_bytes)
            if target_user is None:
                raise ResourceNotFoundError(f"User '{target_user_uuid}' was not found.")

            demoting_admin = (
                target_user.role == "ADMIN"
                and req.role is not None
                and req.role != "ADMIN"
            )
            deactivating_admin = (
                target_user.role == "ADMIN"
                and req.status is not None
                and req.status != "ACTIVE"
            )
            if demoting_admin or deactivating_admin:
                admin_count = await self._users_repo.count_admins(conn)
                if admin_count <= 1:
                    raise ConflictError(
                        "Cannot demote or suspend the last remaining administrator account."
                    )

            updated_user = await self._users_repo.update_user_admin(
                conn, target_user.id, req
            )
            if updated_user is None:
                updated_user = replace(
                    target_user,
                    role=req.role if req.role is not None else target_user.role,
                    status=req.status if req.status is not None else target_user.status,
                    hourly_generation_limit=(
                        req.hourly_generation_limit
                        if req.hourly_generation_limit is not None
                        else target_user.hourly_generation_limit
                    ),
                )

            sessions_revoked = 0
            if req.status == "SUSPENDED":
                sessions_revoked = await self._sessions_repo.revoke_all_for_user(
                    conn, target_user.id, now_utc
                )

            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=target_user.id,
                event_type="ADMIN_USER_UPDATED",
                ip_address=ip_address,
                metadata={
                    "target_username": target_user.username,
                    "role": updated_user.role,
                    "status": updated_user.status,
                    "hourly_generation_limit": updated_user.hourly_generation_limit,
                    "sessions_revoked": sessions_revoked,
                },
            )

        return updated_user.to_dto()

    async def revoke_user_sessions(
        self,
        actor: UserRow,
        target_user_uuid: str,
        ip_address: str = "127.0.0.1",
    ) -> int:
        """Revoke all active sessions for `target_user_uuid` and record `ADMIN_SESSIONS_REVOKED`."""
        self._require_admin(actor)
        target_uuid_bytes = _parse_uuid_or_404(target_user_uuid, "User")
        now_utc = self._clock()

        async with self._pool.transaction() as conn:
            target_user = await self._users_repo.get_by_uuid(conn, target_uuid_bytes)
            if target_user is None:
                raise ResourceNotFoundError(f"User '{target_user_uuid}' was not found.")

            revoked_count = await self._sessions_repo.revoke_all_for_user(
                conn, target_user.id, now_utc
            )
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=target_user.id,
                event_type="ADMIN_SESSIONS_REVOKED",
                ip_address=ip_address,
                metadata={
                    "target_username": target_user.username,
                    "sessions_revoked": revoked_count,
                },
            )

        return revoked_count

    async def create_invite_code(
        self,
        actor: UserRow,
        req: InviteCodeCreateRequest,
        ip_address: str = "127.0.0.1",
    ) -> InviteCodeDTO:
        """Create a new invite code with configurable `max_uses` and `expires_in_hours`."""
        self._require_admin(actor)
        now_utc = self._clock()
        resolved_code = (req.code or _generate_invite_code()).strip().upper()
        expires_at = (
            now_utc + timedelta(hours=req.expires_in_hours)
            if req.expires_in_hours is not None
            else None
        )

        async with self._pool.transaction() as conn:
            try:
                dto = await self._invite_repo.create_invite_code(
                    conn,
                    code=resolved_code,
                    max_uses=req.max_uses,
                    expires_at=expires_at,
                    created_by_user_id=actor.id,
                )
            except UniqueConstraintViolationError as exc:
                raise ConflictError(
                    f"Invite code '{resolved_code}' already exists."
                ) from exc

            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=None,
                event_type="INVITE_CODE_CREATED",
                ip_address=ip_address,
                metadata={
                    "code": dto.code,
                    "max_uses": dto.max_uses,
                    "expires_at": dto.expires_at.isoformat()
                    if dto.expires_at
                    else None,
                },
            )

        return dto

    async def deactivate_invite_code(
        self,
        actor: UserRow,
        invite_uuid: str,
        ip_address: str = "127.0.0.1",
    ) -> bool:
        """Deactivate an invite code by UUIDv7 and record `INVITE_CODE_DEACTIVATED`."""
        self._require_admin(actor)
        invite_uuid_bytes = _parse_uuid_or_404(invite_uuid, "Invite code")

        async with self._pool.transaction() as conn:
            deactivated = await self._invite_repo.deactivate_by_uuid(
                conn, invite_uuid_bytes
            )
            if not deactivated:
                raise ResourceNotFoundError(
                    f"Active invite code '{invite_uuid}' was not found."
                )

            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=None,
                event_type="INVITE_CODE_DEACTIVATED",
                ip_address=ip_address,
                metadata={"invite_uuid": invite_uuid},
            )

        return True

    async def list_invite_codes(
        self,
        actor: UserRow,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> KeysetPage[InviteCodeDTO]:
        """List invite codes using composite keyset pagination."""
        self._require_admin(actor)
        decoded_cursor = _decode_cursor_or_422(cursor)
        async with self._pool.connection() as conn:
            return await self._invite_repo.list_invite_codes_keyset(
                conn, cursor=decoded_cursor, limit=limit
            )

    async def get_system_settings(self, actor: UserRow) -> SystemSettingsDTO:
        """Return the current platform `SystemSettingsDTO`."""
        self._require_admin(actor)
        async with self._pool.connection() as conn:
            return await self._settings_repo.get_or_init_settings(conn)

    async def update_system_settings(
        self,
        actor: UserRow,
        req: SystemSettingsUpdateRequest,
        ip_address: str = "127.0.0.1",
    ) -> SystemSettingsDTO:
        """Update platform visibility, registration mode, default quota, and maintenance toggle."""
        self._require_admin(actor)
        async with self._pool.transaction() as conn:
            updated = await self._settings_repo.update_settings(
                conn, req, updated_by_user_id=actor.id
            )
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor.id,
                target_user_id=None,
                event_type="SYSTEM_SETTINGS_UPDATED",
                ip_address=ip_address,
                metadata={
                    "registration_mode": updated.registration_mode,
                    "default_hourly_quota": updated.default_hourly_quota,
                    "maintenance_mode": updated.maintenance_mode,
                    "allowed_subjects": list(updated.allowed_subjects),
                    "allowed_task_types": list(updated.allowed_task_types),
                },
            )
        return updated

    async def get_overview_metrics(self, actor: UserRow) -> AdminOverviewMetricsDTO:
        """Compute `/admin` overview telemetry metrics (`REQ-ADMIN-02`)."""
        self._require_admin(actor)
        now_utc = self._clock()
        async with self._pool.connection() as conn:
            return await self._telemetry_repo.get_admin_overview_metrics(
                conn, now_utc=now_utc
            )

    async def list_users(
        self,
        actor: UserRow,
        *,
        cursor: str | None = None,
        limit: int = 50,
        status: UserStatus | None = None,
    ) -> KeysetPage[AuthenticatedUserDTO]:
        """List users ordered by `(created_at DESC, id DESC)` using keyset pagination."""
        self._require_admin(actor)
        decoded_cursor = _decode_cursor_or_422(cursor)
        async with self._pool.connection() as conn:
            page = await self._users_repo.list_users_keyset(
                conn,
                cursor=decoded_cursor,
                limit=limit,
                status=status,
            )
            return KeysetPage(
                items=[u.to_dto() for u in page.items],
                next_cursor=page.next_cursor,
            )

    async def list_audit_events(
        self,
        actor: UserRow,
        *,
        cursor: str | None = None,
        limit: int = 50,
        event_type: str | None = None,
        actor_user_id: int | None = None,
        actor_user_uuid: str | None = None,
    ) -> KeysetPage[AuditEventDTO]:
        """List immutable audit events using composite keyset pagination."""
        self._require_admin(actor)
        decoded_cursor = _decode_cursor_or_422(cursor)
        async with self._pool.connection() as conn:
            resolved_actor_id = actor_user_id
            if resolved_actor_id is None and actor_user_uuid:
                uuid_bytes = _parse_uuid_or_404(actor_user_uuid, "User")
                target_user = await self._users_repo.get_by_uuid(conn, uuid_bytes)
                if target_user is None:
                    raise ResourceNotFoundError(
                        f"User '{actor_user_uuid}' was not found."
                    )
                resolved_actor_id = target_user.id
            return await self._telemetry_repo.list_audit_events_keyset(
                conn,
                cursor=decoded_cursor,
                limit=limit,
                event_type=event_type,
                actor_user_id=resolved_actor_id,
            )

    async def list_token_ledger(
        self,
        actor: UserRow,
        *,
        cursor: str | None = None,
        limit: int = 50,
        user_id: int | None = None,
        user_uuid: str | None = None,
    ) -> KeysetPage[TokenLedgerEntryDTO]:
        """List immutable Gemini token & cost ledger rows using composite keyset pagination."""
        self._require_admin(actor)
        decoded_cursor = _decode_cursor_or_422(cursor)
        async with self._pool.connection() as conn:
            resolved_user_id = user_id
            if resolved_user_id is None and user_uuid:
                uuid_bytes = _parse_uuid_or_404(user_uuid, "User")
                target_user = await self._users_repo.get_by_uuid(conn, uuid_bytes)
                if target_user is None:
                    raise ResourceNotFoundError(f"User '{user_uuid}' was not found.")
                resolved_user_id = target_user.id
            return await self._telemetry_repo.list_token_ledger_keyset(
                conn,
                cursor=decoded_cursor,
                limit=limit,
                user_id=resolved_user_id,
            )
