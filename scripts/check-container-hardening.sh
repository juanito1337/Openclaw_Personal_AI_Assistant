#!/usr/bin/env bash
set -euo pipefail
umask 077

IMAGE=${1:?Containerimage angeben}

fail() {
  printf 'ERROR: M4: %s\n' "$*" >&2
  exit 1
}

require_equal() {
  local expected=$1 actual=$2 description=$3
  [[ "$actual" == "$expected" ]] ||
    fail "$description: erwartet '$expected', erhalten '$actual'"
}

docker info >/dev/null || fail "Docker-Daemon nicht erreichbar"
docker image inspect "$IMAGE" >/dev/null || fail "Containerimage nicht vorhanden: $IMAGE"
printf 'M4 Haertungsvertrag gestartet: %s\n' "$IMAGE"

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
  chmod -R u+w "$fixture" >/dev/null 2>&1 || true
  rm -rf "$fixture"
}
trap cleanup EXIT

# Kernel-enforced default: non-root, no capabilities and immutable rootfs.
if ! docker run --rm --network none --read-only --user 1000:1000 \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 \
  --memory 128m --cpus 0.5 --entrypoint /bin/sh "$IMAGE" -c '
    test "$(id -u):$(id -g)" = "1000:1000"
    test "$(awk "/^CapEff:/ {print \$2}" /proc/self/status)" = "0000000000000000"
    ! touch /opt/openclaw-agent/.forbidden 2>/dev/null
  '; then
  fail "Kernelgrenzen fuer Benutzer, Capabilities oder read-only rootfs wurden nicht erzwungen"
fi

# A role sees only explicitly mounted files, not sibling secrets.
printf '%s\n' 'NEXTCLOUD_TOKEN=fixture-only' > "$fixture/mail-agent.env"
if ! docker run --rm --network none --read-only --user 1000:1000 \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$fixture/mail-agent.env:/run/openclaw-env/mail-agent.env:ro" \
  --entrypoint /bin/sh "$IMAGE" -c '
    test -f /run/openclaw-env/mail-agent.env
    test ! -e /run/openclaw-env/gateway.env
    test ! -e /run/openclaw-secrets/himalaya-imap-password
  '; then
  fail "Rolle sieht fehlende oder nicht freigegebene Env-/Secret-Mounts"
fi

# Bridge isolation is exercised without connecting to a production network.
docker network create --internal "$backend" >/dev/null ||
  fail "internes Backend-Testnetz konnte nicht erstellt werden"
docker network create --internal "$foreign" >/dev/null ||
  fail "fremdes internes Testnetz konnte nicht erstellt werden"
if ! docker run -d --name "$server" --network "$backend" --network-alias m4-server \
  --read-only --tmpfs /tmp --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint python3 "$IMAGE" \
  -m http.server 18080 --bind 0.0.0.0 >/dev/null; then
  fail "isolierter Backend-Testserver konnte nicht gestartet werden"
fi
backend_ready=0
for _ in $(seq 1 20); do
  if docker run --rm --network "$backend" --entrypoint curl "$IMAGE" \
      --fail --silent --max-time 2 http://m4-server:18080/ >/dev/null 2>&1; then
    backend_ready=1
    break
  fi
  sleep 1
done
((backend_ready == 1)) || fail "Backend-Service wurde im erlaubten Netz nicht erreichbar"
if ! docker run --rm --network "$backend" --entrypoint curl "$IMAGE" \
  --fail --silent --max-time 3 http://m4-server:18080/ >/dev/null; then
  fail "Backend-Service ist im erlaubten Netz nicht stabil erreichbar"
fi
if ! docker run --rm --network "$foreign" --entrypoint python3 "$IMAGE" -c '
import socket
try:
    socket.getaddrinfo("m4-server", 18080)
except socket.gaierror:
    raise SystemExit(0)
raise SystemExit("foreign network resolved backend-only service")
'; then
  fail "fremdes Netz kann den Backend-Service aufloesen"
fi

# Tini/entrypoint must forward SIGTERM and exit within the bounded grace time.
mkdir -p "$fixture/workspace"
printf '%s\n' '{"layout": 3}' > "$fixture/workspace/.layout-version.json"
# The marker is public test metadata on a read-only mount. CI host UID and image
# UID intentionally differ, so make only this disposable fixture traversable and
# readable instead of depending on numeric owner equality.
chmod 0555 "$fixture/workspace"
chmod 0444 "$fixture/workspace/.layout-version.json"
if ! docker run -d --name "$signal_container" --network none --read-only --tmpfs /tmp \
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  -e OPENCLAW_LAYOUT_MODE=verify -e OPENCLAW_WORKSPACE=/workspace \
  -v "$fixture/workspace:/workspace:ro" "$IMAGE" python3 -c \
  'import signal,time; signal.signal(signal.SIGTERM, lambda *_: exit(0)); print("READY", flush=True); time.sleep(300)' >/dev/null; then
  fail "SIGTERM-Testcontainer konnte nicht gestartet werden"
