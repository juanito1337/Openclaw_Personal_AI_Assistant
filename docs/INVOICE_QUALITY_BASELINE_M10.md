# M10.0 – Rechnungqualitaets-Baseline

Stand: 2026-08-15. Dieses Dokument ist eine Beobachtungsbasis, keine Freigabe
willkuerlicher Qualitaetsgrenzen. M10.0 aendert weder Extraktion noch Datenbank,
Toolvertrag, Routing oder produktive Daten.

## Datenschutz und zwei getrennte Messbereiche

Die operative Aufnahme enthaelt nur aggregierte Zaehler. Einzelne Lieferanten,
Rechnungsnummern, Message-IDs, Dateinamen, Pfade, Hashes, Adressen, Konto- oder
Bestellnummern werden nicht versioniert. Der reproduzierbare Extraktortest nutzt
ausschliesslich den synthetischen Korpus
`tests/fixtures/invoices/m10_sanitized_corpus.json`; alle Mailadressen enden auf
der reservierten Domain `example.invalid`.

Produktive Abfragen sind kein Bestandteil von `check-repo.sh`. Sie duerfen nur in
einer separat autorisierten operativen Sitzung read-only ausgefuehrt werden. Die
lokale und CI-seitige Baseline benoetigt weder `/srv/openclaw` noch Nextcloud,
Postfachzugriff, Secrets oder laufende Container.

## Aggregierter operativer Ausgangswert

Die fuer die M10-Planung bereitgestellten read-only Ausgaben ergeben:

| Messwert | Ausgangswert |
| --- | ---: |
| Rechnungszeilen insgesamt | 63 |
| `review` | 48 |
| bestaetigt, einschliesslich manueller Bestaetigung | 5 |
| leerer `extraction_status` | 10 |
| fehlende Rechnungsnummer | 39 |
| fehlender Bruttobetrag | 27 |
| Datumsproblem | 7 |
| historischer Hinweis auf fehlendes Tesseract | 22 |
| Kategorie leer oder `Ungeklärt` | 19 |
| Extraktionsmethode `text` | 40 |
| Extraktionsmethode `text+ocr-fallback` | 8 |
| mittlere gespeicherte Extraktionskonfidenz | 0,5127 |
| vollstaendige Brutto/Netto/Steuer-Tripel | 10 |
| davon rechnerisch inkonsistent | 9 |
| Steuerwert ohne Bruttowert | 26 |
| Steuerwert groesser als Bruttowert | 4 |
| Reviewzeilen unter einem `Pruefen`-Pfad | 29 |
| Reviewzeilen ausserhalb eines `Pruefen`-Pfads | 19 |

Die Jahresregister enthielten 2 Zeilen fuer 2024, 34 fuer 2025 und 17 fuer 2026.
Der damalige Backfill-Dry-run fand fuer 2024 und 2025 keine Kandidaten und fuer
2026 genau zehn Kandidaten. Davon wurde einer als `confirmed` und neun als
`review` bewertet. Diese zehn Kandidaten sind die Zeilen mit leerem
`extraction_status`; die 48 bereits als `review` markierten Zeilen werden durch die
aktuelle Legacy-Auswahl nicht erneut gesammelt.

Die Baseline kann ohne Ausgabe der Einzelzeilen erneut verdichtet werden. Der
erste Befehl liefert die offiziellen Summen. Der zweite Befehl reicht die
read-only Liste direkt an `jq` weiter und gibt nur Aggregate aus:

```bash
./scripts/assistant.sh invoices status

./scripts/assistant.sh invoices list --limit 5000 | jq '
  .records as $r |
  def blank: . == null or . == "";
  {
    all: ($r | length),
    review: [$r[] | select(.extraction_status == "review")] | length,
    confirmed: [$r[] | select(.extraction_status == "confirmed" or .extraction_status == "confirmed-manual")] | length,
    empty_extraction_status: [$r[] | select(.extraction_status | blank)] | length,
    missing_invoice_number: [$r[] | select(.invoice_number | blank)] | length,
    missing_gross: [$r[] | select(.gross_amount_cents == null)] | length,
    unresolved_category: [$r[] | select((.category | blank) or .category == "Ungeklärt")] | length,
    complete_amount_triples: [$r[] | select(.gross_amount_cents != null and .net_amount_cents != null and .tax_amount_cents != null)] | length,
    inconsistent_amount_triples: [$r[] | select(.gross_amount_cents != null and .net_amount_cents != null and .tax_amount_cents != null and ((.gross_amount_cents - .net_amount_cents - .tax_amount_cents) | fabs) > 2)] | length,
    tax_without_gross: [$r[] | select(.tax_amount_cents != null and .gross_amount_cents == null)] | length,
    tax_above_gross: [$r[] | select(.tax_amount_cents != null and .gross_amount_cents != null and .tax_amount_cents > .gross_amount_cents)] | length,
    review_in_pruefen: [$r[] | select(.extraction_status == "review" and (.nextcloud_path | contains("/Pruefen/")))] | length,
    review_outside_pruefen: [$r[] | select(.extraction_status == "review" and ((.nextcloud_path | contains("/Pruefen/")) | not))] | length,
    average_confidence: ([$r[].extraction_confidence] | add / length)
  }'
```

