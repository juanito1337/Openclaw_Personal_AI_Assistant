# Build- und CI-Nachweise

Stand: M8, 2026-08-06. Der detaillierte Sicherheitsvertrag steht unter
[`architecture/IMAGE_SUPPLY_CHAIN.md`](architecture/IMAGE_SUPPLY_CHAIN.md).

## Gemeinsame Qualitaetspruefung

`.github/workflows/ci.yml` installiert auf Python 3.11, 3.12 und 3.13 die exakt
gepinnten Entwicklungswerkzeuge und ruft wie lokal `./scripts/check-repo.sh` auf.
Python 3.12 baut und installiert zusaetzlich das Wheel in einer frischen Umgebung.
Der M7-Lockcheck ist Bestandteil von `check-repo.sh` und prueft alle externen
Image- und Actionreferenzen.
M8 validiert zusaetzlich den generierten Skill-Toolvertrag und fuehrt einen lokalen
Drei-Szenarien-Recovery-Drill gegen temporaere Roots aus. Der Docker-Containerjob
ruft `scripts/check-m8-integration.sh` auf und bewahrt
`build/m8-integration.json`; keine Fixture publiziert Hostports oder nutzt
produktive Konten, Secrets, Mounts oder Netze.

Die Workflows besitzen global `permissions: {}`. Der Testjob darf nur Inhalte
lesen. Der Releasejob erhaelt nur `contents: read`, `packages: write`,
`id-token: write` und `attestations: write`. Alle Actions stehen als 40-stellige
Commit-SHAs im Supply-Chain-Lock; lesbare Versionskommentare sind keine
Vertrauensanker.

## Lokaler Containerjob in CI

Der Containerjob baut aus demselben sauberen Checkout drei lokale Images:

- `runtime` fuer Gateway, CLI, Layout-Init und Fachworker,
- `proxy-runtime` nur fuer den Ollama-Prioritaetsproxy,
- `maintenance-runtime` nur fuer ClamAV-Update und -Health.

Er startet keinen produktiven Stack und mountet weder `/srv/openclaw` noch
Produktivkonfiguration. Kurzlebige Tests bestaetigen OCI-Release, Commit und Rolle,
notwendige bzw. verbotene Binaries, CLI-/Release-Smoke, M3-State-Isolation und
M4-Hardening. Der Rootfs-Artefaktscan verwirft Secrets, Konfiguration, Datenbanken,
Logs, Laufzeitdaten, Tests, Deployment- und Legacy-Dateien.

Rollen-Smoke, Quell-/Signaturpruefung, Supply-Chain-Scan, dynamische M3-Pruefung und
dynamische M4-Pruefung sind eigene benannte Workflow-Schritte. Die M3-/M4-Skripte
melden die verletzte Invariante mit Soll-/Istwert; der SIGTERM-Test wartet auf die
nachweisbare Bereitschaft des Signalhandlers. Ein Abbruch darf daher weder als
anonymer Sammelschritt noch als timingabhaengiger Erfolg erscheinen.

Je Image erzeugt digest-gepinntes Syft eine SPDX-SBOM. Digest-gepinntes Trivy scannt
kritische Schwachstellen und Secrets. Kritische Befunde werden mit Exitcode 1 ohne
`ignore-unfixed` und ohne Ausnahme blockiert. Die exakte Quellmanifestmenge wird
separat auf Secrets geprueft; verbotene Laufzeitartefakte werden im Wheel und in
jedem exportierten Image-Rootfs geprueft. Eine lokale in-toto/SLSA-
Provenance verknuepft Image-ID, Rolle, Release, Git-Revision, SBOM-Hash und alle
Basisimage-Digests. Der lokale Cosign-Test akzeptiert die richtige Blob-Signatur und
verwirft einen veraenderten Digest.

CI bewahrt SPDX, Provenance und die Messwerte aus `scripts/benchmark-m7.py` als
Artefakte. Der Benchmark misst Image-Bytes, isolierten Python-Kaltstart und Peak-RSS
je Rolle. Buildzeit und CVE-Ergebnis sind Teil desselben Laufs.

Der Testjob bewahrt auch `build/m8-recovery.json`. Dieses Artefakt misst nur kleine
lokale Fixtures; Produktions-RTO und externer Restore bleiben deshalb bewusst offen
und sind im [Recoveryvertrag](architecture/RECOVERY_AND_RELEASE.md) begrenzt.

## Releaseworkflow

`.github/workflows/container.yml` laeuft manuell, fuer `test/**` und fuer
Release-Tags. Nach dem identischen Repositorycheck gilt folgende Reihenfolge:

1. lokale Kandidaten aller Rollen bauen, rollenbezogen testen und scannen,
2. den gesamten Rollenbuild zweimal ohne Cache aus demselben sauberen Checkout
   mit normalisierten Zeitstempeln als OCI-Archive ausfuehren und bytegleiche
   SHA-256-Werte verlangen,
3. jeden Target-Digest mit BuildKit-SBOM und `provenance: mode=max` nach GHCR
   publizieren,
4. jeden Digest keyless mit GitHub-OIDC/Cosign und Rekor signieren,
5. fuer jeden Digest GitHub-SLSA-Provenance und SPDX-SBOM attestieren,
6. Signatur und beide Attestierungen fuer alle drei Rollen unmittelbar verifizieren.

Erst danach zeigt der Workflow einen Deploymentbefehl mit drei unveraenderlichen
`name@sha256:...`-Referenzen. Tags sind nur Auffindbarkeitshilfen. Das Deployment-
Gate prueft die attestierten Digests, erwartete Release-ID, Git-Revision und Rolle
vor jeder Aenderung am laufenden Stack.

## Lokale Reproduktion

Da der aktuelle VS-Code-Prozess die spaeter hinzugefuegte Dockergruppe eventuell
nicht geerbt hat, kann ein einmaliger `sg docker -c '...'`-Aufruf erforderlich sein.
Er veraendert keine Gruppe und keine Produktivcontainer.

```bash
./docker/scripts/build-local.sh <runtime> <proxy> <maintenance>
./scripts/check-role-images.sh <runtime> <proxy> <maintenance> "$(git rev-parse HEAD)"
./scripts/check-source-secrets.sh
./scripts/check-image-supply-chain.sh <runtime> runtime "$(git rev-parse HEAD)" build/m7/runtime
./scripts/check-image-supply-chain.sh <proxy> proxy "$(git rev-parse HEAD)" build/m7/proxy
./scripts/check-image-supply-chain.sh <maintenance> maintenance "$(git rev-parse HEAD)" build/m7/maintenance
./scripts/check-local-signature.sh
./scripts/check-reproducible-images.sh
```

Alle Dockerpruefungen verwenden nur neu gebaute Testimages, temporaere Container
und temporaere Netze. Ein bereits laufender Compose-Stack wird weder gestoppt noch
neu konfiguriert. Erfolgreiche lokale Signaturtests ersetzen nicht die keyless
Registry-Signatur des Releaseworkflows.

Die gemessenen M7-Werte, Scanner-Versionen und OCI-Archiv-Hashes sind unter
[`architecture/image-baseline-m7.json`](architecture/image-baseline-m7.json)
versioniert. Buildausgaben unter `build/` sind dagegen lokale/CI-Artefakte und
kein dauerhafter Vertrauensanker.
