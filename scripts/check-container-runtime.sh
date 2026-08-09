#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
IMAGE=${1:?Containerimage angeben}

fail() {
  printf 'ERROR: M3: %s\n' "$*" >&2
  exit 1
}

require_equal() {
  local expected=$1 actual=$2 description=$3
  [[ "$actual" == "$expected" ]] ||
    fail "$description: erwartet '$expected', erhalten '$actual'"
}

docker info >/dev/null || fail "Docker-Daemon nicht erreichbar"
docker image inspect "$IMAGE" >/dev/null || fail "Containerimage nicht vorhanden: $IMAGE"

revision=$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
    "$IMAGE"
)
layout_min=$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.openclaw.layout-min"}}' \
    "$IMAGE"
)
layout_max=$(
  docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.openclaw.layout-max"}}' \
    "$IMAGE"
)
if [[ -z "$revision" || "$revision" == "<no value>" ]]; then
  fail "OCI-Label org.opencontainers.image.revision fehlt in $IMAGE"
fi
require_equal "1" "$layout_min" \
  "OCI-Label org.opencontainers.image.openclaw.layout-min in $IMAGE"
require_equal "3" "$layout_max" \
  "OCI-Label org.opencontainers.image.openclaw.layout-max in $IMAGE"
printf 'M3 OCI-Vertrag verifiziert: %s (%s, Layout %s..%s)\n' \
  "$IMAGE" "$revision" "$layout_min" "$layout_max"

fixture=$(mktemp -d)
state="$fixture/state"
config="$fixture/config"
secrets="$fixture/secrets"
himalaya="$fixture/himalaya"
mkdir -p "$state/workspace/scripts" "$state/workspace/mail_agent" \
  "$config" "$secrets" "$himalaya"
printf '%s\n' '#!/bin/sh' 'touch /home/node/.openclaw/tampered-script-ran' \
  > "$state/workspace/scripts/assistant.sh"
chmod 700 "$state/workspace/scripts/assistant.sh"
printf '%s\n' '[mail]' 'account = "m2-fixture"' \
  > "$state/workspace/mail_agent/config.toml"
printf '%s\n' '# IDENTITY.md' '' '- Name: Ada Fixture' \
  > "$state/workspace/IDENTITY.md"
printf '%s\n' '# SOUL.md' '' 'Praezise und warm.' \
  > "$state/workspace/SOUL.md"
printf '%s\n' '# USER.md' '' '- Name: Jan Fixture' \
  > "$state/workspace/USER.md"
printf '%s\n' \
  '{"version":1,"setupCompletedAt":"2026-07-21T06:48:03.443Z"}' \
  > "$state/workspace/openclaw-workspace-state.json"
config_before=$(sha256sum "$state/workspace/mail_agent/config.toml" | awk '{print $1}')
# Bind mounts preserve host ownership. Give only this disposable fixture the
# image's runtime UID:GID so the production ownership preflight is exercised
# identically even when the caller reached Docker through `sg docker` or CI uses
# a different host UID.
chmod -R a+rX "$fixture"
if ! docker run --rm --user 0:0 --entrypoint /bin/chown \
  -v "$state:/fixture" "$IMAGE" -R node:node /fixture; then
  fail "Fixture-Eigentuemer konnte nicht auf die Runtime-UID:GID gesetzt werden"
fi

name_one="openclaw-m3-$RANDOM-1"
name_two="openclaw-m3-$RANDOM-2"
artifact_id=""
cleanup() {
  docker rm -f "$name_one" "$name_two" >/dev/null 2>&1 || true
  if [[ -n "$artifact_id" ]]; then
    docker rm -f "$artifact_id" >/dev/null 2>&1 || true
  fi
  docker run --rm --user 0:0 --entrypoint /bin/chown \
    -v "$fixture:/fixture" "$IMAGE" -R "$(id -u):$(id -g)" /fixture \
    >/dev/null 2>&1 || true
  # docker export preserves the deliberately read-only image modes. Restore
  # owner write access only inside this mktemp fixture so cleanup can remove it.
  chmod -R u+w "$fixture" >/dev/null 2>&1 || true
  rm -rf "$fixture"
}
trap cleanup EXIT

runtime_args=(
  --read-only
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,mode=1777"
  --network none
  -v "$state:/home/node/.openclaw"
  -v "$himalaya:/home/node/.config/himalaya:ro"
  -v "$config:/etc/openclaw-agent:ro"
  -v "$secrets:/run/openclaw-secrets:ro"
)

# Two independent container starts exercise the shared layout lock without
# contacting any productive service or network endpoint.
docker run --name "$name_one" "${runtime_args[@]}" "$IMAGE" /bin/true \
  >"$fixture/parallel-one.log" 2>&1 &
pid_one=$!
docker run --name "$name_two" "${runtime_args[@]}" "$IMAGE" /bin/true \
  >"$fixture/parallel-two.log" 2>&1 &
