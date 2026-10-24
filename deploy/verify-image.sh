#!/usr/bin/env bash
# Match the production app restrictions. These checks never call Gemini.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${1:-fulwood-forge:security-candidate}"
docker run --rm --init --read-only --network none \
  --tmpfs /tmp:size=512m,mode=1777 \
  --tmpfs /home/appuser:size=64m,uid=10001,gid=10001,mode=700 \
  --cap-drop ALL --cap-add SYS_CHROOT \
  --security-opt "seccomp=${REPO}/deploy/chrome-seccomp.json" \
  --security-opt no-new-privileges --memory 4g --pids-limit 512 \
  -e SESSION_COOKIE_SECURE=false \
  -w /app/backend "$IMAGE" sh -ec '
    test -s /app/frontend/dist/index.html
    python -m unittest tests.test_security -v
  '
