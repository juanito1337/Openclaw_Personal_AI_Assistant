# Build- und CI-Nachweise

Stand: M10.7, 2026-08-16. Der detaillierte Sicherheitsvertrag steht unter
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
M9 hebt denselben Collection-Vertrag auf 610 pytest-Items an und ergaenzt
Verhaltensregressionen fuer Review-Taxonomie, exakte Einzelkorrektur,
Walk-forward-Lernen, Kalenderdiagnose und atomare Mail-Suchprojektion. Die
SQLite/WAL-Nebenlaeufigkeit wird mit temporaeren Datenbanken und offenem Writer
getestet; die Dockerintegration bleibt der isolierte M8-Protokollstack und
beruehrt keinen laufenden Compose-Stack.
M10.0 hebt den Collection-Vertrag auf 616 pytest-Items an. Der neue Testpfad
verifiziert einen ausschliesslich synthetischen Rechnungskorpus, die
maschinenlesbare Extraktor-Baseline sowie das bestehende Backfill- und
Jahresprioritaetsverhalten. Er greift nicht auf `/srv/openclaw`, Nextcloud,
Postfachdaten, Secrets oder Container zu. Die Messergebnisse und Definitionen
stehen unter
[`INVOICE_QUALITY_BASELINE_M10.md`](INVOICE_QUALITY_BASELINE_M10.md).
M10.1 ergaenzt Verhaltenspruefungen fuer den tatsaechlichen SQLite-/Nextcloud-
Wirkungsvertrag von Export, Backfill und Korrektur. Ausschliesslich synthetische
WebDAV-Antworten pruefen `If-Match`, `If-None-Match`, HTTP 412, SHA- und
Schemaschutz sowie Remote-Fehler. Die neuen Vorschau- und Freigabepfade verwenden
weder Produktivdaten noch einen erreichbaren Nextcloud-Server.
M10.2 ergaenzt einen zweiten, vollstaendig synthetischen Nummern-/Datumskorpus
und Verhaltenspruefungen fuer typisierte Kandidaten, begrenzten Kontext,
explizite Ausschlussrollen, Konflikte und Dateinamen als reine Stuetzung. Der
Evaluator verifiziert M10.0 und M10.2 gegen getrennte versionierte Baselines;
weder lokaler Check noch CI benoetigen dafuer produktive PDFs, SQLite,
Nextcloud, `/srv/openclaw` oder Secrets. Der Collection-Vertrag steigt damit auf
648 pytest-Items, darunter mindestens 595 unittest-kompatible Tests und
weiterhin mindestens 13 freie Rechnungs-pytest-Tests.
M10.3 und M10.4 ergaenzen typisierte Betragsplausibilitaet sowie lokal begrenzte,
versionierte OCR-Feldfusion. M10.5 hebt den Collection-Vertrag auf 671
pytest-Items, darunter mindestens 607 unittest-kompatible Tests und weiterhin 13
freie Rechnungs-pytest-Tests. Der neue Reprocessing-Test verwendet nur
temporaere SQLite-/PDF-Fixtures, simulierte Lesezugriffe und einen inhaltsfreien
Scannerbeleg. Er prueft keinen produktiven Nextcloud-Server und fuehrt keinen
Apply aus.
M10.6 hebt den Vertrag auf 680 pytest-Items, darunter mindestens 616
unittest-kompatible Tests und weiterhin 13 freie Rechnungs-pytest-Tests. Die
Einzeluebernahme-Regressionen verwenden nur temporaere Schema-3/4-SQLite-Dateien,
erfundene PDF-Bytes und simulierte erfolgreiche, konfligierende oder ausfallende
Registerantworten. Parallelitaet, Idempotenz und Wiederaufnahme werden ohne
Produktivdaten, `/srv/openclaw`, Nextcloud-Zugang oder laufenden Stack geprueft.
CI fuehrt denselben Befehl und denselben generierten Toolvertrag aus; M10.6 fuegt
keinen produktiven Deployment- oder Containerstart hinzu.
M10.7 hebt den Vertrag auf 688 pytest-Items, darunter mindestens 624
unittest-kompatible Tests und weiterhin 13 freie Rechnungs-pytest-Tests. Der
neue Backlog-Audit liest nur notwendige SQLite-Spalten und gibt ausschliesslich
Aggregate aus. Seine Tests verwenden keine produktiven Werte, PDFs, Pfade,
Hashes, Nextcloud-Verbindung, Secrets oder laufenden Container. CLI,
Toolkatalog, generierter Skillvertrag und Skillablauf werden gemeinsam geprueft;
M10.7 schaltet weder ein Move-Werkzeug noch einen autonomen Apply frei.

