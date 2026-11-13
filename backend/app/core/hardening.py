"""Transport-level hardening, request correlation logging, and safe XML parsing (`REQ-SEC-01`, `REQ-SEC-05`, `REQ-LOG-01`, `REQ-API-01`).

Provides:
1. `SecurityHeadersMiddleware`: Emits strict HTTP response security headers including
   `Content-Security-Policy` (`REQ-SEC-01` / `VULN-01`).
2. `BodySizeLimitMiddleware`: Enforces `MAX_BODY_BYTES` (5 MiB) and body read deadlines,
   returning IETF RFC 9457 `application/problem+json` (`413 Payload Too Large`).
3. `SecurityMiddleware`: Combined ASGI middleware applying both `SecurityHeadersMiddleware`
   and `BodySizeLimitMiddleware`.
4. `RequestContextLogMiddleware`: Extracts or generates an RFC 9562 `UUIDv7` `X-Request-ID`,
   binds context variables, stamps `X-Request-ID` on every response, and emits a single-line
   structured JSON `http_request` log record (`REQ-LOG-01`).
5. `safe_xml_fromstring`: Hardened `lxml.etree` parser (`resolve_entities=False`,
   `no_network=True`, `load_dtd=False`, `huge_tree=False`) that rejects DTD/ENTITY
   declarations (XXE defense).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import time
from typing import Any

from docx.oxml.parser import element_class_lookup
from lxml import etree
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.limits import KeyedSlidingWindow, ip_rate_limiter
from app.core.logging import (
    bind_request_context,
    clear_request_context,
    log_event,
    request_id_ctx,
    user_id_ctx,
    username_ctx,
)
from app.core.security import is_trusted_proxy_peer
from app.core.uuid_v7 import generate_uuid7_str, uuid7_str_to_bytes
from app.schemas.problem_details import PROBLEM_JSON_MEDIA_TYPE, build_problem_details

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    """Positive int from the environment, falling back on anything unusable."""
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


MAX_BODY_BYTES: int = _int_env("MAX_BODY_BYTES", 5 * 1024 * 1024)

STRICT_CSP: str = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)
_CSP: str = STRICT_CSP

_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", STRICT_CSP.encode("ascii")),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (
        b"permissions-policy",
        (
            b"accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            b"magnetometer=(), microphone=(), payment=(), usb=()"
        ),
    ),
    (b"cross-origin-opener-policy", b"same-origin"),
]

_FORBIDDEN_XML_DECLARATION_RE = re.compile(
    rb"<!\s*(?:DOCTYPE|ENTITY)\b", flags=re.IGNORECASE
)

_SAFE_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
    remove_blank_text=True,
)
_SAFE_XML_PARSER.set_element_class_lookup(element_class_lookup)


def safe_xml_fromstring(xml_data: str | bytes) -> Any:
    """Parse an XML or OXML fragment safely with external entities, DTDs, and network access disabled.

    Raises `ValueError` if `<!DOCTYPE` or `<!ENTITY` declarations are present (`REQ-SEC-05`).
    """
    raw_bytes = xml_data.encode("utf-8") if isinstance(xml_data, str) else xml_data
    if _FORBIDDEN_XML_DECLARATION_RE.search(raw_bytes):
        raise ValueError("XML DTD and ENTITY declarations are forbidden (XXE defense).")
    try:
        return etree.fromstring(raw_bytes, parser=_SAFE_XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Malformed XML fragment: {exc}") from exc


async def _send_problem_json(
    send: Send,
    *,
    status: int,
    problem_type: str,
    title: str,
    detail: str,
    instance: str,
    retry_after_seconds: int | None = None,
) -> None:
    """Serialize and send an IETF RFC 9457 `application/problem+json` response directly over ASGI."""
    payload = build_problem_details(
        status=status,
        title=title,
        detail=detail,
        instance=instance,
        problem_type=problem_type,
        request_id=request_id_ctx.get(),
    )
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", PROBLEM_JSON_MEDIA_TYPE.encode("ascii")),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if retry_after_seconds is not None:
        headers.append((b"retry-after", str(int(retry_after_seconds)).encode("ascii")))
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def _resolve_request_id(headers: dict[bytes, bytes]) -> str:
    """Extract `X-Request-ID` if it is a valid RFC 9562 UUIDv7, otherwise generate a fresh UUIDv7."""
    raw = headers.get(b"x-request-id")
    if raw:
        try:
            candidate = raw.decode("ascii").strip()
            uuid7_str_to_bytes(candidate)
            return candidate.lower()
        except (UnicodeDecodeError, ValueError):
            pass
    return generate_uuid7_str()


class SecurityHeadersMiddleware:
    """Pure ASGI middleware stamping strict security headers (`REQ-SEC-01` / `VULN-01`) onto every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                present = {k.lower() for k, _ in raw}
                for k, v in _SECURITY_HEADERS:
                    if k not in present:
                        raw.append((k, v))
                path = str(scope.get("path", ""))
                if path == "/api" or path.startswith("/api/"):
                    if b"cache-control" not in present:
                        raw.append(
                            (
                                b"cache-control",
                                b"no-store, no-cache, must-revalidate, private",
                            )
                        )
                    if b"pragma" not in present:
                        raw.append((b"pragma", b"no-cache"))
                elif path.startswith("/assets/") and b"cache-control" not in present:
                    raw.append(
                        (
                            b"cache-control",
                            b"public, max-age=31536000, immutable",
                        )
                    )
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Pure ASGI middleware enforcing `MAX_BODY_BYTES` and read deadlines with RFC 9457 `413` responses."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int | None = None) -> None:
        self.app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit_bytes = (
            self._max_body_bytes if self._max_body_bytes is not None else MAX_BODY_BYTES
        )
        instance = str(scope.get("path") or "/")
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}

        try:
            declared = int(headers.get(b"content-length", b"0"))
            if declared < 0:
                raise ValueError("Negative Content-Length")
        except ValueError:
            await _send_problem_json(
                send,
                status=400,
                problem_type="urn:fulwoodforge:error:bad-request",
                title="Bad Request",
                detail="Invalid Content-Length header.",
                instance=instance,
            )
            return

        if declared > limit_bytes:
            await _send_problem_json(
                send,
                status=413,
                problem_type="urn:fulwoodforge:error:payload-too-large",
                title="Payload Too Large",
                detail=f"Request body exceeds the maximum allowed size of {limit_bytes} bytes.",
                instance=instance,
            )
            return

        body = bytearray()
        try:
            async with asyncio.timeout(_int_env("BODY_READ_TIMEOUT_SECONDS", 30)):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit_bytes:
                        await _send_problem_json(
                            send,
                            status=413,
                            problem_type="urn:fulwoodforge:error:payload-too-large",
                            title="Payload Too Large",
                            detail=f"Request body exceeds the maximum allowed size of {limit_bytes} bytes.",
                            instance=instance,
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await _send_problem_json(
                send,
                status=413,
                problem_type="urn:fulwoodforge:error:payload-too-large",
                title="Payload Too Large",
                detail="Request body stream exceeded the allowed read deadline.",
                instance=instance,
            )
            return

        consumed = False

        async def bounded_receive() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, bounded_receive, send)


