#!/usr/bin/env bash
set -euo pipefail
umask 077

SOURCE_ROOT=$(CDPATH='' cd "$(dirname "$0")/../.." && pwd)
cd "$SOURCE_ROOT"

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Bitte als normaler Benutzer mit Docker-Rechten ausfuehren, nicht mit sudo/root." >&2
  exit 2
fi
command -v docker >/dev/null 2>&1 || {
  echo "Docker ist auf diesem Host nicht installiert." >&2
  exit 2
}
docker info >/dev/null 2>&1 || {
  echo "Der aktuelle Benutzer kann nicht auf die Docker-API zugreifen." >&2
  echo "Nach bereits erfolgter Aufnahme in die docker-Gruppe neu anmelden oder einmalig:" >&2
  echo "  sg docker -c './docker/scripts/live-test-branch.sh'" >&2
  echo "Das Deployment-Skript aendert Gruppenrechte nicht selbst." >&2
  exit 2
}

branch=$(git branch --show-current)
[[ "$branch" == test/* ]] || {
  echo "Live-Test-Deployments sind nur aus einem test/**-Branch erlaubt: $branch" >&2
  exit 2
}
[[ -z "$(git status --porcelain)" ]] || {
  echo "Der Arbeitsbaum enthaelt nicht gepushte Aenderungen. Zuerst committen und pushen." >&2
  exit 2
}

upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
[[ -n "$upstream" ]] || {
  echo "Der Test-Branch besitzt noch keinen Upstream. Zuerst: git push -u origin $branch" >&2
  exit 2
}
local_revision=$(git rev-parse HEAD)
remote_revision=$(git rev-parse "$upstream")
[[ "$local_revision" == "$remote_revision" ]] || {
  echo "Lokaler Branch und $upstream zeigen nicht auf denselben Commit." >&2
  echo "Zuerst pushen beziehungsweise pullen." >&2
  exit 2
}

short_revision=$(git rev-parse --short=12 HEAD)
image_tag="sha-$short_revision"

echo "Aktualisiere das Deployment-Bundle; .env und aktive Hooks bleiben erhalten."
"$SOURCE_ROOT/docker/scripts/refresh-deployment.sh"

deployment_env=/srv/openclaw/deployment/.env
[[ -f "$deployment_env" ]] || { echo "Deployment-Umgebung fehlt: $deployment_env" >&2; exit 2; }
set -a
# Administrator-controlled deployment variables; deploy.sh reads the same file.
# shellcheck disable=SC1090
. "$deployment_env"
set +a
repository=${OPENCLAW_IMAGE_REPOSITORY:?OPENCLAW_IMAGE_REPOSITORY fehlt in $deployment_env}

resolve_digest() {
  local tagged=$1 digest
  docker pull "$tagged" >/dev/null
  digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$tagged")
  [[ "$digest" =~ @sha256:[0-9a-f]{64}$ ]] || {
    echo "Kein unveraenderlicher Digest fuer $tagged ermittelbar: $digest" >&2
    return 1
  }
  printf '%s\n' "$digest"
}

runtime_image=$(resolve_digest "$repository:$image_tag")
proxy_image=$(resolve_digest "$repository:$image_tag-proxy")
maintenance_image=$(resolve_digest "$repository:$image_tag-maintenance")

echo "Deploye den signierten Test-Rollensatz fuer: $image_tag"
echo "Das normale Deployment stoppt den bisherigen Writer und erstellt vor Schreibtests ein verifiziertes lokales Backup."
export OPENCLAW_EXPECTED_SOURCE_REVISION="$local_revision"
exec /srv/openclaw/deployment/scripts/deploy.sh \
  "$runtime_image" "$proxy_image" "$maintenance_image"
