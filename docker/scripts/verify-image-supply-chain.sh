#!/usr/bin/env bash
set -euo pipefail
umask 077

image=${1:?Unveraenderliche Image-Referenz angeben}
expected_revision=${2:?Erwartete Git-Revision angeben}
expected_role=${3:?Erwartete Image-Rolle angeben}
expected_release=${OPENCLAW_EXPECTED_RELEASE:-3.4.0-r28}
issuer=${OPENCLAW_SIGNATURE_ISSUER:-https://token.actions.githubusercontent.com}
identity=${OPENCLAW_SIGNATURE_IDENTITY_REGEXP:-'^https://github.com/juanito1337/Openclaw_Personal_AI_Assistant/.github/workflows/container.yml@refs/(heads/main|heads/test/.+|tags/r.+)$'}
cosign_image='ghcr.io/sigstore/cosign/cosign:v3.1.3@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8'

[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "Deployment akzeptiert nur eine unveraenderliche Image-Referenz mit SHA-256-Digest: $image" >&2
  exit 1
}
command -v docker >/dev/null || { echo "docker fehlt" >&2; exit 2; }

docker_config_root=${DOCKER_CONFIG:-${HOME:-}/.docker}
docker_config="$docker_config_root/config.json"
cosign_command=(
  docker run --rm --read-only
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --user "$(id -u):$(id -g)"
  --env HOME=/tmp
  --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=700,uid=$(id -u),gid=$(id -g)"
)
if [[ -r "$docker_config" ]]; then
  cosign_command+=(
    --env DOCKER_CONFIG=/docker-config
    --volume "$docker_config:/docker-config/config.json:ro"
  )
fi
cosign_command+=("$cosign_image")

"${cosign_command[@]}" verify \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$image" >/dev/null
"${cosign_command[@]}" verify-attestation \
  --type slsaprovenance1 \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$image" >/dev/null
"${cosign_command[@]}" verify-attestation \
  --type spdxjson \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$issuer" \
  "$image" >/dev/null

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
printf 'Signatur, Provenance, SBOM und OCI-Identitaet verifiziert: %s\n' "$image"
