#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
image=${1:?Image angeben}
role=${2:?Rolle runtime, proxy oder maintenance angeben}
revision=${3:?Git-Revision angeben}
output=${4:-build/m7/$role}
python=${M7_PYTHON:-$ROOT/.venv/bin/python}
[[ -x "$python" ]] || python=python3

trivy=$($python scripts/m7_supply_chain.py lock-value scanner_images.trivy)
syft=$($python scripts/m7_supply_chain.py lock-value scanner_images.syft)
mkdir -p "$output" build/trivy-cache
temporary=$(mktemp -d)
cleanup() {
  chmod -R u+w "$temporary" 2>/dev/null || true
  rm -rf "$temporary"
}
trap cleanup EXIT

docker image inspect "$image" >/dev/null
docker save --output "$temporary/image.tar" "$image"
chmod 0755 "$temporary"
chmod 0444 "$temporary/image.tar"
docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env SYFT_CHECK_FOR_APP_UPDATE=false \
  --env SYFT_CACHE_DIR=/tmp/syft-cache \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=2g,mode=1777 \
  --volume "$temporary/image.tar:/scan/image.tar:ro" \
  "$syft" docker-archive:/scan/image.tar -o spdx-json > "$output/image.spdx.json"

docker run --rm \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume "$ROOT/build/trivy-cache:/root/.cache/trivy" \
  "$trivy" image --scanners vuln --format json \
  --skip-version-check --no-progress "$image" > "$output/vulnerabilities.json"
vulnerability_result=$(
  "$python" scripts/m7_supply_chain.py verify-vulnerabilities \
    --report "$output/vulnerabilities.json"
)
critical_count=$(jq -r '.severity_counts.CRITICAL // 0' <<< "$vulnerability_result")
docker run --rm \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume "$ROOT/build/trivy-cache:/root/.cache/trivy" \
  "$trivy" image --scanners secret --exit-code 1 --format json \
  --skip-version-check --no-progress "$image" > "$output/image-secrets.json"
secret_count=$(jq '[.Results[]?.Secrets[]?] | length' "$output/image-secrets.json")
if ((secret_count != 0)); then
  printf 'ERROR: %s Secret-Funde in %s.\n' "$secret_count" "$image" >&2
  exit 1
fi

"$python" scripts/m7_supply_chain.py provenance \
  --image "$image" --role "$role" --revision "$revision" \
  --sbom "$output/image.spdx.json" --output "$output/provenance.json"
"$python" scripts/m7_supply_chain.py verify-artifacts \
  --image "$image" --role "$role" --revision "$revision" \
  --sbom "$output/image.spdx.json" --provenance "$output/provenance.json"

container=$(docker create "$image")
trap 'docker rm -f "$container" >/dev/null 2>&1 || true; cleanup' EXIT
mkdir -p "$temporary/rootfs"
docker export "$container" | tar -x -C "$temporary/rootfs"
"$python" scripts/check_artifact.py image-root "$temporary/rootfs"
docker rm -f "$container" >/dev/null
trap cleanup EXIT
printf 'M7 supply-chain check successful: %s (%s); critical=%s secrets=%s\n' \
  "$image" "$role" "$critical_count" "$secret_count"
