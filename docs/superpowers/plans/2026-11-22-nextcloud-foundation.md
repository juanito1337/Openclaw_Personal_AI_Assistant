# Nextcloud Kategorien und Komponentenregister Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den bestätigten Kategorienfehler korrigieren und ein überprüfbares Komponentenregister als Grundlage der Updatefunktion bereitstellen.

**Architecture:** Bestehende iCalendar-Verarbeitung gezielt ergänzen. Externe Komponenten aus vorhandenen Locks ableiten und mit versionierten Herkunfts-/Pflegeangaben verbinden; keine zweite unabhängig gepflegte Versionsliste.

**Tech Stack:** Python-Standardbibliothek, pytest, JSON, vorhandene Shell-/CI-Prüfung.

**Spec:** [Nextcloud-Roadmap R0/NC5](../../NEXTCLOUD_ACCESS_ROADMAP.md) und [Update-Roadmap U0](../../STACK_UPDATE_ROADMAP.md).

## Global Constraints

- Einrichtung und Bedienung bleiben im bestehenden OpenClaw-Plugin.
- Nur Entwicklung im vorhandenen isolierten Worktree; keine Installation, externen Schreibzugriffe, Secretsuche oder produktiven Aktionen.
- Produktionsdaten und Zugangsdaten sind keine Testfixtures. Keine neuen Abhängigkeiten installieren.
- Vorhandene Locks und Quellmanifeste bleiben die maßgeblichen Versionseingaben. Ein Komponentenregister behauptet keinen aktuellen Produktionsstand oder erfolgreichen Upstream-Check.
- Tests verwenden handgeprüfte synthetische Eingaben und reale Parser/Generatoren. Erst fehlenden Fehlernachweis erzeugen, dann implementieren.
- R0 ist eine eigene Implementierung nach iCalendar-Verhalten, keine Übernahme des fremden JavaScript-Bundles. Den Vergleich mit dem exakten Upstream als Referenz kennzeichnen; Kopien oder abgeleitete Fixtures müssten zusätzlich den Lizenzhinweis tragen.
- Agenten ändern nur ihre aufgelisteten Dateien. Quellmanifest und Gesamtinventur aktualisiert der Koordinator am Ende. Keine Veröffentlichung oder eigenständigen Commits durch Implementierer.

## Review Focus

- Ein escaptes Backslash vor einem Komma darf nicht mit einem escapten Komma verwechselt werden: R0 prüft gerade/ungerade Escape-Folgen und Roundtrip.
- Gefaltete VTODO-Zeilen und VALARM dürfen die äußeren Kategorien nicht verändern: R0 prüft reale Komponentenverarbeitung.
- Ein neues Paket oder ein veränderter Pin darf nicht aus dem Register verschwinden: U0 prüft Ableitung und Drift anhand veränderter Fixture-Locks.
- Fehlende Herkunft, mehrdeutige Komponenten-IDs und ungültige Quellverweise dürfen keine scheinbar gültige Inventur ergeben: U0 prüft gezielte Fehlerfälle.
- Versioniert eingebundene und extern betriebene Komponenten müssen unterscheidbar bleiben: U0 prüft unbekannte externe Versionen und referenzierten, nicht ausgelieferten Quellcode.

### Task 1: Kategorien korrekt lesen

**Files:**
- Modify: `personal_assistant/ical_edit.py` falls eine wiederverwendbare Textlistenfunktion benötigt wird.
- Modify: `personal_assistant/connectors/nextcloud/tasks.py`.
- Test: `tests/test_direct_tasks_tool.py`; ergänzende Parserfälle dürfen in `tests/test_ical_text_lists.py` liegen.

**Interfaces:**
- Consumes: `component_properties(raw, "VTODO")` mit bereits entfalteten direkten Properties.
- Produces: unverändertes `_parse_vtodo`-Ergebnisformat; `categories` enthält korrekt decodierte einzelne Texte. Bei eigenem Helper: `split_ical_text_list(value: str) -> list[str]` in `ical_edit.py`.

- [x] **Step 1:** Regression mit `CATEGORIES:Sales\, West,Other` an echter VTODO schreiben; erwartet `['Sales, West', 'Other']`. Fälle für zwei Backslashes vor dem Trennkomma, Backslash plus escaptes Komma, escaptes Semikolon, Unicode, gefaltete Zeilen, leere Liste und VALARM ergänzen. Erwartungswerte als Literale schreiben.
- [x] **Step 2:** `.venv/bin/python -m pytest -q tests/test_direct_tasks_tool.py` ausführen und den gezielten Fehler vor der Korrektur dokumentieren.
- [x] **Step 3:** Trennkommata mit Escape-Zustand erkennen; Textwerte einmal decodieren, keine globale Änderung anderer Propertyparser. Bestehende Leerwertbehandlung erhalten. Keinen fremden Parser oder zusätzliche Abhängigkeit einführen.
- [x] **Step 4:** Gezielte Tests und existierende Task-/iCalendar-Updatefälle ausführen. Einen vom bestehenden Ersteller erzeugten Kategorienwert erneut lesen und den erwarteten Inhalt prüfen.
- [x] **Step 5:** Diff selbst prüfen und Ergebnis mit RED/GREEN-Befehlen, Umfang und offenen Punkten in den Aufgabenbericht schreiben. Änderungen bleiben zur unabhängigen Prüfung im Worktree.

