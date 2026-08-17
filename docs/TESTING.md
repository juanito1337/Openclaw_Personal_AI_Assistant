# Tests, Qualitaetsbaseline und Container-Runtime

Stand: 2026-08-17, fortgeschrieben bis zu den M10-Rolloutkorrekturen fuer Sync,
Monitor und Supervisor. Sie startet keine produktiven Dienste und verwendet weder
`/srv/openclaw` noch produktive Zugangsdaten.

## Einheitlicher lokaler und CI-Prueflauf

Unter Linux x86_64 oder arm64 wird die exakt gepinnte Umgebung einmalig eingerichtet:

```bash
./scripts/bootstrap-dev.sh
```

Das Skript installiert die Python-Abhaengigkeiten aus `requirements-dev.lock` in
`.venv` und laedt ShellCheck 0.11.0 sowie Hadolint 2.15.1 nach `.tools/bin`. Beide
Binaerdateien werden vor der Installation gegen die im Skript fest eingetragenen
offiziellen SHA-256-Werte geprueft. Danach ist der verbindliche Gesamtcheck:

```bash
./scripts/check-repo.sh
```

GitHub Actions ruft genau diese beiden Befehle auf Python 3.11, 3.12 und 3.13 auf.
`check-repo.sh` prueft in dieser Reihenfolge:

1. exakte Quellmenge und SHA-256-Werte in `SOURCE_MANIFEST.sha256`,
2. exaktes Komponenten-Inventar, SHA-verifiziertes Legacy-systemd-Paket und den
   M7-Supply-Chain-Lock,
3. Repository-Hygiene, Bash-Syntax und vorhandene Legacy-systemd-Units,
4. ShellCheck und Hadolint,
5. beide Compose-Varianten mit `docker/deployment.env.example`,
6. `git diff --check`, den Architekturdokumentationsvertrag, die generierte
   M5-Befehlsreferenz und den generierten M8-Skill-Toolvertrag,
7. Ruff und mypy,
8. Python-Kompilierung,
9. die vollstaendige pytest-Suite mit Branch-Coverage,
10. die maschinenlesbare Baseline unter `build/m0-baseline.json`,
11. den M8-Backup-/Restore-Drill in temporaeren Fixture-Roots unter
    `build/m8-recovery.json`.

CI bewahrt JUnit, Coverage-JSON und diese Baseline fuer jede Python-Version als
Build-Artefakt auf. Damit bleiben spaetere Aenderungen sichtbar, ohne in M0 bereits
willkuerliche Coverage- oder Laufzeitgrenzen festzulegen.

Der alleinige Testbefehl ist `./scripts/run-tests.sh`. pytest sammelt damit sowohl
die unittest-Klassen als auch freie pytest-Funktionen. `tests/test-baseline.json`
fordert mindestens 715 Tests, darunter mindestens 650 unittest-kompatible Tests
(die bisherigen 349 sowie M0-M10.8- und Rollout-Regressionstests),
und genau die zuvor ausgelassenen mindestens 13 freien Tests aus
`tests/test_invoice_ocr_register.py`. Eine kleinere Teilcollection bricht bereits
nach dem Sammeln mit einem Fehler ab. Neue Tests duerfen die Zahl erhoehen; die
Baseline wird erst nach einem vollstaendigen gruenen Lauf bewusst angehoben.

## M0-Ausgangswerte und aktueller M10-Rollout-Teststand

Gemessen auf Linux x86_64 mit Python 3.12.3. Die Werte sind Beobachtungen und noch
keine willkuerlichen Mindestquoten. `scripts/quality-baseline.py` erzeugt sie nach
jedem Lauf reproduzierbar aus `build/pytest.xml`, `build/coverage.json` und dem AST
der aktuellen Python-Dateien.

