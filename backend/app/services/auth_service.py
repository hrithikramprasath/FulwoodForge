"""Argon2id Native Authentication & Session Lifecycle Service (`REQ-AUTH-01`..`06`).

Unidirectional Dependency Law:
- Imports ONLY from `app.core`, `app.db`, `app.repositories`, `app.schemas`,
  `app.services.errors`, `argon2`, and Python standard library.
- ZERO imports from `fastapi`, `starlette`, `app.api`, or `app.main`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final

import argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import AppConfig
from app.core.uuid_v7 import uuid7_str_to_bytes
from app.db.errors import UniqueConstraintViolationError
from app.db.pool import AsyncDatabasePool
from app.repositories.idempotency_repo import IdempotencyRepository
from app.repositories.invite_codes_repo import InviteCodesRepository
from app.repositories.sessions_repo import SessionRow, SessionsRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.repositories.users_repo import UserRow, UsersRepository
from app.schemas.auth import (
    AuthenticatedUserDTO,
    AuthStatusResponse,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UserStatus,
)
from app.services.errors import (
    AccountPendingApprovalError,
    AccountSuspendedError,
    AuthenticationError,
    ConflictError,
    InvalidInviteCodeError,
    PasswordChangeRequiredError,
    RateLimitExceededError,
    ResourceNotFoundError,
)

logger = logging.getLogger(__name__)

LOGIN_LOCKOUT_WINDOW_MINUTES: int = 15
LOGIN_MAX_FAILED_ATTEMPTS: int = 5
_MAX_FAILED_LOGIN_ATTEMPTS: Final[int] = LOGIN_MAX_FAILED_ATTEMPTS
_MAX_IP_FAILED_LOGIN_ATTEMPTS: Final[int] = 25
LOGIN_LOCKOUT_RETRY_AFTER_SECONDS: int = 900

_PASSWORD_HASHER = argon2.PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=argon2.Type.ID,
)

# Pre-computed Argon2id dummy hash used when a login username does not exist,
# preventing timing-channel username enumeration (`REQ-AUTH-02`).
_DUMMY_ARGON2_HASH: str = _PASSWORD_HASHER.hash("fulwood-forge-dummy-timing-guard-2026")


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password using Argon2id (`t=3, m=65536, p=1, len=32, salt=16`)."""
    return _PASSWORD_HASHER.hash(plaintext)


