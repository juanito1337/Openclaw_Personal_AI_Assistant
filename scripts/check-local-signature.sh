#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
python=${M7_PYTHON:-$ROOT/.venv/bin/python}
[[ -x "$python" ]] || python=python3
cosign=$($python scripts/m7_supply_chain.py lock-value scanner_images.cosign)
subject=${1:-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}
[[ "$subject" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "Ungueltiger Testdigest" >&2; exit 2; }
temporary=$(mktemp -d)
cleanup() {
  rm -rf "$temporary"
}
trap cleanup EXIT
chmod 0700 "$temporary"
printf '%s\n' "$subject" > "$temporary/subject.txt"
# The pinned container runs with the invoking UID and needs write access only to
# this disposable directory. The release workflow uses keyless OIDC and Rekor;
# this local test key exists solely to exercise positive and negative behavior.
chmod 0777 "$temporary"
docker run --rm --user "$(id -u):$(id -g)" \
  --env HOME=/work \
  --env COSIGN_PASSWORD=m7-local-regression \
  --volume "$temporary:/work" --workdir /work \
  "$cosign" generate-key-pair >/dev/null
docker run --rm --user "$(id -u):$(id -g)" \
  --env HOME=/work \
  --volume "$temporary:/work" --workdir /work \
  "$cosign" signing-config create \
  --no-default-fulcio --no-default-oidc --no-default-rekor --no-default-tsa \
  --out local-signing-config.json >/dev/null
docker run --rm --user "$(id -u):$(id -g)" \
  --env HOME=/work \
  --env COSIGN_PASSWORD=m7-local-regression \
  --volume "$temporary:/work" --workdir /work \
  "$cosign" sign-blob --signing-config local-signing-config.json --key cosign.key \
  --bundle signature.bundle subject.txt >/dev/null
docker run --rm --user "$(id -u):$(id -g)" \
  --env HOME=/work \
  --volume "$temporary:/work" --workdir /work \
  "$cosign" verify-blob --insecure-ignore-tlog=true --key cosign.pub \
  --bundle signature.bundle subject.txt >/dev/null
printf '%s\n' "sha256:$(printf '%064d' 0)" > "$temporary/tampered.txt"
if docker run --rm --user "$(id -u):$(id -g)" \
  --env HOME=/work \
  --volume "$temporary:/work" --workdir /work \
  "$cosign" verify-blob --insecure-ignore-tlog=true --key cosign.pub \
  --bundle signature.bundle tampered.txt >/dev/null 2>&1; then
  echo "Signatur-Negativtest hat einen geaenderten Digest akzeptiert" >&2
  exit 1
fi
echo "Lokaler Cosign Positiv-/Negativtest erfolgreich."