| Messwert | Dokumentierter Wert |
| --- | ---: |
| Tests bei Abschluss M0 gesammelt/ausgefuehrt | 379 / 379 |
| Tests nach M1 gesammelt/ausgefuehrt | 385 / 385 |
| Tests nach M2 gesammelt/ausgefuehrt | 395 / 395 |
| Tests nach M3 gesammelt/ausgefuehrt | 402 / 402 (zusaetzlich 2 pytest-Subtests) |
| Tests nach M4 gesammelt/ausgefuehrt | 413 / 413 (zusaetzlich 2 pytest-Subtests) |
| Tests nach M5 gesammelt/ausgefuehrt | 420 / 420 (zusaetzlich 2 pytest-Subtests) |
| Tests nach M6 gesammelt/ausgefuehrt | 428 / 428 (zusaetzlich 13 Subtest-Faelle im Wheel-Lauf) |
| Tests nach M7 gesammelt/ausgefuehrt | 448 / 448 (461 JUnit-Faelle inklusive 13 Subtests) |
| Tests nach M8 gesammelt/ausgefuehrt | 455 / 455 (468 JUnit-Faelle inklusive 13 Subtests) |
| Tests nach M8-CI-Diagnosehaertung gesammelt/ausgefuehrt | 458 / 458 (JUnit-Zaehler inklusive Subtest-Ereignissen siehe `build/m0-baseline.json`) |
| Tests nach privatrepository-tauglicher Registry-Attestierung | 459 / 459 (JUnit-Zaehler inklusive Subtest-Ereignissen siehe `build/m0-baseline.json`) |
| Tests nach der immutable Plugin-/Gatewaykorrektur | 472 / 472 (494 JUnit-Faelle inklusive 22 Subtests) |
| Tests nach nativer Nextcloud- und aktiver Layout-3-Korrektur | 473 / 473 (JUnit-Zaehler inklusive Subtest-Ereignissen siehe `build/m0-baseline.json`) |
| Tests nach M9 gesammelt/ausgefuehrt | 610 / 610 (643 JUnit-Faelle inklusive 33 Subtests) |
| Tests nach M10.0 gesammelt/ausgefuehrt | 616 / 616 (649 JUnit-Faelle inklusive 33 Subtests) |
| Tests nach M10.1 gesammelt/ausgefuehrt | 626 / 626 (664 JUnit-Faelle inklusive 38 Subtests) |
| Tests nach M10.2 gesammelt/ausgefuehrt | 637 / 637 (678 JUnit-Faelle inklusive 41 Subtests) |
| Tests nach M10.3 gesammelt/ausgefuehrt | 648 / 648 (704 JUnit-Faelle inklusive 56 Subtests) |
| Tests nach M10.4 gesammelt/ausgefuehrt | 659 / 659 (715 JUnit-Faelle inklusive 56 Subtests) |
| Tests nach M10.5 gesammelt/ausgefuehrt | 671 / 671 (735 JUnit-Faelle inklusive 64 Subtests) |
| Tests nach M10.6 gesammelt/ausgefuehrt | 680 / 680 (748 JUnit-Faelle inklusive 68 Subtests) |
| Tests nach M10.7 gesammelt/ausgefuehrt | 688 / 688 (756 JUnit-Faelle inklusive 68 Subtests) |
| Tests nach M10.8 gesammelt/ausgefuehrt | 694 / 694 (774 JUnit-Faelle inklusive 80 Subtests) |
| Tests nach der M10-Rollout-Syncrollenkorrektur gesammelt/ausgefuehrt | 701 / 701 (781 JUnit-Faelle inklusive 80 Subtests) |
| Tests nach der M10-Rollout-Monitor-/Supervisorkorrektur gesammelt/ausgefuehrt | 707 / 707 (787 JUnit-Faelle inklusive 80 Subtests) |
| Tests nach der Gateway-Relay-/Mail-Writer-Korrektur gesammelt/ausgefuehrt | 715 / 715 (795 JUnit-Faelle inklusive 80 Subtests) |
| davon bestehende unittest-Tests | 349 |
| davon zuvor ausgelassene Rechnungs-pytest-Tests | 13 |
| neue M0-Regressionstests | 17 |
| neue M1-Dokumentationstests | 6 |
| neue M2-Runtime- und Migrationstests | 10 |
| neue M3-State-/Nebenlaeufigkeitstests | 7 |
| neue M4-Haertungsregressionstests | 11 |
| neue M5-Tool-/Schichtvertragsregressionstests | 7 |
| neue M6-Legacy-/Upgrade-Regressionsitems | 14 |
| neue M7-Lieferketten-Regressionsitems | 21 |
| neue M8-Recovery-/Skill-Regressionsitems | 7 |
| neue M10.0-Rechnungsqualitaets-Regressionsitems | 6 |
| neue M10.1-Wirkungsvertrags-Regressionsitems | 10 |
| neue M10.2-Nummern-/Datums-Regressionsitems | 11 |
| neue M10.3-Betrags-/Plausibilitaets-Regressionsitems | 11 |
| neue M10.4-OCR-/Fusions-Regressionsitems | 11 |
| neue M10.5-Reprocessing-Vorschau-Regressionsitems | 12 |
| neue M10.6-Einzeluebernahme-Regressionsitems | 9 |
| neue M10.7-Backlog-/Skill-Regressionsitems | 8 |
| neue M10.8-Abnahme-/Artefakt-/Rollout-Regressionsitems | 6 |
| Gesamt-Coverage inklusive Branches (M7) | 59,18 % |
| reine Branch-Coverage (M7) | 43,83 % |
| Gesamt-Coverage inklusive Branches (M8) | 59,18 % |
| reine Branch-Coverage (M8) | 43,83 % |
| Gesamt-Coverage nach der Plugin-/Gatewaykorrektur | 59,34 % |
| reine Branch-Coverage nach der Plugin-/Gatewaykorrektur | 44,01 % |
| Gesamt-Coverage inklusive Branches nach M9 | 62,03 % |
| reine Branch-Coverage nach M9 | 47,12 % |
| Gesamt-Coverage inklusive Branches nach M10.0 | 62,21 % |
| reine Branch-Coverage nach M10.0 | 47,37 % |
| Gesamt-Coverage inklusive Branches nach M10.1 | 62,69 % |
| reine Branch-Coverage nach M10.1 | 47,91 % |
| Gesamt-Coverage inklusive Branches nach M10.2 | 62,92 % |
| reine Branch-Coverage nach M10.2 | 48,32 % |
| Gesamt-Coverage inklusive Branches nach M10.3 | 63,12 % |
| reine Branch-Coverage nach M10.3 | 48,70 % |
| Gesamt-Coverage inklusive Branches nach M10.4 | 63,45 % |
| Gesamt-Coverage inklusive Branches nach M10.5 | 63,70 % |
| reine Branch-Coverage nach M10.5 | 49,35 % |
| Gesamt-Coverage inklusive Branches nach M10.6 | 63,85 % |
| reine Branch-Coverage nach M10.6 | 49,59 % |
| Gesamt-Coverage inklusive Branches nach M10.7 | 64,10 % |
| reine Branch-Coverage nach M10.7 | 49,91 % |
| Gesamt-Coverage inklusive Branches nach M10.8 | 64,09 % |
| reine Branch-Coverage nach M10.8 | 49,89 % |
| Gesamt-Coverage nach der M10-Rollout-Syncrollenkorrektur | 64,24 % |
| reine Branch-Coverage nach der M10-Rollout-Syncrollenkorrektur | 49,99 % |
| Gesamt-Coverage nach der M10-Rollout-Monitor-/Supervisorkorrektur | 64,36 % |
| reine Branch-Coverage nach der M10-Rollout-Monitor-/Supervisorkorrektur | 50,19 % |
| Gesamt-Coverage nach der Gateway-Relay-/Mail-Writer-Korrektur | 64,23 % |
| reine Branch-Coverage nach der Gateway-Relay-/Mail-Writer-Korrektur | 50,05 % |
| Laufzeit des finalen lokalen M6-Testlaufs | 62,94 s |
| Laufzeit des finalen lokalen M7-Gesamtchecks | 63,04 s |
| Laufzeit des finalen lokalen M8-Testlaufs | 56,65 s |
| Laufzeit des finalen lokalen M10.0-Testlaufs | 107,45 s |
| Laufzeit des finalen lokalen M10.1-Testlaufs | 110,01 s |
| Laufzeit des finalen lokalen M10.5-Testlaufs | 70,72 s |
| Laufzeit des finalen lokalen M10.6-Testlaufs | 118,09 s |
| Laufzeit des ersten erfolgreichen lokalen M10.7-Gesamtlaufs | 118,48 s |
| Laufzeit der erfolgreichen M10.7-Kontrollwiederholung | 115,65 s |
| Laufzeit des ersten erfolgreichen lokalen M10.8-Gesamtchecks | 112,13 s |
| Laufzeit der M10-Rollout-Syncrollenkorrektur | 112,42 s |
| Laufzeit der M10-Rollout-Monitor-/Supervisorkorrektur | 117,69 s |
| Laufzeit in der frischen M7-Wheel-Testumgebung | 55,56 s |
| Wheelgroesse nach der Plugin-/Gatewaykorrektur | 397.870 Bytes |
| M7-Wheel-Buildzeit | 4,582 s |
| M10.8-Wheelgroesse | 471.110 Bytes |
| M10.8-Wheel-Buildzeit | 3,488 s |
| M10.8-Tests in frischer Wheel-Umgebung | 694 plus 80 Subtests in 85,19 s |
| Wheelgroesse nach der M10-Rollout-Syncrollenkorrektur | 473.145 Bytes |
| Wheel-Buildzeit nach der M10-Rollout-Syncrollenkorrektur | 3,577 s |
| Wheel-Tests nach der M10-Rollout-Syncrollenkorrektur | 701 plus 80 Subtests in 92,61 s |
| Wheelgroesse nach der M10-Rollout-Monitor-/Supervisorkorrektur | 473.999 Bytes |
| Wheel-Buildzeit nach der M10-Rollout-Monitor-/Supervisorkorrektur | 4,066 s |
| Wheel-Tests nach der M10-Rollout-Monitor-/Supervisorkorrektur | 707 plus 80 Subtests in 93,64 s |
| Wheelgroesse nach der Gateway-Relay-/Mail-Writer-Korrektur | 481.576 Bytes |
| Wheel-Buildzeit nach der Gateway-Relay-/Mail-Writer-Korrektur | 1,821 s |
| Wheel-Tests nach der Gateway-Relay-/Mail-Writer-Korrektur | 715 plus 80 Subtests in 63,27 s |
| Container-Imagegroesse des M6-Testimages | 425.555.866 Bytes |
| Runtime-Imagegroesse mit gepinnten Brave-/Signal-Plugins | 376.600.036 Bytes |
| Runtime-Imagegroesse nach der M10-Rollout-Monitor-/Supervisorkorrektur | 376.793.375 Bytes |
| Runtime-Imagegroesse nach der Gateway-Relay-/Mail-Writer-Korrektur | 376.805.039 Bytes |
| sauberer Container-Erstbuild | ca. 411,92 s |
| M6-Cache-Rebuild | 11 s |
| Container-CLI-Kaltstart | 1.081 ms |
| bekannte mypy-Altbefunde | 109 exakt baselinierte Befunde in 21 Dateien; 15 behoben |
| bekannte Ruff-Altbefunde | 504 exakt baselinierte Befunde; 277 behoben |

