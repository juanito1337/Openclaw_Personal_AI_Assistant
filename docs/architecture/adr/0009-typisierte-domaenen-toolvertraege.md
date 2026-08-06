# ADR-0009: Typisierte domaenennahe Toolvertraege und Portgrenzen

- Status: Accepted
- Datum: 2026-08-06
- Entscheider: Architecture Maintainers, Tool Contract Maintainers
- Betroffene Milestones: M5-M8

## Kontext

Der bisherige Registry-Builder enthielt 124 Werkzeuge in einer 959 Zeilen langen
Funktion. CLI-Hilfe, Registry, Approvalbeschreibung, Dokumentation und Tests waren
manuelle Projektionen. Ausserdem konsumierten Core-Module konkrete Typen und
Hilfsfunktionen aus `mail_agent`. Schon statische Introspektion benoetigte eine
produktionsnahe Konfiguration.

## Entscheidung

Jede Domaene besitzt unter `personal_assistant/tool_catalog/` ihre typisierten
`ToolDefinition`-Objekte. Ein Vertrag enthaelt ID, Domaene, Beschreibung, stabiles
Kommando, Handler, Argument- und Ausgabeschema, Modus, externe Wirkung,
Approvalklasse, Verfuegbarkeitsregel, maschinenlesbare Fehlercodes sowie Doku- und
Testanker. `personal_assistant/tool_registry.py` ist nur noch die kleine Projektion
dieser Definitionen auf die live konfigurierten Rechte.

`tools list --catalog` liefert alle bekannten Definitionen ohne Konfiguration und
ist nicht autoritativ fuer Instanzrechte. `capabilities --schema` beschreibt die
Live-Antwort ebenfalls konfigurationsfrei. Erst `tools list` und `capabilities`
laden Instanzkonfiguration und kennzeichnen die Sicht als `live-capabilities`.

Gemeinsame Typen und Ports liegen in `personal_assistant/contracts/` und importieren
keine Fachadapter. Der konkrete Himalaya-/`mail_agent`-Adapter liegt unter
`personal_assistant/adapters/` und wird ausschliesslich im Bootstrap verdrahtet.
CLI-Parser und Domaenenhandler liegen getrennt unter
`personal_assistant/cli_parsers/` beziehungsweise
`personal_assistant/cli_handlers/`. Die Anwendungsteile fuer Workspace, Mail,
Portfolio, Bestellungen und Sicherheit liegen unter `personal_assistant/services/`;
die Portfolio-Importparser besitzen ein eigenes Modul.

## Konsequenzen

- Eine neue Domaene ergaenzt einen lokalen Katalog und Handler statt eines zentralen
  hunderte Zeilen langen Dispatchers.
- Statischer Katalog und Live-Rechte koennen nicht miteinander verwechselt werden.
- Der generierte Befehlsindex darf nicht manuell gepflegt werden; der Repository-
  Check erkennt Drift.
- Die Registry erteilt weiterhin keine Rechte. Policy, Ressourcenrechte,
  ActionPlan, Approval und Audit bleiben unveraendert autoritativ.
- Bestehende Fachservices duerfen weiter inkrementell verkleinert werden; ein
  zentraler Service Locator ist verboten.

## Verifikation

`tests/test_m5_tool_contract.py` friert alle 124 bisherigen Toolprojektionen und die
Top-Level-Hilfe als Golden Contract ein. Es prueft Typen, Handler, Doku-/Testanker,
Policy-Negativpfade, konfigurationsfreie Introspektion, getrennte Live-Capabilities,
Importzyklen und verbotene Core-Rueckimporte. Der Gesamtcheck validiert ausserdem
`docs/COMMAND_REFERENCE.md` gegen den Generator.
