# Image-Lieferkette und Freigabevertrag

Status: verbindlicher M7-Vertrag. Die dauerhaften Entscheidungen stehen in
[ADR-0011](adr/0011-reproduzierbare-rollenimages.md), alle unveraenderlichen
Fremdartefakte in [`docker/supply-chain.lock.json`](../../docker/supply-chain.lock.json).
Die reproduzierbaren Messwerte stehen in
[`image-baseline-m7.json`](image-baseline-m7.json).

## Rollenimages

Alle Images stammen aus demselben Commit, tragen Release `3.4.0-r28` und
Layout 3, haben aber nach Messung drei Runtime-Ziele:

| Target | Dienste | Beabsichtigter Inhalt |
| --- | --- | --- |
| `runtime` | Layout-Init, Gateway, alle Fachworker, agent-cli | OpenClaw, kompletter Assistant, Himalaya, OCR und ClamAV |
| `proxy-runtime` | Ollama-Prioritaetsproxy | Python-Standardbibliothek, CA, curl und tini; kein OpenClaw, Mail, OCR oder ClamAV |
| `maintenance-runtime` | ClamAV-Updater | freshclam/clamscan, Healthmodul und tini; kein OpenClaw, Mail, OCR oder Himalaya |

Brave und Signal sind offizielle externe OpenClaw-Plugins und deshalb direkte
Buildinputs des Runtime-Images. `docker/openclaw-plugins/package.json` pinnt ihre
Version auf die Core-Version, die npm-Lockdatei sperrt Registry-URL und
Integritaet, und `docker/supply-chain.lock.json` sperrt zusaetzlich den SHA-256
der kompletten Lockdatei sowie die beiden Paketintegritaeten. Proxy- und
Maintenance-Image enthalten diese Plugins nicht.

Der volle Runtime-Schnitt bleibt bewusst gemeinsam: Gateway-Tools und mehrere
Worker rufen OpenClaw sowie Mail-, OCR- oder Antiviruspfade direkt auf. Eine weitere
Trennung ohne eigene Prozessschnittstelle wuerde Code duplizieren oder ungetestete
Laufzeitpfade erzeugen. Proxy und Maintenance haben dagegen nachweislich keine
solche Abhaengigkeit und reduzieren durch eigene Targets die Angriffsoberflaeche.

Tests, `docs/`, Deploymentskripte, Legacy-Units, Entwicklungswerkzeuge und
Entwicklungs-Metadaten werden durch explizite `COPY`-Mengen und `.dockerignore`
nicht in Produktionsimages aufgenommen. Persistente Konfiguration, Secrets,
Datenbanken, Logs und Laufzeitdaten bleiben ebenfalls ausgeschlossen.

## Reproduzierbare Eingaben

- Das OpenClaw-Quellimage sowie die Node-/Python-Alpine-Builder und Runtimebases
  sind mit Tag und SHA-256-Digest gepinnt.
- Direkte Alpine-Abhaengigkeiten sind auf exakte Versionen festgelegt; eine nicht
  mehr verfuegbare oder abweichende Version bricht den Build ab. Transitiver Inhalt
  wird je Rollenimage in der SBOM erfasst.
- Das offizielle versionsgebundene Himalaya-amd64-Releasearchiv und das darin
  enthaltene Binary muessen jeweils exakt die im Lockfile hinterlegte SHA-256
  haben. Der zunaechst getestete Cargo-Quellbuild wurde verworfen, weil zwei saubere
  Builds trotz fixer Pfade unterschiedliche ELF-Konstanten erzeugten.
- Syft, Trivy und Cosign laufen aus digest-gepinnten Scannerimages.
- Jede GitHub Action ist auf einen 40-stelligen Commit gepinnt. Workflow- und
  Jobberechtigungen beginnen leer und erteilen nur den benoetigten Mindestumfang.

`scripts/m7_supply_chain.py verify-lock` vergleicht Lockfile, Dockerfile und alle
Workflows. Dazu gehoeren jetzt auch direkt im Lock erfasste
Runtime-Paketpins. Damit scheitert bereits der Quellcheck, wenn ein aktualisierter
Alpine-Pin nicht gleichzeitig im Supply-Chain-Vertrag festgehalten wurde. Der
dynamische Rollenbuild bleibt die verbindliche Pruefung, dass der gepinnte
Paketstand im unveraenderten Basisimage-Repository tatsaechlich aufloesbar ist.
Es gibt keine pauschale Vulnerability-Ausnahme: jede bekannte kritische
Schwachstelle, auch ohne Fix, stoppt die Freigabe.

