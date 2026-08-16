# ADR-0024: M10-Abnahme und produktiver Rechnungsrollout bleiben getrennt

- Status: Accepted
- Datum: 2026-08-17
- Entscheider: Data Maintainers, Operations Maintainers, Security Maintainers
- Betroffene Milestones: M10.8

## Kontext

M10 liefert belegte Feldextraktion, Plausibilitaet, eine read-only Vorschau und
eine digestgebundene Einzeluebernahme. Eine gruene Entwicklungs-, Wheel- oder
Containerabnahme beweist jedoch weder die Rueckrollbarkeit produktiver
Nextcloud-Aenderungen noch die Eignung eines konkreten historischen Prueffalls.
Ein Bildwechsel allein kann ein bereits ersetztes Jahresregister nicht
wiederherstellen.

## Entscheidung

Der M10-Entwicklungsabschluss endet nach hermetischer Gesamt-, Artefakt-,
Container- und Recovery-Abnahme. Er fuehrt kein Deployment und keinen produktiven
Reprocess aus. Der spaetere Rollout folgt dem separaten Vertrag unter
[`../../INVOICE_M10_ROLLOUT.md`](../../INVOICE_M10_ROLLOUT.md).

Vor einem schreibenden Canary werden die drei signierten Rollendigests, exakte
Quellrevision, Single-Writer-Zustand, ein verifiziertes lokales Releasebackup und
ein verifizierter externer Nextcloud-Snapshot nachgewiesen. Status und Audit
bilden eine read-only Baseline. Danach wird genau ein nicht manuell geschuetzter
Review-/Legacy-Fall vorgeschlagen. Hash, Vorschau-Digest, exakte Felddifferenz,
Evidenz und Konflikte werden Jan gezeigt; erst ein neuer ausdruecklicher Auftrag
autorisiert genau diesen Apply.

Nach dem Apply werden lokale Integritaet, inhaltsfreies Audit,
Register-ETag/SHA/Schema, Health und Single Writer erneut geprueft. Jeder weitere
Fall benoetigt eine neue Vorschau und Freigabe. Bulk-Reprocessing, automatische
Backlogabarbeitung und PDF-Verschiebung bleiben ausgeschlossen.

## Konsequenzen

Entwicklungsfreigabe, Deploymentfreigabe und fachliche Einzelfreigabe sind drei
getrennte Entscheidungen. Ein lokaler Apply mit fehlgeschlagenem Registersync
bleibt sichtbarer Teilerfolg und stoppt den Ablauf. Lokaler Rollback und externer
Restore werden getrennt berichtet; ohne erfolgreichen externen Restore bleibt
der Nextcloud-Zustand unklar.

## Verifikation

Die M10.8-Regression prueft den unveraenderten Feldqualitaetsvergleich,
Toolwirkungen und Approvals, PDF-Ausschluss in Git/Wheel/Image, die CI-Pfade fuer
Wheel und alle Rollenimages sowie die geordnete Trennung von Baseline, Vorschau,
Einzelfreigabe, Nachmessung und Recovery. Produktive Konten und `/srv/openclaw`
werden dabei nicht verwendet.