Die Feinzaehler fuer Datums-/OCR-Probleme und Extraktionsmethoden beruhen auf
versionierten Feldnamen und Issue-Texten, die in spaeteren Paketen geaendert
werden koennen. Bei einem erneuten operativen Snapshot muessen deshalb sowohl der
Git-Commit als auch die verwendete Release-ID mit erfasst werden, nicht jedoch die
Einzelwerte.

## Synthetischer Extraktor-Ausgangswert

Der Korpus umfasst acht erfundene deutsche und englische Rechnungen. Er deckt
explizite und fehlende Rechnungsnummern, Rechnungs-/Leistungs-/Faelligkeitsdaten,
Brutto/Netto/Steuer, Steuersaetze, negative Gutschriften, mehrere Gesamtsummen und
eine auf zwei Textseiten verteilte PDF-Ausgabe ab.

```bash
.venv/bin/python scripts/evaluate_invoice_quality.py --verify
.venv/bin/python -m pytest -q tests/test_invoice_quality_m10.py
```

| Messwert | Ausgangswert |
| --- | ---: |
| synthetische Faelle | 8 |
| erwartete nichtleere Felder | 55 |
| vorhergesagte nichtleere Felder | 55 |
| korrekte Felder | 54 |
| Feldpraezision insgesamt | 0,9818 |
| Feldabdeckung insgesamt | 0,9818 |
| `review` | 1 |
| Reviewquote | 0,1250 |
| `confirmed` | 7 |
| False-confirmed | 1 |
| False-confirmed-Quote unter `confirmed` | 0,1429 |
| vollstaendige Betragstripel | 6 |
| rechnerische Fehler bei vollstaendigen Tripeln | 0 |

Feldpraezision ist `korrekte nichtleere Vorhersagen / alle nichtleeren
Vorhersagen`. Feldabdeckung ist `korrekte nichtleere Vorhersagen / alle
erwarteten nichtleeren Werte`. Ein False-confirmed liegt vor, wenn ein Beleg als
`confirmed` gilt, obwohl mindestens Rechnungsdatum, Rechnungsnummer, Lieferant
oder Bruttobetrag fehlt oder vom Sollwert abweicht. Ein Rechenfehler liegt bei
einem vollstaendigen Betragstripel vor, wenn `Brutto - Netto - Steuer` um mehr als
zwei Cent von null abweicht.

Der sichtbare False-confirmed-Fall ist beabsichtigte Charakterisierung: Bei zwei
plausiblen Gesamtsummen waehlt der aktuelle Extraktor den groesseren statt des
tatsaechlich zahlbaren Betrags. M10.0 behebt diesen Fehler nicht. Ebenso friert der
Test die derzeitige Jahresprioritaet
`invoice_date -> received_date -> created_at -> Pfadjahr -> Maildatum` nur als
beobachtetes Verhalten ein; er erklaert sie nicht zur gewuenschten Semantik.

Die historische vollstaendige M10.0-Erwartung bleibt in
`tests/fixtures/invoices/m100_extractor_baseline.json` erhalten. Die vom
Standard-Verifier verwendete `m10_extractor_baseline.json` folgt dagegen dem
aktuellen, abgenommenen Extraktorstand und wird bei einem spaeteren Paket nur mit
einem dokumentierten Direktvergleich aktualisiert.

## M10.2 – Rechnungsnummern und Datumsrollen

M10.2 ergaenzt den unveraenderten M10.0-Korpus um einen zweiten, ausschliesslich
synthetischen 12-Faelle-Korpus. Er umfasst deutsche und englische Anker,
Unicode, Bindestriche, Schraegstriche, alphanumerische Werte, OCR-Abstaende,
einzeilige und begrenzt zweizeilige Felder, Dateinamen-Evidenz, Nummern- und
Datumskonflikte sowie negative Kunden-, Bestell-, Liefer-, Vertrags-, Telefon-,
Steuer-, Tracking- und IBAN-Felder. Alle Werte sind erfunden; Adressen verwenden
weiterhin `example.invalid`.