## M10.0-Rechnungsqualitaet

M10.0 fuegt keine produktive Extraktions- oder Reprocessing-Logik hinzu. Ein
vollstaendig synthetischer Korpus und ein deterministischer Verifier frieren den
aktuellen Zustand ein. Der historische M10.0-Bericht mit dem damals bekannten
False-confirmed-Fall bleibt als `m100_extractor_baseline.json` erhalten; der
Standard-Verifier prueft den jeweils abgenommenen aktuellen Extraktorstand:

```bash
.venv/bin/python scripts/evaluate_invoice_quality.py --verify
.venv/bin/python -m pytest -q tests/test_invoice_quality_m10.py
```

Der Test belegt ausserdem, dass 48 bereits markierte `review`-Zeilen nicht in die
aktuelle Legacy-Backfill-Auswahl geraten, waehrend zehn leere
`extraction_status`-Zeilen getrennt Kandidaten bleiben. Operative Aggregate,
Metrikdefinitionen und Datenschutzgrenzen stehen in
[`INVOICE_QUALITY_BASELINE_M10.md`](INVOICE_QUALITY_BASELINE_M10.md). Der
Repositorycheck greift dieselbe Baseline ueber die Tests auf und benoetigt dafuer
weder Produktivdaten noch Nextcloud oder Docker.

## M10.1-Rechnungs-Wirkungsvertrag

M10.1 prueft den Unterschied zwischen schreibfreier Vorschau, lokaler
SQLite-Aenderung und externer Registeraktualisierung mit temporaeren Datenbanken
und synthetischen WebDAV-Antworten:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_effect_contract_m101.py
```

Die zehn Regressionstests belegen, dass Export- und Backfill-Vorschau SQLite und
Nextcloud unveraendert lassen, alte Direktaufrufe ohne erforderliches `--yes`
fail-closed enden und alle extern wirksamen Varianten in statischem Katalog,
Live-Projektion und Capability-Ausgabe als `write` erscheinen. Die
Registergrenze prueft einen erfolgreichen `If-Match`-PUT sowie negative Pfade fuer
HTTP 412, falschen SHA-256, falsches CSV-Schema und allgemeinen Remote-Fehler.
Keine Fixture verbindet sich mit einem produktiven Nextcloud-Server.

## M10.2-Belegte Rechnungsnummern und Datumsrollen

M10.2 prueft den typisierten Kandidatenvertrag fuer Rechnungsnummer und
Rechnungsdatum ausschliesslich mit erfundenen Dokumenttexten und Dateinamen:

```bash
.venv/bin/python scripts/evaluate_invoice_quality.py \
  --corpus tests/fixtures/invoices/m102_number_date_corpus.json \
  --baseline tests/fixtures/invoices/m102_number_date_baseline.json \
  --verify
.venv/bin/python -m pytest -q tests/test_invoice_number_date_m102.py
```

Die elf Tests enthalten positive und negative Golden-Faelle fuer deutsche und
englische Anker, Unicode, Bindestrich, Schraegstrich, OCR-Abstaende, begrenzten
Folgezeilenkontext, Dateiname als reine Stuetzung sowie Nummern- und
Datumskonflikte. Sie pruefen die Kandidatenobjekte und das resultierende Verhalten,
nicht Quelltextfragmente. Der direkte Vorher-/Nachher-Vergleich steht in
[`INVOICE_QUALITY_BASELINE_M10.md`](INVOICE_QUALITY_BASELINE_M10.md). Der alte
M10.0-Bericht bleibt weiterhin separat reproduzierbar. Weder der Test noch der
Evaluator oeffnet SQLite, Nextcloud, `/srv/openclaw` oder produktive PDFs.

## M10.3-Typisierte Betraege und rechnerische Plausibilitaet

M10.3 prueft Betragsrollen, Normalisierung und fail-closed Plausibilitaet mit 15
vollstaendig erfundenen deutschen und englischen Dokumenttexten:

```bash
.venv/bin/python scripts/evaluate_invoice_quality.py \
  --corpus tests/fixtures/invoices/m103_amount_corpus.json \
  --baseline tests/fixtures/invoices/m103_amount_baseline.json \
  --verify
.venv/bin/python -m pytest -q tests/test_invoice_amounts_m103.py
```

Die elf Regressionstests mit 15 Subtests pruefen Steuersaetze als ausgeschlossene
Prozentwerte, beschriftete Brutto-/Netto-/Steuer- und Zahlbetraege, mehrere
Summen ohne Groesstwertheuristik, die feste Zwei-Cent-Toleranz, Abschlag, Rabatt,
Einzelpreis, positive und negative Gutschriften, EUR/USD/GBP/CHF sowie gemischte
Waehrungen. Ein eigener Negativtest belegt, dass Mailbetreff, Dateiname und Ollama
keinen Betrag liefern. Kandidatenrollen, ISO-Waehrung, Ausschlussgrund und
typisierte `amount:*`-Reviewgruende werden als Verhalten geprueft.

Der direkte Vorher-/Nachher-Vergleich fuer Praezision, Abdeckung, Rechenfehler und
False-confirmed steht in
[`INVOICE_QUALITY_BASELINE_M10.md`](INVOICE_QUALITY_BASELINE_M10.md). Weder
Evaluator noch Tests oeffnen SQLite, Nextcloud, `/srv/openclaw` oder produktive
PDFs; sie fuehren kein Backfill oder Reprocessing aus.

## M10.4-Begrenzte OCR und Feldfusion

M10.4 prueft lokale OCR-Ausloeser, Seiten- und Ressourcenbudgets sowie die
versionierte Feldfusion auf vollstaendig sanitisierten Text-, Bild-, Misch-,
Mehrseiten- und Fehlerfaellen:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_ocr_m104.py
.venv/bin/python scripts/benchmark-invoice-ocr-m104.py
```

Der Benchmark erzeugt sein Test-PDF selbst, verarbeitet keine produktiven Daten
und meldet nur Werkzeugidentitaeten, Budgets, Laufzeit und Ressourcenzaehler. Die
gemessenen Ausgangswerte stehen in
[`INVOICE_QUALITY_BASELINE_M10.md`](INVOICE_QUALITY_BASELINE_M10.md).

