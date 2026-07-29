#!/usr/bin/env bash
set -euo pipefail
umask 077

IMAGE_ROOT=${OPENCLAW_IMAGE_ROOT:-/opt/openclaw-agent}
WORKSPACE=${OPENCLAW_WORKSPACE:-/home/node/.openclaw/workspace}
STATE_ROOT=$(dirname "$WORKSPACE")
CONFIG_ROOT=${OPENCLAW_CONTAINER_CONFIG_DIR:-/etc/openclaw-agent}
SECRET_ROOT=${OPENCLAW_CONTAINER_SECRET_DIR:-/run/openclaw-secrets}

load_env_dir() {
  local directory=$1
  [[ -d "$directory" ]] || return 0
  local file
  while IFS= read -r -d '' file; do
    set -a
    # These files are local administrator-controlled shell-safe env files.
    # shellcheck disable=SC1090
    . "$file"
    set +a
  done < <(find "$directory" -maxdepth 1 -type f -name '*.env' -print0 | sort -z)
}

load_env_dir "$CONFIG_ROOT"
load_env_dir "$SECRET_ROOT"

configure_custom_ca() {
  local ca_dir="$CONFIG_ROOT/ca"
  [[ -d "$ca_dir" ]] || return 0

  local -a certificates=()
  while IFS= read -r -d '' certificate; do
    certificates+=("$certificate")
  done < <(find "$ca_dir" -maxdepth 1 -type f -name '*.crt' -size +0c -print0 | sort -z)
  (( ${#certificates[@]} > 0 )) || return 0

  local runtime_dir="$STATE_ROOT/.container-runtime"
  local bundle="$runtime_dir/ca-certificates.crt"
  local temporary="$bundle.tmp"
  mkdir -p "$runtime_dir"
  cat /etc/ssl/certs/ca-certificates.crt > "$temporary"
  local certificate
  for certificate in "${certificates[@]}"; do
    printf '\n' >> "$temporary"
    cat "$certificate" >> "$temporary"
  done
  chmod 600 "$temporary"
  mv "$temporary" "$bundle"

  export SSL_CERT_FILE=${SSL_CERT_FILE:-$bundle}
  export REQUESTS_CA_BUNDLE=${REQUESTS_CA_BUNDLE:-$bundle}
  export NODE_EXTRA_CA_CERTS=${NODE_EXTRA_CA_CERTS:-$bundle}
}

configure_custom_ca

mkdir -p \
  "$WORKSPACE/mail_agent/data" \
  "$WORKSPACE/personal_assistant/data/container_jobs" \
  "$WORKSPACE/personal_assistant/data/container_logs" \
  "$WORKSPACE/skills"

sync_workspace() {
  local version source_revision source_id marker lock waited=0
  version=$(tr -d '\r\n' < "$IMAGE_ROOT/VERSION")
  source_revision=""
  if [[ -s "$IMAGE_ROOT/SOURCE_REVISION" ]]; then
    source_revision=$(tr -d '\r\n' < "$IMAGE_ROOT/SOURCE_REVISION")
  fi
  if [[ -n "$source_revision" && "$source_revision" != "local" ]]; then
    source_id="$version@$source_revision"
  else
    source_id="$version"
  fi
  marker="$STATE_ROOT/.container-source-version"
  [[ -f "$marker" ]] && [[ "$(cat "$marker")" == "$source_id" ]] && return 0

  lock="$STATE_ROOT/.workspace-sync.lock"
  until mkdir "$lock" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if (( waited > 60 )); then
      echo "Workspace-Synchronisationssperre blieb belegt: $lock" >&2
      return 1
    fi
  done
  trap 'rmdir "$lock" 2>/dev/null || true' RETURN

  for file in AGENTS.md HEARTBEAT.md README.md CHANGELOG.md RELEASE.json VERSION; do
    install -m 600 "$IMAGE_ROOT/$file" "$WORKSPACE/$file"
  done

  mkdir -p "$WORKSPACE/scripts" "$WORKSPACE/mail_agent" "$WORKSPACE/personal_assistant" \
    "$WORKSPACE/skills/personal-assistant"
  rsync -a --delete "$IMAGE_ROOT/scripts/" "$WORKSPACE/scripts/"
  rsync -a --delete \
    --exclude 'config.toml' --exclude 'rules.toml' --exclude 'data/' \
    "$IMAGE_ROOT/mail_agent/" "$WORKSPACE/mail_agent/"
  rsync -a --delete \
    --exclude 'config.toml' --exclude 'resources.toml' --exclude 'policies.toml' \
    --exclude 'tools.toml' --exclude 'data/' \
    "$IMAGE_ROOT/personal_assistant/" "$WORKSPACE/personal_assistant/"
  rsync -a --delete "$IMAGE_ROOT/skills/personal-assistant/" "$WORKSPACE/skills/personal-assistant/"

  if [[ ! -f "$WORKSPACE/mail_agent/config.toml" ]]; then
    cp "$IMAGE_ROOT/mail_agent/config.example.toml" "$WORKSPACE/mail_agent/config.toml"
    chmod 600 "$WORKSPACE/mail_agent/config.toml"
  fi
  if [[ ! -f "$WORKSPACE/mail_agent/rules.toml" ]]; then
    cp "$IMAGE_ROOT/mail_agent/rules.example.toml" "$WORKSPACE/mail_agent/rules.toml"
    chmod 600 "$WORKSPACE/mail_agent/rules.toml"
  fi
  for name in config resources policies tools; do
    if [[ ! -f "$WORKSPACE/personal_assistant/$name.toml" ]]; then
      cp "$IMAGE_ROOT/personal_assistant/$name.example.toml" "$WORKSPACE/personal_assistant/$name.toml"
      chmod 600 "$WORKSPACE/personal_assistant/$name.toml"
    fi
  done

  printf '%s\n' "$source_id" > "$marker"
  chmod 600 "$marker"
  rmdir "$lock"
  trap - RETURN
}

sync_workspace
cd "$WORKSPACE"
exec "$@"
