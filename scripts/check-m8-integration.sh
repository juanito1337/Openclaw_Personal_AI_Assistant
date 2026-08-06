#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
COMPOSE_FILE="$ROOT/tests/integration/m8/compose.yaml"
PROJECT="openclaw-m8-$PPID-$$"
NETWORK="${PROJECT}_m8"
WRITER_A="${PROJECT}-writer-a"

cleanup() {
  docker rm -f "$WRITER_A" >/dev/null 2>&1 || true
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker info >/dev/null
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" config --quiet
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" pull --quiet
stack_started_ns=$(date +%s%N)
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --wait fake-services
stack_ready_ns=$(date +%s%N)

scenario_started_ns=$(date +%s%N)
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm scenario
scenario_finished_ns=$(date +%s%N)

fake_id=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" ps -q fake-services)
docker network disconnect "$NETWORK" "$fake_id"
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
  scenario python3 /m8/network_probe.py --expect-unreachable
docker network connect --alias fake-services "$NETWORK" "$fake_id"
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
  scenario python3 /m8/network_probe.py

crash_started_ns=$(date +%s%N)
docker kill --signal KILL "$fake_id" >/dev/null
[[ $(docker inspect --format '{{.State.Status}}' "$fake_id") == "exited" ]]
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --wait fake-services
crash_recovered_ns=$(date +%s%N)
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
  scenario python3 /m8/network_probe.py

docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run -d --no-deps --name "$WRITER_A" \
  writer-probe python3 /m8/writer_probe.py --hold-seconds 30 >/dev/null
for _ in $(seq 1 20); do
  docker logs "$WRITER_A" 2>&1 | grep -q "ACQUIRED sole mail writer" && break
  sleep 0.25
done
docker logs "$WRITER_A" 2>&1 | grep -q "ACQUIRED sole mail writer"
set +e
second_output=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
  writer-probe python3 /m8/writer_probe.py 2>&1)
second_status=$?
set -e
[[ $second_status -eq 73 ]]
grep -q "REJECTED second mail writer" <<<"$second_output"
docker kill --signal KILL "$WRITER_A" >/dev/null
docker rm "$WRITER_A" >/dev/null
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
  writer-probe python3 /m8/writer_probe.py | grep -q "ACQUIRED sole mail writer"

mkdir -p "$ROOT/build"
python3 - "$ROOT/build/m8-integration.json" \
  "$stack_started_ns" "$stack_ready_ns" "$scenario_started_ns" "$scenario_finished_ns" \
  "$crash_started_ns" "$crash_recovered_ns" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

values = [int(value) for value in sys.argv[2:]]
payload = {
    "schema_version": 1,
    "milestone": "M8",
    "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
    "scope": "isolated internal Docker network with fixture-only services",
    "stack_ready_seconds": round((values[1] - values[0]) / 1_000_000_000, 6),
    "protocol_scenario_seconds": round((values[3] - values[2]) / 1_000_000_000, 6),
    "container_crash_recovery_seconds": round((values[5] - values[4]) / 1_000_000_000, 6),
    "checks": {
        "imap": True,
        "smtp": True,
        "webdav_carddav_caldav": True,
        "etag_conflict": True,
        "ollama": True,
        "market_data": True,
        "clamav_clean_and_eicar": True,
        "network_loss_and_recovery": True,
        "container_sigkill_and_recovery": True,
        "single_mail_writer": True,
    },
    "production_accounts_or_secrets": False,
    "published_host_ports": 0,
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "M8-Integration erfolgreich: Protokolle, ETag, Netzwerk, Crash und Single-Writer."
