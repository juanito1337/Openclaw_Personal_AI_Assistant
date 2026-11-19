#!/usr/bin/env bash
set -euo pipefail
umask 077
SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)

image=${1:?Unveraenderliche Image-Referenz angeben}
expected_revision=${2:?Erwartete Git-Revision angeben}
expected_role=${3:?Erwartete Image-Rolle angeben}
issuer=${OPENCLAW_SIGNATURE_ISSUER:-https://token.actions.githubusercontent.com}
identity=${OPENCLAW_SIGNATURE_IDENTITY_REGEXP:-'^https://github.com/juanito1337/Openclaw_Personal_AI_Assistant/.github/workflows/container.yml@refs/(heads/main|heads/test/.+|tags/r.+)$'}
cosign_image='ghcr.io/sigstore/cosign/cosign:v3.1.3@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8'

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "Deployment akzeptiert nur eine unveraenderliche Image-Referenz mit SHA-256-Digest: $image" >&2
  exit 1
}
expected_release=${OPENCLAW_EXPECTED_RELEASE:?OPENCLAW_EXPECTED_RELEASE fehlt in der Deployment-Konfiguration}
command -v docker >/dev/null || { echo "docker fehlt" >&2; exit 2; }

cosign_command=(
  docker run --rm --read-only
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --user "$(id -u):$(id -g)"
  --env HOME=/tmp
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=700,uid=$(id -u),gid=$(id -g)"
)
trust_arguments=()
tuf_deadline=''
check_tuf_deadline() {
  if [[ -n "$tuf_deadline" ]]; then
    python3 "$SCRIPT_DIR/sigstore-tuf-deadline.py" --check "$tuf_deadline"
  fi
}

if [[ -n ${OPENCLAW_SIGSTORE_TUF_REPOSITORY:-} ]]; then
  # The repository is untrusted transport, never a caller-supplied trust anchor.
  # Initialize a fresh cache from the pinned Cosign image's embedded root only.
  tuf_work=$(mktemp -d /tmp/openclaw-sigstore.XXXXXXXX)
  trap 'rm -rf -- "$tuf_work"' EXIT
  mkdir -m 700 "$tuf_work/repository" "$tuf_work/cache"
  python3 "$SCRIPT_DIR/prepare-sigstore-tuf.py" \
    "$OPENCLAW_SIGSTORE_TUF_REPOSITORY" "$tuf_work/repository" >/dev/null
  (
    ulimit -f 1024
    "${cosign_command[@]}" --network none --env TUF_ROOT=/cache/tuf \
      --volume "$tuf_work/repository:/repository:ro" \
      --volume "$tuf_work/cache:/cache:rw" \
      "$cosign_image" initialize --mirror file:///repository \
      >"$tuf_work/initialize-status.json"
  )
  tuf_deadline=$(python3 "$SCRIPT_DIR/sigstore-tuf-deadline.py" "$tuf_work/initialize-status.json")
  check_tuf_deadline
  trusted_root="$tuf_work/cache/tuf/targets/trusted_root.json"
  [[ -d "$tuf_work/cache/tuf" && ! -L "$tuf_work/cache/tuf" && \
     -d "$tuf_work/cache/tuf/targets" && ! -L "$tuf_work/cache/tuf/targets" && \
     -f "$trusted_root" && ! -L "$trusted_root" && -s "$trusted_root" ]] || {
    echo "Sigstore-Initialisierung lieferte kein verifiziertes Trust-Target." >&2
    exit 1
  }
  cosign_command+=(--volume "$trusted_root:/verified/trusted_root.json:ro")
  trust_arguments=(--trusted-root /verified/trusted_root.json)
fi

docker_config_root=${DOCKER_CONFIG:-${HOME:-}/.docker}
docker_config="$docker_config_root/config.json"
if [[ -r "$docker_config" ]]; then
  cosign_command+=(
    --env DOCKER_CONFIG=/docker-config
    --volume "$docker_config:/docker-config/config.json:ro"
  )
fi
cosign_command+=("$cosign_image")

check_tuf_deadline
"${cosign_command[@]}" verify \
  "${trust_arguments[@]}" \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$image" >/dev/null
check_tuf_deadline
"${cosign_command[@]}" verify-attestation \
  --type slsaprovenance1 \
  "${trust_arguments[@]}" \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$image" >/dev/null
check_tuf_deadline
"${cosign_command[@]}" verify-attestation \
  --type spdxjson \
  "${trust_arguments[@]}" \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$image" >/dev/null
check_tuf_deadline

docker pull "$image" >/dev/null
actual_revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
actual_release=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")
actual_role=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.openclaw.role"}}' "$image")
[[ "$actual_revision" == "$expected_revision" ]] || {
  echo "Image-Revision stimmt nicht: erwartet $expected_revision, erhalten ${actual_revision:-<fehlend>}" >&2
  exit 1
}
[[ "$actual_release" == "$expected_release" ]] || {
  echo "Image-Release stimmt nicht: erwartet $expected_release, erhalten ${actual_release:-<fehlend>}" >&2
  exit 1
}
[[ "$actual_role" == "$expected_role" ]] || {
  echo "Image-Rolle stimmt nicht: erwartet $expected_role, erhalten ${actual_role:-<fehlend>}" >&2
  exit 1
}
check_tuf_deadline
printf 'Signatur, Provenance, SBOM und OCI-Identitaet verifiziert: %s\n' "$image"
