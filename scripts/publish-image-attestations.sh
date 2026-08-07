#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
repository=${1:?OCI-Repository angeben}
runtime_digest=${2:?Runtime-Digest angeben}
proxy_digest=${3:?Proxy-Digest angeben}
maintenance_digest=${4:?Maintenance-Digest angeben}
artifact_root=${M7_ATTESTATION_ROOT:-$ROOT/build/m7}
cosign=${M7_COSIGN:-cosign}

[[ "$repository" != *@* && "$repository" != *:* ]] || {
  echo "OCI-Repository ohne Tag oder Digest erwartet: $repository" >&2
  exit 2
}
command -v jq >/dev/null || { echo "jq fehlt" >&2; exit 2; }
command -v "$cosign" >/dev/null || { echo "cosign fehlt" >&2; exit 2; }

publish_role() {
  role=$1
  digest=$2
  directory=$3
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "Ungueltiger $role-Digest: $digest" >&2
    return 2
  }
  provenance="$directory/provenance.json"
  predicate="$directory/provenance.predicate.json"
  sbom="$directory/image.spdx.json"
  [[ -f "$provenance" && -f "$sbom" ]] || {
    echo "Attestierungsartefakte fuer $role fehlen unter $directory" >&2
    return 1
  }

  # m7_supply_chain.py hat die vollstaendige lokale in-toto-Statement bereits
  # validiert. Cosign setzt fuer den publizierten Digest den Registry-Subject neu;
  # deshalb wird hier ausschliesslich dessen SLSA-v1-Predicate uebergeben.
  jq -e '.predicate | select(type == "object")' "$provenance" > "$predicate"
  "$cosign" attest --yes --type slsaprovenance1 --predicate "$predicate" \
    "$repository@$digest"
  "$cosign" attest --yes --type spdxjson --predicate "$sbom" \
    "$repository@$digest"
}

publish_role runtime "$runtime_digest" "$artifact_root/runtime"
publish_role proxy "$proxy_digest" "$artifact_root/proxy"
publish_role maintenance "$maintenance_digest" "$artifact_root/maintenance"
printf 'Registry-native SLSA-v1-/SPDX-Attestierungen publiziert: %s\n' "$repository"