```bash
.venv/bin/python scripts/evaluate_invoice_quality.py \
  --corpus tests/fixtures/invoices/m102_number_date_corpus.json \
  --baseline tests/fixtures/invoices/m102_number_date_baseline.json \
  --verify

.venv/bin/python -m pytest -q tests/test_invoice_number_date_m102.py
```

Der Vorher-Wert wurde vor der Extraktoraenderung auf Commit
`b8aa79418540ff4e44a46feba2fbc579ccbf5693` mit demselben Korpus und demselben
Evaluator gemessen. Die kompakten Werte sind versioniert unter
`tests/fixtures/invoices/m102_number_date_comparison.json`; der vollstaendige
Sollbericht nach M10.2 liegt in `m102_number_date_baseline.json`.

| Messwert auf dem M10.2-Korpus | Vor M10.2 | Nach M10.2 |
| --- | ---: | ---: |
| synthetische Faelle | 12 | 12 |
| Rechnungsnummer erwartet | 8 | 8 |
| Rechnungsnummer vorhergesagt | 4 | 8 |
| Rechnungsnummer korrekt | 4 | 8 |
| Rechnungsnummer-Praezision | 1,0000 | 1,0000 |
| Rechnungsnummer-Abdeckung | 0,5000 | 1,0000 |
| Rechnungsdatum erwartet/vorhergesagt/korrekt | 11 / 11 / 11 | 11 / 11 / 11 |
| Rechnungsdatum-Praezision | 1,0000 | 1,0000 |
| Rechnungsdatum-Abdeckung | 1,0000 | 1,0000 |
| `confirmed` | 3 | 7 |
| `review` | 9 | 5 |
| False-confirmed | 0 | 0 |

Der Anstieg bestaetigter Faelle stammt ausschliesslich aus zuvor nicht erkannten,
aber beschrifteten Nummernfeldern. Dateinamen allein, Nummern anderer Rollen,
datumsfoermige Nummernwerte sowie widerspruechliche Rechnungsnummern oder
Rechnungsdaten bleiben `review`. M10.2 veraendert weder SQLite-Schema noch
Backfill-Auswahl, Nextcloud-PDFs oder Jahresregister und fuehrt kein Reprocessing
aus. Der in M10.0 sichtbare Fehler bei mehreren Betraegen blieb bis M10.3 als
historische Baseline erhalten.

## M10.3 – Typisierte Betraege und Plausibilitaet

M10.3 ergaenzt einen separaten, vollstaendig synthetischen 15-Faelle-Korpus fuer
deutsche und englische Dezimal-/Tausenderformate, EUR, USD, GBP und CHF,
Steuersaetze, mehrere Summen, Rundung, Abschlag, Rabatt, Einzelpreis, positive
und negative Gutschriften sowie Waehrungskonflikte. Der Vorher-Wert wurde vor der
Betragsaenderung auf Commit
`593361b2486d1e412a846280c5fffed7f6759395` mit demselben Korpus gemessen.

```bash
.venv/bin/python scripts/evaluate_invoice_quality.py \
  --corpus tests/fixtures/invoices/m103_amount_corpus.json \
  --baseline tests/fixtures/invoices/m103_amount_baseline.json \
  --verify

.venv/bin/python -m pytest -q tests/test_invoice_amounts_m103.py
```

Die kompakten Vorher-/Nachher-Werte liegen in
`tests/fixtures/invoices/m103_amount_comparison.json`; der vollstaendige aktuelle
Bericht liegt in `m103_amount_baseline.json`.

| Messwert auf dem M10.3-Korpus | Vor M10.3 | Nach M10.3 |
| --- | ---: | ---: |
| synthetische Faelle | 15 | 15 |
| Brutto-Praezision | 0,8333 | 1,0000 |
| Brutto-Abdeckung | 0,7143 | 1,0000 |
| Netto-Praezision / -Abdeckung | 0,8182 / 0,8182 | 1,0000 / 1,0000 |
| Steuer-Praezision / -Abdeckung | 0,9000 / 0,8182 | 1,0000 / 1,0000 |
| Waehrungs-Praezision / -Abdeckung | 0,8667 / 0,9286 | 1,0000 / 1,0000 |
| False-confirmed | 5 | 0 |
| vollstaendige Betragstripel | 7 | 11 |
| rechnerisch inkonsistente ausgegebene Tripel | 4 | 2 |