fi
signal_ready=0
for _ in $(seq 1 50); do
  if docker logs "$signal_container" 2>&1 | grep -qx 'READY'; then
    signal_ready=1
    break
  fi
  sleep 0.1
done
((signal_ready == 1)) || fail "SIGTERM-Handler wurde nicht innerhalb von 5 Sekunden bereit"
docker stop --time 5 "$signal_container" >/dev/null ||
  fail "SIGTERM-Testcontainer beendete sich nicht innerhalb der Grace Time"
signal_exit=$(docker inspect --format '{{.State.ExitCode}}' "$signal_container")
require_equal "0" "$signal_exit" "Exitcode nach weitergeleitetem SIGTERM"

# Inspect and exercise hard PID/memory/CPU limits in disposable containers.
if ! docker create --name "$limits_container" --pids-limit 32 --memory 64m --cpus 0.25 \
  --entrypoint /bin/true "$IMAGE" >/dev/null; then
  fail "Ressourcenlimit-Testcontainer konnte nicht erstellt werden"
fi
require_equal "32" \
  "$(docker inspect --format '{{.HostConfig.PidsLimit}}' "$limits_container")" \
  "PID-Limit"
require_equal "67108864" \
  "$(docker inspect --format '{{.HostConfig.Memory}}' "$limits_container")" \
  "Memory-Limit"
require_equal "250000000" \
  "$(docker inspect --format '{{.HostConfig.NanoCpus}}' "$limits_container")" \
  "CPU-Limit"
if ! docker run --rm --pids-limit 32 --network none --user 1000:1000 \
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
'; then
  fail "PID-Limit wurde nicht nachweisbar erzwungen"
fi
if docker run --name "$oom_container" --memory 64m --memory-swap 64m --network none \
    --entrypoint python3 "$IMAGE" -c '
import sys
try:
    allocation = []
    for allocated_mib in range(1, 513):
        allocation.append(bytearray(1024 * 1024))
        if allocated_mib % 8 == 0:
            print(f"OPENCLAW_MEMORY_PRESSURE: {allocated_mib} MiB", flush=True)
except MemoryError:
    print("OPENCLAW_MEMORY_LIMIT_ENFORCED: MemoryError", file=sys.stderr)
    raise SystemExit(42)
print(f"memory limit was not enforced: {len(allocation)} MiB", file=sys.stderr)
' >"$fixture/oom.stdout" 2>"$fixture/oom.stderr"; then
  echo "OOM-Grenze wurde nicht erzwungen" >&2
  exit 1
fi
oom_memory=$(docker inspect --format '{{.HostConfig.Memory}}' "$oom_container")
oom_exit=$(docker inspect --format '{{.State.ExitCode}}' "$oom_container")
oom_killed=$(docker inspect --format '{{.State.OOMKilled}}' "$oom_container")
oom_error=$(docker inspect --format '{{.State.Error}}' "$oom_container")
pressure_mib=$(
  sed -n 's/^OPENCLAW_MEMORY_PRESSURE: \([0-9][0-9]*\) MiB$/\1/p' \
    "$fixture/oom.stdout" | tail -n 1
)
require_equal "67108864" "$oom_memory" "Memory-Limit des Allokationstests"
if [[ "$oom_killed" != "true" ]]; then
  controlled_memory_error=false
  bounded_sigkill=false
  if [[ "$oom_exit" == "42" ]] &&
      grep -Fq 'OPENCLAW_MEMORY_LIMIT_ENFORCED: MemoryError' "$fixture/oom.stderr"; then
    controlled_memory_error=true
  fi
  if [[ "$oom_exit" == "137" && -z "$oom_error" && "$pressure_mib" =~ ^[0-9]+$ ]] &&
      ((pressure_mib >= 8 && pressure_mib < 512)); then
    bounded_sigkill=true
  fi
  if [[ "$controlled_memory_error" != "true" && "$bounded_sigkill" != "true" ]]; then
    cat "$fixture/oom.stdout" "$fixture/oom.stderr" >&2
    fail "Speicherlimit nicht nachgewiesen: Exit=$oom_exit, OOMKilled=$oom_killed, StateError=${oom_error:-<leer>}, letzter Druck=${pressure_mib:-<fehlend>} MiB"
  fi
fi

echo "Dynamische M4-Haertung erfolgreich: $IMAGE"
