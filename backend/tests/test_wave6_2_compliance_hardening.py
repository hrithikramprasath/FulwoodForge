"""Wave 6.2 Compliance & Surgical Security Regression Tests.

Validates:
1. HTTP Cache-Control: no-store on /api/* responses.
2. Private LAN IP header spoofing restriction (_is_trusted_local_proxy_peer).
3. Offline breached & contextual username password screening.
4. Pre-flight UK pupil & staff PII safeguarding DLP scanner.
5. Immediate sibling session revocation upon password rotation.
6. IP-wide password spraying protection and rate limiting.
7. Revision resource URL allowlist, prompt canary leak guard, and safety settings.
"""

from __future__ import annotations

import pytest
from app.core.security import _is_trusted_local_proxy_peer
from app.main import app
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest
from app.schemas.content import RevisionResource, TeacherRequest
from app.services.errors import (
    AuthenticationError,
    RateLimitExceededError,
    UpstreamGenerationError,
)
from app.services.gemini import (
    _PROMPT_CANARY,
    _postprocess,
    sanitise_revision_url,
)
from pydantic import ValidationError
from starlette.testclient import TestClient

from tests.test_wave4_runtime_api import (
    build_in_memory_runtime,
    make_sample_generated_content,
)


def test_api_responses_include_cache_control_no_store() -> None:
    """Verify /api/auth/status returns Cache-Control: no-store and Pragma: no-cache."""
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


def test_private_lan_ip_cannot_spoof_forwarded_headers() -> None:
    """Verify private LAN student IPs cannot spoof proxy peer identity."""
    assert _is_trusted_local_proxy_peer("192.168.1.50") is False
    assert _is_trusted_local_proxy_peer("10.0.0.42") is False
    assert _is_trusted_local_proxy_peer("127.0.0.1") is True
    assert _is_trusted_local_proxy_peer("172.18.0.2") is True


def test_breached_and_username_passwords_rejected() -> None:
    """Verify known breached passwords, low-entropy repetitions, and username-containing passwords fail."""
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

    with pytest.raises(ValidationError, match="known breached password lists"):
        RegisterRequest(
            username="teacher_bob",
            password="aaaaaaaaaa",
        )


def test_pupil_pii_dlp_blocks_upn_nino_email_and_phone_but_allows_sen_scaffolding() -> (
    None
):
    """Verify DLP scanner blocks pupil/staff PII while allowing pedagogical keywords."""
    # UK UPN
    with pytest.raises(ValidationError, match="Unique Pupil Number"):
        TeacherRequest(
            task_type="worksheet",
            subject="Mathematics",
            year_group="9",
            topic="W801200001001 revision questions",
            ability="mixed",
        )

    # UK NINO
    with pytest.raises(ValidationError, match="National Insurance Number"):
        TeacherRequest(
            task_type="worksheet",
            subject="Mathematics",
            year_group="9",
            topic="Staff record QQ 12 34 56 C worksheet",
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

    # UK phone
    with pytest.raises(ValidationError, match="UK phone number"):
        TeacherRequest(
            task_type="worksheet",
            subject="Mathematics",
            year_group="9",
            topic="Call 07700 900123 for tutoring",
            ability="mixed",
        )

    # Educational SEN / ADHD pedagogy allowed
    valid_req = TeacherRequest(
        task_type="worksheet",
        subject="Biology",
        year_group="10",
        topic="Osmosis worksheet with SEN scaffolding and ADHD chunked steps",
        ability="mixed",
    )
    assert (
        valid_req.topic
        == "Osmosis worksheet with SEN scaffolding and ADHD chunked steps"
    )


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


def test_sanitise_revision_url_blocks_slopsquatting_and_canary_leak_rejected() -> None:
    """Verify revision URLs allow only safe UK educational domains, and canary leak raises 502."""
    assert (
        sanitise_revision_url("https://evil-squatter.xyz/ worksheet.exe")
        == "https://www.bbc.co.uk/bitesize"
    )
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

    # Verify _postprocess rejects prompt canary token leak
    content_canary = make_sample_generated_content()
    content_canary.lessons[
        0
    ].title = f"Leaked Confidential Token: {_PROMPT_CANARY} in lesson"
    with pytest.raises(UpstreamGenerationError, match="confidentiality guard") as exc:
        _postprocess(content_canary, req)
    assert exc.value.status_code == 502
