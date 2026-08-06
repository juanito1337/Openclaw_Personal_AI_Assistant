#!/usr/bin/env bash
set -euo pipefail
umask 077

IMAGE=${1:?Containerimage angeben}
docker info >/dev/null
docker image inspect "$IMAGE" >/dev/null

fixture=$(mktemp -d)
suffix="${RANDOM}-$$"
backend="openclaw-m4-backend-$suffix"
foreign="openclaw-m4-foreign-$suffix"
server="openclaw-m4-server-$suffix"
signal_container="openclaw-m4-signal-$suffix"
limits_container="openclaw-m4-limits-$suffix"
oom_container="openclaw-m4-oom-$suffix"
cleanup() {
  docker rm -f "$server" "$signal_container" "$limits_container" "$oom_container" >/dev/null 2>&1 || true
  docker network rm "$backend" "$foreign" >/dev/null 2>&1 || true
  rm -rf "$fixture"
}
trap cleanup EXIT

# Kernel-enforced default: non-root, no capabilities and immutable rootfs.
docker run --rm --network none --read-only --user 1000:1000 \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 \
  --memory 128m --cpus 0.5 --entrypoint /bin/sh "$IMAGE" -c '
    test "$(id -u):$(id -g)" = "1000:1000"
    test "$(awk "/^CapEff:/ {print \$2}" /proc/self/status)" = "0000000000000000"
    ! touch /opt/openclaw-agent/.forbidden 2>/dev/null
  '

# A role sees only explicitly mounted files, not sibling secrets.
printf '%s\n' 'NEXTCLOUD_TOKEN=fixture-only' > "$fixture/mail-agent.env"
docker run --rm --network none --read-only --user 1000:1000 \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$fixture/mail-agent.env:/run/openclaw-env/mail-agent.env:ro" \
  --entrypoint /bin/sh "$IMAGE" -c '
    test -f /run/openclaw-env/mail-agent.env
    test ! -e /run/openclaw-env/gateway.env
    test ! -e /run/openclaw-secrets/himalaya-imap-password
  '

# Bridge isolation is exercised without connecting to a production network.
docker network create --internal "$backend" >/dev/null
docker network create --internal "$foreign" >/dev/null
docker run -d --name "$server" --network "$backend" --network-alias m4-server \
  --read-only --tmpfs /tmp --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint python3 "$IMAGE" \
  -m http.server 18080 --bind 0.0.0.0 >/dev/null
for _ in $(seq 1 20); do
  if docker run --rm --network "$backend" --entrypoint curl "$IMAGE" \
      --fail --silent --max-time 2 http://m4-server:18080/ >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker run --rm --network "$backend" --entrypoint curl "$IMAGE" \
  --fail --silent --max-time 3 http://m4-server:18080/ >/dev/null
docker run --rm --network "$foreign" --entrypoint python3 "$IMAGE" -c '
import socket
try:
    socket.getaddrinfo("m4-server", 18080)
except socket.gaierror:
    raise SystemExit(0)
raise SystemExit("foreign network resolved backend-only service")
'

# Tini/entrypoint must forward SIGTERM and exit within the bounded grace time.
mkdir -p "$fixture/workspace"
printf '%s\n' '{"layout": 3}' > "$fixture/workspace/.layout-version.json"
docker run -d --name "$signal_container" --network none --read-only --tmpfs /tmp \
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  -e OPENCLAW_LAYOUT_MODE=verify -e OPENCLAW_WORKSPACE=/workspace \
  -v "$fixture/workspace:/workspace:ro" "$IMAGE" python3 -c \
  'import signal,time; signal.signal(signal.SIGTERM, lambda *_: exit(0)); time.sleep(300)' >/dev/null
sleep 1
docker stop --time 5 "$signal_container" >/dev/null
test "$(docker inspect --format '{{.State.ExitCode}}' "$signal_container")" = "0"

# Inspect and exercise hard PID/memory/CPU limits in disposable containers.
docker create --name "$limits_container" --pids-limit 32 --memory 64m --cpus 0.25 \
  --entrypoint /bin/true "$IMAGE" >/dev/null
test "$(docker inspect --format '{{.HostConfig.PidsLimit}}' "$limits_container")" = "32"
test "$(docker inspect --format '{{.HostConfig.Memory}}' "$limits_container")" = "67108864"
test "$(docker inspect --format '{{.HostConfig.NanoCpus}}' "$limits_container")" = "250000000"
docker run --rm --pids-limit 32 --network none --user 1000:1000 \
  --entrypoint python3 "$IMAGE" -c '
import os
import time
for _ in range(128):
    try:
        child = os.fork()
    except OSError:
        raise SystemExit(0)
    if child == 0:
        time.sleep(30)
        raise SystemExit(0)
raise SystemExit("PID limit was not enforced")
'
if docker run --name "$oom_container" --memory 64m --memory-swap 64m --network none \
    --entrypoint python3 "$IMAGE" -c 'bytearray(512 * 1024 * 1024)' >/dev/null 2>&1; then
  echo "OOM-Grenze wurde nicht erzwungen" >&2
  exit 1
fi
test "$(docker inspect --format '{{.State.OOMKilled}}' "$oom_container")" = "true"

echo "Dynamische M4-Haertung erfolgreich: $IMAGE"
