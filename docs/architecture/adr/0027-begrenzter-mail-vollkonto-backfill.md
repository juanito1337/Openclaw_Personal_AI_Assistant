# ADR-0027: Begrenzter Mail-Vollkonto-Backfill

- Status: Accepted
- Datum: 2026-08-19
- Entscheider: Data Maintainers, Security Maintainers
- Bezug: M11.2, ADR-0017, ADR-0026

## Kontext

Die aktive Mail-Suchprojektion kennt nur bereits vom Mailworker verarbeitete
Nachrichten. Ein Vollkonto-Aufbau muss viele Ordner lesen koennen, ohne das Konto
in RAM zu laden, einen zweiten externen Writer zu schaffen oder bei einem
Abbruch eine teilweise Generation als vollstaendig zu veroeffentlichen.
Connectoren liefern zudem unterschiedliche IMAP-Nachweise. Insbesondere belegt
Himalaya 1.2 nummeriertes Paging und Raw-Export, exponiert in diesem Pfad jedoch
keine UIDVALIDITY-, UIDNEXT-, MODSEQ-, CONDSTORE-, QRESYNC- oder IDLE-Werte.

## Entscheidung

M11.2 fuehrt einen Mail-Owner-Crawler mit zwei getrennten Toolvertraegen ein:

- `mail.index.plan` inventarisiert Ordner und jede Connectorfaehigkeit read-only.
- `mail.index.backfill` benoetigt die explizite lokale Freigabe
  `explicit-user-local-mail-index-backfill` und den Mail-Prozesslock.

Der Backfill schreibt ausschliesslich unter
`<mail-data>/search_backfill_v2/`: ein atomares JSON-Checkpoint und immutable
v2-Content-, Occurrence- und Seitenpartitionsdateien. Die aktive
`search_documents`-Projektion wird in M11.2 nicht ersetzt. Eine Seitenpartition
wird zuerst vollstaendig publiziert; erst danach wird der Cursor atomar auf die
naechste Seite gesetzt. Nach einem Crash wird daher hoechstens die letzte Seite
deterministisch wiederholt. Content-addressierte Dateien und eindeutige
Occurrence-IDs verhindern Duplikate.

Der Lauf hat explizite Grenzen fuer Seitengroesse, Seiten, Nachrichten,
Gesamtbytes, Einzelmailgroesse, Laufzeit und Request-Intervall. Er verarbeitet
nie das gesamte Konto gleichzeitig im Speicher. Ordnername, stabile
Connector-ID und UIDVALIDITY werden getrennt erfasst. Neu, entfernt und
umbenannt werden durch den Vergleich des aktuellen Inventars mit dem gebundenen
Checkpoint sichtbar.

Komplette Raw-Mail und jede physisch dekodierte Anlage durchlaufen vor Parsing
und Bodypublikation das bestehende fail-closed ClamAV-Gate. Der Cache ist an
Raw-SHA-256 und Scanneridentitaet gebunden. Bei Identitaetswechsel wird der Lauf
neu begonnen und erneut gescannt. Fund, Scannerfehler, Decodefehler oder
Groessenlimit speichern nur Locator, Digest und typisierten Status; weder Body
noch Anlagenbytes erscheinen in Projektion oder Checkpoint.

Eine Root-Generation ist nur dann `complete=true` und autoritativ, wenn alle
Ordnerseiten ohne Fehler und mit belegten UID- plus UIDVALIDITY-Locatorn
publiziert wurden. Der Himalaya-Fallback verwendet Page-Number sowie
Mailbox-ID-plus-Raw-SHA fuer begrenzte Wiederaufnahme, bleibt aber zwingend
unvollstaendig. Fehlendes MODSEQ/QRESYNC wird durch Vollscan ersetzt; fehlende
IDLE-Unterstuetzung wird nicht simuliert. M11.2 erzeugt keine Tombstones und
keine inkrementelle Move-/Delete-Semantik; das ist M11.3.

## Konsequenzen

- Plan und Backfill besitzen klare Wirkungs- und Freigabegrenzen.
- Crash, Timeout, Rate-Limit, Ordnerfehler und Scannerblock erhalten den letzten
  sicheren Checkpoint und koennen keine Abwesenheit beweisen.
- Quarantaeneordner bleiben sichtbar als `quarantine-untrusted` und unterliegen
  weiterhin Rescue-only-Regeln.
- Der aktuelle Himalaya-Produktionsconnector kann Daten vorbereiten, aber noch
  keine autoritative Vollkontoabdeckung attestieren.
- Ein produktiver Backfill, Job-Rollout, v2-Aktivierung, Deltaabgleich und Ranking
  sind ausdruecklich nicht Bestandteil dieser Entscheidung.

## Verifikation

`tests/test_mail_search_backfill_m112.py` verwendet ausschliesslich Fake-IMAP,
synthetische EMLs, Fake-ClamAV und temporaere Verzeichnisse. Die Tests decken
Mehrordner-Paging, Nullbestand, Unicode, grosse Mails, doppelte/fehlende
Message-ID, Crash/Resume an jeder Seitengrenze, Limits, Fehler, UIDVALIDITY,
Ordnerrename/-reset, Scannerblock/-identitaetswechsel und gebundenen
Spitzenverbrauch ab.
