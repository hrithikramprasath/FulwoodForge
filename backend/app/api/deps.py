"""FastAPI dependency injection and native ff_session authentication guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from app.core.config import AppConfig
from app.core.logging import user_id_ctx, username_ctx
from app.core.security import (
    DEFAULT_SESSION_COOKIE_NAME,
    check_csrf_headers,
    extract_client_ip,
)
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService, SessionRow, UserRow
from app.services.errors import (
    AuthenticationError,
    PermissionDeniedError,
    ServiceError,
)
from app.services.lesson_service import LessonService


class DatabaseLifecycleProtocol(Protocol):
    """Minimal database pool lifecycle protocol exposed to the runtime container."""

    async def ping(self) -> bool:
        """Return True if the database pool can execute a health check query."""
        ...

    async def close(self) -> None:
        """Close all pooled connections gracefully."""
        ...


@dataclass(slots=True)
class RuntimeContainer:
    """Application-scoped dependency container stored on app.state.container."""

    config: AppConfig
    pool: DatabaseLifecycleProtocol
    auth_service: AuthService
    admin_service: AdminService
    lesson_service: LessonService


def get_container(request: Request) -> RuntimeContainer:
    """Retrieve the active RuntimeContainer from FastAPI app.state."""
    container: RuntimeContainer | None = getattr(request.app.state, "container", None)
    if container is None:
        raise ServiceError(
            "Application runtime container is not initialized.",
            problem_type="urn:fulwoodforge:error:service-unavailable",
            title="Service Unavailable",
            status_code=503,
        )
    return container


def get_auth_service(request: Request) -> AuthService:
    """Return the configured AuthService instance."""
    return get_container(request).auth_service


def get_admin_service(request: Request) -> AdminService:
    """Return the configured AdminService instance."""
    return get_container(request).admin_service


def get_lesson_service(request: Request) -> LessonService:
    """Return the configured LessonService instance."""
    return get_container(request).lesson_service


def get_client_ip(request: Request) -> str:
    """Return the normalized client IP address for rate limiting and audit logging."""
    return extract_client_ip(request)


def verify_request_csrf(request: Request, *, has_cookie: bool) -> None:
    """Enforce Sec-Fetch-Site and Origin CSRF checks when a session cookie is present."""
    if not has_cookie:
        return
    container: RuntimeContainer | None = getattr(request.app.state, "container", None)
    allowed_origins = (
        container.config.cors_origins
        if container is not None and hasattr(container.config, "cors_origins")
        else ()
    )
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    ok, reason = check_csrf_headers(
        method=request.method,
        headers=request.headers,
        request_origin=request_origin,
        allowed_origins=allowed_origins,
    )
    if not ok:
        raise PermissionDeniedError(reason)


def _extract_session_cookie(request: Request) -> str | None:
    container: RuntimeContainer | None = getattr(request.app.state, "container", None)
    cookie_name = (
        container.config.session_cookie_name
        if container is not None
        else DEFAULT_SESSION_COOKIE_NAME
    )
    return request.cookies.get(cookie_name)


def _bind_actor_context(request: Request, user: UserRow) -> None:
    """Bind the authenticated actor's identity to contextvars and request.state."""
    user_id_ctx.set(user.id)
    username_ctx.set(user.username)
    request.state.user_id = user.id
    request.state.username = user.username


async def require_authenticated_actor(request: Request) -> tuple[UserRow, SessionRow]:
    """Authenticate the caller via the ff_session cookie (allowing password-change-required state)."""
    token = _extract_session_cookie(request)
    verify_request_csrf(request, has_cookie=bool(token))
    if not token:
        raise AuthenticationError("Authentication required. Please sign in.")
    auth_service = get_auth_service(request)
    user, session = await auth_service.authenticate_session(
        token,
        allow_password_change_only=True,
    )
    _bind_actor_context(request, user)
    return user, session


async def require_active_user(request: Request) -> UserRow:
    """Authenticate an ACTIVE user who does not have a pending forced password change."""
    token = _extract_session_cookie(request)
    verify_request_csrf(request, has_cookie=bool(token))
    if not token:
        raise AuthenticationError("Authentication required. Please sign in.")
    auth_service = get_auth_service(request)
    user, _ = await auth_service.authenticate_session(
        token,
        allow_password_change_only=False,
    )
    _bind_actor_context(request, user)
    return user


async def require_admin_user(request: Request) -> UserRow:
    """Authenticate an ACTIVE administrator (role == 'ADMIN')."""
    user = await require_active_user(request)
    if user.role != "ADMIN":
        raise PermissionDeniedError(
            "Administrator privileges are required for this resource."
        )
    return user
