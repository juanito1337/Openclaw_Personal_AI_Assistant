#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
IMAGE=${OPENCLAW_M12_RUNTIME_IMAGE:-openclaw-agent:m12-candidate}

docker info >/dev/null
docker image inspect "$IMAGE" >/dev/null
mkdir -p "$ROOT/build"
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /state:rw,noexec,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=0700 \
  --volume "$ROOT/tests/integration/m12/scenario.py:/m12/scenario.py:ro" \
  --volume "$ROOT/build:/output" \
  --entrypoint python3 \
  "$IMAGE" /m12/scenario.py

test -s "$ROOT/build/m12-integration.json"
echo "M12-Integration erfolgreich: nativer Inventory-Connector, Move, Copy, Delete und Suchurteil."
