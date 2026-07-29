#!/usr/bin/env bash
set -euo pipefail
ROOT=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
image=${1:-openclaw-agent:r27.0.1-local}
revision=${OPENCLAW_SOURCE_REVISION:-$(git rev-parse --verify HEAD 2>/dev/null || printf 'local')}
docker build \
  --build-arg OPENCLAW_BASE_IMAGE="${OPENCLAW_BASE_IMAGE:-ghcr.io/openclaw/openclaw:2026.6.11}" \
  --build-arg HIMALAYA_VERSION="${HIMALAYA_VERSION:-1.2.0}" \
  --build-arg OPENCLAW_SOURCE_REVISION="$revision" \
  -t "$image" .
echo "Gebaut: $image ($revision)"
