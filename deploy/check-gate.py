#!/usr/bin/env python3
"""Verify the live Fulwood Forge stack's native health, auth status, and RFC 9457 auth gate.

Designed to be called from deploy/maintenance.sh, deploy/monthly-rebuild.sh,
and systemd ExecStartPost: exits 0 only if every check passes, non-zero otherwise.

The three invariants checked are:

  1. GET /health/ready returns 200 OK with {"status": "ready"} — proves
     Uvicorn, the MySQL 8.4 connection pool, and Pandoc are healthy.
  2. GET /api/auth/status returns 200 OK with {"authenticated": false, ...}
     when called without an ff_session cookie.
  3. POST /api/generate without an ff_session cookie is rejected with 401
     and Content-Type: application/problem+json (RFC 9457).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:4180"
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class CheckResult:
    """Result of a single smoke-check invariant."""

    name: str
    passed: bool
    detail: str


Fetcher = Callable[[str, str], tuple[int, dict[str, str], bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib from following 3xx responses."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _validate_http_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Check URL must be http(s) with a host, got {url!r}")


def default_fetcher(url: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    """Perform an HTTP request without following redirects; return (status, headers, body)."""
    _validate_http_url(url)
    payload = b"{}" if method.upper() == "POST" else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)  # nosec B310
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
            status: int = resp.getcode()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            body: bytes = resp.read(4096)
            return status, resp_headers, body
    except urllib.error.HTTPError as err:
        err_headers = {k.lower(): v for k, v in err.headers.items()}
        err_body = err.read(4096) if err.fp else b""
        return err.code, err_headers, err_body


def check_health_ready(base_url: str, fetch: Fetcher) -> CheckResult:
    """GET /health/ready must return 200 OK with {"status": "ready"}."""
    url = f"{base_url.rstrip('/')}/health/ready"
    try:
        status, _, body = fetch(url, "GET")
    except (OSError, ValueError, RuntimeError) as exc:
        return CheckResult("health_ready", False, f"request failed: {exc}")

    if status != 200:
        return CheckResult(
            "health_ready",
            False,
            f"expected 200 from /health/ready, got {status}",
        )
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return CheckResult("health_ready", False, "response body is not valid JSON")

    if not isinstance(parsed, dict) or parsed.get("status") != "ready":
        return CheckResult(
            "health_ready",
            False,
            f"expected status='ready' in /health/ready JSON, got {parsed!r}",
        )
    return CheckResult("health_ready", True, "200 OK (status=ready)")


def check_auth_status(base_url: str, fetch: Fetcher) -> CheckResult:
    """GET /api/auth/status must return 200 OK with authenticated=false when unauthenticated."""
    url = f"{base_url.rstrip('/')}/api/auth/status"
    try:
        status, _, body = fetch(url, "GET")
    except (OSError, ValueError, RuntimeError) as exc:
        return CheckResult("auth_status", False, f"request failed: {exc}")

    if status != 200:
        return CheckResult(
            "auth_status",
            False,
            f"expected 200 from /api/auth/status, got {status}",
        )
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return CheckResult("auth_status", False, "response body is not valid JSON")

    if not isinstance(parsed, dict) or parsed.get("authenticated") is not False:
        return CheckResult(
            "auth_status",
            False,
            f"expected authenticated=false from /api/auth/status, got {parsed!r}",
        )
    return CheckResult("auth_status", True, "200 OK (authenticated=false)")


def check_api_rejects_unauthenticated(base_url: str, fetch: Fetcher) -> CheckResult:
    """POST /api/generate without ff_session must return 401 application/problem+json."""
    url = f"{base_url.rstrip('/')}/api/generate"
    try:
        status, headers, body = fetch(url, "POST")
    except (OSError, ValueError, RuntimeError) as exc:
        return CheckResult("api_unauth_rejected", False, f"request failed: {exc}")

    if status != 401:
        return CheckResult(
            "api_unauth_rejected",
            False,
            f"expected 401 on unauthenticated POST /api/generate, got {status}",
        )
    content_type = headers.get("content-type", "").lower()
    if not content_type.startswith("application/problem+json"):
        return CheckResult(
            "api_unauth_rejected",
            False,
            f"expected Content-Type application/problem+json, got {content_type!r}",
        )
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return CheckResult(
            "api_unauth_rejected", False, "401 response body is not valid JSON"
        )

    if not isinstance(parsed, dict) or parsed.get("status") != 401:
        return CheckResult(
            "api_unauth_rejected",
            False,
            f"expected RFC 9457 ProblemDetails with status=401, got {parsed!r}",
        )
    return CheckResult(
        "api_unauth_rejected", True, "status 401 application/problem+json"
    )


def run_all(base_url: str, fetch: Fetcher = default_fetcher) -> list[CheckResult]:
    """Run all three gate checks against base_url."""
    return [
        check_health_ready(base_url, fetch),
        check_auth_status(base_url, fetch),
        check_api_rejects_unauthenticated(base_url, fetch),
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for deploy/check-gate.py."""
    args = argv if argv is not None else sys.argv[1:]
    base_url = args[0] if args else DEFAULT_BASE_URL
    try:
        _validate_http_url(base_url)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    results = run_all(base_url)
    failed = [r for r in results if not r.passed]
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        print(f"[{tag}] {r.name}: {r.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
