"""Centralized IETF RFC 9457 Problem Details exception handlers for FastAPI."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import log_event, request_id_ctx
from app.schemas.problem_details import (
    PROBLEM_JSON_MEDIA_TYPE,
    InvalidParam,
    ProblemDetails,
)
from app.services.errors import ServiceError

logger = logging.getLogger(__name__)

_HTTP_STATUS_MAP: dict[int, tuple[str, str]] = {
    400: ("urn:fulwoodforge:error:bad-request", "Bad Request"),
    401: ("urn:fulwoodforge:error:unauthenticated", "Authentication Required"),
    403: ("urn:fulwoodforge:error:forbidden", "Permission Denied"),
    404: ("urn:fulwoodforge:error:not-found", "Resource Not Found"),
    405: ("urn:fulwoodforge:error:method-not-allowed", "Method Not Allowed"),
    409: ("urn:fulwoodforge:error:conflict", "Resource Conflict"),
    413: ("urn:fulwoodforge:error:payload-too-large", "Payload Too Large"),
    422: ("urn:fulwoodforge:error:validation-failed", "Request Validation Failed"),
    429: ("urn:fulwoodforge:error:rate-limit-exceeded", "Rate Limit Exceeded"),
    502: ("urn:fulwoodforge:error:upstream-failure", "Upstream Generation Failure"),
    503: ("urn:fulwoodforge:error:service-unavailable", "Service Unavailable"),
}


def build_problem_response(
    problem: ProblemDetails,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Serialize a ProblemDetails instance into an application/problem+json response."""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(by_alias=True, exclude_none=True),
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers=headers,
    )


async def service_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Serialize domain ServiceError instances to RFC 9457 ProblemDetails."""
    if not isinstance(exc, ServiceError):
        return await unhandled_exception_handler(request, exc)
    problem = exc.to_problem_details(
        instance=request.url.path,
        request_id=request_id_ctx.get(),
    )
    return build_problem_response(problem, headers=exc.headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Serialize FastAPI RequestValidationError and Pydantic ValidationError to RFC 9457 422."""
    raw_errors = (
        exc.errors()
        if isinstance(exc, (RequestValidationError, ValidationError))
        else []
    )
    invalid_params: list[InvalidParam] = []
    for err in raw_errors:
        loc = err.get("loc", ())
        field_name = ".".join(str(p) for p in loc if p != "body") or "body"
        reason = str(err.get("msg", "Invalid value."))[:512]
        invalid_params.append(
            InvalidParam(
                name=field_name[:128],
                reason=reason,
            )
        )
    problem = ProblemDetails(
        type="urn:fulwoodforge:error:validation-failed",
        title="Request Validation Failed",
        status=422,
        detail="One or more request fields failed validation.",
        instance=request.url.path,
        request_id=request_id_ctx.get(),
        invalid_params=invalid_params or None,
    )
    return build_problem_response(problem)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map standard Starlette/FastAPI HTTPException instances to RFC 9457 ProblemDetails."""
    if not isinstance(exc, (StarletteHTTPException, HTTPException)):
        return await unhandled_exception_handler(request, exc)
    status_code = exc.status_code
    error_type, title = _HTTP_STATUS_MAP.get(
        status_code,
        (f"urn:fulwoodforge:error:http-{status_code}", "HTTP Error"),
    )
    detail = str(exc.detail) if exc.detail else title
    headers = dict(exc.headers) if exc.headers else None
    problem = ProblemDetails(
        type=error_type,
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path,
        request_id=request_id_ctx.get(),
    )
    return build_problem_response(problem, headers=headers)


async def recursion_error_handler(request: Request, _exc: Exception) -> JSONResponse:
    """Convert deeply nested JSON RecursionError into an RFC 9457 422 response."""
    problem = ProblemDetails(
        type="urn:fulwoodforge:error:validation-failed",
        title="Request Validation Failed",
        status=422,
        detail="JSON nesting depth limit exceeded.",
        instance=request.url.path,
        request_id=request_id_ctx.get(),
    )
    return build_problem_response(problem)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unhandled exceptions and return a sanitized RFC 9457 500 response."""
    log_event(
        logger,
        "unhandled_exception",
        f"Unhandled {type(exc).__name__} on {request.method} {request.url.path}",
        level=logging.ERROR,
        path=request.url.path,
        method=request.method,
        status_code=500,
        error_class=type(exc).__name__,
    )
    problem = ProblemDetails(
        type="urn:fulwoodforge:error:internal-server-error",
        title="Internal Server Error",
        status=500,
        detail="An unexpected internal error occurred.",
        instance=request.url.path,
        request_id=request_id_ctx.get(),
    )
    return build_problem_response(problem)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all RFC 9457 exception handlers on the FastAPI application."""
    app.add_exception_handler(ServiceError, service_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RecursionError, recursion_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
