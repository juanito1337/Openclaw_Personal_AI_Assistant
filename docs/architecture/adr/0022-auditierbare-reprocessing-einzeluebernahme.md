# ADR-0022: Reprocessing-Apply ist eine gebundene, auditierbare Einzeluebernahme

- Status: Accepted
- Datum: 2026-08-16
- Entscheider: Data Maintainers, Security Maintainers
- Betroffene Milestones: M10.6

## Kontext

ADR-0021 bindet eine read-only Rechnungsneubewertung an PDF-Hash, aktuellen
Datensatz, Extraktorversion und kanonischen Vorschlag. Dieser Digest erteilt noch
keine Schreibfreigabe. Zwischen Anzeige und Uebernahme koennen PDF, SQLite-Zeile,
Status, Extraktor oder Regeln driften. Eine lokale SQLite-Aenderung und der
anschliessende Nextcloud-Registerersatz bilden zudem keine gemeinsame
Transaktion; ein Remote-Konflikt darf deshalb weder verschwiegen noch als lokal
zurueckgerollt dargestellt werden.

## Entscheidung

`invoices reprocess-apply` akzeptiert genau einen vollstaendigen PDF-SHA-256 und
genau einen erwarteten `preview_sha256`. Es besitzt keine Bulk-, Such-, Limit-
oder freie SQL-Option und verlangt das Approval
`explicit-user-single-invoice-reprocess` durch `--yes`.

Vor jeder ersten Aenderung wird die archivierte Datei innerhalb des
konfigurierten Rechnungsroots read-only geladen, gegen den freigegebenen Hash
geprueft, fail-closed gescannt und mit dem aktuellen Extraktor erneut bewertet.
Der neu berechnete Preview-Digest muss exakt passen. Bestaetigte oder manuell
korrigierte Zeilen, nicht verbesserte Vorschlaege, offene Konflikte, unbestaetigte
Pflichtdaten und unplausible Betragsarithmetik werden abgelehnt.

Schema 4 fuegt `invoice_reprocess_audit` additiv und wiederholbar hinzu. Die
genau-eine-Zeilen-Aenderung und der erste Auditdatensatz laufen unter
`BEGIN IMMEDIATE` in einer SQLite-Transaktion. Das Audit speichert nur
Operation-/Zustands-/Vorschlagshashes, Extraktorversion, Approval, fachliche
Status, betroffene Registerjahre, Claim, Versuchszahl und Ergebnis. PDF-/OCR-
Text, Mailinhalt, Archivpfad, Remote-Antworten und Zugangsdaten sind ausgeschlossen.

Nach dem lokalen Commit werden nur die betroffenen alten und neuen
Jahresregister ueber den bestehenden festen Pfad mit Schema-, Inhalts-SHA- und
ETag-Bedingung synchronisiert. Ein zeitlich begrenzter Claim verhindert zwei
gleichzeitige Registerwriter fuer dieselbe Operation. Remote-Konflikt oder
Ausfall endet sichtbar als `register-failed`; derselbe unveraenderte Hash/Digest
kann die Operation idempotent wiederaufnehmen. Ein bereits abgeschlossener
Aufruf gleicht den erwarteten Remote-Zustand erneut ab, statt Erfolg nur aus dem
lokalen Audit abzuleiten.

Das Original-PDF und sein bestehender Archivpfad sind waehrend Vorschau, Apply
und Wiederaufnahme unveraenderlich.

## Konsequenzen

Eine Nutzerfreigabe kann nicht auf einen anderen Beleg, einen geaenderten
Vorschlag oder eine neue Extraktorversion uebertragen werden. Lokale und externe
Teilergebnisse bleiben nachvollziehbar, ohne vertraulichen Dokumentinhalt zu
duplizieren. Der Registerabgleich kann sicher wiederholt werden, ist aber
weiterhin keine verteilte Transaktion; nach einem lokalen Commit darf ein Remote-
Fehler nicht als vollstaendiger Rollback beschrieben werden.

## Verifikation

Hermetische Tests verwenden ausschliesslich temporaere SQLite-Dateien, erfundene
PDF-Bytes und simulierte Registerantworten. Sie pruefen Migration und
Wiederholung, fehlendes Approval, falschen Hash/Digest, Datensatz- und
Statusdrift, Schutz manueller Werte, Regression und Rechenfehler, exakt eine
lokale Zeile, inhaltsfreies Audit, Idempotenz, parallelen Apply, Remote-Konflikt,
Ausfall und Wiederaufnahme. Originalbytes und Archivpfad bleiben unveraendert.