Die Workflows besitzen global `permissions: {}`. Der Testjob darf nur Inhalte
lesen. Der Releasejob erhaelt nur `contents: read`, `packages: write` und
`id-token: write`. Alle Actions stehen als 40-stellige
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
Containerseitig restriktiv erzeugte M3-Pfade werden mit einem read-only State-Mount
im kurzlebigen Pruefcontainer inspiziert. Eine abweichende Host-UID ist damit kein
Grund, Laufzeitrechte zu lockern oder einen vorhandenen Pfad als fehlend zu melden.
Der oeffentliche M4-Testmarker ist auf seinem read-only Mount explizit fuer die
abweichende Image-UID lesbar; produktive State-, Config- und Secret-Modi bleiben
davon unberuehrt.
Die dynamische Memory-Abnahme akzeptiert genau drei cgroup-konforme Ergebnisse
einer schrittweise protokollierten uebergrossen Allokation: Docker-Kernel-OOM-Kill,
den kontrolliert markierten Python-`MemoryError` oder SIGKILL 137 bei leerem
Docker-State-Fehler, exakt bestaetigtem 64-MiB-Limit und nachweisbar begonnener,
aber unvollstaendiger Allokation. Ein beliebiger Prozessfehler oder ein SIGKILL
ohne diese zusaetzliche Evidenz gilt nicht als Nachweis.

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
5. fuer jeden Digest Registry-native Cosign-Attestierungen mit SLSA-v1-Provenance
   und SPDX-SBOM publizieren,
6. Signatur und beide Attestierungen fuer alle drei Rollen unmittelbar verifizieren.

Erst danach zeigt der Workflow einen Deploymentbefehl mit drei unveraenderlichen
`name@sha256:...`-Referenzen. Tags sind nur Auffindbarkeitshilfen. Die Registry-
native Form ist auch fuer benutzereigene private GitHub-Repositories verfuegbar und
benoetigt deshalb weder GitHubs Attestation-API noch `attestations: write`. Das
Deployment-Gate prueft die attestierten Digests, erwartete Release-ID, Git-Revision und Rolle
vor jeder Aenderung am laufenden Stack.

## Lokale Reproduktion

Da der aktuelle VS-Code-Prozess die spaeter hinzugefuegte Dockergruppe eventuell
nicht geerbt hat, kann ein einmaliger `sg docker -c '...'`-Aufruf erforderlich sein.
Er veraendert keine Gruppe und keine Produktivcontainer.
Die Deploymentpruefung verwendet ebenfalls das im Supply-Chain-Lock
digest-gepinnte Cosign-Containerimage. Die bestehende Docker-Registry-Anmeldung
wird darin nur read-only sichtbar; eine Hostinstallation von Cosign entfaellt.

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
Registry-Signatur des Releaseworkflows. Der Rollen-Smoke startet den echten
Proxy-Entrypoint in einem privaten internen Fixture-Netz und verlangt einen
erfolgreichen Upstream-Healthcheck; ein bloss importierbares Proxy-Modul reicht
nicht fuer die Imagefreigabe.

Die gemessenen M7-Werte, Scanner-Versionen und OCI-Archiv-Hashes sind unter
[`architecture/image-baseline-m7.json`](architecture/image-baseline-m7.json)
versioniert. Buildausgaben unter `build/` sind dagegen lokale/CI-Artefakte und
kein dauerhafter Vertrauensanker.
