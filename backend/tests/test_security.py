"""Offline security regressions and runtime integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from app.core import hardening, security
from app.core.config import load_config
from app.core.processes import renderer_env, run_worker
from app.main import app
from app.schemas.content import GeneratedContent, GenerateRequest, TeacherRequest
from app.services.export import MEDIA_TYPES, _launch_browser
from app.services.gemini import GenerationResult
from app.services.lesson_service import _default_export_artifact
from app.services.omml import _pandoc_omaths
from pydantic import ValidationError

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from starlette.testclient import TestClient

from tests.test_wave4_runtime_api import (
    build_in_memory_runtime,
    make_sample_generated_content,
)

SAMPLE = {
    "title": "T",
    "task_type": "worksheet",
    "subject": "Maths",
    "year_group": "9",
    "audience_note": "a",
    "difficulty_note": "d",
    "overview": {"summary": "s", "objectives": ["o"], "assessment_focus": "a"},
    "lessons": [{"title": "L", "lesson_focus": "f", "objectives": ["o"]}],
}


class RequestLimits(unittest.TestCase):
    def test_streamed_bodies(self) -> None:
        async def run(
            chunks: list[dict[str, object]],
            headers: list[tuple[bytes, bytes]] | tuple[()] = (),
        ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
            messages: list[dict[str, object]] = []
            calls: list[dict[str, object]] = []

            async def receive() -> dict[str, object]:
                return chunks.pop(0)

            async def send(message: dict[str, object]) -> None:
                messages.append(message)

            async def inner_app(scope: object, receive_fn: Any, send_fn: Any) -> None:
                calls.append(await receive_fn())
                await send_fn(
                    {"type": "http.response.start", "status": 200, "headers": []}
                )
                await send_fn({"type": "http.response.body", "body": b"ok"})

            await hardening.SecurityMiddleware(inner_app)(
                {"type": "http", "headers": headers, "path": "/api/generate"},
                receive,
                send,  # type: ignore[arg-type]
            )
            return messages, calls

        for headers in ([], [(b"content-length", b"1")]):
            with patch.object(hardening, "MAX_BODY_BYTES", 10):
                messages, calls = asyncio.run(
                    run(
                        [
                            {
                                "type": "http.request",
                                "body": b"x" * 6,
                                "more_body": True,
                            },
                            {"type": "http.request", "body": b"x" * 6},
                        ],
                        headers,
                    )
                )
            self.assertEqual(messages[0]["status"], 413)
            self.assertEqual(calls, [])
            hdrs = dict(messages[0]["headers"])  # type: ignore[call-overload]
            self.assertIn(b"content-security-policy", hdrs)
            self.assertEqual(hdrs[b"content-type"], b"application/problem+json")
        with patch.object(hardening, "MAX_BODY_BYTES", 10):
            messages, calls = asyncio.run(
                run(
                    [
                        {"type": "http.request", "body": b"abc", "more_body": True},
                        {"type": "http.request", "body": b"def"},
                    ]
                )
            )
        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(calls[0]["body"], b"abcdef")

    def test_body_deadline(self) -> None:
        async def run() -> None:
            messages: list[dict[str, object]] = []

            async def receive() -> dict[str, object]:
                await asyncio.sleep(1)
                return {"type": "http.request", "body": b""}

            async def send(message: dict[str, object]) -> None:
                messages.append(message)

            async def unreachable(*args: object) -> None:
                self.fail("slow body reached application")

            with patch.object(hardening, "_int_env", return_value=0.01):
                await hardening.SecurityMiddleware(unreachable)(
                    {"type": "http", "headers": [], "path": "/api/generate"},
                    receive,
                    send,  # type: ignore[arg-type]
                )
            self.assertEqual(messages[0]["status"], 413)
            body = json.loads(messages[1]["body"])  # type: ignore[arg-type]
            self.assertEqual(body["type"], "urn:fulwoodforge:error:payload-too-large")
            self.assertEqual(body["title"], "Payload Too Large")

        asyncio.run(run())


class NativeSessionAuthentication(unittest.TestCase):
    """Verify native ff_session authentication, proxy-header rejection, and CSRF defense."""

    def setUp(self) -> None:
        self.container, self.store = build_in_memory_runtime(
            cookie_secure=True, registration_mode="OPEN"
        )
        app.state.container = self.container
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)

    def test_api_routes_reject_forged_proxy_headers_and_require_ff_session(
        self,
    ) -> None:
        for path, method in (
            ("/api/lessons", "GET"),
            ("/api/admin/overview", "GET"),
            ("/api/generate", "POST"),
        ):
            for headers in (
                {},
                {"X-Forwarded-Email": "forged@example.org"},
                {
                    "X-Fulwood-Proxy-Token": "s" * 48,
                    "X-Forwarded-Email": "forged@example.org",
                },
                {"Cookie": "ff_session=forged-nonexistent-session-token"},
            ):
                resp = self.client.request(
                    method,
                    path,
                    headers=headers,
                    json={} if method == "POST" else None,
                )
                self.assertEqual(resp.status_code, 401)
                self.assertTrue(
                    resp.headers["content-type"].startswith("application/problem+json")
                )
                self.assertEqual(
                    resp.json()["type"], "urn:fulwoodforge:error:unauthenticated"
                )

    def test_csrf_origin_and_sec_fetch_site_protection(self) -> None:
        reg = self.client.post(
            "/api/auth/register",
            json={"username": "teacher_csrf", "password": "ValidPassword!2026"},
        )
        self.assertEqual(reg.status_code, 201)

        # Cross-origin Origin header on state-mutating POST with ff_session cookie is rejected with 403
        cross_origin = self.client.post(
            "/api/auth/logout",
            headers={"Origin": "https://evil.example.org"},
        )
        self.assertEqual(cross_origin.status_code, 403)
        self.assertTrue(
            cross_origin.headers["content-type"].startswith("application/problem+json")
        )
        self.assertEqual(
            cross_origin.json()["type"], "urn:fulwoodforge:error:forbidden"
        )

        # Sec-Fetch-Site: cross-site is also rejected with 403
        cross_site = self.client.post(
            "/api/auth/logout",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(cross_site.status_code, 403)
        self.assertEqual(cross_site.json()["type"], "urn:fulwoodforge:error:forbidden")

        # Same-origin Origin header succeeds
        same_origin = self.client.post(
            "/api/auth/logout",
            headers={"Origin": "https://testserver"},
        )
        self.assertEqual(same_origin.status_code, 204)

        # Re-login and verify an origin configured in AppConfig.cors_origins is permitted
        relogin = self.client.post(
            "/api/auth/login",
            json={"username": "teacher_csrf", "password": "ValidPassword!2026"},
        )
        self.assertEqual(relogin.status_code, 200)
        configured_origin = self.client.post(
            "/api/auth/logout",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(configured_origin.status_code, 204)

    def test_unit_csrf_helper_rejects_mismatched_origin(self) -> None:
        ok, reason = security.check_csrf_headers(
            method="POST",
            headers={"origin": "https://attacker.org"},
            request_origin="https://forge.example.org",
        )
        self.assertFalse(ok)
        self.assertIn("CSRF", reason)


class ApplicationIntegration(unittest.TestCase):
    """Exercise middleware, native session auth, route dispatch, and real worker exports together."""

    def setUp(self) -> None:
        self.generator_calls: list[GenerateRequest] = []

        async def _fake_generator(req: GenerateRequest) -> GenerationResult:
            self.generator_calls.append(req)
            return GenerationResult(
                content=make_sample_generated_content(
                    title=f"{req.subject}: {req.topic}",
                    task_type=req.task_type,
                    subject=req.subject,
                ),
                model_name="gemini-3.5-flash-lite",
                prompt_tokens=250,
                candidate_tokens=500,
                total_tokens=750,
                latency_ms=80,
            )

        self.container, self.store = build_in_memory_runtime(
            cookie_secure=True,
            registration_mode="OPEN",
            generator_fn=_fake_generator,
            exporter_fn=_default_export_artifact,
        )
        app.state.container = self.container

        self.unauthed_client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.unauthed_client.close)

        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        reg = self.client.post(
            "/api/auth/register",
            json={"username": "teacher_sec", "password": "StrongPassword!2026"},
        )
        self.assertEqual(reg.status_code, 201)

    def test_untrusted_posts_never_reach_generation(self) -> None:
        for headers in ({}, {"X-Forwarded-Email": "forged@example.org"}):
            response = self.unauthed_client.post(
                "/api/generate", json={}, headers=headers
            )
            self.assertEqual(response.status_code, 401)
            self.assertIn("content-security-policy", response.headers)
            self.assertTrue(
                response.headers["content-type"].startswith("application/problem+json")
            )
        self.assertEqual(len(self.generator_calls), 0)

    def test_authenticated_generation_uses_verified_identity(self) -> None:
        request = {
            "task_type": "worksheet",
            "subject": "Maths",
            "year_group": "9",
            "topic": "Algebra",
            "ability": "mixed",
        }
        response = self.client.post("/api/generate", json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.generator_calls), 1)
        self.assertIn("x-generation-uuid", response.headers)
        self.assertEqual(response.headers["x-cache-hit"], "false")
        saved_rows = list(self.store.generations.values())
        self.assertEqual(len(saved_rows), 1)
        teacher_user = self.store.users[saved_rows[0].user_id]
        self.assertEqual(teacher_user.username, "teacher_sec")

    def test_authenticated_ui_is_served(self) -> None:
        for route in ("/", "/login", "/register", "/admin"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertIn("<html", response.text.lower())
            self.assertEqual(
                response.headers["content-security-policy"],
                hardening.STRICT_CSP,
            )

    def test_all_export_routes_run_workers(self) -> None:
        for fmt, media_type in MEDIA_TYPES.items():
            with self.subTest(format=fmt):
                response = self.client.post(f"/api/export/{fmt}", json=SAMPLE)
                self.assertEqual(
                    response.status_code,
                    200,
                    response.text[:200] if response.status_code != 200 else "",
                )
                self.assertEqual(response.headers["content-type"], media_type)
                self.assertTrue(
                    response.content.startswith(
                        b"%PDF" if fmt.endswith("pdf") else b"PK"
                    )
                )
                self.assertIn(
                    "attachment; filename=", response.headers["content-disposition"]
                )
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_production_startup_rejects_missing_secrets(self) -> None:
        with self.assertRaises(RuntimeError):
            load_config({"APP_ENV": "production"})


class ContentBounds(unittest.TestCase):
    def test_resource_structure_and_metadata(self) -> None:
        GeneratedContent.model_validate(SAMPLE)
        for key, value in (
            ("title", "x" * 256),
            ("lessons", cast(list[object], SAMPLE["lessons"]) * 13),
            ("unused", ["x"] * 101),
            ("unused", "$" * 2401),
        ):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                GeneratedContent.model_validate({**SAMPLE, key: value})

    def test_counts(self) -> None:
        request = {
            "task_type": "worksheet",
            "subject": "Maths",
            "year_group": "9",
            "topic": "Algebra",
            "ability": "mixed",
        }
        for value in ("²", "１２", "-1", "0", "99999999999999999", "1.5"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                TeacherRequest.model_validate({**request, "question_count": value})
        self.assertEqual(
            TeacherRequest.model_validate(
                {**request, "question_count": " 20 "}
            ).question_count,
            "20",
        )
        self.assertIsNone(
            TeacherRequest.model_validate({**request, "slide_count": " "}).slide_count
        )


class ProcessIsolation(unittest.TestCase):
    def test_no_service_secrets_in_renderer_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test",
                "MYSQL_PASSWORD": "test",
                "BOOTSTRAP_ADMIN_PASSWORD": "test",
                "ACCESS_TOKEN": "test",
            },
        ):
            env = renderer_env()
            for key in (
                "GEMINI_API_KEY",
                "MYSQL_PASSWORD",
                "BOOTSTRAP_ADMIN_PASSWORD",
                "ACCESS_TOKEN",
            ):
                self.assertNotIn(key, env)

    def test_worker_deadline_is_enforced(self) -> None:
        with self.assertRaises(TimeoutError):
            run_worker(
                "export",
                json.dumps({"content": SAMPLE, "format": "pdf"}).encode(),
                0.001,
            )


class CacheRetention(unittest.TestCase):
    def test_idle_cleanup_deletes_expired_content(self) -> None:
        from app.services import gemini

        gemini._request_cache.clear()
        gemini._memory_cache_put("sample", GeneratedContent.model_validate(SAMPLE))
        self.assertIsNotNone(gemini._memory_cache_get("sample"))
        gemini._request_cache["sample"] = (0.0, GeneratedContent.model_validate(SAMPLE))
        gemini.purge_memory_cache()
        self.assertEqual(len(gemini._request_cache), 0)
        self.assertIsNone(gemini._memory_cache_get("sample"))


class PandocIsolation(unittest.TestCase):
    def test_markdown_breakout_cannot_fetch_url(self) -> None:
        fetched: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fetched.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _pandoc_omaths(
                f"$x\n\n![probe](http://127.0.0.1:{server.server_port}/marker)\n\nx$"
            )
            self.assertEqual(fetched, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class BrowserIsolation(unittest.TestCase):
    def test_chrome_sandbox_is_really_enabled(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = _launch_browser(p)
            try:
                page = browser.new_page()
                page.goto("chrome://sandbox")
                page.wait_for_selector("td")
                text = page.locator("body").inner_text()
                self.assertRegex(text, r"Layer 1 Sandbox\s+Namespace")
                self.assertRegex(text, r"Seccomp-BPF sandbox\s+Yes")
                found = False
                for file in Path("/proc").glob("[0-9]*/cmdline"):
                    try:
                        args = file.read_bytes().split(b"\0")
                    except OSError:
                        continue
                    if args and args[0] == b"/opt/google/chrome/chrome":
                        found = True
                        self.assertNotIn(b"--no-sandbox", args)
                self.assertTrue(found)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
