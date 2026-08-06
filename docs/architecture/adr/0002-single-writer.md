# ADR-0002: Single-Writer fuer externe Schreibdomaenen

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Security Maintainers
- Betroffene Milestones: M1-M8

## Kontext

Parallele systemd- und Container-Mailwriter koennen Mails doppelt verschieben,
weiterleiten oder inkonsistente lokale Zustandsnachweise erzeugen. Auch externe
Objektupdates benoetigen eine eindeutige kontrollierte Ausfuehrungsgrenze.

## Entscheidung

Pro externer Schreibdomaene darf genau ein produktiver Writerpfad aktiv sein. Der
Container-Mailworker und der historische systemd-Mailwriter duerfen niemals parallel
laufen. Bestehende Remoteobjekte werden nur durch exakte Ressource/UID, aktuelle
Versionskennung und den registrierten Approval-/ActionPlan-Pfad geaendert.

## Konsequenzen

Deployments muessen Writer vor Migration, Smoke und Rollback stoppen und pruefen.
Horizontale Skalierung eines Writers ist ohne neue Serialisierungsentscheidung nicht
zulaessig.

## Verifikation

Deployment-/Rollbacktests, Jobcontroller, Advisory Locks, Idempotenz- und ETag-Tests.

## Offene Fragen

Ob spaetere verteilte Writer ein Lease-Protokoll benoetigen, ist nicht entschieden.
