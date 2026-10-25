"""Transport-Agnostic Domain Exceptions with IETF RFC 9457 Metadata (`REQ-API-01`).

Unidirectional Dependency Law:
- Imports ONLY from `app.schemas.problem_details` and Python standard library.
- ZERO imports from `fastapi`, `starlette`, `app.api`, or `app.main`.
"""

from __future__ import annotations

from app.schemas.problem_details import InvalidParam, ProblemDetails


class ServiceError(Exception):
    """Base transport-agnostic domain exception mapping to RFC 9457 `application/problem+json`."""

    problem_type: str = "urn:fulwoodforge:error:service-error"
    title: str = "Service Error"
    status_code: int = 500

    def __init__(
        self,
        detail: str,
        *,
        problem_type: str | None = None,
        title: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
        invalid_params: list[InvalidParam] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail: str = detail
        if problem_type is not None:
            self.problem_type = problem_type
        if title is not None:
            self.title = title
        if status_code is not None:
            self.status_code = status_code
        self.headers: dict[str, str] | None = dict(headers) if headers else None
        self.invalid_params: list[InvalidParam] | None = invalid_params

    def to_problem_details(
        self,
        *,
        instance: str,
        request_id: str | None = None,
    ) -> ProblemDetails:
        """Convert this domain exception into a canonical RFC 9457 `ProblemDetails` model."""
        return ProblemDetails(
            type=self.problem_type,
            title=self.title,
            status=self.status_code,
            detail=self.detail,
            instance=instance,
            invalid_params=self.invalid_params,
            request_id=request_id,
        )


class AuthenticationError(ServiceError):
    """Raised when credentials or session tokens are missing, invalid, expired, or revoked (`401`)."""

    problem_type = "urn:fulwoodforge:error:unauthenticated"
    title = "Authentication Required"
    status_code = 401

    def __init__(
        self,
        detail: str = "Authentication credentials are invalid or have expired.",
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail, headers=headers)


class PermissionDeniedError(ServiceError):
    """Raised when an authenticated actor lacks RBAC role or object-level ownership (`403`)."""

    problem_type = "urn:fulwoodforge:error:forbidden"
    title = "Permission Denied"
    status_code = 403

    def __init__(
        self,
        detail: str = "You do not have permission to access or modify this resource.",
    ) -> None:
        super().__init__(detail)


class PasswordChangeRequiredError(ServiceError):
    """Raised when `user.must_change_password` is True (`REQ-AUTH-06`, `403`)."""

    problem_type = "urn:fulwoodforge:error:password-change-required"
    title = "Password Change Required"
    status_code = 403

    def __init__(
        self,
        detail: str = "You must change your temporary password before accessing this resource.",
    ) -> None:
        super().__init__(detail)


class AccountPendingApprovalError(ServiceError):
    """Raised when an account is in `PENDING` status awaiting administrator approval (`403`)."""

    problem_type = "urn:fulwoodforge:error:account-pending"
    title = "Account Pending Approval"
    status_code = 403

    def __init__(
        self,
        detail: str = "Your account is pending administrator approval.",
    ) -> None:
        super().__init__(detail)


class AccountSuspendedError(ServiceError):
    """Raised when an account has been `SUSPENDED` by an administrator (`403`)."""

    problem_type = "urn:fulwoodforge:error:account-suspended"
    title = "Account Suspended"
    status_code = 403

    def __init__(
        self,
        detail: str = "Your account has been suspended by an administrator.",
    ) -> None:
        super().__init__(detail)


class ResourceNotFoundError(ServiceError):
    """Raised when a requested entity UUID does not exist (`404`)."""

    problem_type = "urn:fulwoodforge:error:not-found"
    title = "Resource Not Found"
    status_code = 404

    def __init__(
        self,
        detail: str = "The requested resource was not found.",
    ) -> None:
        super().__init__(detail)


class ConflictError(ServiceError):
    """Raised on duplicate username, duplicate invite code, or concurrent idempotency conflict (`409`)."""

    problem_type = "urn:fulwoodforge:error:conflict"
    title = "Resource Conflict"
    status_code = 409

    def __init__(
        self,
        detail: str = "The request conflicts with the current state of the resource.",
    ) -> None:
        super().__init__(detail)


class InvalidInviteCodeError(ServiceError):
    """Raised when an invite code is missing, invalid, deactivated, expired, or exhausted (`422`)."""

    problem_type = "urn:fulwoodforge:error:invalid-invite-code"
    title = "Invalid Invite Code"
    status_code = 422

    def __init__(
        self,
        detail: str = "A valid, active invite code is required to register.",
    ) -> None:
        super().__init__(detail)


class RateLimitExceededError(ServiceError):
    """Raised when a per-user or login brute-force rate limit is exceeded (`429`)."""

    problem_type = "urn:fulwoodforge:error:rate-limit-exceeded"
    title = "Rate Limit Exceeded"
    status_code = 429

    def __init__(
        self,
        detail: str = "Rate limit exceeded. Please wait before retrying.",
        *,
        retry_after_seconds: int = 60,
    ) -> None:
        clamped_retry = max(1, int(retry_after_seconds))
        self.retry_after_seconds: int = clamped_retry
        super().__init__(
            detail,
            headers={"Retry-After": str(clamped_retry)},
        )


class UpstreamGenerationError(ServiceError):
    """Raised when upstream AI generation or document rendering fails after retries (`502` / `503`)."""

    problem_type = "urn:fulwoodforge:error:upstream-generation-failed"
    title = "Upstream Generation Failed"
    status_code = 502

    def __init__(
        self,
        detail: str = "The upstream AI generation service is temporarily unavailable.",
        *,
        status_code: int = 502,
    ) -> None:
        resolved_status = status_code if status_code in (422, 502, 503) else 502
        super().__init__(detail, status_code=resolved_status)


class MaintenanceModeError(ServiceError):
    """Raised when non-admin users attempt generation while maintenance mode is active (`503`)."""

    problem_type = "urn:fulwoodforge:error:maintenance-mode"
    title = "Maintenance Mode Active"
    status_code = 503

    def __init__(
        self,
        detail: str = "Fulwood Forge is currently in maintenance mode. Please try again later.",
    ) -> None:
        super().__init__(detail)
