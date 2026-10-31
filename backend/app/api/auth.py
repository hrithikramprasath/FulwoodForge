"""Authentication and session lifecycle router (/api/auth)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    RuntimeContainer,
    get_auth_service,
    get_client_ip,
    get_container,
    require_authenticated_actor,
    verify_request_csrf,
)
from app.core.logging import username_ctx
from app.core.security import (
    DEFAULT_SESSION_COOKIE_NAME,
    clear_session_cookie,
    set_session_cookie,
)
from app.schemas.auth import (
    AuthenticatedUserDTO,
    AuthStatusResponse,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
)
from app.services.auth_service import AuthService, SessionRow, UserRow

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _read_session_cookie(request: Request, container: RuntimeContainer) -> str | None:
    cookie_name = container.config.session_cookie_name or DEFAULT_SESSION_COOKIE_NAME
    return request.cookies.get(cookie_name)


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(
    request: Request,
    container: Annotated[RuntimeContainer, Depends(get_container)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthStatusResponse:
    """Return current session authentication status and public system configuration."""
    token = _read_session_cookie(request, container)
    status_resp = await auth_service.get_auth_status(token)
    if status_resp.user is not None:
        username_ctx.set(status_resp.user.username)
        request.state.username = status_resp.user.username
    return status_resp


@router.post(
    "/register",
    response_model=AuthStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    container: Annotated[RuntimeContainer, Depends(get_container)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> AuthStatusResponse:
    """Register a new teacher account according to system_settings.registration_mode."""
    verify_request_csrf(request, has_cookie=True)
    user_dto = await auth_service.register_user(payload, ip_address=client_ip)
    username_ctx.set(user_dto.username)
    request.state.username = user_dto.username

    if user_dto.status == "ACTIVE":
        user_agent = request.headers.get("user-agent", "")[:255]
        raw_token = await auth_service.issue_session_for_user(
            user_dto.uuid,
            ip_address=client_ip,
            user_agent=user_agent,
        )
        set_session_cookie(response, raw_token, container.config)
        response.status_code = status.HTTP_201_CREATED
        return await auth_service.get_auth_status(raw_token)

    base_status = await auth_service.get_auth_status(None)
    response.status_code = status.HTTP_202_ACCEPTED
    return AuthStatusResponse(
        authenticated=False,
        user=user_dto,
        registration_mode=base_status.registration_mode,
        maintenance_mode=base_status.maintenance_mode,
        allowed_subjects=[],
        allowed_task_types=[],
    )


@router.post("/login", response_model=AuthenticatedUserDTO)
async def login_user(
    payload: LoginRequest,
    request: Request,
    response: Response,
    container: Annotated[RuntimeContainer, Depends(get_container)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> AuthenticatedUserDTO:
    """Authenticate username/password credentials and issue an HttpOnly ff_session cookie."""
    verify_request_csrf(request, has_cookie=True)
    user_agent = request.headers.get("user-agent", "")[:255]
    raw_token, user_dto = await auth_service.login_user(
        payload,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    username_ctx.set(user_dto.username)
    request.state.username = user_dto.username
    set_session_cookie(response, raw_token, container.config)
    return user_dto


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_user(
    request: Request,
    container: Annotated[RuntimeContainer, Depends(get_container)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> Response:
    """Revoke the caller's current ff_session token and clear the session cookie."""
    token = _read_session_cookie(request, container)
    verify_request_csrf(request, has_cookie=bool(token))
    if token:
        await auth_service.logout_session(token, ip_address=client_ip)
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookie(resp, container.config)
    return resp


@router.post("/change-password", response_model=AuthenticatedUserDTO)
async def change_password(
    payload: ChangePasswordRequest,
    actor_and_session: Annotated[
        tuple[UserRow, SessionRow], Depends(require_authenticated_actor)
    ],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    client_ip: Annotated[str, Depends(get_client_ip)],
) -> AuthenticatedUserDTO:
    """Change the authenticated user's password and clear must_change_password."""
    actor, session = actor_and_session
    return await auth_service.change_password(
        actor.id,
        session.id,
        payload,
        ip_address=client_ip,
    )
