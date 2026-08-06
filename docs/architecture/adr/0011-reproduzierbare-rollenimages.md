# ADR-0011: Reproduzierbare Rollenimages mit attestierter Freigabe

- Status: Accepted
- Datum: 2026-08-06
- Entscheider: Architecture Maintainers, Security Maintainers
- Betroffene Milestones: M7-M8

## Kontext

Bis M6 verwendeten alle Rollen ein gemeinsames, tag-basiertes Image. Die gemessene
M6-Groesse betrug 425.555.866 Bytes. Proxy und ClamAV-Wartung benoetigen weder den
kompletten OpenClaw-Node-Stack noch Mail/OCR; Gateway, CLI und Fachworker teilen
dagegen direkte OpenClaw-, Assistant-, Mail-, OCR- und Antiviruspfade. Basisimages,
Actions und Freigabeevidenz waren nicht durchgehend unveraenderlich gebunden.

## Entscheidung

Ein Commit erzeugt drei kompatible Targets derselben Releaseidentitaet: den vollen
`runtime` fuer Gateway, CLI und Fachworker, ein minimales `proxy-runtime` und ein
minimales `maintenance-runtime`. Weitere Workerimages werden ohne gemessenen Nutzen
und eine explizite Prozessschnittstelle nicht eingefuehrt.

Alle externen Build-/Scannerimages und GitHub Actions werden per Digest bzw.
Commit-SHA gepinnt. Direkte Systempakete sowie das offizielle versionsgebundene
Himalaya-Archiv und sein Binary werden exakt verifiziert. Ein eigener Cargo-Build
wird nach zwei abweichenden sauberen ELF-Ergebnissen bewusst nicht als
reproduzierbar ausgegeben. Jedes veroeffentlichte Rollenimage
erhaelt OCI-Identitaet, SBOM, SLSA-Provenance und keyless Cosign-Signatur. Kritische
CVEs besitzen keine Ausnahme. Ein Deployment verifiziert alle drei unveraenderlichen
Digests vor dem Stoppen des laufenden Stacks.

Diese Entscheidung praezisiert ADR-0001: Der modulare Monolith und gemeinsame
Release-/Toolvertrag bleiben bestehen; gemeinsam bedeutet nicht mehr zwingend ein
identisches Root-Dateisystem fuer technisch unabhaengige Infrastrukturrollen.

## Konsequenzen

Proxy und Maintenance haben weniger Pakete und eine kleinere Angriffsoberflaeche.
Ein Release besteht betrieblich aus drei zusammengehoerigen Digestreferenzen, die
gemeinsam gesichert, ausgerollt und zurueckgerollt werden. Releasejobs benoetigen
OIDC, Registry-Attestierungen und auf dem Deploymenthost Cosign. Ein nicht mehr
verfuegbares gepinntes Alpine-Paket oder eine neue kritische Schwachstelle bricht
fail-closed und muss in einer sichtbaren Lock-/Versionsaenderung behoben werden.

Der direkte M6/M7-Vergleich misst Groessenreduktionen von 11,53 % fuer Runtime,
94,50 % fuer Proxy und 89,28 % fuer Maintenance bei jeweils 0 kritischen CVEs.
Der Alpine-Pfad startet auf dem Messhost zugleich 33,32 %, 103,68 % und 52,26 %
langsamer; der Peak-RSS aendert sich um -1,34 %, +19,24 % und +19,63 %. Diese
Regression wird transparent akzeptiert, weil der zuvor untersuchte Debian-Pfad
21 nicht behobene kritische CVEs enthielt und die beiden Infrastrukturrollen ihre
Angriffsoberflaeche massiv reduzieren. Die Werte sind eine beobachtete Baseline,
kein nachtraeglich gewaehltes Performance-Gate.

## Verifikation

Lock-/Workflowtests, zwei No-Cache-Builds, Rollen-Smokes, SPDX-/Provenance-Pruefung,
Cosign-Positiv-/Negativtest, Trivy-CVE-/Secret-Scans, Rootfs-Artefaktscan,
Compose-Rendering und Deployment-Gate-Regressionspruefungen.

## Offene Fragen

Ob Mail/OCR spaeter ein eigener Dienst mit enger Prozessschnittstelle wird, ist
nicht entschieden und gehoert nicht zu M7.
