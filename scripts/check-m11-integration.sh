#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
COMPOSE_FILE="$ROOT/tests/integration/m11/compose.yaml"
PROJECT="openclaw-m11-$PPID-$$"
NETWORK="${PROJECT}_m11"
IMAGE=${OPENCLAW_M11_RUNTIME_IMAGE:-openclaw-agent:m11-candidate}

cleanup() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" down --volumes --remove-orphans \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT

export OPENCLAW_M11_RUNTIME_IMAGE="$IMAGE"
docker info >/dev/null
docker image inspect "$IMAGE" >/dev/null
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" config --quiet
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" pull --quiet fake-services state-init
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm state-init

stack_started_ns=$(date +%s%N)
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --wait fake-services
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm projection-publisher
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --wait sync-worker
stack_ready_ns=$(date +%s%N)
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm gateway

run_scenario() {
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
    gateway "$@"
}

restart_sync() {
  run_scenario clear-ready
  docker compose -p "$PROJECT" -f "$COMPOSE_FILE" restart --timeout 2 sync-worker >/dev/null
  for _ in $(seq 1 30); do
    if [[ $(docker inspect --format '{{.State.Health.Status}}' \
      "${PROJECT}-sync-worker-1" 2>/dev/null || true) == healthy ]]; then
      return 0
    fi
    sleep 1
  done
  echo "M11-Syncworker wurde nach Restart nicht gesund." >&2
  return 1
}

run_scenario mutate add
run_scenario reconcile --expect new
restart_sync
run_scenario gateway --expect new

run_scenario metrics before-move
run_scenario mutate move-alpha
run_scenario reconcile --expect move
restart_sync
run_scenario metrics after-move --compare before-move

run_scenario mutate copy-alpha
run_scenario reconcile --expect copy
restart_sync
run_scenario gateway --expect copy

run_scenario mutate partial-on
run_scenario mutate delete-beta
run_scenario reconcile --expect partial
run_scenario mutate partial-off
run_scenario reconcile --expect delete
restart_sync
run_scenario gateway --expect deleted

run_scenario metrics before-locator-only
run_scenario mutate quarantine-copy
run_scenario reconcile --expect quarantine
run_scenario mutate rename-archive
run_scenario reconcile --expect rename
run_scenario mutate uidvalidity-reset
run_scenario reconcile --expect uidvalidity
restart_sync
run_scenario metrics after-locator-only --compare before-locator-only
run_scenario gateway --expect locator

fake_id=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" ps -q fake-services)
docker network disconnect "$NETWORK" "$fake_id"
run_scenario network --expect-unreachable
docker network connect --alias fake-services "$NETWORK" "$fake_id"
run_scenario network

crash_started_ns=$(date +%s%N)
sync_id=$(docker compose -p "$PROJECT" -f "$COMPOSE_FILE" ps -q sync-worker)
docker kill --signal KILL "$sync_id" >/dev/null
[[ $(docker inspect --format '{{.State.Status}}' "$sync_id") == exited ]]
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --wait sync-worker
crash_recovered_ns=$(date +%s%N)
run_scenario gateway --expect locator

mkdir -p "$ROOT/build"
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" run --rm --no-deps \
  gateway summary > "$ROOT/build/m11-resource-summary.json"
python3 - "$ROOT/build/m11-integration.json" \
  "$ROOT/build/m11-resource-summary.json" \
  "$stack_started_ns" "$stack_ready_ns" "$crash_started_ns" "$crash_recovered_ns" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

resources = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
values = [int(value) for value in sys.argv[3:]]
payload = {
    "schema_version": 1,
    "milestone": "M11.8",
    "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
    "scope": "isolated internal Docker network with synthetic example.invalid mail",
    "stack_ready_seconds": round((values[1] - values[0]) / 1_000_000_000, 6),
    "sync_worker_crash_recovery_seconds": round((values[3] - values[2]) / 1_000_000_000, 6),
    "resources": resources,
    "checks": {
        "fake_imap_protocol": True,
        "clamav_clean_infected_error": True,
        "projection_publisher": True,
        "sync_worker_and_fts": True,
        "fake_embedding_service": True,
        "gateway_hybrid_search": True,
        "incremental_new_mail": True,
        "move_copy_delete_quarantine": True,
        "folder_rename_and_uidvalidity_reset": True,
        "locator_only_content_reuse": True,
        "partial_scan_fail_closed": True,
        "semantic_failure_lexical_fallback": True,
        "network_loss_and_recovery": True,
        "sync_worker_sigkill_and_restart": True,
    },
    "production_accounts_or_secrets": False,
    "productive_mounts": False,
    "published_host_ports": 0,
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "M11-Integration erfolgreich: Index, FTS, Embeddings, Locator, Netz und Crash."