def extract_asgi_client_ip(scope: Scope) -> str:
    """Extract normalized client IP from ASGI scope, honoring CF-Connecting-IP from trusted local proxy peers."""
    client = scope.get("client")
    peer = (
        str(client[0])[:45]
        if client is not None and len(client) > 0 and client[0]
        else "127.0.0.1"
    )
    if is_trusted_proxy_peer(peer):
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        raw_cf = headers.get(b"cf-connecting-ip")
        if raw_cf:
            try:
                candidate = raw_cf.decode("ascii").strip()
                return str(ipaddress.ip_address(candidate))[:45]
            except (UnicodeDecodeError, ValueError):
                pass
    return peer


class IpRateLimitMiddleware:
    """Pure ASGI middleware enforcing sliding-window rate limits per client IP on /api/* routes."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: KeyedSlidingWindow | None = None,
        exempt_paths: tuple[str, ...] = (
            "/health",
            "/health/live",
            "/health/ready",
        ),
    ) -> None:
        self.app = app
        self._limiter = limiter if limiter is not None else ip_rate_limiter
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._limiter.default_limit <= 0:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "/")
        if (
            not (path == "/api" or path.startswith("/api/"))
            or path in self._exempt_paths
        ):
            await self.app(scope, receive, send)
            return

        ip = extract_asgi_client_ip(scope)
        allowed, retry_after = self._limiter.check_and_record(ip)
        if not allowed:
            log_event(
                logger,
                "ip_rate_limit_exceeded",
                "Client IP exceeded sliding-window rate limit",
                level=logging.WARNING,
                client_ip=ip,
                path=path,
                retry_after_seconds=retry_after,
            )
            await _send_problem_json(
                send,
                status=429,
                problem_type="urn:fulwoodforge:error:rate-limit-exceeded",
                title="Rate Limit Exceeded",
                detail="Too many requests from this IP address. Please wait before retrying.",
                instance=path,
                retry_after_seconds=retry_after,
            )
            return

        await self.app(scope, receive, send)


class SecurityMiddleware:
    """Combined ASGI middleware applying `SecurityHeadersMiddleware`, `IpRateLimitMiddleware`, and `BodySizeLimitMiddleware`."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int | None = None,
        ip_limiter: KeyedSlidingWindow | None = None,
    ) -> None:
        self.app = SecurityHeadersMiddleware(
            IpRateLimitMiddleware(
                BodySizeLimitMiddleware(app, max_body_bytes=max_body_bytes),
                limiter=ip_limiter,
            )
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


class RequestContextLogMiddleware:
    """Pure ASGI middleware binding `X-Request-ID` (`UUIDv7`) and emitting single-line JSON request logs (`REQ-LOG-01`)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        req_id = _resolve_request_id(headers)
        bind_request_context(request_id=req_id)

        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = req_id

        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "/")
        status_code = 500
        start_ns = time.perf_counter_ns()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 200))
                raw_headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in raw_headers}
                if b"x-request-id" not in present:
                    raw_headers.append((b"x-request-id", req_id.encode("ascii")))
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter_ns() - start_ns) / 1_000_000.0, 3)
            log_event(
                logger,
                "http_request",
                f"{method} {path} {status_code}",
                request_id=request_id_ctx.get() or req_id,
                user_id=user_id_ctx.get(),
                username=username_ctx.get(),
                path=path,
                method=method,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            clear_request_context()