Die zwei verbleibenden Rechenfehler sind bewusst unplausible synthetische
Dokumente: ein widerspruechliches Brutto/Netto/Steuer-Tripel und ein Zahlbetrag
nach Abschlag, der nicht dem unveraenderten Netto/Steuer-Tripel entspricht. Beide
werden mit typisiertem Reviewgrund abgelehnt und sind deshalb kein
False-confirmed. Die dokumentierte Rundungstoleranz betraegt exakt zwei Cent.

Der urspruengliche acht Faelle umfassende M10-Korpus erreicht nach M10.3 fuer alle
55 erwarteten Felder Praezision und Abdeckung 1,0000; der zuvor sichtbare
Mehrfachsummenfehler ist korrigiert. M10.3 aendert weder SQLite-Schema noch
Backfill-/Reprocessing-Auswahl, Nextcloud-PDFs oder Jahresregister und fuehrt
keinen produktiven Lauf aus.

## M10.4 – Begrenzte OCR und Feldfusion

Der sanitiserte M10.4-Korpus beschreibt Textschicht-, bildbasierte, gemischte,
mehrseitige und korrupte PDFs ohne produktive Inhalte. Pflichtfelder werden
einzeln bewertet. Sind Rechnungsdatum, Rechnungsnummer, Bruttobetrag und ein
dokumentbelegter Rechnungssteller brauchbar, bleibt OCR aus. Andernfalls darf der
lokale Fallback nur innerhalb der in `docs/INVOICE_OCR_REGISTER.md`
dokumentierten PDF-, Seiten-, DPI-, Gesamtzeit-, Render- und Ausgabebudgets
arbeiten. Native/OCR-Konflikte werden als typisierte Reviewgruende sichtbar.

