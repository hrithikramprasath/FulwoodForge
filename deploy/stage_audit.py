#!/usr/bin/env python3
"""Instant-cost audits for init.sh Stages 4 (palette/emoji), 5 (imports) and 6 (test integrity).

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
    out = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *patterns,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\n")
    return [ROOT / p for p in out if p and (ROOT / p).is_file()]


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
_SCAN_SUFFIXES = {".tsx", ".ts", ".css", ".mjs", ".py"}
_SCAN_ROOTS = ("backend/app", "frontend/src", "frontend/renderer", "deploy")
# Test files assert the absence of the forbidden strings, so they must name them.
_SELF = {"deploy/stage_audit.py"}


def hygiene() -> int:
    bad: list[str] = []
    for path in tracked_and_new(*_SCAN_ROOTS):
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix not in _SCAN_SUFFIXES or rel in _SELF or "node_modules" in rel:
            continue
        if "/tests/" in rel or "/__tests__/" in rel:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PURPLE.search(line):
                bad.append(f"{rel}:{n}: MagicSchool purple")
            if _EMOJI.search(line):
                bad.append(f"{rel}:{n}: emoji")
    return report("Stage 4 palette/emoji", bad)


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


def _py_layer(path: Path) -> str:
    parts = path.relative_to(ROOT / "backend").with_suffix("").parts
    return parts[1] if len(parts) > 2 else parts[-1]


def _py_imports(tree: ast.AST) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.append(node.module)
    return mods


def imports() -> int:
    bad: list[str] = []
    for path in tracked_and_new("backend/app"):
        if path.suffix != ".py":
            continue
        rel = path.relative_to(ROOT).as_posix()
        layer = _py_layer(path)
        if layer not in _PY_RANK:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for mod in _py_imports(tree):
            if mod.startswith("app."):
                target = mod.split(".")[1]
                if _PY_RANK.get(target, -1) > _PY_RANK[layer]:
                    bad.append(f"{rel}: '{layer}' imports higher layer '{mod}'")
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
    imp = re.compile(r"""(?:from|import)\s+["']([^"']+)["']|from\s+["']([^"']+)["']""")
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
        text = path.read_text(encoding="utf-8")
        for m in imp.finditer(text):
            spec = m.group(1) or m.group(2)
            if spec.startswith("@/"):
                target = spec[2:].split("/")[0]
                if ts_rank.get(target, -1) > ts_rank[layer]:
                    bad.append(f"{rel}: '{layer}' imports higher layer '{spec}'")
        if layer == "components":
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
        rel = path.relative_to(ROOT).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SKIP.search(line):
                bad.append(f"{rel}:{n}: skipped test")
            if _WEAK.search(line):
                bad.append(f"{rel}:{n}: weakened assertion/suppression")
    for path in tracked_and_new("backend/app"):
        if path.suffix == ".py" and re.search(
            r"^if __name__ == ['\"]__main__['\"]",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        ):
            bad.append(f"{path.relative_to(ROOT).as_posix()}: embedded __main__ block")
    for path in tracked_and_new("frontend/src"):
        if path.suffix in {".ts", ".tsx"} and re.search(
            r"^\s*void\s+\w+\s*;", path.read_text(encoding="utf-8"), re.MULTILINE
        ):
            bad.append(
                f"{path.relative_to(ROOT).as_posix()}: unused-variable suppression"
            )
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if "py_compile" in ci:
        bad.append("ci.yml: py_compile shortcut present")
    for needle in ("pytest backend/tests", "npm --prefix frontend test -- --run"):
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
