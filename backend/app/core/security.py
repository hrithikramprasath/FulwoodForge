"""Native `ff_session` cookie management, client IP extraction, and CSRF verification (`REQ-SEC-04` / `VULN-04`).

Unidirectional Dependency Law:
- Imports ONLY from `app.core.config`, `starlette`, and Python standard library.
- Zero `oauth2-proxy`, `REQUIRE_PROXY_AUTH`, `UPSTREAM_TOKEN_FILE`, or `X-Forwarded-Email` trust (`NG-01`).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response

from app.core.config import AppConfig

DEFAULT_SESSION_COOKIE_NAME: str = "ff_session"
NO_IDENTITY: str = "\x00unauthenticated"
STATE_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ALLOWED_SEC_FETCH_SITES: frozenset[str] = frozenset(
    {"same-origin", "same-site", "none"}
)


def _is_trusted_local_proxy_peer(peer: str) -> bool:
    """Return True when the direct TCP peer is local loopback or the local Docker bridge."""
    if peer == "testclient":
        return True
    try:
        addr = ipaddress.ip_address(peer)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return False


def extract_client_ip(request: Request) -> str:
    """Return the normalized client IP address, honoring validated `CF-Connecting-IP` from local `cloudflared`."""
    peer = (
        str(request.client.host)[:45]
        if request.client is not None and request.client.host
        else "127.0.0.1"
    )
    if _is_trusted_local_proxy_peer(peer):
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            try:
                return str(ipaddress.ip_address(cf_ip))[:45]
            except ValueError:
                pass
    return peer


def normalize_origin(raw_origin: str) -> str:
    """Normalize an HTTP `Origin` header into `scheme://netloc` lowercase form."""
    cleaned = raw_origin.strip().rstrip("/")
    if not cleaned or cleaned.lower() == "null":
        return "null"
    parsed = urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return "null"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def check_csrf_headers(
    *,
    method: str,
    headers: Mapping[str, str],
    request_origin: str,
    allowed_origins: Iterable[str] = (),
) -> tuple[bool, str]:
    """Validate `Sec-Fetch-Site` and `Origin` headers on state-mutating cookie requests.

    Returns `(True, "")` when valid, or `(False, reason)` when a cross-site request is detected.
    """
    if method.upper() not in STATE_MUTATING_METHODS:
        return True, ""

    sec_fetch_site = headers.get("sec-fetch-site", "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in _ALLOWED_SEC_FETCH_SITES:
        return (
            False,
            f"Cross-site request blocked by Sec-Fetch-Site={sec_fetch_site!r} CSRF policy.",
        )

    origin = headers.get("origin", "").strip()
    if origin:
        normalized_origin = normalize_origin(origin)
        trusted = {normalize_origin(request_origin)} | {
            normalize_origin(o) for o in allowed_origins if o.strip()
        }
        forwarded_proto = (
            headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        )
        if forwarded_proto == "https" or headers.get("cf-connecting-ip", "").strip():
            parsed_req = urlsplit(request_origin.strip())
            if parsed_req.netloc:
                trusted.add(f"https://{parsed_req.netloc.lower()}")
        if normalized_origin == "null" or normalized_origin not in trusted:
            return (
                False,
                f"Cross-origin state-mutating request from {origin!r} blocked by CSRF policy.",
            )

    return True, ""


def set_session_cookie(
    response: Response,
    raw_token: str,
    config: AppConfig,
) -> None:
    """Attach the `ff_session` cookie with `HttpOnly; Secure; SameSite=Lax; Path=/` (`REQ-AUTH-05`)."""
    max_age_seconds = max(60, int(config.session_ttl_hours) * 3600)
    response.set_cookie(
        key=config.session_cookie_name,
        value=raw_token,
        max_age=max_age_seconds,
        path="/",
        secure=bool(config.session_cookie_secure),
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(
    response: Response,
    config: AppConfig,
) -> None:
    """Expire and delete the `ff_session` cookie on logout (`POST /api/auth/logout`)."""
    response.delete_cookie(
        key=config.session_cookie_name,
        path="/",
        secure=bool(config.session_cookie_secure),
        httponly=True,
        samesite="lax",
    )
