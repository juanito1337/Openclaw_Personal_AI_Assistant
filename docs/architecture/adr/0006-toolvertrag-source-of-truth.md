# ADR-0006: Maschinenlesbares Toolinventar als Source of Truth

- Status: Superseded durch [ADR-0009](0009-typisierte-domaenen-toolvertraege.md)
- Datum: 2026-08-05
- Entscheider: Tool Contract Maintainers
- Betroffene Milestones: M1-M8

## Kontext

Ein unterstuetztes Tool muss derzeit gleichzeitig in CLI, `tools list`,
Agentenvertrag/Skill und Tests sichtbar sein. Ohne kanonisches Inventar koennen
Beschreibung, Schreibmodus und Approval auseinanderlaufen.

## Entscheidung

`personal_assistant/tool_registry.py` ist die maschinenlesbare Source of Truth fuer
Tool-ID, Beschreibung, stabiles Kommando, Modus, externe Schreibwirkung und
Approvalklasse. CLI-Parser und `assistant.sh` bleiben die ausfuehrbare Oberflaeche;
`AGENTS.md` und Skill bleiben die normative Sicherheitsprojektion. Tests muessen alle
Projektionen gegen die Registry abgleichen.

## Konsequenzen

Die Registry erteilt selbst keine Rechte und ersetzt weder Policy noch Ressourcen.
Bis zur Generierung typisierter Vertraege in M5 bestehen noch manuell gepflegte
Projektionen; neue Tools duerfen diese Duplizierung nicht umgehen.

## Verifikation

Tool-Architekturtests pruefen ID-Eindeutigkeit, CLI-Erreichbarkeit, Dokumentation und
Regressionstest. M1-Dokumentationstests pruefen den ADR- und Erweiterungsvertrag.

## Offene Fragen

Welche Teile von CLI-Hilfe, Skill und Policy-Metadaten werden in M5 direkt aus einem
typisierten Schema generiert?
