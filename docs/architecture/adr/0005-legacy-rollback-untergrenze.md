# ADR-0005: Verifizierte Legacy-Rollback-Untergrenze

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Operations Maintainers
- Betroffene Milestones: M1-M8

## Kontext

Die erste Containerumstellung muss auf einen zuvor nativen systemd-Betrieb
rueckrollen koennen. Ein unvollstaendiges Legacy-Home darf nicht erst nach dem Stop
des aktuellen Stacks entdeckt werden.

## Entscheidung

Legacy-systemd ist kein aktiver Primaerbetrieb, sondern nur eine Rollback-Untergrenze.
Vor dem Stop des aktuellen Containers muss ein startbares Legacy-Home verifiziert
oder aus dem mit SHA-256 verknuepften Migrationsarchiv wiederhergestellt sein. Fehlt
beides, bricht Rollback ab und laesst die aktuelle Runtime laufen.

## Konsequenzen

Migrationsarchive und ihre Referenzen muessen solange erhalten bleiben, wie ein
Legacy-Rollback unterstuetzt wird. Die spaetere Entfernung benoetigt eine eigene ADR
und nachgewiesene Mindestversion.

## Verifikation

Migration-, Backup-, Restore- und Rollbacktests einschliesslich unvollstaendiger
Legacy-Quellen.

## Offene Fragen

Ab welcher erfolgreich migrierten Release-Untergrenze darf M8 die Legacy-Rueckkehr
beenden?
