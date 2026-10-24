#!/usr/bin/env python3
"""Instant-cost audits for init.sh Stages 4 (secrets/palette/emoji), 5 (imports) and 6 (test/CI integrity).

Usage: stage_audit.py {hygiene|imports|tests}. Exits 1 and prints every violation found.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- helpers


def tracked_and_new(*patterns: str) -> list[Path]:
    """Git-tracked plus untracked-but-not-ignored files matching pathspecs."""
    cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    if patterns:
        cmd.extend(["--", *patterns])
    out = subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\n")
    return [ROOT / p for p in out if p and (ROOT / p).is_file()]


def _read_utf8(path: Path) -> str | None:
    """Read a file as UTF-8 text, returning None if it is binary or unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def report(name: str, violations: list[str]) -> int:
    if violations:
        print(f"[{name}] FAILED with {len(violations)} violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"[{name}] OK")
    return 0


# --------------------------------------------------------------------------- stage 4

_EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d]")
_PURPLE = re.compile("#762bfa", re.IGNORECASE)
_LEAK_RE = re.compile(
    r"AIza[0-9A-Za-z_-]{35}|"
    r"GOCSPX-[0-9A-Za-z_-]{28}|"
    r"(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}|"
    r"github_pat_[0-9A-Za-z_]{80,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|"
    r"\.ts\.net\b|"
    r"/home/hr1th1krr\b|"
    r"/home/hrithikramprasath\b"
)
_FORBIDDEN_FILE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".tar",
}
_SCAN_SUFFIXES = {".tsx", ".ts", ".css", ".mjs", ".py"}
_SELF = {"deploy/stage_audit.py"}
_LEAK_EXEMPT = {
    "init.sh",
    "AGENTS.md",
    "SPEC.md",
    "deploy/stage_audit.py",
    ".github/workflows/ci.yml",
}


def hygiene() -> int:
    bad: list[str] = []
    for path in tracked_and_new():
        rel = path.relative_to(ROOT).as_posix()
        if "node_modules" in rel:
            continue
        name = path.name
        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            bad.append(f"{rel}: tracked environment file is forbidden")
            continue
        if path.suffix.lower() in _FORBIDDEN_FILE_SUFFIXES:
            bad.append(
                f"{rel}: forbidden key, database, or archive artifact ({path.suffix})"
            )
            continue
        content = _read_utf8(path)
        if content is None:
            continue
        if rel not in _LEAK_EXEMPT:
            for n, line in enumerate(content.splitlines(), 1):
                if _LEAK_RE.search(line):
                    bad.append(
                        f"{rel}:{n}: credential, private key, or host/path leak pattern"
                    )
        if (
            path.suffix in _SCAN_SUFFIXES
            and rel not in _SELF
            and "/tests/" not in rel
            and "/__tests__/" not in rel
        ):
            for n, line in enumerate(content.splitlines(), 1):
                if _PURPLE.search(line):
                    bad.append(f"{rel}:{n}: MagicSchool purple")
                if _EMOJI.search(line):
                    bad.append(f"{rel}:{n}: emoji")
    return report("Stage 4 secrets/palette/emoji", bad)


# --------------------------------------------------------------------------- stage 5

# Lower rank may never import higher rank. Types -> Config -> Repository -> Service -> Controller -> Runtime
_PY_RANK = {
    "schemas": 0,
    "core": 1,
    "db": 2,
    "repositories": 2,
    "services": 3,
    "api": 4,
    "main": 5,
}
_API_FORBIDDEN = (
    "google",
    "docx",
    "pptx",
    "sqlite3",
    "app.repositories",
    "app.db",
    "asyncmy",
)
_NON_HTTP_LAYERS = {"schemas", "core", "db", "repositories", "services"}


def _py_layer(path: Path) -> str:
    parts = path.relative_to(ROOT / "backend").with_suffix("").parts
    return parts[1] if len(parts) > 2 else parts[-1]


