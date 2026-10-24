#!/usr/bin/env bash
# Hourly trigger catches missed daily runs after a reboot without rebuilding every hour.
set -uo pipefail
umask 077
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${FULWOOD_STATE_DIR:-${HOME}/.local/state/fulwood-forge}"
mkdir -p "$STATE"
exec 8>"$STATE/maintenance.lock"
flock -n 8 || exit 0
last_epoch() {
  local value
  value="$(sed -n 's/^epoch=//p' "$1" 2>/dev/null | head -1)"
  [[ "$value" =~ ^[0-9]+$ ]] && echo "$value" || echo 0
}
now="$(date +%s)"
# Retry a failed update after six hours; otherwise refresh daily.
if (( now - $(last_epoch "$STATE/applied") >= 86400 && now - $(last_epoch "$STATE/attempt") >= 21600 )); then
  "$REPO/deploy/monthly-rebuild.sh" || true
fi
if (( now - $(last_epoch "$STATE/security-status") >= 86400 )); then
  python3 "$REPO/deploy/security-check.py" > "$STATE/security.log" 2>&1 || true
fi
