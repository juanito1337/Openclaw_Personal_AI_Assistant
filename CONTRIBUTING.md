# Beitragen zu OpenClaw

Diese Regeln halten Architektur, Git-Historie, Migrationen und Releases fuer den
lokalen Personal Assistant nachvollziehbar. Der aktuelle Architekturvertrag beginnt
unter [`docs/architecture/README.md`](docs/architecture/README.md).

## Arbeitsumfang und Branches

- Ein Branch und Pull Request behandelt genau einen Milestone oder einen eng
  begrenzten Fehler.
- Empfohlene Namen sind `milestone/m1-architecture-contract`, `fix/<thema>`,
  `docs/<thema>` oder `test/<thema>`.
- `test/**` besitzt zusaetzlich die dokumentierte Bedeutung eines automatisch
  gebauten Live-Testimages. Solche Images sind keine Produktionsfreigabe.
- Keine Runtime-, Daten- oder Berechtigungsmigration mit einer reinen
  Dokumentationsaenderung vermischen.
- Vorhandene Benutzerarbeit im Worktree bleibt erhalten; fremde Aenderungen werden
  weder zurueckgesetzt noch ungefragt formatiert.

## Commitkonvention

Commits folgen Conventional Commits:

```text
<type>(<scope>): <kurze imperative Beschreibung>
```

Erlaubte Typen sind `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `ci`,
`chore`, `perf` und `revert`. Sinnvolle Scopes sind `core`, `mail`, `calendar`,
`contacts`, `tasks`, `portfolio`, `container`, `security`, `docs` und `release`.
Breaking Changes verwenden `!` und einen `BREAKING CHANGE:`-Footer. Ein Commit darf
keine Secrets, produktiven Konfigurationen, Datenbanken, Logs oder Laufzeitdaten
enthalten.

## Pull Requests und Review

Jeder PR beschreibt:

1. Problem und abgegrenzten Scope,
2. betroffene Architekturkomponenten, Datenowner und Trust Boundaries,
3. neue oder veraenderte lokale/externe Schreibrechte,
4. Migrations-, Backup- und Rollbackauswirkungen,
5. ausgefuehrte positive und negative Tests,
6. bewusst nicht bearbeitete Punkte.

Pflichtreview richtet sich nach dem Dokument-/Komponentenowner. Aenderungen an
Single-Writer, Secrets, Policy, ActionPlan, Antivirus, Remote-Writes, Backup oder
Rollback benoetigen zusaetzlich Security- oder Operations-Review. Prozessgrenzen,
Datenowner und Toolvertraege benoetigen vor der Implementierung ein ADR.

## Lokale Pflichtchecks

```bash
./scripts/assistant.sh version --verify
./scripts/check-repo.sh
./scripts/check-wheel.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
git diff --check
```

Bei Container- oder Artefaktaenderungen werden alle betroffenen Rollenimages gebaut,
mit Rollen-Smokes, SPDX-/Provenance-Pruefung, Critical-CVE-/Secret-Scan und
exportiertem Root-Dateisystem abgenommen. Geaenderte Buildinputs aktualisieren den
Supply-Chain-Lock bewusst; Basisimages und Actions duerfen nie nur mit einem Tag
referenziert werden. Produktive Jobs, `/srv/openclaw` und echte Remote-Writes sind
keine Entwicklungstests.

CI und lokal verwenden `scripts/check-repo.sh` als denselben Qualitaetspfad. Eine
kleinere Testcollection, ein neues Ruff-/mypy-Problem, ein ungueltiger interner Link
oder eine Manifestabweichung ist ein Fehler.

## Daten- und Schemamigrationen

- Migrationen sind vorwaertsgerichtet, idempotent und fuer bereits migrierte
  Datenbanken sicher wiederholbar.
- Vor Learning-, Health- oder Produktivnutzung muss die erwartete Schema-Version
  verifiziert sein.
- Produktive SQLite-Dateien werden niemals zur Reparatur geloescht oder leer neu
  angelegt.
- Tests decken Neuinstallation, Upgrade von der unterstuetzten Untergrenze,
  Wiederholung, absichtlich unvollstaendige Daten und Rollback/Restore ab.
- Eine Datenpfadaenderung aktualisiert den
  [Datenkatalog](docs/architecture/DATA_CATALOG.md), Owner, Backupumfang und
  Restore-Pruefung im selben PR.
- Externe Remoteaenderungen benoetigen einen eigenen Snapshot-/Restorevertrag; ein
  lokales SQLite-Backup genuegt dafuer nicht.

## Release-Checkliste

1. Releaseinhalt und Kompatibilitaetsuntergrenze festlegen.
2. `CHANGELOG.md`, `RELEASE.json`, `VERSION`, Agentenvertrag und Skill konsistent
   aktualisieren.
3. Daten-/Konfigurationsmigrationen samt Rollbacknachweis pruefen.
4. Vollstaendigen Repository-, Wheel-, Compose- und Containerlauf ausfuehren.
5. Wheel, Quellmanifest und alle Rollenimages auf Secrets und Laufzeitdaten pruefen.
6. `SOURCE_MANIFEST.sha256` ausschliesslich zuletzt mit
   `./scripts/source-manifest.py generate` aktualisieren.
7. `version --verify`, `git diff --check` und einen sauberen Review-Diff pruefen.
8. Erst nach gruenem CI einen annotierten Release-Tag sowie die exakt geprueften,
   SBOM-/SLSA-attestierten und signierten Rollenimage-Digests veroeffentlichen.

Eine Imagefreigabe erweitert keine Instanzberechtigung. Produktive Installation,
Backup, Smoke-Test und Rollback folgen der
[Docker-Betriebsanleitung](docs/DOCKER_DEPLOYMENT.md).

## Dokumentationspflege

Aktive Architektur steht nur unter `docs/architecture/`. Chronologische
Release-Details gehoeren in `CHANGELOG.md`; alte Zielbilder werden unter
`docs/archive/` mit sichtbarem Historienhinweis erhalten. Neue Architekturdokumente
muessen genau einen Eintrag in `docs/architecture/owners.json` und gueltige relative
Links besitzen.
