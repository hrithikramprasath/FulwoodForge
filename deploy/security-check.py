#!/usr/bin/env python3
"""Scan deployed images and report new fixed vulnerabilities / stale upstream versions.

No credentials are sent to advisory providers. Docker archives contain image layers only.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(
    os.getenv("FULWOOD_STATE_DIR", str(Path.home() / ".local/state/fulwood-forge"))
)
SCANNER = (
    "aquasec/trivy:0.74.0"
    "@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
)


def command(*args: str) -> str:
    """Execute a subprocess command in ROOT and return stripped stdout."""
    return subprocess.check_output(args, text=True, cwd=ROOT, timeout=600).strip()


def fetch(url: str) -> bytes:
    """Fetch an HTTPS advisory URL."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Advisory URL must use https, got {url!r}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FulwoodForge-security-check"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310
        data: bytes = response.read()
        return data


def check() -> int:
    """Scan running app and mysql containers with Trivy and check upstream versions."""
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache = STATE / "trivy-cache"
    cache.mkdir(exist_ok=True, mode=0o700)
    problems: list[str] = []
    for service in ("app", "mysql"):
        container = command("docker", "compose", "ps", "-q", service)
        if not container:
            raise RuntimeError(f"{service} is not running")
        image = command("docker", "inspect", "--format", "{{.Image}}", container)
        with tempfile.TemporaryDirectory(prefix="fulwood-scan-") as directory:
            archive = Path(directory) / "image.tar"
            subprocess.run(
                ["docker", "save", "-o", str(archive), image],
                check=True,
                timeout=600,
            )
            archive.chmod(0o600)
            result = command(
                "docker",
                "run",
                "--rm",
                "--cap-drop",
                "ALL",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "3g",
                "-v",
                f"{archive}:/image.tar:ro",
                "-v",
                f"{cache}:/cache",
                SCANNER,
                "--cache-dir",
                "/cache",
                "image",
                "--input",
                "/image.tar",
                "--scanners",
                "vuln",
                "--quiet",
                "--format",
                "json",
            )
        report = json.loads(result)
        report["FulwoodScannedImage"] = image
        (STATE / f"{service}-vulnerabilities.json").write_text(json.dumps(report))
        findings = [
            v
            for item in report.get("Results", [])
            for v in item.get("Vulnerabilities", [])
        ]
        fixed = [
            v
            for v in findings
            if v.get("FixedVersion") and v["Severity"] in ("HIGH", "CRITICAL")
        ]
        print(
            f"{service}: {len(findings)} package/advisory matches; "
            f"{len(fixed)} high/critical matches with an available fix"
        )
        for v in fixed:
            problems.append(
                f"{service}: {v['VulnerabilityID']} {v['PkgName']} "
                f"{v['InstalledVersion']} -> {v['FixedVersion']}"
            )
        if service == "app":
            installed = command(
                "docker", "exec", container, "google-chrome", "--version"
            ).split()[-1]
            packages = fetch(
                "https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages"
            ).decode()
            stanza = next(
                (
                    s
                    for s in packages.split("\n\n")
                    if s.startswith("Package: google-chrome-stable\n")
                ),
                "",
            )
            stable = re.search(r"^Version: ([\d.]+)", stanza, re.MULTILINE)
            if not stable:
                raise RuntimeError("Could not determine Chrome stable version")
            if tuple(map(int, installed.split("."))) < tuple(
                map(int, stable[1].split("."))
            ):
                problems.append(f"Chrome is outdated: {installed} < {stable[1]}")
        else:
            installed_tag = (
                command("docker", "inspect", "--format", "{{.Config.Image}}", container)
                .split(":")[-1]
                .split("@")[0]
            )
            if not installed_tag.startswith("8.4"):
                problems.append(
                    f"mysql LTS release requires review: installed {installed_tag}, expected 8.4"
                )
    for problem in problems:
        print(problem)
    outcome = "attention" if problems else "ok"
    epoch = int(datetime.datetime.now(datetime.UTC).timestamp())
    (STATE / "security-status").write_text(f"outcome={outcome}\nepoch={epoch}\n")
    print(
        "Unfixed findings remain in the JSON reports; "
        "a clean fixable check is not a claim of zero vulnerabilities."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(check())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        err_epoch = int(datetime.datetime.now(datetime.UTC).timestamp())
        (STATE / "security-status").write_text(f"outcome=failed\nepoch={err_epoch}\n")
        print(f"Security check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
