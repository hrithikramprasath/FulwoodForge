"""Offline tests for deploy/monthly-rebuild.sh and deploy/check-gate.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = ROOT / "deploy" / "check-gate.py"
gate_spec = importlib.util.spec_from_file_location("check_gate", GATE_SCRIPT)
assert gate_spec is not None and gate_spec.loader is not None
sys.modules["check_gate"] = gate_mod = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(gate_mod)


class Maintenance(unittest.TestCase):
    def run_case(self, mode: list[str], failure: str = "") -> tuple[int, str, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            tools = work / "bin"
            tools.mkdir()
            state = work / "state"
            state.mkdir()
            (state / "applied").write_text("outcome=ok\nepoch=123\n")
            docker = tools / "docker"
            docker.write_text(
                """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FULWOOD_STATE_DIR/commands"
if [[ "$*" == 'compose ps -q app' ]]; then echo app-id;
elif [[ "$*" == *'{{.Image}}'* ]]; then echo sha256:previous;
elif [[ "$*" == *'{{.State.Health.Status}}'* ]]; then echo healthy;
elif [[ "$*" == 'compose up -d --no-build' && "$FAIL_AT" == rollback ]]; then exit 1;
elif [[ "$1" == run && "$FAIL_AT" == verify ]]; then exit 1;
elif [[ "$*" == 'compose up -d --no-build' && "$FAIL_AT" == swap && ! -f "$FULWOOD_STATE_DIR/swapped" ]]; then
  touch "$FULWOOD_STATE_DIR/swapped"; exit 1;
elif [[ "$1 $2" == "buildx build" && "$FAIL_AT" == build ]]; then exit 1;
fi
"""
            )
            docker.chmod(0o755)
            python = tools / "python3"
            python.write_text(
                """#!/usr/bin/env bash
if [[ "$FAIL_AT" == gate && ! -f "$FULWOOD_STATE_DIR/gate-failed" ]]; then
  count=$(cat "$FULWOOD_STATE_DIR/gate-count" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$FULWOOD_STATE_DIR/gate-count"
  if (( count >= 24 )); then touch "$FULWOOD_STATE_DIR/gate-failed"; fi
  exit 1
fi
exit 0
"""
            )
            sleep = tools / "sleep"
            sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
            sleep.chmod(0o755)
            python.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(tools) + ":" + os.environ["PATH"],
                "FULWOOD_STATE_DIR": str(state),
                "FAIL_AT": failure,
            }
            result = subprocess.run(
                ["bash", str(ROOT / "deploy/monthly-rebuild.sh"), *mode],
                env=env,
                timeout=10,
                check=False,
            )
            return (
                result.returncode,
                (state / "applied").read_text(),
                (state / "attempt").read_text(),
                (state / "commands").read_text(),
            )

    def test_dry_run_never_replaces_production_or_applied_timestamp(self) -> None:
        code, applied, attempt, commands = self.run_case(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("epoch=123", applied)
        self.assertIn("dry-run-ok", attempt)
        self.assertNotIn("compose up", commands)
        self.assertNotIn("prune", commands)
        self.assertNotIn("oauth2-proxy", commands)

    def test_build_failure_does_not_replace_production(self) -> None:
        code, applied, attempt, commands = self.run_case([], "build")
        self.assertNotEqual(code, 0)
        self.assertIn("epoch=123", applied)
        self.assertIn("production was not replaced", attempt)
        self.assertNotIn("compose up", commands)

    def test_failed_swap_rolls_back_and_verifies(self) -> None:
        code, applied, attempt, commands = self.run_case([], "swap")
        self.assertNotEqual(code, 0)
        self.assertIn("epoch=123", applied)
        self.assertIn("previous image restored and health/gate verified", attempt)
        self.assertEqual(commands.count("compose up -d --no-build"), 2)

    def test_failed_verification_never_replaces_production(self) -> None:
        code, applied, attempt, commands = self.run_case([], "verify")
        self.assertNotEqual(code, 0)
        self.assertIn("epoch=123", applied)
        self.assertIn("production was not replaced", attempt)
        self.assertNotIn("compose up", commands)

    def test_failed_gate_rolls_back_even_when_app_is_healthy(self) -> None:
        code, applied, attempt, commands = self.run_case([], "gate")
        self.assertNotEqual(code, 0)
        self.assertIn("epoch=123", applied)
        self.assertIn("previous image restored and health/gate verified", attempt)
        self.assertEqual(commands.count("compose up -d --no-build"), 2)

    def test_failed_rollback_is_reported_as_critical(self) -> None:
        code, applied, attempt, commands = self.run_case([], "rollback")
        self.assertNotEqual(code, 0)
        self.assertIn("epoch=123", applied)
        self.assertIn("CRITICAL: update and rollback verification failed", attempt)
        self.assertEqual(commands.count("compose up -d --no-build"), 2)

    def test_success_records_applied_update(self) -> None:
        code, applied, attempt, commands = self.run_case([])
        self.assertEqual(code, 0)
        self.assertNotIn("epoch=123", applied)
        self.assertEqual(applied, attempt)
        self.assertIn(
            "image prune -f --filter label=org.opencontainers.image.title=Fulwood Forge",
            commands,
        )
        self.assertNotIn("builder prune", commands)


class CheckGateTests(unittest.TestCase):
    """Verify deploy/check-gate.py enforces /health/ready, /api/auth/status, and RFC 9457 401."""

    def test_run_all_passes_when_native_endpoints_match_contract(self) -> None:
        def fake_fetch(url: str, method: str) -> tuple[int, dict[str, str], bytes]:
            if url.endswith("/health/ready") and method == "GET":
                return (
                    200,
                    {"content-type": "application/json"},
                    json.dumps(
                        {
                            "status": "ready",
                            "database": "connected",
                            "pandoc": "available",
                        }
                    ).encode(),
                )
            if url.endswith("/api/auth/status") and method == "GET":
                return (
                    200,
                    {"content-type": "application/json"},
                    json.dumps(
                        {
                            "authenticated": False,
                            "user": None,
                            "registration_mode": "INVITE_ONLY",
                        }
                    ).encode(),
                )
            if url.endswith("/api/generate") and method == "POST":
                return (
                    401,
                    {"content-type": "application/problem+json; charset=utf-8"},
                    json.dumps(
                        {
                            "type": "urn:fulwoodforge:error:unauthenticated",
                            "title": "Authentication Required",
                            "status": 401,
                            "detail": "Authentication required.",
                            "instance": "/api/generate",
                        }
                    ).encode(),
                )
            raise AssertionError(f"Unexpected call: {method} {url}")

        results = gate_mod.run_all("http://127.0.0.1:8000", fetch=fake_fetch)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.passed for r in results))

    def test_gate_fails_when_generate_does_not_return_problem_json(self) -> None:
        def fake_fetch(url: str, _method: str) -> tuple[int, dict[str, str], bytes]:
            if url.endswith("/health/ready"):
                return 200, {}, b'{"status":"ready"}'
            if url.endswith("/api/auth/status"):
                return 200, {}, b'{"authenticated":false}'
            return 200, {"content-type": "application/json"}, b'{"title":"oops"}'

        results = gate_mod.run_all("http://127.0.0.1:8000", fetch=fake_fetch)
        by_name = {r.name: r for r in results}
        self.assertFalse(by_name["api_unauth_rejected"].passed)


if __name__ == "__main__":
    unittest.main()
