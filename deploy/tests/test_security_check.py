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


if __name__ == "__main__":
    unittest.main()
