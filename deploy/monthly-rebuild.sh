#!/usr/bin/env bash
# Historical filename retained for existing installations; run DAILY now.
# Rebuilds OS/Chrome; Python/npm changes must first update the reviewed lockfiles.
set -Eeuo pipefail
umask 077
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${FULWOOD_STATE_DIR:-${HOME}/.local/state/fulwood-forge}"
mkdir -p "$STATE"
exec 9>"$STATE/rebuild.lock"
flock -n 9 || { echo 'Another rebuild is already running'; exit 0; }
exec >>"$STATE/rebuild.log" 2>&1
DRY_RUN=0
case "${1:-}" in '') ;; --dry-run) DRY_RUN=1 ;; *) echo 'Usage: monthly-rebuild.sh [--dry-run]'; exit 2 ;; esac
cd "$REPO"
CANDIDATE="fulwood-forge:candidate-$(date +%s)"
SWAPPING=0
PREVIOUS=""
status() {
  printf 'outcome=%s\nepoch=%s\ndetail=%s\n' "$1" "$(date +%s)" "$2" > "$STATE/attempt.tmp"
  mv "$STATE/attempt.tmp" "$STATE/attempt"
}
healthy() {
  local id health
  for ((i=0; i<24; i++)); do
    id="$(docker compose ps -q app)"
    health="$(docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || true)"
    if [[ "$health" == healthy ]] && python3 deploy/check-gate.py; then return 0; fi
    sleep 5
  done
  return 1
}
failed() {
  local code=$?
  trap - ERR
  if (( SWAPPING )); then
    echo 'Deployment failed; restoring the previous app image under the current security configuration.'
    if docker tag "$PREVIOUS" fulwood-forge:latest && docker compose up -d --no-build && healthy; then
      status failed 'Update failed; previous image restored and health/gate verified'
    else
      status failed 'CRITICAL: update and rollback verification failed; inspect containers now'
    fi
  else
    status failed 'Candidate preparation failed; production was not replaced'
  fi
  docker image rm "$CANDIDATE" >/dev/null 2>&1 || true
  echo "$(date -Is) rebuild failed (exit $code)"
  exit "$code"
}
trap failed ERR
status running 'Building and verifying candidate'
echo "$(date -Is) rebuild started; dry_run=$DRY_RUN"
# Validate secrets/config before doing a costly build. Config output is never printed.
docker compose config --quiet
BUILDER=fulwoodforge-maintenance
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDER" --driver docker-container --buildkitd-config deploy/buildkitd.toml
fi
docker buildx build --builder "$BUILDER" --load --pull --no-cache -t "$CANDIDATE" .
deploy/verify-image.sh "$CANDIDATE"
if (( DRY_RUN )); then
  status dry-run-ok 'Verified candidate; last applied timestamp unchanged'
  docker image rm "$CANDIDATE"
  exit 0
fi
APP_ID="$(docker compose ps -q app)"
PREVIOUS="$(docker inspect --format '{{.Image}}' "$APP_ID")"
docker tag "$PREVIOUS" fulwood-forge:previous
SWAPPING=1
docker tag "$CANDIDATE" fulwood-forge:latest
docker compose up -d --no-build
healthy
SWAPPING=0
status ok 'Applied; app health and authentication gate verified'
cp "$STATE/attempt" "$STATE/applied"
docker image rm "$CANDIDATE"
echo "$(date -Is) update applied"
# Delete only untagged images produced by this app. :latest and :previous survive.
docker image prune -f --filter 'label=org.opencontainers.image.title=Fulwood Forge'
# The dedicated builder's own GC bounds its cache; no global builder pruning.
