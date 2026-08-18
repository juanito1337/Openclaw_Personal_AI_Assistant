#!/usr/bin/env bash
set -euo pipefail
ROOT=$(CDPATH='' cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
runtime=${1:-openclaw-agent:r28-local}
proxy=${2:-${runtime}-proxy}
maintenance=${3:-${runtime}-maintenance}
revision=${OPENCLAW_SOURCE_REVISION:-$(git rev-parse --verify HEAD 2>/dev/null || printf 'local')}
created=${OPENCLAW_BUILD_CREATED:-$(git show -s --format=%cI HEAD 2>/dev/null || printf '1970-01-01T00:00:00Z')}
source_epoch=${SOURCE_DATE_EPOCH:-$(git show -s --format=%ct HEAD 2>/dev/null || printf '0')}

common_args=(
  --provenance=false
  --build-arg "OPENCLAW_BASE_IMAGE=${OPENCLAW_BASE_IMAGE:-ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac}"
  --build-arg "NODE_BASE_IMAGE=${NODE_BASE_IMAGE:-node:24-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a}"
  --build-arg "PYTHON_BASE_IMAGE=${PYTHON_BASE_IMAGE:-python:3.11-alpine3.22@sha256:a4fc589b32e824f3f02ed9d7e7be19518aa47e105b80416336af9f202275a489}"
  --build-arg "HIMALAYA_VERSION=${HIMALAYA_VERSION:-1.2.0}"
  --build-arg "HIMALAYA_ARCHIVE_SHA256=${HIMALAYA_ARCHIVE_SHA256:-e04e6382e3e664ef34b01afa1a2216113194a2975d2859727647b22d9b36d4e4}"
  --build-arg "HIMALAYA_SHA256=${HIMALAYA_SHA256:-9529d2584add1c4343f32524e6f985e7c98d491f3b854747318020eb1ec1df7f}"
  --build-arg "OPENCLAW_SOURCE_REVISION=$revision"
  --build-arg "OPENCLAW_BUILD_CREATED=$created"
  --build-arg "SOURCE_DATE_EPOCH=$source_epoch"
  --build-arg "OPENCLAW_VERSION=3.4.0-r28"
)
build_flags=()
if [[ ${M7_BUILD_NO_CACHE:-0} == 1 ]]; then
  build_flags+=(--no-cache)
fi

build_target() {
  local target=$1 tag=$2 artifact=$3
  if [[ -n ${M7_OCI_OUTPUT_DIR:-} ]]; then
    mkdir -p "$M7_OCI_OUTPUT_DIR"
    docker buildx build "${build_flags[@]}" "${common_args[@]}" \
      --output="type=oci,dest=$M7_OCI_OUTPUT_DIR/$artifact,rewrite-timestamp=true" \
      --target "$target" .
  else
    docker build "${build_flags[@]}" "${common_args[@]}" --target "$target" -t "$tag" .
  fi
}

build_target runtime "$runtime" runtime.tar
build_target proxy-runtime "$proxy" proxy.tar
build_target maintenance-runtime "$maintenance" maintenance.tar
if [[ -n ${M7_OCI_OUTPUT_DIR:-} ]]; then
  printf 'Gebaut: OCI-Artefakte=%s revision=%s\n' "$M7_OCI_OUTPUT_DIR" "$revision"
else
  printf 'Gebaut: runtime=%s proxy=%s maintenance=%s revision=%s\n' \
    "$runtime" "$proxy" "$maintenance" "$revision"
fi