pid_two=$!
parallel_failed=0
wait "$pid_one" || parallel_failed=1
wait "$pid_two" || parallel_failed=1
if (( parallel_failed != 0 )); then
  cat "$fixture/parallel-one.log" "$fixture/parallel-two.log" >&2
  echo "Parallele M3-Layoutinitialisierung fehlgeschlagen." >&2
  exit 1
fi
docker rm "$name_one" "$name_two" >/dev/null ||
  fail "erfolgreich beendete parallele Layoutcontainer konnten nicht entfernt werden"

# Container-created directories intentionally belong to the image UID and may be
# mode 0700. Inspect through a disposable root container instead of weakening
# those modes or confusing a different CI host UID with a missing path.
if ! docker run --rm --user 0:0 --network none --read-only \
  --entrypoint /bin/sh -v "$state:/fixture:ro" "$IMAGE" -c '
set -eu
fail() {
  printf "ERROR: M3 fixture: %s\n" "$*" >&2
  exit 1
}
assert_absent() {
  [ ! -e "$1" ] && [ ! -L "$1" ] || fail "$2: unerwartet vorhanden: $1"
}
assert_file() {
  [ -f "$1" ] || fail "$2: Datei fehlt: $1"
}
assert_directory() {
  [ -d "$1" ] || fail "$2: Verzeichnis fehlt: $1"
}
assert_equal() {
  [ "$1" = "$2" ] || fail "$3: erwartet $1, erhalten $2"
}
assert_absent /fixture/tampered-script-ran "Legacy-Skript wurde ausgefuehrt"
assert_absent /fixture/workspace/scripts/assistant.sh "Legacy-Runtime-Skript wurde nicht entfernt"
assert_equal /opt/openclaw-agent/AGENTS.md \
  "$(readlink /fixture/workspace/AGENTS.md 2>/dev/null || true)" \
  "Release-Link AGENTS.md"
assert_equal /opt/openclaw-agent/skills/personal-assistant \
  "$(readlink /fixture/workspace/skills/personal-assistant 2>/dev/null || true)" \
  "Release-Link personal-assistant"
actual_layout=$(python3 -P -c \
  "import json,sys; print(json.load(open(sys.argv[1]))[\"layout\"])" \
  /fixture/.container-layout.json) || fail "Layoutmarker ist nicht als JSON lesbar"
assert_equal 3 "$actual_layout" "publizierte Layoutversion"
assert_file /fixture/v3/instance/.layout-version.json "Instanz-Layoutmarker"
assert_file /fixture/v3/instance/IDENTITY.md "aktives Identitaetsprofil"
assert_file /fixture/v3/instance/SOUL.md "aktives Persoenlichkeitsprofil"
assert_file /fixture/v3/instance/USER.md "aktives Nutzerprofil"
assert_file /fixture/v3/instance/openclaw-workspace-state.json "aktiver Setupstatus"
grep -q -- "- Name: Ada Fixture" /fixture/v3/instance/IDENTITY.md ||
  fail "aktives Identitaetsprofil wurde nicht bytegetreu migriert"
grep -q -- '"setupCompletedAt"' \
  /fixture/v3/instance/openclaw-workspace-state.json ||
  fail "abgeschlossener Setupstatus wurde nicht aktiv migriert"
assert_absent /fixture/v3/instance/local-workspace/IDENTITY.md \
  "Identitaetsprofil wurde faelschlich quarantiniert"
assert_absent /fixture/v3/instance/local-workspace/openclaw-workspace-state.json \
  "Setupstatus wurde faelschlich quarantiniert"
assert_directory /fixture/v3/domains/mail "Mail-Domaene"
assert_directory /fixture/v3/domains/portfolio "Portfolio-Domaene"
assert_directory /fixture/v3/domains/knowledge "Knowledge-Domaene"
assert_directory /fixture/v3/shared/core "gemeinsamer Core-State"
assert_directory /fixture/v3/shared/coordination "gemeinsamer Koordinations-State"
assert_directory /fixture/.layout-migrations/backups "Migrationsbackup-Wurzel"
backup_count=$(find /fixture/.layout-migrations/backups -name "*.tar.gz" -type f | wc -l)
assert_equal 1 "$backup_count" "Anzahl atomarer Migrationsbackups"
'; then
  fail "Layoutnachbedingungen im UID-unabhaengigen Pruefcontainer verletzt"
fi

if ! status_json=$(docker run --rm "${runtime_args[@]}" "$IMAGE" \
  /opt/openclaw-agent/scripts/assistant.sh status 2>"$fixture/status.stderr"); then
  cat "$fixture/status.stderr" >&2
  echo "M3-Statuspruefung im isolierten Container fehlgeschlagen." >&2
  exit 1
