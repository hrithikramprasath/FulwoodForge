"""Wave 6.2 Compliance & Surgical Security Regression Tests.

Validates:
1. HTTP Cache-Control: no-store on /api/* responses (and non-/api/* exclusion).
2. Private LAN IP header spoofing restriction (_is_trusted_local_proxy_peer).
3. Offline breached & contextual username/display_name password screening.
4. Pre-flight UK pupil & staff PII safeguarding DLP scanner (case/delimiter bypass & GCSE false-positive resistance).
5. Immediate sibling session revocation upon password rotation (service + SQL repository parameter binding).
6. IP-wide password spraying protection, rate limiting, and non-retryable 429 semantics.
7. Revision resource URL allowlist (credential/port/backslash parser-differential hardening), prompt canary leak guard, and non-retried 422 safety block across the worker boundary.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from app.core.hardening import (
    SecurityHeadersMiddleware,
    extract_asgi_client_ip,
)
from app.core.limits import (
    KeyedSlidingWindow,
    ip_rate_limiter,
    ip_registration_limiter,
)
from app.core.security import _is_trusted_local_proxy_peer
from app.main import app
from app.repositories.sessions_repo import SessionsRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest
from app.schemas.content import RevisionResource, TeacherRequest
from app.services.auth_service import AuthService
from app.services.errors import (
    AuthenticationError,
    RateLimitExceededError,
    UpstreamGenerationError,
)
from app.services.gemini import (
    _PROMPT_CANARY,
    _generate_once_local_with_usage,
    _parse_worker_response,
    _postprocess,
    generate_with_usage_async,
    sanitise_revision_url,
)
from app.services.retry import is_transient_error
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.types import Scope

from tests.test_wave2_mysql_repositories import RecordingConnection, RecordingCursor
from tests.test_wave4_runtime_api import (
    build_in_memory_runtime,
    make_sample_generated_content,
)


def test_api_responses_include_cache_control_no_store() -> None:
    """Verify /api/* returns Cache-Control: no-store while non-/api/* routes are not forced to no-store."""
    container, _ = build_in_memory_runtime()
    app.state.container = container
    with TestClient(app, base_url="https://testserver") as client:
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        assert (
            resp.headers.get("cache-control")
            == "no-store, no-cache, must-revalidate, private"
        )
        assert resp.headers.get("pragma") == "no-cache"

        live_resp = client.get("/health/live")
        assert live_resp.status_code == 200
        assert (
            live_resp.headers.get("cache-control")
            != "no-store, no-cache, must-revalidate, private"
        )

        asset_resp = client.get("/assets/index-hash.js")
        assert (
            asset_resp.headers.get("cache-control")
            == "public, max-age=31536000, immutable"
        )


def test_auth_status_payload_minimization_and_assets_cache_control() -> None:
    """Verify unauthenticated /api/auth/status returns empty subject/task lists while authenticated returns populated lists."""
    container, _ = build_in_memory_runtime()
    app.state.container = container
    with TestClient(app, base_url="https://testserver") as client:
        # 1. Unauthenticated /api/auth/status
        unauthed_resp = client.get("/api/auth/status")
        assert unauthed_resp.status_code == 200
        assert (
            unauthed_resp.headers.get("cache-control")
            == "no-store, no-cache, must-revalidate, private"
        )
        unauthed_data = unauthed_resp.json()
        assert unauthed_data["authenticated"] is False
        assert unauthed_data["user"] is None
        assert unauthed_data["allowed_subjects"] == []
        assert unauthed_data["allowed_task_types"] == []
        assert "registration_mode" in unauthed_data
        assert "maintenance_mode" in unauthed_data

        # 2. Authenticated /api/auth/status
        login_resp = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "BootstrapAdminPassword!2026",
            },
        )
        assert login_resp.status_code == 200
        authed_resp = client.get("/api/auth/status")
        assert authed_resp.status_code == 200
        authed_data = authed_resp.json()
        assert authed_data["authenticated"] is True
        assert authed_data["user"]["username"] == "admin"
        assert len(authed_data["allowed_subjects"]) > 0
        assert len(authed_data["allowed_task_types"]) > 0


@pytest.mark.asyncio
async def test_security_headers_middleware_immutable_assets_cache_control() -> None:
    """Verify SecurityHeadersMiddleware stamps immutable Cache-Control on /assets/* and no-store on /api/*."""
    captured: list[tuple[bytes, bytes]] = []

    async def dummy_app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def capture_send(message: Any) -> None:
        if message["type"] == "http.response.start":
            captured.extend(message.get("headers", []))

    async def dummy_receive() -> dict[str, Any]:
        return {"type": "http.request"}

    middleware = SecurityHeadersMiddleware(dummy_app)

    # Simulated /assets/index-hash.js
    captured.clear()
    await middleware(
        {"type": "http", "path": "/assets/index-hash.js", "method": "GET"},
        dummy_receive,
        capture_send,
    )
    headers = dict(captured)
    assert headers.get(b"cache-control") == b"public, max-age=31536000, immutable"

    # Simulated /api/auth/status
    captured.clear()
    await middleware(
        {"type": "http", "path": "/api/auth/status", "method": "GET"},
        dummy_receive,
        capture_send,
    )
    headers = dict(captured)
    assert (
        headers.get(b"cache-control") == b"no-store, no-cache, must-revalidate, private"
    )
    assert headers.get(b"pragma") == b"no-cache"


def test_private_lan_ip_cannot_spoof_forwarded_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify private LAN student IPs cannot spoof proxy peer identity unless in TRUSTED_PROXY_CIDRS."""
    monkeypatch.delenv("TRUSTED_PROXY_CIDRS", raising=False)
    assert _is_trusted_local_proxy_peer("192.168.1.50") is False
    assert _is_trusted_local_proxy_peer("10.0.0.42") is False
    assert _is_trusted_local_proxy_peer("127.0.0.1") is True
    assert _is_trusted_local_proxy_peer("::1") is True
    assert _is_trusted_local_proxy_peer("172.18.0.2") is True
    assert _is_trusted_local_proxy_peer("unknown") is False
    assert _is_trusted_local_proxy_peer("") is False
    assert _is_trusted_local_proxy_peer("unix:/var/run/docker.sock") is False

    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.200.0.0/24, invalid-cidr")
    assert _is_trusted_local_proxy_peer("10.200.0.10") is True
    assert _is_trusted_local_proxy_peer("10.0.0.42") is False


def test_breached_and_username_passwords_rejected() -> None:
    """Verify breached passwords, low-entropy repetitions, and username/display_name passwords fail."""
    with pytest.raises(ValidationError, match="known breached password lists"):
        RegisterRequest(
            username="teacher_bob",
            password="password12345",
        )

    with pytest.raises(ValidationError, match="must not contain your username"):
        RegisterRequest(
            username="teacher_bob",
            password="teacher_bob_2026",
        )

    with pytest.raises(
        ValidationError, match="must not contain your username or display name"
    ):
        RegisterRequest(
            username="t_smith",
            display_name="Fulwood Teacher",
            password="MyFulwoodTeacher!2026",
        )

    with pytest.raises(ValidationError, match="known breached password lists"):
        RegisterRequest(
            username="teacher_bob",
            password="aaaaaaaaaa",
        )

    with pytest.raises(ValidationError, match="known breached password lists"):
        ChangePasswordRequest(
            current_password="OldValidPassword!2026",
            new_password="qwerty12345",
        )

    with pytest.raises(ValidationError, match="known breached password lists"):
        ChangePasswordRequest(
            current_password="OldValidPassword!2026",
            new_password="ababababab",
        )


def test_pupil_pii_dlp_blocks_upn_nino_email_and_phone_but_allows_sen_scaffolding() -> (
    None
):
    """Verify DLP scanner blocks pupil/staff PII (including case/hyphen variants) while allowing GCSE STEM/SEN topics."""
    # UK UPN (uppercase, lowercase, and hyphenated)
    for upn_topic in (
        "W801200001001 revision questions",
        "pupil w801200001001 intervention",
        "UPN W801-2000-01001 worksheet",
    ):
        with pytest.raises(ValidationError, match="Unique Pupil Number"):
            TeacherRequest(
                task_type="worksheet",
                subject="Mathematics",
                year_group="9",
                topic=upn_topic,
                ability="mixed",
            )

    # UK NINO (spaced and hyphenated)
    for nino_topic in (
        "Staff record QQ 12 34 56 C worksheet",
        "NINO ab-12-34-56-a record",
    ):
        with pytest.raises(ValidationError, match="National Insurance Number"):
            TeacherRequest(
                task_type="worksheet",
                subject="Mathematics",
                year_group="9",
                topic=nino_topic,
                ability="mixed",
            )

    # Email
    with pytest.raises(ValidationError, match="email address"):
        TeacherRequest(
            task_type="worksheet",
            subject="Mathematics",
            year_group="9",
            topic="Send homework to pupil@school.uk for review",
            ability="mixed",
        )

    # UK phone (spaced, +44, and hyphenated)
    for phone_topic in (
        "Call 07700 900123 for tutoring",
        "Contact +44 7700 900123 after class",
        "Mobile 07700-900-123",
    ):
        with pytest.raises(ValidationError, match="UK phone number"):
            TeacherRequest(
                task_type="worksheet",
                subject="Mathematics",
                year_group="9",
                topic=phone_topic,
                ability="mixed",
            )

    # Educational SEN / ADHD pedagogy, GCSE paper codes, scientific constants, binary, and ISBNs allowed
    for safe_topic in (
        "Osmosis worksheet with SEN scaffolding and ADHD chunked steps",
        "Avogadro constant 6.022 x 10^23 mole calculations for AQA 8462/1H",
        "Two's complement binary addition with 01101001 and 10110100",
        "Edexcel 1MA1/2H quadratic graphs and textbook ISBN 978-0-19-835183-2",
    ):
        valid_req = TeacherRequest(
            task_type="worksheet",
            subject="Biology",
            year_group="10",
            topic=safe_topic,
            ability="mixed",
        )
        assert valid_req.topic == safe_topic


@pytest.mark.asyncio
async def test_password_change_revokes_sibling_sessions() -> None:
    """Create 2 sessions for a user, rotate password in session 1, and assert session 2 is revoked."""
    container, store = build_in_memory_runtime(registration_mode="OPEN")
    auth_service = container.auth_service

    # Register user
    await auth_service.register_user(
        RegisterRequest(username="teacher_sibling", password="InitialPassword!2026")
    )
    user_row = next(u for u in store.users.values() if u.username == "teacher_sibling")

    # Login twice to generate two active sessions
    token1, _ = await auth_service.login_user(
        LoginRequest(username="teacher_sibling", password="InitialPassword!2026"),
        ip_address="127.0.0.1",
    )
    token2, _ = await auth_service.login_user(
        LoginRequest(username="teacher_sibling", password="InitialPassword!2026"),
        ip_address="127.0.0.1",
    )

    authed1, sess1 = await auth_service.authenticate_session(token1)
    authed2, sess2 = await auth_service.authenticate_session(token2)
    assert sess1.id != sess2.id
    assert authed1.id == user_row.id
    assert authed2.id == user_row.id

    # Change password from session 1
    await auth_service.change_password(
        user_id=user_row.id,
        current_session_id=sess1.id,
        req=ChangePasswordRequest(
            current_password="InitialPassword!2026",
            new_password="NewRotatedPassword!2026",
        ),
    )

    # Session 1 remains valid
    _, active_sess1 = await auth_service.authenticate_session(token1)
    assert active_sess1.revoked_at is None

    # Session 2 has been revoked immediately
    with pytest.raises(AuthenticationError):
        await auth_service.authenticate_session(token2)

    # Verify audit event metadata records revoked_sibling_sessions
    last_audit = store.audit_events[-1][3]
    assert last_audit.event_type == "PASSWORD_CHANGED"
    assert last_audit.metadata.get("revoked_sibling_sessions") == 1

    # Verify SQL repository parameter binding order (now_str, user_id, keep_session_id)
    cursor = RecordingCursor(rowcount_queue=[2])
    conn = RecordingConnection(cursor)
    repo = SessionsRepository()
    now_utc = datetime(2026, 10, 25, 14, 0, 0, tzinfo=UTC)
    revoked_count = await repo.revoke_other_sessions_for_user(
        conn, user_id=42, keep_session_id=99, now_utc=now_utc
    )
    assert revoked_count == 2
    sql_query, sql_params = cursor.executed[-1]
    assert "id != %s" in sql_query
    assert sql_params is not None
    assert sql_params[1] == 42
    assert sql_params[2] == 99


@pytest.mark.asyncio
async def test_ip_wide_password_spray_rate_limit() -> None:
    """Simulate 25 failed logins from one IP across different usernames; 26th is rate limited."""
    container, _ = build_in_memory_runtime()
    auth_service = container.auth_service
    spray_ip = "198.51.100.99"

    for i in range(25):
        with pytest.raises(AuthenticationError):
            await auth_service.login_user(
                LoginRequest(
                    username=f"spray_target_{i}", password="WrongPassword!123"
                ),
                ip_address=spray_ip,
            )

    with pytest.raises(RateLimitExceededError) as exc_info:
        await auth_service.login_user(
            LoginRequest(username="spray_target_26", password="WrongPassword!123"),
            ip_address=spray_ip,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert exc_info.value.headers.get("Retry-After") == "900"
    assert is_transient_error(exc_info.value) is False

    # Verify SQL TelemetryRepository.count_ip_failed_logins_since query and binding
    cursor = RecordingCursor(fetchone_queue=[(25,)])
    conn = RecordingConnection(cursor)
    tel_repo = TelemetryRepository()
    count = await tel_repo.count_ip_failed_logins_since(
        conn,
        ip_address=spray_ip,
        since_utc=datetime(2026, 10, 25, 14, 0, 0, tzinfo=UTC),
    )
    assert count == 25
    sql_q, sql_p = cursor.executed[-1]
    assert "WHERE event_type = 'AUTH_LOGIN_FAILED'" in sql_q
    assert sql_p is not None
    assert sql_p[0] == spray_ip


@pytest.mark.asyncio
async def test_sanitise_revision_url_blocks_slopsquatting_and_canary_leak_rejected() -> (
    None
):
    """Verify revision URLs block slopsquatting/credential/backslash tricks, canary leak raises 502, and 422 safety blocks do not retry."""
    for bad_url in (
        "https://evil-squatter.xyz/ worksheet.exe",
        "https://user:pass@www.bbc.co.uk/bitesize",
        "https://evil.com\\@www.bbc.co.uk/bitesize",
        "https://www.bbc.co.uk\\@evil.com/bitesize",
        "https://www.bbc.co.uk:8443/bitesize",
        "http://www.bbc.co.uk/bitesize",
    ):
        assert sanitise_revision_url(bad_url) == "https://www.bbc.co.uk/bitesize"

    assert (
        sanitise_revision_url("https://www.corbettmaths.com/2019/09/02/quadratics/")
        == "https://www.corbettmaths.com/2019/09/02/quadratics/"
    )

    req = TeacherRequest(
        task_type="worksheet",
        subject="Mathematics",
        year_group="10",
        topic="Quadratic equations",
        ability="mixed",
    )

    # Verify _postprocess sanitizes revision URLs inside content
    content_with_rev = make_sample_generated_content()
    content_with_rev.lessons[0].revision_resources = [
        RevisionResource(
            name="Practice",
            url="https://evil-squatter.xyz/scam.html",
            purpose="Practice exam questions.",
        )
    ]
    sanitized = _postprocess(content_with_rev, req)
    assert sanitized.lessons[0].revision_resources is not None
    assert (
        sanitized.lessons[0].revision_resources[0].url
        == "https://www.bbc.co.uk/bitesize"
    )

    # Verify _postprocess rejects prompt canary token leak (including lowercase)
    content_canary = make_sample_generated_content()
    content_canary.lessons[
        0
    ].title = f"Leaked Confidential Token: {_PROMPT_CANARY.lower()} in lesson"
    with pytest.raises(UpstreamGenerationError, match="confidentiality guard") as exc:
        _postprocess(content_canary, req)
    assert exc.value.status_code == 502

    # Verify _generate_once_local_with_usage raises 422 on FinishReason.SAFETY chunk
    class _FakeCandidate:
        finish_reason = "FinishReason.SAFETY"

    class _FakeChunk:
        candidates = (_FakeCandidate(),)
        text = ""
        usage_metadata = None

    class _FakeModels:
        def generate_content_stream(
            self, *, model: str, contents: str, config: object
        ) -> list[_FakeChunk]:
            assert model
            assert contents
            assert config is not None
            return [_FakeChunk()]

    class _FakeClient:
        models = _FakeModels()

    with patch("app.services.gemini.get_client", return_value=_FakeClient()):
        with pytest.raises(
            UpstreamGenerationError, match="classroom safety filter"
        ) as local_safety_exc:
            _generate_once_local_with_usage("prompt", "test-key")
        assert local_safety_exc.value.status_code == 422

    # Verify 422 classroom safety block propagates across worker boundary with ZERO retries
    worker_safety_payload = json.dumps(
        {
            "error": {
                "code": 422,
                "retryable": False,
                "message": "Generation blocked by classroom safety filter.",
            }
        }
    ).encode()
    with pytest.raises(
        UpstreamGenerationError, match="classroom safety filter"
    ) as parse_exc:
        _parse_worker_response(worker_safety_payload)
    assert parse_exc.value.status_code == 422

    sleep_calls: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch(
        "app.services.gemini.run_worker_async",
        return_value=worker_safety_payload,
    ) as mock_worker:
        with pytest.raises(
            UpstreamGenerationError, match="classroom safety filter"
        ) as safety_exc:
            await generate_with_usage_async(
                req, api_key="test-key", sleep_fn=_record_sleep
            )
        assert safety_exc.value.status_code == 422
        assert mock_worker.call_count == 1
        assert sleep_calls == []


def test_ip_rate_limiting_enforces_rfc9457_429_and_retry_after() -> None:
    """Verify exceeding IP rate limit returns RFC 9457 429 problem details with Retry-After and security headers."""
    container, _ = build_in_memory_runtime()
    app.state.container = container
    with (
        patch.object(ip_rate_limiter, "default_limit", 3),
        TestClient(app, base_url="https://testserver") as client,
    ):
        for _ in range(3):
            resp = client.get("/api/auth/status")
            assert resp.status_code == 200

        resp_throttled = client.get("/api/auth/status")
        assert resp_throttled.status_code == 429
        assert resp_throttled.headers.get("content-type") == "application/problem+json"
        assert resp_throttled.headers.get("cache-control") == (
            "no-store, no-cache, must-revalidate, private"
        )
        assert resp_throttled.headers.get("x-content-type-options") == "nosniff"
        assert "content-security-policy" in resp_throttled.headers

        retry_after = resp_throttled.headers.get("retry-after")
        assert retry_after is not None
        assert int(retry_after) >= 1

        body = resp_throttled.json()
        assert body["type"] == "urn:fulwoodforge:error:rate-limit-exceeded"
        assert body["title"] == "Rate Limit Exceeded"
        assert (
            body["detail"]
            == "Too many requests from this IP address. Please wait before retrying."
        )
        assert body["instance"] == "/api/auth/status"
        assert body["status"] == 429
        assert body.get("request_id") is not None


def test_ip_rate_limiting_exempts_health_probes_and_static_assets() -> None:
    """Verify health probes and non-API asset paths remain unthrottled even when client IP has exceeded the limit."""
    container, _ = build_in_memory_runtime()
    app.state.container = container
    with (
        patch.object(ip_rate_limiter, "default_limit", 2),
        TestClient(app, base_url="https://testserver") as client,
    ):
        for _ in range(2):
            assert client.get("/api/auth/status").status_code == 200
        assert client.get("/api/auth/status").status_code == 429

        # Health probes must NOT be throttled
        assert client.get("/health").status_code == 200
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200

        # Static asset routes must NOT be throttled (returns 404 Not Found because assets are not built in tests, never 429)
        asset_resp = client.get("/assets/index-hash.js")
        assert asset_resp.status_code == 404


def test_ip_rate_limiting_isolates_buckets_by_client_ip() -> None:
    """Verify client IPs are throttled independently using trusted proxy CF-Connecting-IP."""
    container, _ = build_in_memory_runtime()
    app.state.container = container
    with (
        patch.object(ip_rate_limiter, "default_limit", 2),
        TestClient(app, base_url="https://testserver") as client,
    ):
        client1_headers = {"CF-Connecting-IP": "198.51.100.10"}
        client2_headers = {"CF-Connecting-IP": "198.51.100.20"}

        # Client 1 uses its quota
        assert (
            client.get("/api/auth/status", headers=client1_headers).status_code == 200
        )
        assert (
            client.get("/api/auth/status", headers=client1_headers).status_code == 200
        )
        assert (
            client.get("/api/auth/status", headers=client1_headers).status_code == 429
        )

        # Client 2 quota is completely unconsumed
        assert (
            client.get("/api/auth/status", headers=client2_headers).status_code == 200
        )
        assert (
            client.get("/api/auth/status", headers=client2_headers).status_code == 200
        )
        assert (
            client.get("/api/auth/status", headers=client2_headers).status_code == 429
        )


def test_extract_asgi_client_ip_spoof_resistance() -> None:
    """Verify extract_asgi_client_ip rejects spoofed headers from untrusted peers and accepts from trusted peers."""
    # Untrusted client attempting to spoof CF-Connecting-IP
    untrusted_scope: Scope = {
        "type": "http",
        "client": ("198.51.100.5", 54321),
        "headers": [(b"cf-connecting-ip", b"1.1.1.1")],
    }
    assert extract_asgi_client_ip(untrusted_scope) == "198.51.100.5"

    # Trusted client (loopback) with valid CF-Connecting-IP
    trusted_scope: Scope = {
        "type": "http",
        "client": ("127.0.0.1", 54321),
        "headers": [(b"cf-connecting-ip", b"203.0.113.77")],
    }
    assert extract_asgi_client_ip(trusted_scope) == "203.0.113.77"

    # Trusted client with malformed header falls back to peer IP
    trusted_malformed_scope: Scope = {
        "type": "http",
        "client": ("127.0.0.1", 54321),
        "headers": [(b"cf-connecting-ip", b"invalid-ip-string")],
    }
    assert extract_asgi_client_ip(trusted_malformed_scope) == "127.0.0.1"

    # Missing client tuple defaults to 127.0.0.1
    no_client_scope: Scope = {
        "type": "http",
        "client": None,
        "headers": [],
    }
    assert extract_asgi_client_ip(no_client_scope) == "127.0.0.1"


def test_ip_rate_limiting_disabled_when_limit_zero_or_negative() -> None:
    """Verify rate limiting is disabled when default_limit <= 0."""
    container, _ = build_in_memory_runtime()
    app.state.container = container
    with (
        patch.object(ip_rate_limiter, "default_limit", 0),
        TestClient(app, base_url="https://testserver") as client,
    ):
        for _ in range(10):
            resp = client.get("/api/auth/status")
            assert resp.status_code == 200


def test_keyed_sliding_window_bounded_eviction() -> None:
    """Verify KeyedSlidingWindow bounds memory growth by purging expired keys when exceeding threshold."""
    now = 10000.0
    limiter = KeyedSlidingWindow(5, 60.0, "test_eviction", clock=lambda: now)

    # Pre-populate 10005 keys with expired timestamps
    for i in range(10005):
        limiter._buckets[f"ip_{i}"] = deque([now - 120.0])  # Expired > 60s ago

    assert len(limiter._buckets) == 10005
    allowed, retry_after = limiter.check_and_record("new_active_ip")
    assert allowed is True
    assert retry_after == 0
    # Expired buckets should have been purged, leaving only the newly added key
    assert len(limiter._buckets) == 1
    assert "new_active_ip" in limiter._buckets


def test_registration_pre_check_avoids_argon2id_hashing_on_invalid_invite_or_duplicate_user() -> (
    None
):
    """Verify register_user never executes CPU/memory-bound Argon2id hashing on missing/invalid invite code or duplicate user."""
    container, _ = build_in_memory_runtime(
        cookie_secure=True, registration_mode="INVITE_ONLY"
    )
    app.state.container = container

    with TestClient(app, base_url="https://testserver") as client:
        # Case 1: Missing invite code in INVITE_ONLY mode
        with patch.object(AuthService, "hash_password") as mock_hash:
            resp = client.post(
                "/api/auth/register",
                json={
                    "username": "teacher_no_invite",
                    "password": "ValidPassword!2026",
                },
            )
            assert resp.status_code == 422
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["type"] == "urn:fulwoodforge:error:invalid-invite-code"
            assert mock_hash.call_count == 0

        # Case 2: Invalid/exhausted invite code in INVITE_ONLY mode
        with patch.object(AuthService, "hash_password") as mock_hash:
            resp = client.post(
                "/api/auth/register",
                json={
                    "username": "teacher_bad_invite",
                    "password": "ValidPassword!2026",
                    "invite_code": "DOES-NOT-EXIST",
                },
            )
            assert resp.status_code == 422
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["type"] == "urn:fulwoodforge:error:invalid-invite-code"
            assert mock_hash.call_count == 0

        # Create valid invite code as admin, then register teacher_one
        admin_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "BootstrapAdminPassword!2026",
            },
        )
        assert admin_login.status_code == 200
        inv_resp = client.post(
            "/api/admin/invite-codes",
            json={
                "code": "FF-PRECHECK-2026",
                "max_uses": 5,
                "expires_in_hours": 24,
            },
        )
        assert inv_resp.status_code == 201

        reg_success = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_unique",
                "password": "ValidPassword!2026",
                "invite_code": "FF-PRECHECK-2026",
            },
        )
        assert reg_success.status_code == 201

        # Case 3: Duplicate username pre-check rejects before hashing
        with patch.object(AuthService, "hash_password") as mock_hash:
            resp = client.post(
                "/api/auth/register",
                json={
                    "username": "teacher_unique",
                    "password": "ValidPassword!2026",
                    "invite_code": "FF-PRECHECK-2026",
                },
            )
            assert resp.status_code == 409
            assert resp.headers["content-type"].startswith("application/problem+json")
            assert resp.json()["type"] == "urn:fulwoodforge:error:conflict"
            assert mock_hash.call_count == 0


def test_registration_per_ip_rate_limiting() -> None:
    """Verify flooding POST /api/auth/register from the same IP returns HTTP 429 with Retry-After header."""
    container, _ = build_in_memory_runtime(cookie_secure=True, registration_mode="OPEN")
    app.state.container = container

    with (
        patch.object(ip_registration_limiter, "default_limit", 3),
        TestClient(app, base_url="https://testserver") as client,
    ):
        target_ip_headers = {"cf-connecting-ip": "203.0.113.88"}
        # 3 requests within quota should succeed
        for i in range(3):
            resp = client.post(
                "/api/auth/register",
                json={
                    "username": f"teacher_flood_{i}",
                    "password": "ValidPassword!2026",
                },
                headers=target_ip_headers,
            )
            assert resp.status_code == 201

        # 4th request from same IP should be blocked with 429
        blocked = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_flood_exceeded",
                "password": "ValidPassword!2026",
            },
            headers=target_ip_headers,
        )
        assert blocked.status_code == 429
        assert blocked.headers["content-type"].startswith("application/problem+json")
        assert blocked.json()["type"] == "urn:fulwoodforge:error:rate-limit-exceeded"
        assert "retry-after" in blocked.headers
        assert int(blocked.headers["retry-after"]) > 0

        # Different IP is not throttled
        diff_ip_headers = {"cf-connecting-ip": "203.0.113.89"}
        diff_resp = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_diff_ip",
                "password": "ValidPassword!2026",
            },
            headers=diff_ip_headers,
        )
        assert diff_resp.status_code == 201


def test_auth_endpoints_csrf_protection_login_and_register() -> None:
    """Verify POST /api/auth/login and POST /api/auth/register reject cross-site and untrusted origin headers with 403."""
    container, _ = build_in_memory_runtime(cookie_secure=True, registration_mode="OPEN")
    app.state.container = container

    with TestClient(app, base_url="https://testserver") as client:
        # 1. Login with untrusted Origin header -> 403
        bad_origin_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "BootstrapAdminPassword!2026",
            },
            headers={"Origin": "https://evil.example.com"},
        )
        assert bad_origin_login.status_code == 403
        assert bad_origin_login.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert bad_origin_login.json()["type"] == "urn:fulwoodforge:error:forbidden"

        # 2. Login with cross-site Sec-Fetch-Site -> 403
        bad_site_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "BootstrapAdminPassword!2026",
            },
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert bad_site_login.status_code == 403
        assert bad_site_login.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert bad_site_login.json()["type"] == "urn:fulwoodforge:error:forbidden"

        # 3. Register with untrusted Origin header -> 403
        bad_origin_reg = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_csrf_test",
                "password": "ValidPassword!2026",
            },
            headers={"Origin": "https://evil.example.com"},
        )
        assert bad_origin_reg.status_code == 403
        assert bad_origin_reg.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert bad_origin_reg.json()["type"] == "urn:fulwoodforge:error:forbidden"

        # 4. Register with cross-site Sec-Fetch-Site -> 403
        bad_site_reg = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_csrf_test2",
                "password": "ValidPassword!2026",
            },
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert bad_site_reg.status_code == 403
        assert bad_site_reg.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert bad_site_reg.json()["type"] == "urn:fulwoodforge:error:forbidden"

        # 5. Same-origin Origin header succeeds on login
        good_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "BootstrapAdminPassword!2026",
            },
            headers={"Origin": "https://testserver"},
        )
        assert good_login.status_code == 200

        # 6. Same-origin Origin header succeeds on register
        good_reg = client.post(
            "/api/auth/register",
            json={
                "username": "teacher_csrf_valid",
                "password": "ValidPassword!2026",
            },
            headers={"Origin": "https://testserver"},
        )
        assert good_reg.status_code == 201
