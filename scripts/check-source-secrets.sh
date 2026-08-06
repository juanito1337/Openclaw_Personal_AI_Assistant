#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
python=${M7_PYTHON:-$ROOT/.venv/bin/python}
[[ -x "$python" ]] || python=python3
trivy=$($python scripts/m7_supply_chain.py lock-value scanner_images.trivy)
temporary=$(mktemp -d)
cleanup() {
  rm -rf "$temporary"
}
trap cleanup EXIT

# Export precisely the integrity-manifest source set, never ignored local
# configuration or ignored runtime data. Two behavior tests contain intentional
# secret-detector fixtures and are excluded explicitly below.
while IFS= read -r line; do
  path=${line:66}
  [[ -f "$path" ]] || continue
  mkdir -p "$temporary/source/$(dirname "$path")"
  cp -- "$path" "$temporary/source/$path"
done < SOURCE_MANIFEST.sha256

mkdir -p build/trivy-cache
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777 \
  --volume "$temporary/source:/workspace:ro" \
  --volume "$ROOT/build/trivy-cache:/root/.cache/trivy" \
  "$trivy" filesystem --scanners secret --exit-code 1 --skip-version-check --no-progress \
  --skip-files /workspace/tests/test_artifact_hygiene.py \
  --skip-files /workspace/tests/test_performance_telemetry.py \
  /workspace
echo "M7 source secret scan successful."