## M10.5-Read-only Reprocessing-Vorschau

M10.5 testet Status- und Jahressemantik, Alt-/Neu-Projektion,
Qualitaetsklassifikation, Digestbindung, Datenschutz und Seiteneffektfreiheit mit
einer ausschliesslich temporaeren SQLite und erfundenen PDF-Bytes:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_reprocess_preview_m105.py
```

Die Regressionen waehlen `review` und `unclassified` getrennt, verwerfen
manipulierte Statuswerte und schliessen `confirmed` sowie `confirmed-manual` auf
zwei Ebenen aus. Ein Golden-Fall haelt Quelljahr 2024, Pfadjahr 2025,
Empfangsjahr 2026 und erkanntes Rechnungsjahr 2027 gleichzeitig auseinander.
Weitere Tests vergleichen die SQLite-Datei, den synthetischen PDF-Bestand, einen
Register-ETag und einen nicht vorhandenen Auditpfad vor und nach der Vorschau.

Der Digest-Test variiert PDF-SHA-256, aktuellen Datensatz, Extraktorversion und
Neuvorschlag einzeln. Die Datenschutzpruefung gibt echte Feldwerte und begrenzte
Evidenztypen aus, verwirft aber absichtlich eingebettete PDF-/OCR-Zeilen und freie
Issue-Texte. Weder diese Tests noch die M10.5-Implementierung stellen einen
Apply-Pfad bereit. Ein manueller Lauf gegen eine konfigurierte Instanz ist nur
read-only, liest aber die ausgewaehlten Nextcloud-PDFs und fuehrt den lokalen
ClamAV- und gegebenenfalls OCR-Prozess aus.

## M10.6-Auditierbare Einzeluebernahme

M10.6 prueft den schreibenden Vertrag ausschliesslich mit temporaerer SQLite,
erfundenen PDF-Bytes und simulierten Registerantworten:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_reprocess_apply_m106.py
```

Die neun Tests decken fehlendes `--yes`, falschen Beleg-Hash, falschen Preview-
Digest, PDF-/Datensatz-/Statusdrift, manuell geschuetzte Werte, nicht verbesserte
Vorschlaege und unplausible Arithmetik ohne Schreibwirkung ab. Der positive Fall
weist genau eine geaenderte Invoice-Zeile, eine inhaltsfreie Auditzeile und den
ETag-/SHA-/Schemavertrag fuer altes und neues Registerjahr nach. Die Migration
von Schema 3 auf 4 wird zweimal ausgefuehrt und mit erhaltener Invoice-Zeile sowie
`PRAGMA quick_check` geprueft.

Ein kontrollierter Paralleltest laesst zwei Aufrufer denselben Hash/Digest
gleichzeitig extrahieren: genau einer fuehrt den lokalen Apply und Registerclaim
aus, der zweite sieht die laufende Operation. Weitere Tests simulieren einen
Remote-Konflikt nach lokalem Commit, pruefen den sichtbaren Fehler und setzen
denselben Auditvorgang anschliessend idempotent fort. Ein abgeschlossener
Wiederholungsaufruf validiert die Register erneut. Synthetische PDF-Evidenz und
Archivpfad duerfen in keiner Auditspalte erscheinen; PDF-Bytes und Pfad bleiben
unveraendert. Es wurde kein produktiver Apply ausgefuehrt.

## M10.7-Aggregierter Backlog-Audit und Agentenablauf

M10.7 prueft den neuen registrierten Read-Befehl ausschliesslich mit einer
temporaeren SQLite und erfundenen Datensaetzen:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_backlog_audit_m107.py
```

Die acht Tests vergleichen Statusverteilung, getrennte Legacy-/Review-/
Bestaetigt-/manuelle Kohorten, Pflichtfeldluecken, Betrags-/Datumsplausibilitaet,
typisierte Reviewgruende, Quelljahre und Pfadabweichungen exakt. Ein absichtlich
wie privater Inhalt aussehender Wert wird weder als Extraktorversion noch als
Reviewgrund, Pfad, Feldwert oder Identifier ausgegeben. SQLite-Hauptdatei und
Verzeichnisbestand bleiben bei einer geschlossenen Fixture bytegleich; PDFs,
Nextcloud, Register und Auditwriter werden nicht geoeffnet.

CLI- und Tooltests belegen den realen Befehl `invoices audit`, den Modus `read`,
fehlende externe Wirkung und die Abwesenheit eines Invoice-Move-Werkzeugs. Der
Skilltest prueft die Reihenfolge Status -> Audit -> Preview -> dargestellte
Einzelaenderung -> separater Nutzerauftrag fuer Apply; der Verhaltenspfad lehnt
Apply ohne `--yes` weiterhin vor jeder Daten- oder Remoteaktion ab.

Der operative Ausgangswert von 19 Reviewzeilen ausserhalb `Pruefen` bleibt ein
bereits dokumentiertes read-only Aggregat und wird nicht als private Fixture
kopiert. Ein spaeterer produktiver Triage-Lauf benoetigt zuerst Status/Audit und
vor jedem einzelnen Apply das verifizierte Backup samt separater Freigabe. Ein
Remote-Registerkonflikt ist kein lokaler Rollback; ein Image-Rollback stellt
Nextcloud-Aenderungen nicht wieder her.

## M10.8-Gesamtabnahme und Artefaktgrenze

Die Abschlussregression fuehrt die drei versionierten, sanitisierten
Feldqualitaetsvergleiche, den endgueltigen Toolwirkungsvertrag, die
Quellbaumhygiene, den PDF-Guard fuer Wheel/Image, die CI-Rollenpfade und den
geordneten Rolloutvertrag zusammen:

```bash
.venv/bin/python -m pytest -q tests/test_invoice_acceptance_m108.py
./scripts/check-wheel.sh
sg docker -c './docker/scripts/build-local.sh \
  openclaw-agent:m10-acceptance \
  openclaw-agent:m10-acceptance-proxy \
  openclaw-agent:m10-acceptance-maintenance'
sg docker -c './scripts/check-role-images.sh \
  openclaw-agent:m10-acceptance \
  openclaw-agent:m10-acceptance-proxy \
  openclaw-agent:m10-acceptance-maintenance \
  "$(git rev-parse HEAD)"'
