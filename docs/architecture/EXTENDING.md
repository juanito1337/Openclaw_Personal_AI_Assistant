# Architektur- und Erweiterungsregeln

## Neue Funktion

1. Ordne die Funktion einer bestehenden Fachkomponente und einem Datenowner zu.
2. Definiere Read, lokale Writes und externe Writes getrennt.
3. Lege Handler und typisierte Beschreibung in der passenden Domaene unter
   `personal_assistant/cli_handlers/` und `personal_assistant/tool_catalog/` an.
   Der Vertrag benoetigt Kommando, Argument-/Ausgabeschema, Modus, externe Wirkung,
   Approval, Verfuegbarkeit, Fehlercodes sowie Doku- und Testanker.
4. Dokumentiere Policy, Approval, Idempotenz, Audit und Fehlervertrag in `AGENTS.md`
   oder im Personal-Assistant-Skill.
5. Fuege positive und negative Regressionstests hinzu.
6. Aktualisiere Rollen-/Datenmatrix, wenn Prozess-, Secret-, Mount-, Netzwerk- oder
   Datenbesitz betroffen sind.

Eine Hilfsfunktion ist kein Agententool. Ein neues Tool ist erst vollstaendig, wenn
CLI, statischer Katalog, Live-Projektion, Betriebsvertrag/Skill und Regressionstest
uebereinstimmen. Danach `python3 scripts/generate-command-reference.py` ausfuehren;
`check-repo.sh` lehnt eine veraltete Referenz ab.

## Neue Containerrolle

Eine Rolle ist nur gerechtfertigt, wenn sie eine unabhaengige Lebensdauer,
Healthgrenze oder klaren Datenowner benoetigt. Sie bleibt Teil desselben Personal
Assistant und verwendet nach Moeglichkeit dasselbe Releaseimage. Vor Aufnahme sind
Kommando-Allowlist, Healthcheck, Sollzustand, Schedulerverhalten, Datenowner, Mounts,
Secrets, Netz und Schreibrechte zu dokumentieren und zu testen.

Neue Rollen muessen vor Aufnahme in Compose in `state-access.json` und
`runtime-hardening.json` mit Mounts, einzelnen Env-/Secretdateien, Netzen,
Nicht-root-Benutzer und Ressourcenlimits erfasst werden. Hostnetz, Root oder ein
neuer Host-Gateway-Zugang erfordern eine eigene ADR und Negativtests.

Ein neues oder geaendertes Runtime-Target muss ausserdem in
[`IMAGE_SUPPLY_CHAIN.md`](IMAGE_SUPPLY_CHAIN.md), `docker/supply-chain.lock.json`,
Compose, SBOM-/CVE-/Secret-Scans, Signatur-/Provenance-Pruefung, Deployment/Rollback
und Rollen-Smokes gemeinsam erscheinen. Ein eigenes Image ist nur nach einem
gemessenen Groessen- oder Angriffsoberflaechennutzen gerechtfertigt.

## Neue persistente Daten

- genau ein logischer Owner und dokumentierte Writer/Leser,
- eigener SQLite-Schema- und Migrationspfad,
- atomare JSON-Writes oder Transaktionen,
- definierte Backup-/Restore-Eigenschaft,
- keine neue Datenbank als Workaround fuer eine bestehende Ownergrenze,
- keine Reparatur durch Loeschen produktiver Datenbanken.

## Abhaengigkeiten

Zielrichtung ist `Einstieg/Worker -> Bootstrap -> Core -> Ports` sowie
`Bootstrap -> Adapter`. Gemeinsame Typen gehoeren nach `personal_assistant/contracts/`;
dieses Paket und Core-Module duerfen weder `mail_agent` noch konkrete Adapter
importieren. Neue Infrastruktur wird im Bootstrap injiziert, nicht ueber einen
Service Locator gesucht. Der M5-Importtest muss fuer jede Erweiterung gruen bleiben.

## Architekturentscheidung

Eine Aenderung an Prozessgrenzen, Datenowner, Single-Writer, Remote-Write-Vertrag,
Rollback-Untergrenze oder Toolvertrag benoetigt vor Implementierung ein nummeriertes
ADR. Unentschiedene Varianten erhalten Status `Proposed`; Dokumentation darf keine
nicht implementierte Sicherheitseigenschaft als erreicht bezeichnen.

## Entfernen und Kompatibilitaet

Eine Datei wird nicht aufgrund ihres Namens oder Alters entfernt. Vor einer
Bereinigung muessen statische produktive Aufrufer, Verhaltens- und Deploymenttests,
Rollbackrelevanz, Ersatzpfad und minimale Upgradeversion in
`legacy-decisions.json` dokumentiert sein. Anschliessend werden in derselben
Aenderung Komponenten-Inventar, Toolkatalog, Skill, Packaging, Dokumentation und
Regressionstests aktualisiert.

Aktive Komponenten verwenden die Klassifikation `active`; befristete Import- oder
Rollbackpfade `compatibility`, reine Schema-/State-Uebergaenge `migration-only`,
historische Unterlagen `deprecated` und nachweislich aufruflose Komponenten
`unused`. Ein Kompatibilitaetspaket darf erst nach einer ADR mit End-of-Support-
Version und erfolgreicher Recovery-Pruefung entfallen.

```bash
.venv/bin/python scripts/generate-component-inventory.py generate
.venv/bin/python scripts/generate-component-inventory.py verify
.venv/bin/python scripts/verify-legacy-package.py verify
```

Git-, PR-, Review-, Migrations- und Releaseanforderungen stehen in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md).