### Task 2: Komponentenregister aus vorhandenen Quellen ableiten

**Files:**
- Create: `scripts/component_registry.py` als offline Entwickler-/CI-Werkzeug.
- Create: `docs/architecture/upstream-sources.json` für Herkunft, Typ, Owner, Updateweg und Prüfbelege.
- Create: `docs/architecture/external-components.json` als deterministisch erzeugte Projektion.
- Create: `docs/architecture/EXTERNAL_COMPONENTS.md` mit Pflege-/Prüfanleitung und Grenzen.
- Create: `tests/test_component_registry.py`.
- Modify: `scripts/check-repo.sh` zur Prüfung des Registers.

**Interfaces:**
- Consumes: `docker/supply-chain.lock.json`, beide npm-Paketlocks und vorhandene Pluginverträge, `Dockerfile`, `requirements-dev.lock`, `pyproject.toml`, `scripts/bootstrap-dev.sh`, `.github/workflows/*.{yml,yaml}` soweit für vorhandene externe Pins nötig. Nur ausdrücklich erlaubte Quellpfade, keine generische Geheimnis-/Dateisysteminventur.
- Produces: `build_registry(root: Path) -> dict[str, Any]`, `validate_registry(payload: dict[str, Any]) -> None` und CLI `generate` / `verify` mit optionalem `--root` für isolierte Tests; `verify` endet bei fehlendem, ungültigem oder veraltetem Register ungleich null. Kein Netzwerkzugriff.
- Schema: `schema_version: 1`, `components` nach eindeutiger `id` sortiert. Jeder Eintrag enthält `kind`, `source`, `pin` (oder explizit `null` für externe Dienste), `owner`, `update_source`, `license_status`, `source_files`, `tests`, `local_changes`, `review` (geprüfter Stand oder ausdrücklich unbekannt) und `deployment_scope` (`product`, `build`, `external`, `reference`). Keine Uhrzeit des Generatorlaufs als angeblicher Prüfzeitpunkt.

- [x] **Step 1:** Verhaltenstests mit einem kleinen realistischen Fixture-Quellbaum schreiben: Pinänderung ändert Ausgabe, fehlende Herkunft/Quelle scheitert, doppelte IDs scheitern, erzeugte Projektion ist deterministisch, `verify` erkennt Drift und verändert keine Datei.
- [x] **Step 2:** Fehlende Implementierung als RED nachweisen.
- [x] **Step 3:** Versionen/Digests aus den vorhandenen Quellen ableiten und Herkunftsangaben ergänzen. Mindestens Basis-/Scannerimages, Binärartefakte, offizielle Plugins, Security-Overrides, UI-Patches, npm-Transitiva, direkte Alpinepins, Python-Build-/Testpakete, Buildhilfen und Actions erfassen. Für transitive OS-/Upstream-Pakete auf die erzeugte SBOM verweisen und deren Abdeckung als buildabhängig ausweisen; keine unvollständige Liste als vollständig bezeichnen.
- [x] **Step 4:** Externe Nextcloud-/App-/Ollama-/Hostkomponenten als `external` mit unbekannter installierter Version erfassen. `keithvassallomt/openclaw-nextcloud` bei Commit `c6659f3e20761b6b681f3e9135103a205eb23720` als `reference` mit den konkret geprüften Parser-/Testfundstellen verzeichnen. Die R0-Korrektur als eigene Implementierung kennzeichnen, nicht als installierte Fremd-Skill. Nachweis vom 2026-11-21 mit Verweis auf den vorhandenen Funktionsvergleich; keine neue Liveprüfung behaupten.
- [x] **Step 5:** Herkunfts-/Pflegeprüfung in `check-repo.sh` aufnehmen; Dokumentation erläutert bei jeder neuen Komponente Quellen, Owner, Lizenzprüfung, lokale Patches und Updateentscheidung. Unbekannte Lizenz-/Reviewangaben ausdrücklich als offen darstellen.
- [x] **Step 6:** Tests, Generator und `verify` ausführen. Diff selbst prüfen und Bericht schreiben; keine Gesamtmanifeständerung oder Veröffentlichung.

## Anschluss

Nach diesen beiden geprüften Aufgaben folgt ein eigener Implementierungsplan
für NC0 bis NC2 auf Grundlage der bestehenden Setup- und Runtime-Schnittstellen.
Die Autorisierung zur Entwicklung liegt vor. Das Nachfolgepaket muss Rechte
auch in Direktaufrufen, Suche, Index und Hintergrundabläufen durchsetzen und
über die vorhandene administrative Oberfläche bedienbar machen.
