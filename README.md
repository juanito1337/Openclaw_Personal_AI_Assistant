# OpenClaw Personal Assistant 3.4.0-r27.2.5

OpenClaw ist ein einzelner lokaler Personal Assistant, dessen Gateway, Koordinator,
Fachworker und Diagnose-CLI als getrennte Prozesse aus demselben unveraenderlichen
Release laufen. Gateway und Fachworker teilen den vollen Runtime-Target; Proxy und
ClamAV-Wartung verwenden kleinere, attestierte Rollenimages desselben Commits. Die
Prozesse sind keine eigenstaendigen Agenten. Persistenter
Zustand, Instanzkonfiguration und Secrets bleiben ausserhalb des Images unter
`/srv/openclaw`.

## Verbindliche Dokumentation

| Thema | Einstieg |
| --- | --- |
| Systemkontext, Container- und Komponentenarchitektur | [Architekturvertrag](docs/architecture/README.md) |
| Komponentenstatus, Legacy- und Upgradegrenze | [M6-Inventar](docs/architecture/component-inventory.json) und [Kompatibilitaet](docs/architecture/compatibility-policy.json) |
| Rollenimages, SBOM, Provenance, Scan und Signatur | [Image-Lieferkette](docs/architecture/IMAGE_SUPPLY_CHAIN.md) |
| Produktiver Betrieb, Deployment und Rollback | [Docker-Betrieb](docs/DOCKER_DEPLOYMENT.md) |
| Recovery-Drill, RTO/RPO, Releasecheckliste und Canary | [M8-Recoveryvertrag](docs/architecture/RECOVERY_AND_RELEASE.md) |
| Lokale Tests, CI und Baseline | [Testanleitung](docs/TESTING.md) |
| Toolkatalog und stabile CLI-Kommandos | [Generierte Befehlsreferenz](docs/COMMAND_REFERENCE.md) |
| Depot, EUR-Bewertung und providergebundene Aktienanalyse | [Portfolio- und Research-Vertrag](docs/PORTFOLIO_ADVISOR.md) |
| Erweiterungs-, Git-, Review- und Releaseregeln | [Beitragen](CONTRIBUTING.md) |
| Trust Boundaries und Schwachstellenprozess | [Sicherheit](SECURITY.md) |
| Architekturentscheidungen | [ADR-Index](docs/architecture/adr/README.md) |
| Geplante Container-Milestones | [Container-Roadmap](docs/CONTAINER_ARCHITECTURE_ROADMAP.md) |
| Mail-Qualitaet, Review-Triage und kontrollierter Rollout | [M9-Roadmap](docs/MAIL_QUALITY_REVIEW_ROADMAP.md) und [Rolloutvertrag](docs/MAIL_QUALITY_ROLLOUT.md) |
| Rechnungsqualitaet und sichere Neubewertung | [M10-Roadmap](docs/INVOICE_QUALITY_REPROCESSING_ROADMAP.md), [Baseline](docs/INVOICE_QUALITY_BASELINE_M10.md), [Betriebsvertrag](docs/INVOICE_OCR_REGISTER.md) und [separater Rolloutvertrag](docs/INVOICE_M10_ROLLOUT.md) |
| Chronologische Aenderungen | [Changelog](CHANGELOG.md) |

Historische Architektur-, Git- und Releasebeschreibungen liegen klar als nicht
verbindliche Zeitstaende unter [`docs/archive/`](docs/archive/README.md).

## Entwicklungsstart

```bash
./scripts/assistant.sh version --verify
./scripts/bootstrap-dev.sh
./scripts/check-repo.sh
./scripts/check-wheel.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
```

Diese Befehle verwenden den Checkout und neutrale Beispielkonfigurationen. Sie
starten keine produktiven Jobs und greifen nicht auf `/srv/openclaw` zu. Ein
Container-Build fuer die Artefaktabnahme ist in der [Build- und
CI-Anleitung](docs/BUILD_AND_CI.md) dokumentiert.

## Betriebsgrenze

Die produktive Runtime ist der Docker-Stack. Native systemd-Units sind nur noch eine
eingefrorene und SHA-verifizierte Rollback-Kompatibilitaet unter `legacy/systemd/`.
Die direkte Upgrade-Untergrenze ist `3.4.0-r26.1`. Es darf nie gleichzeitig einen
systemd-Mailwriter und den Container-Mailworker geben. Das Ersetzen eines Images
allein stellt keine bereits veraenderten Remote-Mails, Kontakte, Kalender, Aufgaben
oder Nextcloud-Dateien wieder her.

Unterstuetzte Agentenfunktionen werden ausschliesslich ueber stabile
`./scripts/assistant.sh ...`-Befehle und den registrierten Toolvertrag exponiert.
Sicherheits- und Freigaberegeln stehen in `AGENTS.md` und im Personal-Assistant-Skill.
