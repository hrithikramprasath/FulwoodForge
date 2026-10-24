"""Offline checks for scan privacy and advisory reporting."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "security-check.py"
spec = importlib.util.spec_from_file_location("security_check", SCRIPT)
assert spec is not None and spec.loader is not None
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class SecurityCheck(unittest.TestCase):
    def run_scan(
        self, findings: list[dict[str, str]]
    ) -> tuple[int, str, list[dict[str, object]]]:
        archives: list[Path] = []
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"

            def save_image(args: list[str], **_kwargs: object) -> None:
                archive = Path(args[3])
                archive.write_bytes(b"fixture image")
                archives.append(archive)

            def command(*args: str) -> str:
                if args[:3] == ("docker", "compose", "ps"):
                    return args[-1] + "-container"
                if args[:2] == ("docker", "inspect"):
                    return (
                        "mysql:8.4.4"
                        if args[3] == "{{.Config.Image}}"
                        else "sha256:fixture"
                    )
                if args[:2] == ("docker", "exec"):
                    return "Google Chrome 1.2.3"
                self.assertEqual(args[:2], ("docker", "run"))
                archive = archives[-1]
                self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
                self.assertEqual(archive.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(
                    args[args.index("--user") + 1],
                    f"{os.getuid()}:{os.getgid()}",
                )
                self.assertIn(f"{archive}:/image.tar:ro", args)
                self.assertIn(f"{state / 'trivy-cache'}:/cache", args)
                return json.dumps({"Results": [{"Vulnerabilities": findings}]})

            def fetch(url: str) -> bytes:
                if "Packages" in url:
                    return b"Package: google-chrome-stable\nVersion: 1.2.3-1\n"
                return b'{"tag_name":"8.4"}'

            with (
                patch.object(scan, "STATE", state),
                patch.object(scan, "command", command),
                patch.object(scan.subprocess, "run", save_image),
                patch.object(scan, "fetch", fetch),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = scan.check()
            status = (state / "security-status").read_text()
            reports = [
                json.loads((state / f"{service}-vulnerabilities.json").read_text())
                for service in ("app", "mysql")
            ]
        self.assertEqual(len(archives), 2)
        self.assertTrue(all(not archive.exists() for archive in archives))
        return code, status, reports

    def test_clean_scan_keeps_archives_private_and_removes_them(self) -> None:
        code, status, reports = self.run_scan([])
        self.assertEqual(code, 0)
        self.assertIn("outcome=ok", status)
        self.assertTrue(
            all(report["FulwoodScannedImage"] == "sha256:fixture" for report in reports)
        )

    def test_fixable_high_findings_require_attention(self) -> None:
        finding = {
            "VulnerabilityID": "TEST-1",
            "PkgName": "fixture",
            "InstalledVersion": "1",
            "FixedVersion": "2",
            "Severity": "HIGH",
        }
        code, status, _ = self.run_scan([finding])
        self.assertEqual(code, 1)
        self.assertIn("outcome=attention", status)

    def test_unfixed_findings_remain_in_reports(self) -> None:
        finding = {
            "VulnerabilityID": "TEST-2",
            "PkgName": "fixture",
            "InstalledVersion": "1",
            "Severity": "CRITICAL",
        }
        code, _, reports = self.run_scan([finding])
        self.assertEqual(code, 0)
        self.assertTrue(
            all(
                report["Results"][0]["Vulnerabilities"] == [finding]  # type: ignore[index]
                for report in reports
            )
        )


AUDIT_SCRIPT = Path(__file__).resolve().parents[1] / "stage_audit.py"
audit_spec = importlib.util.spec_from_file_location("stage_audit", AUDIT_SCRIPT)
assert audit_spec is not None and audit_spec.loader is not None
stage_audit = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(stage_audit)


class StageAuditTests(unittest.TestCase):
    """Verify deploy/stage_audit.py catches leaks, upward imports, and test/CI regressions."""

    def test_live_repository_passes_all_three_stage_audits(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(stage_audit.hygiene(), 0)
            self.assertEqual(stage_audit.imports(), 0)
            self.assertEqual(stage_audit.tests(), 0)

    def test_hygiene_detects_tracked_env_files_and_credential_leaks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env.production"
            env_file.write_text("SECRET=1\n", encoding="utf-8")
            leaked_py = root / "backend" / "app" / "leak.py"
            leaked_py.parent.mkdir(parents=True)
            fake_key = "AIza" + ("B" * 35)
            leaked_py.write_text(f'KEY = "{fake_key}"\n', encoding="utf-8")

            with (
                patch.object(stage_audit, "ROOT", root),
                patch.object(
                    stage_audit,
                    "tracked_and_new",
                    side_effect=lambda *pats: (
                        [env_file, leaked_py] if not pats else [leaked_py]
                    ),
                ),
                contextlib.redirect_stderr(io.StringIO()) as err,
            ):
                code = stage_audit.hygiene()
            self.assertEqual(code, 1)
            output = err.getvalue()
            self.assertIn(
                ".env.production: tracked environment file is forbidden", output
            )
            self.assertIn("credential, private key, or host/path leak pattern", output)

    def test_imports_detects_upward_relative_and_fastapi_layer_violations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_core = root / "backend" / "app" / "core" / "bad.py"
            bad_core.parent.mkdir(parents=True)
            bad_core.write_text(
                "from fastapi import HTTPException\n"
                "from .. import services\n"
                "from app import api\n",
                encoding="utf-8",
            )
            bad_api = root / "backend" / "app" / "api" / "bad_ctrl.py"
            bad_api.parent.mkdir(parents=True)
            bad_api.write_text(
                "from app import repositories\n",
                encoding="utf-8",
            )
            bad_lib = root / "frontend" / "src" / "lib" / "bad.ts"
            bad_lib.parent.mkdir(parents=True, exist_ok=True)
            bad_lib.write_text(
                "import { ResultView } from '../components/preview/ResultView';\n",
                encoding="utf-8",
            )
            bad_ui = root / "frontend" / "src" / "App.tsx"
            bad_ui.parent.mkdir(parents=True, exist_ok=True)
            bad_ui.write_text(
                "export const x = () => fetch('/api/lessons');\n", encoding="utf-8"
            )

            def fake_tracked(*pats: str) -> list[Path]:
                if pats == ("backend/app",):
                    return [bad_core, bad_api]
                if pats == ("frontend/src",):
                    return [bad_lib, bad_ui]
                return []

            with (
                patch.object(stage_audit, "ROOT", root),
                patch.object(stage_audit, "tracked_and_new", side_effect=fake_tracked),
                contextlib.redirect_stderr(io.StringIO()) as err,
            ):
                code = stage_audit.imports()
            self.assertEqual(code, 1)
            output = err.getvalue()
            self.assertIn("non-HTTP layer 'core' imports 'fastapi'", output)
            self.assertIn("'core' imports higher layer 'app.services'", output)
            self.assertIn("'core' imports higher layer 'app.api'", output)
            self.assertIn(
                "controller imports forbidden module 'app.repositories'", output
            )
            self.assertIn(
                "'lib' imports higher layer '../components/preview/ResultView'", output
            )
            self.assertIn("raw fetch() in UI component", output)

    def test_non_utf8_binary_files_do_not_crash_stage_audits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_py = root / "backend" / "app" / "core" / "binary.py"
            bin_py.parent.mkdir(parents=True)
            bin_py.write_bytes(b"\xff\xfe\x00\x80invalid-utf8")

            with (
                patch.object(stage_audit, "ROOT", root),
                patch.object(
                    stage_audit, "tracked_and_new", side_effect=lambda *_p: [bin_py]
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(stage_audit.hygiene(), 0)
                self.assertEqual(stage_audit.imports(), 0)


if __name__ == "__main__":
    unittest.main()