def verify_password(password_hash: str, plaintext: str) -> bool:
    """Constant-time Argon2id verification returning `True` on match and `False` otherwise."""
    try:
        return bool(_PASSWORD_HASHER.verify(password_hash, plaintext))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_session_token(raw_token: str) -> bytes:
    """Compute the 32-byte SHA-256 digest of a raw `ff_session` opaque cookie token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).digest()


class AuthService:
    """Transport-agnostic service for registration, login, session validation, and password rotation."""

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
    def hash_password(plaintext: str) -> str:
        """Hash a plaintext password using Argon2id (`REQ-AUTH-02`)."""
        return hash_password(plaintext)

    @staticmethod
    def verify_password(password_hash: str, plaintext: str) -> bool:
        """Verify a plaintext password against an Argon2id hash (`REQ-AUTH-02`)."""
        return verify_password(password_hash, plaintext)

    async def register_user(
        self,
        req: RegisterRequest,
        ip_address: str = "127.0.0.1",
    ) -> AuthenticatedUserDTO:
        """Register a new teacher account enforcing the active `registration_mode` (`REQ-AUTH-04`)."""
        now_utc = self._clock()
        password_hash = await asyncio.to_thread(self.hash_password, req.password)

        async with self._pool.transaction() as conn:
            settings = await self._settings_repo.get_or_init_settings(conn)

            existing = await self._users_repo.get_by_username(conn, req.username)
            if existing is not None:
                raise ConflictError(f"Username '{req.username}' is already registered.")

            initial_status: UserStatus = "ACTIVE"
            used_invite_code: str | None = None

            if settings.registration_mode == "INVITE_ONLY":
                if not req.invite_code:
                    raise InvalidInviteCodeError(
                        "An invite code is required to register while Invite-Only mode is active."
                    )
                invite_row = await self._invite_repo.get_active_for_update(
                    conn, req.invite_code, now_utc
                )
                if invite_row is None:
                    raise InvalidInviteCodeError(
                        "The provided invite code is invalid, expired, or has reached its maximum uses."
                    )
                await self._invite_repo.increment_uses(conn, invite_row.id)
                used_invite_code = invite_row.code
                initial_status = "ACTIVE"
            elif settings.registration_mode == "ADMIN_APPROVAL":
                initial_status = "PENDING"
            else:
                initial_status = "ACTIVE"

            try:
                user_row = await self._users_repo.create_user(
                    conn,
                    username=req.username,
                    display_name=req.display_name,
                    password_hash=password_hash,
                    role="USER",
                    status=initial_status,
                    must_change_password=False,
                    hourly_generation_limit=settings.default_hourly_quota,
                )
            except UniqueConstraintViolationError as exc:
                raise ConflictError(
                    f"Username '{req.username}' is already registered."
                ) from exc

            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=user_row.id,
                target_user_id=user_row.id,
                event_type="USER_REGISTERED",
                ip_address=ip_address,
                metadata={
                    "username": user_row.username,
                    "registration_mode": settings.registration_mode,
                    "status": initial_status,
                    "invite_code": used_invite_code,
                },
            )

        logger.info(
            "User registered: username=%s status=%s mode=%s",
            user_row.username,
            user_row.status,
            settings.registration_mode,
        )
        return user_row.to_dto()

    async def login_user(
        self,
        req: LoginRequest,
        ip_address: str = "127.0.0.1",
        user_agent: str = "",
    ) -> tuple[str, AuthenticatedUserDTO]:
        """Authenticate credentials with 5-attempt / 15-min lockout and issue an opaque `ff_session` token."""
        now_utc = self._clock()
        window_start = now_utc - timedelta(minutes=LOGIN_LOCKOUT_WINDOW_MINUTES)

        async with self._pool.transaction() as conn:
            failed_count = await self._telemetry_repo.count_failed_logins_since(
                conn,
                req.username,
                ip_address,
                window_start,
            )
            ip_failures = await self._telemetry_repo.count_ip_failed_logins_since(
                conn,
                ip_address=ip_address,
                since_utc=window_start,
            )
            if (
                failed_count >= _MAX_FAILED_LOGIN_ATTEMPTS
                or ip_failures >= _MAX_IP_FAILED_LOGIN_ATTEMPTS
            ):
                await self._telemetry_repo.insert_audit_event(
                    conn,
                    actor_user_id=None,
                    target_user_id=None,
                    event_type="LOGIN_RATE_LIMITED",
                    ip_address=ip_address,
                    metadata={
                        "username": req.username,
                        "failed_attempts": failed_count,
                        "ip_failed_attempts": ip_failures,
                    },
                )
                await conn.commit()
                raise RateLimitExceededError(
                    "Too many failed login attempts. Please wait 15 minutes before trying again.",
                    retry_after_seconds=LOGIN_LOCKOUT_RETRY_AFTER_SECONDS,
                )

            user_row = await self._users_repo.get_by_username(conn, req.username)
            if user_row is None:
                await asyncio.to_thread(
                    self.verify_password, _DUMMY_ARGON2_HASH, req.password
                )
                await self._telemetry_repo.insert_audit_event(
                    conn,
                    actor_user_id=None,
                    target_user_id=None,
                    event_type="AUTH_LOGIN_FAILED",
                    ip_address=ip_address,
                    metadata={"username": req.username, "reason": "unknown_user"},
                )
                await conn.commit()
                raise AuthenticationError("Invalid username or password.")

            password_valid = await asyncio.to_thread(
                self.verify_password, user_row.password_hash, req.password
            )
            if not password_valid:
                await self._telemetry_repo.insert_audit_event(
                    conn,
                    actor_user_id=user_row.id,
                    target_user_id=user_row.id,
                    event_type="AUTH_LOGIN_FAILED",
                    ip_address=ip_address,
                    metadata={"username": req.username, "reason": "bad_password"},
                )
                await conn.commit()
                raise AuthenticationError("Invalid username or password.")

            if user_row.status == "PENDING":
                raise AccountPendingApprovalError()
            if user_row.status == "SUSPENDED":
                raise AccountSuspendedError()

            raw_token = secrets.token_urlsafe(32)
            token_sha256 = hash_session_token(raw_token)
            expires_at = now_utc + timedelta(hours=self._config.session_ttl_hours)

            await self._sessions_repo.create_session(
                conn,
                user_id=user_row.id,
                token_sha256=token_sha256,
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=expires_at,
            )
            await self._users_repo.touch_last_login(conn, user_row.id, now_utc)
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=user_row.id,
                target_user_id=user_row.id,
                event_type="AUTH_LOGIN_SUCCESS",
                ip_address=ip_address,
                metadata={"username": user_row.username},
            )

        updated_user = replace(user_row, last_login_at=now_utc)
        logger.info("User logged in: username=%s", user_row.username)
        return raw_token, updated_user.to_dto()

    async def authenticate_session(
        self,
        raw_token: str | None,
        *,
        allow_password_change_only: bool = False,
    ) -> tuple[UserRow, SessionRow]:
        """Validate an `ff_session` cookie token, enforce `must_change_password`, and slide expiry."""
        if raw_token is None or not raw_token.strip():
            raise AuthenticationError("Authentication session cookie is required.")

        now_utc = self._clock()
        token_sha256 = hash_session_token(raw_token)

        async with self._pool.transaction() as conn:
            found = await self._sessions_repo.get_active_session_with_user(
                conn, token_sha256, now_utc
            )
            if found is None:
                raise AuthenticationError(
                    "Session is invalid, expired, or has been revoked."
                )

            session_row, user_row = found
            if session_row.revoked_at is not None or session_row.expires_at <= now_utc:
                raise AuthenticationError("Session has expired or been revoked.")

            if user_row.status == "PENDING":
                raise AccountPendingApprovalError()
            if user_row.status == "SUSPENDED":
                raise AccountSuspendedError()
            if user_row.status != "ACTIVE":
                raise AuthenticationError("User account is not active.")

            if user_row.must_change_password and not allow_password_change_only:
                raise PasswordChangeRequiredError()

            new_expires_at = now_utc + timedelta(hours=self._config.session_ttl_hours)
            await self._sessions_repo.extend_session(
                conn, session_row.id, new_expires_at
            )
            extended_session = replace(session_row, expires_at=new_expires_at)
            return user_row, extended_session

    async def change_password(
        self,
        user_id: int,
        current_session_id: int,
        req: ChangePasswordRequest,
        ip_address: str = "127.0.0.1",
    ) -> AuthenticatedUserDTO:
        """Rotate a user's password, clear `must_change_password`, and record `PASSWORD_CHANGED` (`REQ-AUTH-06`)."""
        async with self._pool.transaction() as conn:
            user_row = await self._users_repo.get_by_id(conn, user_id)
            if user_row is None:
                raise ResourceNotFoundError("User account not found.")

            current_valid = await asyncio.to_thread(
                self.verify_password, user_row.password_hash, req.current_password
            )
            if not current_valid:
                raise AuthenticationError("Current password is incorrect.")

            new_hash = await asyncio.to_thread(self.hash_password, req.new_password)
            await self._users_repo.update_password(conn, user_row.id, new_hash)
            now_utc = self._clock()
            revoked_siblings = await self._sessions_repo.revoke_other_sessions_for_user(
                conn,
                user_id=user_row.id,
                keep_session_id=current_session_id,
                now_utc=now_utc,
            )
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=user_row.id,
                target_user_id=user_row.id,
                event_type="PASSWORD_CHANGED",
                ip_address=ip_address,
                metadata={
                    "username": user_row.username,
                    "session_id": current_session_id,
                    "revoked_sibling_sessions": revoked_siblings,
                },
            )

        updated_user = replace(
            user_row,
            password_hash=new_hash,
            must_change_password=False,
        )
        logger.info("Password changed for user: username=%s", user_row.username)
        return updated_user.to_dto()

    async def logout_session(
        self,
        raw_token: str | None,
        ip_address: str = "127.0.0.1",
    ) -> None:
        """Revoke the session corresponding to `raw_token` and record `AUTH_LOGOUT`."""
        if raw_token is None or not raw_token.strip():
            return

        now_utc = self._clock()
        token_sha256 = hash_session_token(raw_token)

        async with self._pool.transaction() as conn:
            found = await self._sessions_repo.get_active_session_with_user(
                conn, token_sha256, now_utc
            )
            await self._sessions_repo.revoke_session_by_token_hash(
                conn, token_sha256, now_utc
            )
            actor_id = found[1].id if found is not None else None
            username = found[1].username if found is not None else None
            await self._telemetry_repo.insert_audit_event(
                conn,
                actor_user_id=actor_id,
                target_user_id=actor_id,
                event_type="AUTH_LOGOUT",
                ip_address=ip_address,
                metadata={"username": username} if username else {},
            )

    async def get_auth_status(
        self,
        raw_token: str | None,
    ) -> AuthStatusResponse:
        """Return platform bootstrap visibility and current authenticated user state (`GET /api/auth/status`)."""
        user_dto: AuthenticatedUserDTO | None = None
        if raw_token is not None and raw_token.strip():
            try:
                user_row, _ = await self.authenticate_session(
                    raw_token, allow_password_change_only=True
                )
                user_dto = user_row.to_dto()
            except (
                AuthenticationError,
                AccountPendingApprovalError,
                AccountSuspendedError,
            ):
                user_dto = None

        async with self._pool.connection() as conn:
            settings = await self._settings_repo.get_or_init_settings(conn)

        return AuthStatusResponse(
            authenticated=user_dto is not None,
            user=user_dto,
            registration_mode=settings.registration_mode,
            maintenance_mode=settings.maintenance_mode,
            allowed_subjects=settings.allowed_subjects,
            allowed_task_types=settings.allowed_task_types,
        )

    async def issue_session_for_user(
        self,
        user_uuid: str,
        ip_address: str = "127.0.0.1",
        user_agent: str = "",
    ) -> str:
        """Create and persist an `ff_session` token for an active user identified by `user_uuid`."""
        now_utc = self._clock()
        uuid_bytes = uuid7_str_to_bytes(user_uuid)
        raw_token = secrets.token_urlsafe(32)
        token_sha256 = hash_session_token(raw_token)
        expires_at = now_utc + timedelta(hours=self._config.session_ttl_hours)

        async with self._pool.transaction() as conn:
            user_row = await self._users_repo.get_by_uuid(conn, uuid_bytes)
            if user_row is None:
                raise ResourceNotFoundError(f"User '{user_uuid}' was not found.")
            await self._sessions_repo.create_session(
                conn,
                user_id=user_row.id,
                token_sha256=token_sha256,
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=expires_at,
            )
            await self._users_repo.touch_last_login(conn, user_row.id, now_utc)
        return raw_token

    async def purge_expired_state(
        self,
        idempotency_repo: IdempotencyRepository | None = None,
    ) -> int:
        """Delete expired `idempotency_keys` rows during background maintenance."""
        repo = idempotency_repo or IdempotencyRepository()
        now_utc = self._clock()
        async with self._pool.transaction() as conn:
            return await repo.delete_expired(conn, now_utc)


__all__ = [
    "LOGIN_LOCKOUT_RETRY_AFTER_SECONDS",
    "LOGIN_LOCKOUT_WINDOW_MINUTES",
    "LOGIN_MAX_FAILED_ATTEMPTS",
    "AuthService",
    "SessionRow",
    "UserRow",
    "hash_password",
    "hash_session_token",
    "verify_password",
]
