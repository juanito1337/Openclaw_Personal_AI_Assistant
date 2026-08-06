#!/usr/bin/env bash
set -euo pipefail
umask 077

runtime=${1:?Runtime-Image angeben}
proxy=${2:?Proxy-Image angeben}
maintenance=${3:?Maintenance-Image angeben}
revision=${4:?Git-Revision angeben}
release=3.4.0-r27.2.5

verify_labels() {
  local image=$1 role=$2 actual
  actual=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.openclaw.role"}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
  [[ "$actual" == "$role $release $revision" ]] || {
    echo "Unerwartete OCI-Identitaet fuer $image: $actual" >&2
    return 1
  }
}

verify_labels "$runtime" runtime
verify_labels "$proxy" proxy
verify_labels "$maintenance" maintenance

docker run --rm --network none --entrypoint /bin/sh "$runtime" -c '
  command -v openclaw
  command -v himalaya
  command -v pdftotext
  command -v tesseract
  command -v clamscan
  test "$(node -p '"'"'require("/usr/local/lib/node_modules/npm/node_modules/tar/package.json").version'"'"')" = 7.5.19
  test "$(sha256sum /usr/local/bin/himalaya | cut -d" " -f1)" = 9529d2584add1c4343f32524e6f985e7c98d491f3b854747318020eb1ec1df7f
  test ! -e /opt/openclaw-agent/tests
  test ! -e /opt/openclaw-agent/docs
  test ! -e /opt/openclaw-agent/docker/scripts
  test ! -e /opt/openclaw-agent/legacy
'
docker run --rm --network none \
  --entrypoint /opt/openclaw-agent/scripts/assistant.sh "$runtime" --help >/dev/null
docker run --rm --network none --entrypoint /opt/openclaw-agent/scripts/assistant.sh "$runtime" version --verify >/dev/null

docker run --rm --network none --entrypoint python3 "$proxy" -P -c '
import personal_assistant.ollama_priority_proxy
from pathlib import Path
assert not Path("/app").exists()
assert not Path("/usr/local/bin/openclaw").exists()
assert not Path("/usr/local/bin/himalaya").exists()
assert not Path("/usr/bin/tesseract").exists()
assert not Path("/usr/bin/clamscan").exists()
'
docker run --rm --network none --entrypoint python3 "$maintenance" -P -c '
import personal_assistant.clamav_health
from pathlib import Path
assert Path("/usr/bin/freshclam").is_file()
assert Path("/usr/bin/clamscan").is_file()
assert not Path("/app").exists()
assert not Path("/usr/local/bin/openclaw").exists()
assert not Path("/usr/local/bin/himalaya").exists()
assert not Path("/usr/bin/tesseract").exists()
'
echo "M7 role smoke tests successful."