fi
if ! STATUS_JSON="$status_json" EXPECTED_REVISION="$revision" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["STATUS_JSON"])
runtime = payload["runtime"]
assert runtime["ok"], runtime
assert runtime["layout"] == 3, runtime
assert runtime["state_paths"]["coordination"], runtime
assert runtime["source_revision"] == os.environ["EXPECTED_REVISION"], runtime
assert runtime["oci_revision"] == os.environ["EXPECTED_REVISION"], runtime
assert runtime["module_paths"]["personal_assistant"].startswith("/opt/openclaw-agent/"), runtime
assert runtime["module_paths"]["mail_agent"].startswith("/opt/openclaw-agent/"), runtime
assert runtime["executable_paths"]["assistant"] == "/opt/openclaw-agent/scripts/assistant.sh"
PY
then
  fail "Status-JSON verletzt den M3-Laufzeitvertrag"
fi

# A restart with the same image may refresh release-owned links, but it must not
# rewrite an existing instance configuration.
if ! docker run --rm "${runtime_args[@]}" "$IMAGE" /bin/true >/dev/null 2>&1; then
  fail "idempotenter Neustart mit bestehendem Layout fehlgeschlagen"
fi
config_after=$(sha256sum "$state/workspace/mail_agent/config.toml" | awk '{print $1}')
require_equal "$config_before" "$config_after" "Hash der bestehenden Instanzkonfiguration"

role_args=(
  --read-only
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,mode=1777"
  --network none
  -e OPENCLAW_LAYOUT_MODE=verify
  -v "$state/v3/instance:/home/node/.openclaw/workspace:ro"
  -v "$config:/etc/openclaw-agent:ro"
  -v "$secrets:/run/openclaw-secrets:ro"
)

# M3 verifies actual kernel mount boundaries, not only YAML text. Each probe
# writes only to its fixture-owned roots and confirms unrelated roots are absent
# or read-only.
if ! docker run --rm "${role_args[@]}" \
  -v "$state/v3/domains/portfolio:/var/lib/openclaw/portfolio" \
  -v "$state/v3/shared/coordination:/var/lib/openclaw/coordination" \
  "$IMAGE" /bin/sh -c \
  'test ! -e /var/lib/openclaw/mail && test ! -e /var/lib/openclaw/knowledge && touch /var/lib/openclaw/portfolio/.m3-probe'; then
  fail "Portfolio-Rollenmount verletzt die Domaenengrenze"
fi
if ! docker run --rm "${role_args[@]}" \
  -v "$state/v3/domains/mail:/var/lib/openclaw/mail" \
  -v "$state/v3/shared/core:/var/lib/openclaw/core" \
  -v "$state/v3/shared/coordination:/var/lib/openclaw/coordination" \
  "$IMAGE" /bin/sh -c \
  'test ! -e /var/lib/openclaw/portfolio && test ! -e /var/lib/openclaw/knowledge && touch /var/lib/openclaw/mail/.m3-probe'; then
  fail "Mail-Rollenmount verletzt die Domaenengrenze"
fi
if ! docker run --rm "${role_args[@]}" \
  -v "$state/v3/domains/monitoring:/var/lib/openclaw/monitoring" \
  -v "$state/v3/domains/knowledge:/var/lib/openclaw/knowledge:ro" \
  -v "$state/v3/shared/core:/var/lib/openclaw/core:ro" \
  -v "$state/v3/shared/coordination:/var/lib/openclaw/coordination" \
  "$IMAGE" /bin/sh -c \
  'touch /var/lib/openclaw/monitoring/.m3-probe && ! touch /var/lib/openclaw/core/.forbidden 2>/dev/null'; then
  fail "Monitoring-Rollenmount verletzt Schreib-/Leserechte"
fi

# Even root cannot change image code when the container root filesystem is
# read-only. The writable state mount remains available only for instance data.
if ! docker run --rm --user 0:0 --read-only --entrypoint /bin/sh "$IMAGE" -c \
  'if touch /opt/openclaw-agent/.m2-write-test 2>/dev/null; then exit 1; fi'; then
  fail "Image-Code ist bei read-only rootfs veraenderbar"
fi

mkdir -p "$fixture/rootfs"
artifact_id=$(docker create "$IMAGE") || fail "Artefaktcontainer konnte nicht erstellt werden"
if ! docker export "$artifact_id" | tar -x -C "$fixture/rootfs"; then
  fail "Image-Dateisystem konnte nicht exportiert werden"
fi
python3 "$ROOT/scripts/check_artifact.py" image-root "$fixture/rootfs" ||
  fail "exportiertes Image verletzt den Artefaktvertrag"
docker rm "$artifact_id" >/dev/null || fail "Artefaktcontainer konnte nicht entfernt werden"
artifact_id=""
echo "Dynamische M3-Containerpruefung erfolgreich: $IMAGE ($revision, Layout 1..3)"