sg docker -c './scripts/check-m8-integration.sh'
```

Die sechs Tests vergleichen echte Evaluatorausgaben mit allen drei Baselines,
pruefen `read`/`write`/Approval direkt im typisierten Toolkatalog und rufen den
Artefaktpruefer mit einer PDF-Negativfixture auf. `git ls-files` muss ohne PDF,
Maildatei, Datenbank, Log oder aktive Laufzeitkonfiguration bleiben. Das ehemals
getrackte synthetische M10-PDF wird nun deterministisch nur in einem temporaeren
Verzeichnis erzeugt. Der CI-Vertrag muss Wheel, alle drei Rollenimages,
rollenbezogene Rootfs-/Supply-Chain-Pruefungen und das interne Fehlernetz
enthalten.

Der [separate M10-Rolloutvertrag](INVOICE_M10_ROLLOUT.md) ist Bestandteil der
Dokumentationsabnahme, aber keine Deployment- oder Apply-Freigabe. Lokale
Containerchecks verwenden ausschliesslich neue Testimages, kurzlebige Container
und eindeutig benannte interne Netze; sie stoppen oder veraendern keinen
laufenden produktiven Compose-Stack.

Kritische Sicherheitsmodule im aktuellen M8-Lauf:

| Modul | Coverage |
| --- | ---: |
| `personal_assistant/policy.py` | 87,16 % |
| `personal_assistant/job_control.py` | 83,65 % |
| `personal_assistant/source_manifest.py` | 86,08 % |
| `personal_assistant/release.py` | 77,59 % |
| `personal_assistant/antivirus.py` | 63,54 % |
| `personal_assistant/clamav_health.py` | 75,00 % |
| `personal_assistant/container_health.py` | 63,77 % |
| `personal_assistant/container_entrypoint.py` | 37,43 % |
| `personal_assistant/immutable_plugins.py` | 75,34 % |
| `mail_agent/assistant_bridge.py` | 35,00 % |
| `personal_assistant/actions.py` | 30,63 % |

Die groessten Module waren zu Beginn `personal_assistant/service.py` (2358 Zeilen),
`personal_assistant/portfolio.py` (2160), `personal_assistant/cli.py` (1692),
`mail_agent/app.py` (1406) und `mail_agent/classifier.py` (1351). Die groessten
Funktionen sind `build_tool_registry` (959 Zeilen), `personal_assistant.cli.main`
(764), `personal_assistant.cli.parser` (593), `extended_help` (429) und
`load_tool_settings` (294). Diese Werte begruenden spaetere Milestones, loesen in
M0 aber noch keine Refaktorierung aus.

M5 ersetzt `build_tool_registry` durch eine kleine Projektion, zerlegt Parser und
Handler des CLI nach Domaenen und trennt Workspace-, Mail-, Portfolio-, Bestell-
und Sicherheitsdienste als Anwendungs-Mixins. Die Portfolio-Importparser liegen in
einem eigenen Modul. Die 124 bei M5 charakterisierten Werkzeuge, zwei spaeter
registrierte read-only Mappingvorschlaege und zehn Research-/Philosophiewerkzeuge
ergaben zunaechst 136 Toolprojektionen in
`tests/golden/m5-tool-contract.json`; einschliesslich der spaeteren M9-/M10-
Werkzeuge sind es aktuell 147 Toolprojektionen. Die stabile Top-Level-Hilfe steht in
`tests/golden/m5-cli-help.txt`. Aktuelle Modul- und Funktionsgroessen werden
weiterhin ausschliesslich durch `scripts/quality-baseline.py` gemessen.

M6 entfernt sieben nicht produktiv aufgerufene Produktdateien und isoliert 14
Rollbackdateien in einem eigenen Manifest. Das Inventar unter
`docs/architecture/component-inventory.json` umfasst 311 aktive, historische,
Kompatibilitaets- oder Migrationskomponenten. Es speichert keine produktive
Telemetrie und keinen Nutzerinhalt.

Die Containerwerte wurden lokal mit einem separaten Testimage ohne produktive
Mounts oder Netzwerke gemessen. Der Erstbuild umfasst das Herunterladen der
Basisimages und die vollstaendige Himalaya-Kompilierung; ein Cache-Rebuild ist
daher nicht mit diesem Ausgangswert vergleichbar. Der `container`-Job in
`.github/workflows/ci.yml` erfasst dieselben Werte auf dem CI-Docker-Runner und
schreibt sie in die Job Summary. Dabei wird kein produktiver Stack gestartet;
gemessen wird nur `personal-assistant --help` mit ueberschriebenem Entry Point.

## Ruff-/mypy-Ausgangsbaseline und enge Ausnahmen

Ruff prueft `E`, `F`, `I`, `B`, `UP` und `SIM` ohne global ignorierten Fehlercode.
Die 781 historischen `E501`-Zeilen sind in `tests/ruff-baseline.json` einzeln an
Datei, Code, Meldung und SHA-256 der konkreten Quellzeile gebunden. Damit kann keine
neue oder veraenderte ueberlange Zeile unter einer pauschalen Ausnahme verschwinden.
Nur `personal_assistant/tool_catalog/*.py` ignoriert eng `E501`: diese Dateien
enthalten exakte, generierte Beschreibungs- und Kommandozeichenketten des Golden
Contracts. Alle anderen Ruff-Regeln gelten dort weiter; normale Pythonmodule
erhalten diese Ausnahme nicht.
Die Baseline wird nur nach einem bewussten, vollstaendig gruenen Lauf neu erzeugt:

```bash
.venv/bin/python scripts/check-ruff.py \
  --baseline tests/ruff-baseline.json \
  --write-baseline mail_agent personal_assistant docker tests scripts
```

mypy prueft alle Module unter `mail_agent`, `personal_assistant` und `docker`.
Die historisch gewachsene Codebasis ist noch nicht vollstaendig typrein. Statt
`ignore_errors`, globaler Fehlercode-Unterdrueckung oder hunderten Inline-Ignores
enthaelt `tests/mypy-baseline.json` jeden akzeptierten Altbefund einzeln als
Kombination aus Datei, Fehlercode und Meldung. `scripts/check-mypy.py` laesst nur
diese exakten 124 Fingerprints zu; jeder neue oder zusaetzliche Befund ist rot.
Behobene Altbefunde sind erlaubt und muessen anschliessend aus der Baseline entfernt
werden. Eine Aktualisierung erfolgt ausschliesslich bewusst mit:

```bash
.venv/bin/python scripts/check-mypy.py \
  --baseline tests/mypy-baseline.json \
  --write-baseline mail_agent personal_assistant docker
```

Hadolint ignoriert keinen Befund. Direkte Alpine-Pakete und nicht-root-Benutzer sind
im M7-Dockerfile explizit und reproduzierbar festgelegt.
ShellCheck-Ausnahmen stehen direkt an den dynamischen `source`- beziehungsweise
absichtlich im Container ausgewerteten Shellzeilen.

## Quellmanifest

Das Manifest darf nur durch den Generator aktualisiert werden:

```bash
./scripts/source-manifest.py generate
./scripts/source-manifest.py verify
./scripts/assistant.sh version --verify
```

Die Quelle ist `git ls-files --cached --others --exclude-standard`; dadurch werden
neue, noch nicht committete Dateien im Entwicklungs-Worktree ebenfalls erfasst.
`SOURCE_MANIFEST.sha256` selbst ist immer ausgeschlossen. Fehlende Dateien,
fehlende oder zusaetzliche Eintraege, geaenderte Inhalte, doppelte Eintraege und
ungueltige Pfade sind Fehler. In einem exportierten Baum ohne `.git` wird die
tatsaechlich vorhandene Dateimenge gegen das Manifest verglichen; dadurch sind dort
auch unaufgefuehrte zusaetzliche Dateien ein Fehler. Der separate Artefaktcheck
kontrolliert zusaetzlich verbotene Laufzeit- und Secret-Dateien.

## Architektur- und Dokumentationstests

`scripts/check-docs.py` ist Bestandteil von `check-repo.sh` und damit von lokaler
Pruefung und CI. Der Check folgt realen relativen Markdown-Links, gleicht jedes
aktive Dokument unter `docs/architecture/` exakt mit
`docs/architecture/owners.json` ab, prueft die aktuelle Releaseidentitaet sowie die
Rollen-/SQLite-Matrix und stellt sicher, dass Zielbild, Betriebsmodell,
Testanleitung und Erweiterungsregeln vom README in hoechstens zwei Links erreichbar
sind. Historische Dokumente unter `docs/archive/` sind bewusst nicht normativ und
werden nicht als aktueller Architekturvertrag ausgewertet.

## M5-Tool-, CLI- und Schichtvertrag

Die statische, konfigurationsfreie Sicht wird so geprueft:

```bash
./scripts/assistant.sh tools list --catalog
./scripts/assistant.sh capabilities --schema
.venv/bin/python scripts/generate-command-reference.py --check
.venv/bin/python -m pytest -q tests/test_m5_tool_contract.py
```

Der statische Katalog listet alle bekannten Werkzeuge und erteilt keine Rechte. Die
Live-Befehle `tools list` und `capabilities` laden dagegen Konfiguration und
Ressourcen; die Capability-Antwort traegt `view=live-capabilities` und
`configured=true`. Der M5-Test prueft alle Tool-IDs, Modi, externe Wirkung,
Approvalklassen, Schemata, Fehlercodes, importierbare Domaenenhandler sowie reale
Doku- und Testanker. Er vergleicht die voll freigeschaltete Live-Projektion exakt
mit dem Golden Contract, fuehrt Policy-Negativtests aus und verwirft Importzyklen
oder Core-Imports konkreter Mailinfrastruktur.

## M6-Inventar, Legacy-Paket und Vorher-/Nachhervergleich

Die M6-Vertraege werden lokal und in CI durch dieselben Befehle geprueft:

```bash
.venv/bin/python scripts/generate-component-inventory.py verify
.venv/bin/python scripts/verify-legacy-package.py verify
.venv/bin/python -m pytest -q tests/test_m6_legacy_cleanup.py
.venv/bin/python scripts/benchmark-m6.py --samples 12 \
  --image openclaw-agent:m6-local --output build/m6-after.json
```

Das Inventar deckt alle im Quellmanifest enthaltenen Pythonmodule,
Shellentrypoints, Skills, systemd-Units, Migrationspfade und Markdown-Dokumente ab.
Jeder Datensatz enthaelt Owner, Klassifikation, produktive Referenzen,
Test-/Dokumentationsevidenz, Coverage-Snapshot, letztes bekanntes Git-Datum und
Rollbackrelevanz. `last_git_change=null` bedeutet, dass eine neue Datei im
ungecommitten M6-Worktree noch keinen Git-Zeitstempel besitzt. Produktive
Runtime-Telemetrie und Nutzerinhalte werden aus Datenschutzgruenden nicht in dieses
Repository-Inventar uebernommen.

Der Vergleich wurde auf demselben Linux-x86_64-Host und mit Python 3.12.3 gemessen.
Er ist eine Beobachtung, keine neue Qualitaetsschwelle:

| Messwert | vor M6 (M5) | nach M6 | Aenderung |
| --- | ---: | ---: | ---: |
| pytest-Items | 420 | 428 | +8 |
| Python-Dateien im Quellmanifest | 199 | 197 | -2 |
| Python-Bytes im Quellmanifest | 2.183.318 | 2.193.694 | +10.376 |
| Shell-Dateien im Quellmanifest | 30 | 29 | -1 |
| Shell-Bytes im Quellmanifest | 79.736 | 80.607 | +871 |
| Wheel-Bytes | 402.835 | 395.102 | -7.733 (-1,92 %) |
| Image-Bytes | 425.536.788 | 425.555.866 | +19.078 (+0,004 %) |
| Kaltimport `personal_assistant.cli`, Median 12 Prozesse | 231,273 ms | 291,912 ms | +60,639 ms |
| Kaltimport `mail_agent.cli`, Median 12 Prozesse | 249,090 ms | 303,095 ms | +54,005 ms |
| Cache-Imagebuild | 10,83 s | 11 s | +0,17 s |
| isolierter Container-CLI-Kaltstart | 975 ms | 1.081 ms | +106 ms |

Die produktiven Altdateien wurden kleiner und das Wheel schrumpfte; Inventar,
Verifier, ADR und 15 neue M6-/Manifest-Regressionsitems erhoehen dagegen den
Repositoryumfang. Das gemeinsame Runtime-Image blieb praktisch gleich gross, weil
M7 die rollenbezogene Image-Minimierung bewusst noch nicht vorzieht. Prozess- und
Container-Kaltzeiten schwanken auf dem gleichzeitig betriebenen Host deutlich;
deshalb dokumentiert M6 die gemessene Verschlechterung, ohne daraus eine
willkuerliche Sperre abzuleiten.

Das Image `openclaw-agent:m6-local` wurde mit dem M3-Runtimecheck und dem
M4-Hardeningcheck in temporaeren Containern abgenommen. Der exportierte Rootfs-
Artefaktscan war gruen, `/opt/openclaw-agent/legacy` ist nicht vorhanden, und alle
acht bereits laufenden Produktivcontainer behielten vor und nach der Abnahme ihre
Container-IDs und den Status `healthy`.

## M7-Rollenimages und Vorher-/Nachhervergleich

Die maschinenlesbare Evidenz steht in
[`architecture/image-baseline-m7.json`](architecture/image-baseline-m7.json).
Beide No-Cache-Builds wurden aus demselben sauberen Export mit normalisierten
Zeitstempeln als OCI-Archive erzeugt. Die drei Archiv-SHA-256-Werte waren zwischen
den Laeufen bytegenau identisch; der erste Gesamtlauf dauerte 184 s, der zweite
180 s. Syft 1.50.0, Trivy 0.73.0 und Cosign 3.1.3 sind als unveraenderliche
Scannerimages gepinnt.

| Rolle | M6-Bytes | M7-Bytes | Groesse | kritische CVEs | SPDX-Pakete |
| --- | ---: | ---: | ---: | ---: | ---: |
| Runtime | 425.555.866 | 376.499.617 | -11,53 % | 0 | 926 |
| Proxy | 425.555.866 | 23.417.257 | -94,50 % | 0 | 84 |
| Maintenance | 425.555.866 | 45.627.796 | -89,28 % | 0 | 95 |

Der direkt vergleichbare 12-Prozess-Benchmark auf demselben Host zeigt zugleich
eine Verschlechterung des Kaltstarts: Runtime +33,32 %, Proxy +103,68 % und
Maintenance +52,26 %. Der Median des Peak-RSS aenderte sich um -1,34 %, +19,24 %
beziehungsweise +19,63 %. Diese Regression wird fuer M7 bewusst akzeptiert: Der
Alpine-Wechsel beseitigt 21 nicht behobene kritische Befunde des zuvor getesteten
Debian-Runtimepfads und reduziert Proxy-/Maintenance-Image und Paketzahl massiv.
M7 setzt dafuer keinen nachtraeglich gewaehlten Performance-Grenzwert; der gleiche
CI-Benchmark macht spaetere Aenderungen sichtbar. Die Entscheidung ist in
[ADR-0011](architecture/adr/0011-reproduzierbare-rollenimages.md) festgehalten.

## M8-Integration, Fehler und Recovery

Der lokale/CI-identische hermetische Dockerlauf ist:

```bash
sg docker -c './scripts/check-m8-integration.sh'
```

Er verwendet nur das digest-gepinnte Python-Fixture-Image, ein internes temporaeres
Netz und ein temporaeres Writer-Volume. Es gibt keine Hostports, produktiven Mounts,
Konten oder Secrets. Geprueft werden IMAP, SMTP, WebDAV/CardDAV/CalDAV,
ETag/HTTP-412, Ollama, Marktdaten, ClamAV clean/EICAR, Netztrennung/-recovery,
SIGKILL/Containerrecovery und der Nachweis, dass ein zweiter Mailwriter abgewiesen
wird. Laufzeiten stehen in `build/m8-integration.json`.
Der lokale M8-Abnahmelauf mass 2,583 s bis zum gesunden Fixture-Stack, 2,219 s
fuer das komplette Protokollszenario und 2,558 s vom SIGKILL bis zur erneut
gesunden Fake-Service-Instanz. Diese Werte sind Fixture-Beobachtungen, keine
Produktions-SLAs.

Der lokale Restore-Drill laeuft auch innerhalb von `check-repo.sh`:

```bash
.venv/bin/python scripts/m8-recovery-drill.py --output build/m8-recovery.json
```

Er erzeugt kleine temporaere r26.1-/r27.2.5-States mit realen SQLite-Dateien, nutzt
die produktiven Backup-, Verify-, Restore-Test- und lokalen Restore-Skripte und
vergleicht `state`, `config` und `secrets` bytegenau. Das dritte Szenario injiziert
einen unlesbaren Layoutmarker, verlangt einen unveraenderten Preflight-Abbruch und
stellt danach den Snapshot wieder her. Weder `/srv/openclaw` noch Docker-
Produktivcontainer werden verwendet.

`tests/test_m8_recovery.py` fuehrt den echten Deployment-Shellablauf mit ersetzten
Protokollgrenzen in einem Tempverzeichnis aus: Smoke-Exit 23 muss den automatischen
Rollback ausloesen; ein Rollbackfehler wird als Exit 70 sichtbar. Weitere
Verhaltenstests pruefen Offline-Pflicht, Hook-Abbruch vor Containerstop und den
lokalen Wiederanlauf trotz fehlschlagendem externen Hook. DB-Lock, Full-/Read-only-
Volume, SIGKILL, ungueltige Migration und verlorene Scheduler-Lease werden durch
die bestehenden M3-/Schedulertests real injiziert.

Die gemessenen Recoverywerte und ihre bewusst engen Grenzen stehen im
[M8-Recoveryvertrag](architecture/RECOVERY_AND_RELEASE.md). Ein Fixture-RTO ist
kein Produktions-SLA; Remoteaenderungen sind ohne externen Snapshot nicht durch
lokalen Rollback wiederherstellbar.

Skilldrift wird ohne Textduplikation geprueft:

```bash
.venv/bin/python scripts/generate-skill-tool-contract.py --check
.venv/bin/python -m pytest -q tests/test_m8_skill_contract.py
```

Der Test gleicht alle 146 Tool-IDs, Commands, Modi, externe Wirkungen, Approvals,
Release und Testanker gegen die typisierte Registry ab, prueft die kurze
Triggerbeschreibung und verlangt die domaenenspezifischen Referenzen sowie die
Abwesenheit des entfernten Zweit-Agent-Skills.

Der Portfolio-Mappingpfad wird ohne produktive Provider- oder Modellzugriffe mit
kontrollierten EODHD- und Ollama-Antworten geprueft:

```bash
.venv/bin/python -m pytest -q tests/test_portfolio_mapping.py
```

Providergebundene Research- und Investmentprofilregressionen verwenden
vollstaendige kontrollierte EODHD-Fundamental-/EOD-Fixtures und einen injizierten
Screener. Sie pruefen deterministische Wiederholbarkeit, Identitaetsbindung,
Secret-Redaktion, Mindestdaten/Enthaltung, Ranking, Ausschluss bestehender Werte,
append-only Profil-/Feedbackhistorie und fehlende automatische Profilmutation:

```bash
.venv/bin/python -m pytest -q tests/test_portfolio_research.py
```

Die Regressionen belegen exakte ISIN-Filterung, den Ollama-JSON-Vertrag und die
Koordinatorheader. Sie pruefen ausserdem, dass erfundene Kandidaten-IDs oder MICs
fehlschlagen und ein Vorschlag ohne separate Freigabe weder Instrument noch
Watchlist veraendert.

Die finale lokale M8-Artefaktabnahme baute das Wheel in 2,865 s; es blieb 395.102
Bytes gross und bestand in der frischen Installationsumgebung 455 Tests plus 13
Subtests in 43,95 s. Der cache-gestuetzte Build der drei M8-Rollen dauerte 27,7 s.
Rollen-Smokes, M3-Runtime, M4-Haertung, Rootfs-Artefaktscan, SPDX, Provenance und
Trivy waren gruen:

| M8-Rolle | Image-Bytes | SPDX-Pakete | kritische CVEs | Secret-Befunde |
| --- | ---: | ---: | ---: | ---: |
| Runtime | 376.488.274 | 926 | 0 | 0 |
| Proxy | 23.417.259 | 84 | 0 | 0 |
| Maintenance | 45.627.770 | 95 | 0 | 0 |

Diese lokalen Images sind nicht signierte produktive Releaseartefakte. Das
Deployment-Gate akzeptiert weiterhin nur die drei unveraenderlichen, attestierten
Registry-Digests eines freigegebenen Commits.

Die anschliessende immutable Plugin-/Gatewaykorrektur baute ein Wheel mit 397.870
Bytes in 4,486 s und bestand in der frischen Installationsumgebung 472 Tests plus
22 Subtests. Das lokale Runtime-Image mit Brave und Signal war 376.600.036 Bytes
gross; M3, M4, der Offline-Gatewaystart, SBOM, Provenance und Trivy blieben mit
null kritischen CVEs und null Secret-Befunden gruen.

## M9-Mailqualitaet und Suchprojektion

Die M9-Regressionspakete laufen im gemeinsamen Testpfad und koennen zusaetzlich
zielgerichtet ohne produktive Konten ausgefuehrt werden:

```bash
.venv/bin/python -m pytest -q \
  tests/test_mail_review_m9.py \
  tests/test_subject_patterns_m95.py \
  tests/test_mail_projection_m96.py \
  tests/test_mail_search_snapshot.py
```

Die Tests verwenden Fake-IMAP-Adapter, kontrollierte Ollama-Antworten, temporaere
SQLite-Datenbanken und synthetische Nachrichten. Sie pruefen typisierte
Reviewgruende, read-only Vorschlaege, exakte Einzelkorrektur, getrenntes Routing,
chronologisches Lernen ohne Selbsttreffer sowie Enthaltung bei Konflikten und
Modellfehlern. Fuer die Suchprojektion bleibt ein echter WAL-Writer mit
`BEGIN IMMEDIATE` offen; der Sync-Worker darf die Mail-Datenbank trotzdem nicht
aufrufen. Crash vor Manifestpublikation, Korruption, Alter und die letzte
vollstaendige Generation werden als Verhalten und nicht als Textsuche geprueft.

Der finale M9-Repositorylauf sammelte und bestand 610 pytest-Items. JUnit meldete
einschliesslich Subtests 643 Faelle. Die Branch-einbezogene Gesamt-Coverage lag bei
62,03 Prozent, die reine Branch-Coverage bei 47,12 Prozent. Das neue
`mail_projection`-Modul erreichte 74,40 Prozent, der Mail-Projektionswriter 90,79
Prozent. Diese Werte sind reproduzierbare Beobachtungen und keine nachtraeglich
gesetzten fachlichen Erfolgsgrenzen.

Der isolierte M9-Wheellauf baute das Artefakt in 1,899 Sekunden mit 442.024 Bytes,
bestand den Secret-/Laufzeitdatenscan, installierte es in eine frische Umgebung
und fuehrte dort erneut alle 610 pytest-Items erfolgreich aus.

## Wheel- und Artefaktpruefung

```bash
./scripts/check-wheel.sh
```

Das Skript exportiert die exakte Git-Quellmenge in ein leeres temporaeres
Verzeichnis, verifiziert dort das Manifest, baut ein Wheel, installiert es in eine
frische venv und prueft CLI-Hilfe, Paketimport aus `site-packages`,
`version --verify` und denselben Testbefehl. Der installierte Testmodus laedt die
beiden Produktpakete vor pytest nachweislich aus `site-packages` und bricht ab, falls
waehrend Collection oder Ausfuehrung ein Produktmodul aus dem Snapshot importiert
wird. Die Coverage-Baseline entsteht im normalen Repository-Lauf; sie wird im
Wheel-Lauf nicht redundant mit abweichenden Installationspfaden neu gemessen. Wheel
und die produktbezogenen Pfade des exportierten Image-Dateisystems werden durch
`scripts/check_artifact.py` auf
produktive Konfigurationen, Datenbanken, Logs, lokale virtuelle Umgebungen,
Laufzeitdateien, Schluesseldateien und typische Secret-Muster geprueft. Dazu gehoeren
auch `/home/node/.openclaw`, `/etc/openclaw-env`, `/run/openclaw-env` und
`/run/openclaw-secrets`, nicht nur `/opt/openclaw-agent`.

## Dynamische M3-Containerabnahme

Nach dem isolierten Imagebuild wird dasselbe Skript lokal und in GitHub Actions
ausgefuehrt:

```bash
./scripts/check-container-runtime.sh openclaw-agent:m3-test
```

Es verwendet ausschliesslich ein mit `mktemp` angelegtes Fixture-State-Verzeichnis,
`--network none` und kurzlebige Container. Geprueft werden zwei parallele Starts,
die Layoutsperre, eine manipulierte Legacy-`assistant.sh`, read-only Imagecode,
unveraenderte Instanzkonfiguration beim Neustart, Release-/OCI-Revision,
tatsaechliche Modul- und Executable-Pfade, Layout 3, getrennte Rollenmounts sowie der
vollstaendige Image-Artefaktscan.
Ein vorhandener Compose-Stack und `/srv/openclaw` werden nicht verwendet.

## Dynamische M4-Containerabnahme

Nach der M3-Pruefung wird dasselbe gebaute Image weiter isoliert geprueft:

```bash
./scripts/check-container-hardening.sh openclaw-agent:m4-test
```

Der Prueflauf legt nur temporaere Docker-Netze und Container an. Er bestaetigt
Nicht-root-UID/GID, leere effektive Capability-Maske, read-only Rootfs, fehlende
fremde Secretdateien, positive Backend- und negative Fremdnetz-Erreichbarkeit,
SIGTERM-Weitergabe, Docker-Inspect-Werte fuer PID/CPU/RAM, eine tatsaechlich
erzwungene PID-Grenze und einen isolierten OOM-Kill. Er verbindet sich weder mit
Produktionsnetzen noch mit `/srv/openclaw` und startet keinen fachlichen Job.

ClamAV-Negativfaelle laufen ohne Internet als Python-Fixtures: fehlende oder alte
`main`/`daily`/`bytecode`-Signaturen und eine nicht belegbare Scanneridentitaet
brechen fail-closed ab. `runtime-hardening.json` wird separat gegen gerendertes
Compose geprueft; dadurch fallen neue Ports, Netze, Rootrollen, Secrets oder
gelockerte Ressourcenlimits bereits im normalen Repository-Check auf.

## M3-State-, Parallelitaets- und Zugriffstests

`tests/test_state_layout_m3.py` verwendet ausschliesslich temporaere Fixtures und
reale Prozesse. Es prueft acht konkurrierende ActionPlan-Writer, gleichzeitige
Schedulerzugriffe von Mail/Sync/Portfolio/Monitor, SQLite-Lock- und WAL-Verhalten,
SIGKILL vor Commit, abgelaufene Leases (zusammen mit `test_work_scheduler.py`),
Full-/Read-only-Preflight, konsistente Backups, Restore alter und neuer Layouts und
die Mountmatrix aus gerendertem Compose.

Persistente Zugriffe einer geaenderten Rolle werden instrumentiert geprueft:

```bash
./scripts/audit-state-access.py --role <rolle> \
  --root "<bereich>=<absoluter-fixture-pfad>" -- <fixture-kommando>
```

Die synthetische, reproduzierbare Vorher-/Nachher-Messung wird so wiederholt:

```bash
.venv/bin/python scripts/benchmark-state-layout.py
```

Lokaler Messwert vom 2026-08-05 (7 Laeufe, sechs logische Schemas plus 500
WAL-Inserts): Legacy-Verzeichnis Median `285.646 ms`, Layout-3-Teilbaeume
`324.948 ms`; geschriebene Bytes `8,491,008` gegen `8,802,304`, gelesene
Block-I/O-Bytes `0` gegen `4,096`. Das ist eine Fixture-Baseline, kein produktiver
SLA-Grenzwert; der Mehraufwand umfasst die getrennte Wissensdatenbank.

## Gepinnte Werkzeugversionen

| Werkzeug | Version |
| --- | --- |
| pip | 26.2.1 |
| setuptools | 83.0.0 |
| build | 1.5.0 |
| pytest | 9.1.1 |
| pytest-cov | 7.1.0 |
| Coverage.py | 7.15.3 |
| Ruff | 0.15.22 |
| mypy | 2.3.0 |
| ShellCheck | 0.11.0 |
| Hadolint | 2.15.1 |

Die transitiven Python-Abhaengigkeiten sind ebenfalls exakt in
`requirements-dev.lock` festgelegt. Docker, Compose, Git und die jeweilige
Python-Version stammen vom Host beziehungsweise CI-Runner und werden in
`build/m0-baseline.json` protokolliert.