## Freigabeevidenz

Der normale CI-Containerjob baut und prueft alle drei Rollen lokal. Der
Releaseworkflow baut sie zweimal ohne Cache und exportiert mit normalisierten
Zeitstempeln bytegleiche OCI-Archive. Er veroeffentlicht anschliessend exakt den
getesteten Commit mit BuildKit-SBOM und Provenance, haengt zusaetzliche Registry-
native SPDX-/SLSA-v1-Cosign-Attestierungen an und signiert jeden unveraenderlichen
Digest keyless mit GitHub OIDC und Rekor. Die Freigabe prueft danach alle drei
Signaturen und beide Attestierungstypen erneut. Dieser Pfad ist bewusst unabhaengig
von GitHubs Attestation-API, die fuer benutzereigene private Repositories nicht
verfuegbar ist.

Lokale Scanner- und Nachweisaufrufe:

```bash
.venv/bin/python scripts/m7_supply_chain.py verify-lock
./scripts/check-role-images.sh <runtime> <proxy> <maintenance> <git-sha>
./scripts/check-source-secrets.sh
./scripts/check-image-supply-chain.sh <image> <rolle> <git-sha> <ausgabe>
./scripts/check-local-signature.sh
./scripts/check-reproducible-images.sh
```

Syft erzeugt SPDX-JSON. Trivy scannt jedes Rollenimage auf kritische CVEs und
Secrets sowie die exakte Quellmanifestmenge auf Secrets. Die lokale Provenance ist
eine deterministische in-toto/SLSA-Verhaltenspruefung, die Image-ID, Git-Revision,
Release, Rolle, SBOM-Hash und gepinnte Materialien verknuepft. Die veroeffentlichte
Freigabe verwendet dieselbe gepruefte Provenance als SLSA-v1-Predicate; Cosign
bindet dessen Subject an den publizierten Registry-Digest.

## Deployment-Gate

`docker/scripts/deploy.sh` akzeptiert fuer Runtime, Proxy und Maintenance nur
`name@sha256:...`. Noch bevor ein Writer oder Gateway gestoppt wird, muss fuer jedes
Image Folgendes erfolgreich sein:

1. Cosign-Signatur mit dem konfigurierten GitHub-Workflow als Identitaet,
2. SLSA-Provenance-Attestierung,
3. SPDX-SBOM-Attestierung,
4. exakter 40-stelliger Git-Commit im OCI-Label,
5. erwartete Release-ID und Rollenbezeichnung.

Kann das digest-gepinnte Cosign-Pruefimage nicht gestartet werden, ist die Referenz
veraenderlich, ist das Image unsigniert oder passt eine Identitaet nicht, endet der
Vorgang vor jeder Laufzeitaenderung. Der Verifier nutzt ein read-only Rootfs ohne
Capabilities und bindet nur die vorhandene Docker-Registry-Konfiguration read-only
ein; ein separates Host-Cosign ist nicht erforderlich. Backups halten
alle drei vorherigen Digestreferenzen fest; Rollback stellt denselben Rollensatz
wieder her. Ein Image-/State-Rollback stellt keine erfolgreichen Remote-Writes
wieder her.

## Reproduzierbare Messung

`scripts/benchmark-m7.py` schreibt pro Rolle Image-Bytes, Median des isolierten
Python-Kaltstarts und Peak-RSS nach `build/m7-baseline.json`. SBOM-Paketzahl,
Trivy-Ergebnis und Buildzeit entstehen im selben CI-Lauf. Diese Werte sind Baseline,
keine willkuerliche Schranke; kritische CVEs und Integritaetsfehler sind dagegen
harte Freigabesperren.

Lokal wurden 376.499.617 Bytes fuer Runtime, 23.417.257 Bytes fuer Proxy und
45.627.796 Bytes fuer Maintenance gemessen. Alle drei Images haben 0 kritische
CVEs und 0 Secret-Befunde. Gegenueber dem identischen M6-Image sinkt die Groesse
um 11,53 %, 94,50 % beziehungsweise 89,28 %. Die zugleich gemessene
Kaltstartverschlechterung und der begruendete Alpine-Sicherheitstausch stehen
vollstaendig in der Baseline und ADR-0011.
