#!/usr/bin/env bash
set -euo pipefail
umask 077

root=$(CDPATH='' cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
image=${OPENCLAW_M14_MAINTENANCE_IMAGE:-${1:-openclaw-agent:m14-maintenance}}
stamp="$$-$(date +%s)"
daemon="openclaw-m14-clamd-$stamp"
socket_volume="openclaw-m14-socket-$stamp"
fixture=$(mktemp -d)

cleanup() {
  docker rm -f "$daemon" >/dev/null 2>&1 || true
  docker volume rm -f "$socket_volume" >/dev/null 2>&1 || true
  rm -rf -- "$fixture"
}
trap cleanup EXIT

mkdir -p "$fixture/database"
# A private synthetic signature keeps this test hermetic and avoids embedding a
# conventional antivirus test string in the repository.
printf '%s\n' 'M14.Test.Signature:0:*:4d31342d4549434152' > "$fixture/database/m14.ndb"
cat > "$fixture/clamd.conf" <<'EOF'
DatabaseDirectory /fixture/database
LocalSocket /run/clamav/clamd.sock
LocalSocketGroup clamav
LocalSocketMode 660
FixStaleSocket yes
Foreground yes
LogTime yes
LogClean no
TemporaryDirectory /tmp
MaxScanSize 1048576
MaxFileSize 1048576
StreamMaxLength 1048576
MaxThreads 2
MaxQueue 8
ReadTimeout 30
CommandReadTimeout 30
SendBufTimeout 30
MaxScanTime 30000
ExitOnOOM yes
EOF
chmod 0755 "$fixture" "$fixture/database"
chmod 0444 "$fixture/database/m14.ndb" "$fixture/clamd.conf"

docker volume create "$socket_volume" >/dev/null
docker run --rm --network none --read-only \
  --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --security-opt no-new-privileges:true \
  --mount "type=volume,src=$socket_volume,dst=/run/clamav" \
  --entrypoint /sbin/tini \
  "$image" -- /opt/openclaw-agent/docker/clamav-socket-init.sh

started_ns=$(date +%s%N)
docker run -d --name "$daemon" --network none --read-only \
  --user 100:101 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --pids-limit 64 --memory 1g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --mount "type=volume,src=$socket_volume,dst=/run/clamav" \
  --mount "type=bind,src=$fixture,dst=/fixture,readonly" \
  --entrypoint /sbin/tini \
  "$image" -- /usr/sbin/clamd --config-file=/fixture/clamd.conf >/dev/null

ready=false
for _ in $(seq 1 120); do
  if docker run --rm --network none --read-only \
    --user 100:101 --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --mount "type=volume,src=$socket_volume,dst=/run/clamav,readonly" \
    --entrypoint python3 "$image" -P -c '
from personal_assistant.clamd_client import ClamdUnixClient
client=ClamdUnixClient("/run/clamav/clamd.sock", timeout_seconds=2, max_stream_bytes=1048576)
assert client.ping()
' >/dev/null 2>&1; then
    ready=true
    break
  fi
  [[ $(docker inspect --format '{{.State.Running}}' "$daemon") == true ]] || break
  sleep 1
done
if [[ "$ready" != true ]]; then
  docker logs "$daemon" >&2 || true
  echo "Hermetischer clamd wurde nicht bereit" >&2
  exit 1
fi
ready_ns=$(date +%s%N)
ready_ms=$(( (ready_ns - started_ns) / 1000000 ))

scan_metrics=$(docker run --rm -i --network none --read-only \
  --user 100:101 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=volume,src=$socket_volume,dst=/run/clamav,readonly" \
  --entrypoint python3 "$image" -P - <<'PY'
import json
import time
from personal_assistant.clamd_client import ClamdUnixClient
client=ClamdUnixClient("/run/clamav/clamd.sock", timeout_seconds=5, max_stream_bytes=1048576)
assert client.scan(b"harmless fixture").status == "clean"
infected=client.scan(b"M14-EICAR")
assert infected.status == "infected"
assert infected.signature.startswith("M14.Test.Signature")
payload=b"M14-clean-benchmark\n" * 3276
started=time.monotonic()
for _ in range(32):
    assert client.scan(payload).status == "clean"
elapsed=time.monotonic()-started
print(json.dumps({
    "payload_bytes": len(payload),
    "scans": 32,
    "elapsed_seconds": round(elapsed, 6),
    "scans_per_second": round(32 / elapsed, 3),
    "bytes_per_second": round((32 * len(payload)) / elapsed, 3),
}, sort_keys=True))
PY
)

# Exercise a real database reload with a second private signature. Production
# clients cannot issue RELOAD; this raw command exists only inside the
# hermetic test harness to verify clamd's update behavior.
printf '%s\n' 'M14.Reload.Signature:0:*:4d31342d52454c4f4144' > "$fixture/database/m14-reload.ndb"
chmod 0444 "$fixture/database/m14-reload.ndb"
docker run --rm -i --network none --read-only \
  --user 100:101 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=volume,src=$socket_volume,dst=/run/clamav,readonly" \
  --entrypoint python3 "$image" -P - <<'PY'
import socket
import time
from personal_assistant.clamd_client import ClamdUnixClient

sock=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(5)
sock.connect("/run/clamav/clamd.sock")
sock.sendall(b"zRELOAD\0")
reply=sock.recv(4096).rstrip(b"\0\n")
sock.close()
assert reply in {b"RELOADING", b"RELOAD OK"}, reply

client=ClamdUnixClient("/run/clamav/clamd.sock", timeout_seconds=5, max_stream_bytes=1048576)
for _ in range(100):
    verdict=client.scan(b"M14-RELOAD")
    if verdict.status == "infected":
        assert verdict.signature.startswith("M14.Reload.Signature")
        break
    time.sleep(0.05)
else:
    raise AssertionError("reloaded synthetic signature was not activated")
PY

[[ $(docker inspect --format '{{.HostConfig.NetworkMode}}' "$daemon") == none ]]
[[ $(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$daemon") == true ]]
[[ $(docker inspect --format '{{.Config.User}}' "$daemon") == 100:101 ]]
[[ $(docker inspect --format '{{json .HostConfig.CapDrop}}' "$daemon") == '["ALL"]' ]]

# A real daemon outage must be observable as a typed fail-closed error, and a
# restart with the same read-only signature fixture must restore readiness.
docker stop --time 10 "$daemon" >/dev/null
docker run --rm -i --network none --read-only \
  --user 100:101 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=volume,src=$socket_volume,dst=/run/clamav,readonly" \
  --entrypoint python3 "$image" -P - <<'PY'
from personal_assistant.clamd_client import ClamdClientError, ClamdUnixClient
client=ClamdUnixClient("/run/clamav/clamd.sock", timeout_seconds=2, max_stream_bytes=1048576)
try:
    client.ping()
except ClamdClientError as exc:
    assert exc.category in {"socket-unavailable", "socket-invalid"}, exc.category
else:
    raise AssertionError("stopped clamd unexpectedly answered PING")
PY
# Exercise the initializer a second time against the same persistent volume.
# The first run deliberately leaves /run/clamav owned by the daemon user.
docker run --rm --network none --read-only \
  --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE \
  --security-opt no-new-privileges:true \
  --mount "type=volume,src=$socket_volume,dst=/run/clamav" \
  --entrypoint /sbin/tini \
  "$image" -- /opt/openclaw-agent/docker/clamav-socket-init.sh
docker start "$daemon" >/dev/null
recovered=false
for _ in $(seq 1 120); do
  if docker run --rm --network none --read-only \
    --user 100:101 --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --mount "type=volume,src=$socket_volume,dst=/run/clamav,readonly" \
    --entrypoint python3 "$image" -P -c '
from personal_assistant.clamd_client import ClamdUnixClient
assert ClamdUnixClient("/run/clamav/clamd.sock", timeout_seconds=2, max_stream_bytes=1048576).ping()
' >/dev/null 2>&1; then
    recovered=true
    break
  fi
  [[ $(docker inspect --format '{{.State.Running}}' "$daemon") == true ]] || break
  sleep 1
done
[[ "$recovered" == true ]] || {
  docker logs "$daemon" >&2 || true
  echo "Hermetischer clamd wurde nach Neustart nicht bereit" >&2
  exit 1
}

mkdir -p "$root/build"
image_bytes=$(docker image inspect --format '{{.Size}}' "$image")
pid_count=$(docker top "$daemon" | tail -n +2 | wc -l)
memory_observation=$(docker stats --no-stream --format '{{.MemUsage}}' "$daemon")
python3 - "$root/build/m14-hermetic-baseline.json" "$image" "$ready_ms" \
  "$image_bytes" "$pid_count" "$memory_observation" "$scan_metrics" <<'PY'
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
payload = {
    "schema": 1,
    "fixture": "synthetic-private-signature",
    "network": "none",
    "image": sys.argv[2],
    "ready_ms": int(sys.argv[3]),
    "image_bytes": int(sys.argv[4]),
    "pid_count": int(sys.argv[5]),
    "memory_observation": sys.argv[6],
    "scan": json.loads(sys.argv[7]),
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "M14 hermetic clamd socket, clean/infected, load, reload, stop/recovery and hardening scenario: OK"
echo "M14 baseline: $root/build/m14-hermetic-baseline.json"
