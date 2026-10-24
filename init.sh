#!/usr/bin/env bash
# Fulwood Forge — 7-Stage Cost-Ordered Verification & Harness Runner (init.sh)
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "=== Fulwood Forge Verification Harness ==="
echo "Repository: $REPO_ROOT"

echo "[Stage 0] Verifying Spec-Driven Harness Files..."
for f in AGENTS.md CLAUDE.md SPEC.md features.json claude-progress.txt; do
  if [[ ! -f "$REPO_ROOT/$f" ]]; then
    echo "ERROR: Missing required harness file: $f" >&2
    exit 1
  fi
done
grep -q "\[CANARY: MYSQL-8.4-ANTI-VIBE-LOCKED\]" "$REPO_ROOT/AGENTS.md"
grep -q "\[CANARY: MYSQL-8.4-ANTI-VIBE-LOCKED\]" "$REPO_ROOT/SPEC.md"
echo "[Stage 0] Harness files & Canary verified."

if [[ "${1:-}" == "--harness-only" ]]; then
  echo "Wave 0 harness verification passed."
  exit 0
fi

# Dependencies (idempotent; no-ops when already installed).
if [[ ! -d frontend/node_modules ]]; then
  npm --prefix frontend ci --no-audit --no-fund
fi
if [[ ! -d frontend/renderer/node_modules ]]; then
  npm --prefix frontend/renderer ci --omit=dev --no-audit --no-fund
fi
uv sync --project backend --frozen --quiet

echo "[Stage 1] Formatting Check..."
uvx ruff format --check backend deploy
npm --prefix frontend run format:check

echo "[Stage 2] Linter & SAST Security Scan..."
uvx ruff check backend deploy
uvx bandit -r backend/app deploy -ll -q
npm --prefix frontend run lint -- --max-warnings=0

echo "[Stage 3] Strict Compiler Type Check..."
uv run --project backend mypy --strict --ignore-missing-imports backend/app backend/tests deploy
npx --prefix frontend tsc -p frontend/tsconfig.app.json --noEmit
npx --prefix frontend tsc -p frontend/tsconfig.node.json --noEmit

echo "[Stage 4] Secret, Host Leak, Purple Palette & Emoji Scan..."
if git grep -nE "(AIza[0-9A-Za-z_-]{35}|GOCSPX-[0-9A-Za-z_-]{28}|\.ts\.net|/home/hr1th1krr)" -- ':!init.sh' ':!AGENTS.md' ':!SPEC.md' ':!deploy/stage_audit.py' ':!.github/workflows/ci.yml'; then
  echo "ERROR: Hardcoded credential or host leak pattern detected in tracked files!" >&2
  exit 1
fi
python3 deploy/stage_audit.py hygiene

echo "[Stage 5] Unidirectional Import Audit..."
python3 deploy/stage_audit.py imports

echo "[Stage 6] Anti-Reward-Hacking Test Audit..."
python3 deploy/stage_audit.py tests

echo "[Stage 7] Full Automated Test Suite & Production Build..."
python3 -m unittest discover -s deploy/tests -v
uv run --project backend pytest backend/tests -v
npm --prefix frontend test -- --run
npm --prefix frontend run build

echo "=== All Verification Stages Passed [CANARY: MYSQL-8.4-ANTI-VIBE-LOCKED] ==="