def _py_imports(tree: ast.AST, path: Path) -> list[str]:
    mods: list[str] = []
    pkg_parts = list(path.relative_to(ROOT / "backend").with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base_mod = ""
            if node.level == 0 and node.module:
                base_mod = node.module
            elif node.level > 0:
                up = node.level - 1
                base = pkg_parts[: len(pkg_parts) - up] if up <= len(pkg_parts) else []
                if node.module:
                    base.extend(node.module.split("."))
                if base:
                    base_mod = ".".join(base)
            if base_mod:
                mods.append(base_mod)
                for alias in node.names:
                    if alias.name != "*":
                        mods.append(f"{base_mod}.{alias.name}")
    return mods


def _resolve_ts_target(spec: str, path: Path, frontend_src: Path) -> str:
    """Resolve a TypeScript import specifier (@/... or relative ./ or ../) to its top-level src layer."""
    if spec.startswith("@/"):
        return spec[2:].split("/")[0]
    if spec.startswith("."):
        resolved = (path.resolve().parent / spec).resolve()
        if resolved.is_relative_to(frontend_src):
            parts = resolved.relative_to(frontend_src).with_suffix("").parts
            if parts:
                return parts[0]
    return ""


def imports() -> int:
    bad: list[str] = []
    for path in tracked_and_new("backend/app"):
        if path.suffix != ".py":
            continue
        source = _read_utf8(path)
        if source is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        layer = _py_layer(path)
        if layer not in _PY_RANK:
            continue
        tree = ast.parse(source)
        for mod in _py_imports(tree, path):
            if mod.startswith("app."):
                target = mod.split(".")[1]
                if _PY_RANK.get(target, -1) > _PY_RANK[layer]:
                    bad.append(f"{rel}: '{layer}' imports higher layer '{mod}'")
            if layer in _NON_HTTP_LAYERS and (
                mod == "fastapi" or mod.startswith("fastapi.")
            ):
                bad.append(f"{rel}: non-HTTP layer '{layer}' imports 'fastapi'")
            if layer == "api" and any(
                mod == f or mod.startswith(f + ".") for f in _API_FORBIDDEN
            ):
                bad.append(f"{rel}: controller imports forbidden module '{mod}'")
            if layer == "main" and (
                mod.startswith("app.repositories") or mod in {"sqlite3", "asyncmy"}
            ):
                bad.append(f"{rel}: entrypoint imports repository/driver '{mod}'")
    # Frontend: schemas/types -> lib -> components -> App/main
    ts_rank = {"types": 0, "schemas": 0, "lib": 1, "components": 2, "App": 3, "main": 3}
    imp = re.compile(r"""(?:from|import)\s+["']([^"']+)["']""")
    frontend_src = (ROOT / "frontend" / "src").resolve()
    for path in tracked_and_new("frontend/src"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if "__tests__" in rel:
            continue
        sub = path.relative_to(ROOT / "frontend" / "src").with_suffix("").parts
        layer = sub[0]
        if layer not in ts_rank:
            continue
        text = _read_utf8(path)
        if text is None:
            continue
        for m in imp.finditer(text):
            spec = m.group(1)
            target = _resolve_ts_target(spec, path, frontend_src)
            if target and ts_rank.get(target, -1) > ts_rank[layer]:
                bad.append(f"{rel}: '{layer}' imports higher layer '{spec}'")
        if layer in {"components", "App"}:
            if re.search(r"(?<![\w.])fetch\(", text):
                bad.append(f"{rel}: raw fetch() in UI component")
            if (
                "marked.parse(" in text
                and "sanitize" not in text
                and "DOMPurify" not in text
            ):
                bad.append(f"{rel}: unsanitized marked.parse() in UI component")
    return report("Stage 5 import hierarchy", bad)


# --------------------------------------------------------------------------- stage 6

_SKIP = re.compile(
    r"@pytest\.mark\.skip|pytest\.skip\(|unittest\.skip|self\.skipTest|"
    r"\b(?:it|describe|test)\.skip\b|\bxit\(|\bxdescribe\(|\.todo\("
)
_WEAK = re.compile(r"status_code\s*<\s*500|void\s+\w+\s*;")
_CI_REQUIRED = (
    "gitleaks/gitleaks-action",
    "playwright install --with-deps chrome",
    "./init.sh --harness-only",
    "ruff format --check backend deploy",
    "ruff check backend deploy",
    "bandit -r backend/app deploy",
    "mypy --strict --ignore-missing-imports backend/app backend/tests deploy",
    "pytest backend/tests",
    "npm --prefix frontend test -- --run",
    "npm --prefix frontend run build",
    "pip-audit",
    "deploy/verify-image.sh",
    "aquasec/trivy",
)


def tests() -> int:
    bad: list[str] = []
    files = [
        p
        for p in tracked_and_new(
            "backend/tests", "deploy/tests", "frontend/src/__tests__"
        )
        if p.suffix in {".py", ".ts", ".tsx"}
    ]
    for path in files:
        content = _read_utf8(path)
        if content is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for n, line in enumerate(content.splitlines(), 1):
            if _SKIP.search(line):
                bad.append(f"{rel}:{n}: skipped test")
            if _WEAK.search(line):
                bad.append(f"{rel}:{n}: weakened assertion/suppression")
    for path in tracked_and_new("backend/app"):
        if path.suffix != ".py":
            continue
        content = _read_utf8(path)
        if content and re.search(
            r"^if __name__ == ['\"]__main__['\"]",
            content,
            re.MULTILINE,
        ):
            bad.append(f"{path.relative_to(ROOT).as_posix()}: embedded __main__ block")
    for path in tracked_and_new("frontend/src"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        content = _read_utf8(path)
        if content and re.search(r"^\s*void\s+\w+\s*;", content, re.MULTILINE):
            bad.append(
                f"{path.relative_to(ROOT).as_posix()}: unused-variable suppression"
            )
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if "py_compile" in ci:
        bad.append("ci.yml: py_compile shortcut present")
    for needle in _CI_REQUIRED:
        if needle not in ci:
            bad.append(f"ci.yml: missing '{needle}'")
    return report("Stage 6 test integrity", bad)


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    funcs = {"hygiene": hygiene, "imports": imports, "tests": tests}
    if stage not in funcs:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(funcs[stage]())