Reproduzierbare Funktions- und Laufzeitpruefung:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_ocr_m104.py
.venv/bin/python scripts/benchmark-invoice-ocr-m104.py
```

Gemessen wurde am 2026-08-16 auf dem lokalen Entwicklungsrechner mit einem vom
Benchmark selbst erzeugten, 1.284 Byte grossen sanitisierten Drei-Seiten-PDF:

| Messwert | M10.4-Ausgangswert |
| --- | ---: |
| `pdftotext` / `pdfinfo` / `pdftoppm` | 24.02.0 |
| Tesseract | 5.3.4 |
| nativer Lauf | 19,040 ms |
| gesamter OCR-Lauf | 3.556,265 ms |
| intern gemessener OCR-Lauf | 3.555,622 ms |
| ausgewaehlte Seiten | 1 und 3 von 3 |
| gerenderte PNG-Daten | 122.217 Byte |
| native Ausgabe | 158 Zeichen |
| OCR-Ausgabe | 105 Zeichen |
| maximal gemeldeter Child-RSS | 130.912 KiB |

Die Zeiten und RSS-Zahl sind beobachtete Werte, keine Quality Gates; sie variieren
mit CPU, Cache und Werkzeugbuild. Der Benchmark gibt seine Werkzeugversionen,
Budgets und Messwerte als JSON aus, verarbeitet keine produktive PDF und greift
weder auf `/srv/openclaw` noch auf Nextcloud oder eine externe OCR zu. M10.4
aendert kein Datenbankschema und fuehrt weder Backfill noch Reprocessing aus.
Der vollstaendige Repositorylauf sammelte und bestand 659 pytest-Items und
erreichte 63,45 Prozent Branch-einbezogene Gesamt-Coverage. Der direkt betroffene
Extraktor `mail_agent/invoice_extract.py` erreichte dabei 84,27 Prozent.

## M10.5 – Read-only Reprocessing-Vorschau

M10.5 veraendert die M10.0- bis M10.4-Extraktionsmetriken nicht, sondern stellt
den versionierten Extraktor fuer vorhandene `review`- oder unklassifizierte
Zeilen ueber einen neuen read-only Vertrag bereit. Die vollstaendige Suite
sammelt und besteht 671 pytest-Items; 607 sind unittest-kompatibel und die 13
freien Rechnungs-pytest-Tests bleiben enthalten. Die branch-einbezogene
Gesamt-Coverage betraegt 63,70 Prozent, die reine Branch-Coverage 49,35 Prozent.
Das neue Modul `mail_agent/invoice_reprocess.py` erreicht 75,07 Prozent, der
unveraenderte Extraktor weiterhin 84,27 Prozent.

Die M10.5-Fixture verwendet vier absichtlich verschiedene Jahreswerte (Quelljahr
2024, Pfadjahr 2025, Empfangsjahr 2026 und erkanntes Rechnungsjahr 2027) und
vollstaendig erfundene PDF-Bytes. Vorher-/Nachhervergleiche belegen bytegleiche
SQLite- und PDF-Zustaende, unveraenderten synthetischen Register-ETag und keinen
erzeugten Auditpfad. Digestdrift wird getrennt fuer PDF-SHA-256, Altzustand,
Extraktorversion und Neuvorschlag geprueft. In Git befinden sich keine
produktiven Einzelwerte, Pfade, Hashes, PDFs oder Nextcloud-Antworten.

## M10.6 – Auditierbare Einzeluebernahme

M10.6 veraendert die synthetischen Extraktionskorpora und deren M10.0- bis
M10.4-Feldmetriken nicht. Die gemeinsame Suite sammelt 680 pytest-Items und
meldet 748 JUnit-Faelle einschliesslich Subtests; 616 Items sind
unittest-kompatibel und die 13 zuvor ausgelassenen freien Rechnungs-pytest-Tests
bleiben enthalten. Die branch-einbezogene Gesamt-Coverage betraegt 63,85 Prozent,
die reine Branch-Coverage 49,59 Prozent. Das neue Modul
`mail_agent/invoice_reprocess_apply.py` erreicht 75,58 Prozent; der bestehende
Extraktor bleibt bei 84,27 Prozent und die Vorschau erreicht 76,78 Prozent.

Die neun M10.6-Items erzeugen genau eine temporaere Rechnungszeile, eine
Schema-3/4-Migrationsfixture und erfundene PDF-Bytes. Gemessen werden nur
Operationsergebnis, Zeilen-/Auditanzahl, Fingerprints, fachliche Status,
Registerjahre und Versuchszahl. PDF-/OCR-Text, Mailinhalt, Zugangsdaten,
produktive Pfade und Remote-Antwortinhalte werden weder in die Fixture-Ausgabe
noch in das Audit uebernommen. Ein simuliertes altes/neues Jahresregister, ETag-
Konflikt, Remote-Ausfall, Wiederaufnahme und konkurrierender Apply benoetigen
weder `/srv/openclaw` noch Netzwerk oder Container. Es wurde kein produktiver
Reprocess oder Apply ausgefuehrt.

## M10.7 – Aggregierter Backlog-Audit

M10.7 aendert weder Extraktor noch die M10.0- bis M10.4-Feldmetriken. Die neue
hermetische Fixture enthaelt sieben vollstaendig erfundene Rechnungszeilen: eine
unklassifizierte Legacy-Zeile, drei Reviewzeilen, eine bestaetigte, eine manuell
korrigierte und eine technische Fehlerzeile. Sie misst ausschliesslich Aggregate.

```bash
.venv/bin/python -m pytest -q tests/test_invoice_backlog_audit_m107.py
```

| Synthetischer Auditmesswert | Wert |
| --- | ---: |
| Datensaetze | 7 |
| unklassifizierte Legacy-Zeilen | 1 |
| Reviewzeilen | 3 |
| bestaetigte Zeilen | 1 |
| manuelle Korrekturen | 1 |
| Review im Pruefpfad / ausserhalb / ohne Pfad | 1 / 1 / 1 |
| Register-/Pfad-Jahresabweichungen | 1 |
| inkonsistente Betragstripel | 1 |
| Steuer ohne Brutto | 1 |
| ungueltige Datumswerte | 1 |
| nach Formatpruefung redigierte Versionswerte | 1 |
| private Inhalte, Identifier oder Pfade in der Ausgabe | 0 |
| externe/PDF-/Registerzugriffe | 0 |

Der bereits read-only erhobene operative M10-Ausgangswert von 19 Reviewzeilen
ausserhalb `Pruefen` bleibt unveraendert dokumentiert. Er wird weder in den
synthetischen Korpus kopiert noch als produktiver Lauf wiederholt. Der neue
Zaehler `review_outside_review_subfolder` macht denselben Zustand bei einem
spaeter autorisierten Status/Audit-Lauf direkt sichtbar, ohne Einzelpfade
auszugeben oder eine Verschiebung anzubieten.

Die gemeinsame Suite sammelt 688 pytest-Items und meldet 756 JUnit-Faelle
einschliesslich 68 Subtests. Die branch-einbezogene Gesamt-Coverage betraegt
64,10 Prozent, die reine Branch-Coverage 49,91 Prozent. Das neue Modul
`mail_agent/invoice_backlog_audit.py` erreicht 94,22 Prozent. Diese Werte stammen
aus dem erfolgreichen vollstaendigen lokalen Testpfad nach der abschliessenden
deterministischen Manifestregenerierung.
