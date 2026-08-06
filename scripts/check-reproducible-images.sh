#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
revision=${OPENCLAW_SOURCE_REVISION:-$(git rev-parse --verify HEAD)}
created=${OPENCLAW_BUILD_CREATED:-$(git show -s --format=%cI "$revision")}
export OPENCLAW_SOURCE_REVISION="$revision" OPENCLAW_BUILD_CREATED="$created" M7_BUILD_NO_CACHE=1
temporary=$(mktemp -d)
cleanup() {
  rm -rf "$temporary"
}
trap cleanup EXIT

first_started=$(date +%s)
M7_OCI_OUTPUT_DIR="$temporary/first" ./docker/scripts/build-local.sh
first_seconds=$(($(date +%s) - first_started))
second_started=$(date +%s)
M7_OCI_OUTPUT_DIR="$temporary/second" ./docker/scripts/build-local.sh
second_seconds=$(($(date +%s) - second_started))

for role in runtime proxy maintenance; do
  first=$(sha256sum "$temporary/first/$role.tar" | cut -d' ' -f1)
  second=$(sha256sum "$temporary/second/$role.tar" | cut -d' ' -f1)
  if [[ "$first" != "$second" ]]; then
    printf '%s-OCI-Artefakt ist nicht reproduzierbar: %s != %s\n' \
      "$role" "$first" "$second" >&2
    exit 1
  fi
  printf 'Reproduzierbares %s-OCI-Artefakt: sha256:%s\n' "$role" "$first"
done
printf 'Saubere Buildzeiten: erster Lauf=%ss zweiter Lauf=%ss\n' \
  "$first_seconds" "$second_seconds"
