# M10-Roadmap: Rechnungsqualitaet und sichere Neubewertung

Stand: 2026-08-15
Empfohlener Arbeitsbranch: `development/invoice-quality-reprocessing-m10`
Status: Planung; kein M10-Paket implementiert und kein Produktivlauf freigegeben

## Ziel

M10 verbessert die belegte Extraktion von Rechnungsdatum, Rechnungsnummer,
Lieferant, Brutto-, Netto- und Steuerbetrag. Bereits archivierte Original-PDFs
bleiben unveraendert. Unsichere Ergebnisse bleiben sichtbar in Pruefung, statt
durch Dateinamen, groesste Zahlenwerte oder Modellvermutungen scheinbar
vervollstaendigt zu werden.

Der Milestone fuehrt ausserdem einen sicheren Weg ein, bestehende unklassifizierte
und bereits als `review` markierte Datensaetze read-only neu zu bewerten. Eine
spaetere Uebernahme muss gegen genau die zuvor angezeigte Vorschau, den aktuellen
Datensatz und den PDF-Hash geschuetzt sein. Manuell bestaetigte oder korrigierte
Metadaten werden niemals automatisch ersetzt.

M10 ist keine Steuerberatung, keine DATEV-Integration und kein Auftrag, historische
PDFs zu verschieben, umzubenennen, zu loeschen oder zu ueberschreiben. Der
Milestone fuehrt auch keine produktive Neubewertung aus. Ein produktiver Lauf ist
ein spaeterer, separater und ausdruecklich freizugebender Betriebsschritt.

## Gemessener Ausgangsstand

Die folgenden aggregierten Werte stammen aus den read-only Ausgaben vom
2026-08-14/15. Roh-PDFs, Mailinhalte, Message-IDs, Dateinamen, Lieferantennamen und
Hashes werden nicht in Git oder Test-Fixtures uebernommen.

| Messwert | Ausgangswert |
| --- | ---: |
| Rechnungsdatensaetze insgesamt | 63 |
| Status `review` | 48 |
| Bestaetigt oder manuell bestaetigt | 5 |
| Noch ohne Extraktionsstatus | 10 |
| Fehlende Rechnungsnummer in den 48 Prueffaellen | 39 |
| Fehlender Bruttobetrag | 27 |
| Beanstandetes Rechnungsdatum | 7 |
| Historischer Hinweis `OCR-Werkzeug fehlt: tesseract` | 22 |
| Kategorie `Ungeklaert` | 19 |
| Extraktion nur aus PDF-Text | 40 |
| Extraktion mit OCR-Fallback | 8 |
| Mittlere Extraktionskonfidenz der Prueffaelle | 0,5127 |
| Datensaetze mit Brutto, Netto und Steuer | 10 |
| Davon rechnerisch inkonsistent | 9 |
| Steuerbetrag vorhanden, Bruttobetrag fehlt | 26 |
| Steuerbetrag groesser als Bruttobetrag | 4 |
| Prueffaelle im Nextcloud-Unterordner `Pruefen` | 29 |
| Prueffaelle ausserhalb dieses Unterordners | 19 |

Die 22 historischen Tesseract-Hinweise entstanden ausschliesslich am 25. und
26. Juli 2026. Tesseract ist inzwischen mit `deu` und `eng` verfuegbar. Die
aktuelle Backfill-Funktion verarbeitet diese Datensaetze trotzdem nicht, weil sie
nur Zeilen mit leerem `extraction_status` auswaehlt.

Die Backfill-Vorschau lieferte fuer `--year 2024` und `--year 2025` jeweils null
Kandidaten. `--year 2026` lieferte genau die zehn unklassifizierten Altzeilen;
neun blieben `review`, nur eine wurde `confirmed`. Mehrere PDF-Pfade lagen in
historischen Jahresordnern. Ursache ist die heutige Jahresableitung, die bei noch
unextrahierten Zeilen Empfangs- oder Erstellungsdatum vor dem Pfadjahr verwendet.

Diese Werte sind eine Baseline, noch keine willkuerlichen Freigabegrenzen. M10
darf eine Verbesserung nur behaupten, wenn Feldgenauigkeit, Review-Abdeckung,
Plausibilitaet und Sicherheitsfehler gemeinsam berichtet werden.

## Wesentliche Befunde

1. Rechnungsnummern bleiben haeufig leer, obwohl ein beschriftetes Feld oder eine
   plausible Nummer im Dateinamen sichtbar ist. Der Dateiname darf nur stuetzende,
   nie alleinige Evidenz sein.
2. Betragsfelder sind nicht ausreichend gegeneinander validiert. Steuersatz,
   Steuerbetrag, Einzelpreis, Zwischensumme und Zahlbetrag muessen typisiert
   unterschieden werden.
3. Die vorhandene Backfill-Auswahl ist eine Legacy-Ergaenzung fuer leere
   Extraktionszustaende und kein Reprocessing vorhandener Prueffaelle.
4. Die Bedeutung von `--year` ist fuer Legacy-Zeilen nicht eindeutig und stimmt
   nicht zwingend mit Pfad- oder Rechnungsjahr ueberein.
5. `backfill`, `correct` und Teile des Exportpfads koennen nach lokaler
   Datenbankaenderung das verwaltete Nextcloud-Jahresregister ersetzen. Der
   typisierte Toolvertrag muss diesen externen Effekt und die erforderliche
   Freigabe exakt ausweisen.
6. Prueffall, Archivpfad und Registerstatus sind getrennte Zustaende. Ein
   `uploaded`-Status beweist keine korrekten Metadaten.

## Zielarchitektur

```text
unveraendertes PDF + Scanneridentitaet
                |
                v
     versionierte Feldkandidaten
                |
                v
  deterministische Plausibilitaet
                |
                v
 read-only Alt/Neu-Vorschau + Digest
                |
       ausdrueckliche Einzelfreigabe
                |
                v
 ETag-geschuetztes Register + Audit
```

- Jeder Feldwert traegt Quelle, normalisierten Wert, Evidenztyp, Konfidenz und
  Extraktorversion.
- Native PDF-Texte bleiben primaer. OCR wird lokal, begrenzt und nur bei
  fehlenden oder unbrauchbaren Pflichtfeldern eingesetzt.
- Ollama darf erklaeren oder zwischen bereits extrahierten Kandidaten abstimmen,
  aber keine Nummer, kein Datum und keinen Betrag erzeugen.
- Feldkonflikte und unplausible Rechenbeziehungen fuehren fail-closed zu `review`.
- Eine Vorschau veraendert weder SQLite noch Nextcloud und besitzt einen
  deterministischen Digest ueber PDF, Altzustand und vorgeschlagenen Neuzustand.
- Eine Uebernahme gilt fuer genau einen PDF-Hash und genau einen unveraenderten
  Vorschau-Digest. Bulk-Uebernahme bleibt ausserhalb M10.
- Bestaetigte und manuell korrigierte Werte sind vor automatischer Neubewertung
  geschuetzt.

## Sicherheits- und Arbeitsvertrag

- Jedes Paket wird einzeln implementiert, getestet, dokumentiert und committet.
- Ein Paket beginnt erst nach gruener Abnahme des vorherigen Pakets.
- Entwicklung verwendet ausschliesslich synthetische oder vollstaendig sanitierte
  PDF-/Text-Fixtures und temporaere Datenbanken. Produktive PDFs, Register,
  Message-IDs, Hashes, Pfade oder Mailinhalte gelangen nicht nach Git, CI, Wheel
  oder Image.
- Keine Entwicklungsarbeit veraendert `/srv/openclaw`, produktive Jobs,
  Nextcloud-Dateien oder produktive SQLite-Datenbanken.
- Jede PDF wird vor Verarbeitung ueber den bestehenden fail-closed
  ClamAV-Vertrag abgesichert. Keine externe OCR- oder Dokumenten-API.
- Original-PDFs werden niemals verschoben, umbenannt, geloescht oder
  ueberschrieben. Die 19 historischen Pfadabweichungen werden nur berichtet.
- Kein produktiver Reprocess, Backfill, Export oder `correct --yes` ohne separaten
  ausdruecklichen Auftrag und verifiziertes Backup.
- Eine bestaetigte oder manuell korrigierte Zeile darf weder in einer Vorschau als
  automatisch aenderbar erscheinen noch durch Apply veraendert werden.
- Ein neuer Agentenbefehl braucht stabile CLI, typisierten Toolkatalog,
  generierte Befehls-/Skilldokumentation und Verhaltensregressionstest.
- Toolmodus und `writes_external_data` muessen dem tatsaechlichen Effekt
  entsprechen. Eine Nextcloud-Registeraenderung ist kein rein lokaler Schreibzugriff.
- Bestehende Benutzerveraenderungen im Worktree bleiben erhalten. Keine Arbeiten
  aus einem spaeteren Paket vorziehen.

## Paketuebersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M10.0 | Reproduzierbare Baseline und sanitierter Evaluationskorpus | M9 abgenommen |
| M10.1 | Wahrer Tool-/Effektvertrag fuer Register und Korrektur | M10.0 |
| M10.2 | Belegte Rechnungsnummern und sichere Datumsfelder | M10.1 |
| M10.3 | Typisierte Betraege und rechnerische Plausibilitaet | M10.2 |
| M10.4 | Begrenzte OCR-Auswahl und versionierte Feldfusion | M10.3 |
| M10.5 | Read-only Reprocessing-Vorschau mit klarer Jahressemantik | M10.4 |
| M10.6 | Auditierbare, explizite Einzeluebernahme | M10.5 |
| M10.7 | Register-/Backlog-Audit und agentengerechte Bedienung | M10.6 |
| M10.8 | Gesamt-Abnahme und separater Rolloutvertrag | M10.7 |

## M10.0 – Baseline und sanitierter Evaluationskorpus

Umsetzungsstand: abgeschlossen auf dem Entwicklungszweig
`development/invoice-quality-reprocessing-m10`. Die Abnahme ist vollstaendig
lokal und synthetisch; M10.1 wurde nicht begonnen.

### Scope

- Die oben dokumentierten Aggregate mit bestehenden read-only Werkzeugen
  reproduzierbar erfassen, ohne private Einzelwerte zu versionieren.
- Charakterisierungstests fuer den aktuellen Extraktor, die Backfill-Auswahl und
  die heutige Jahresprioritaet anlegen.
- Einen kleinen sanitisierten Korpus fuer deutsche und englische Rechnungen mit
  bekannten Sollfeldern erstellen. Er muss mindestens explizite und fehlende
  Rechnungsnummern, mehrere Datumsarten, Brutto/Netto/Steuer, Prozentwerte,
  Gutschrift und mehrseitige PDFs abdecken.
- Ein Evaluationsformat definieren, das Feldpraezision, Feldabdeckung,
  False-confirmed, Reviewquote und Rechenfehler getrennt ausweist.
- Noch keine Extraktions-, Datenbank-, Tool- oder Routinglogik aendern.

### Pflichttests und Abnahme

- Der Korpus enthaelt keine reale Adresse, Message-ID, Originaldatei, Hash,
  Kontonummer, Bestellnummer oder anderen privaten Inhalt.
- Tests belegen, dass bestehende `review`-Zeilen nicht durch Legacy-Backfill
  gesammelt werden und dass zehn leere Extraktionszustaende getrennt sind.
- Die aktuelle Jahresprioritaet ist als Verhaltenstest sichtbar, nicht als
  gewuenschte Semantik festgeschrieben.
- Evaluationsbericht und Befehle sind deterministisch.
- `check-repo.sh`, Quellmanifest und `git diff --check` sind gruen.

### Entwicklungsprompt

```text
Setze ausschliesslich M10.0 aus
docs/INVOICE_QUALITY_REPROCESSING_ROADMAP.md um. Lies AGENTS.md,
skills/personal-assistant/references/records.md und den generierten Toolvertrag
vollstaendig. Fuehre zuerst ./scripts/assistant.sh version --verify und
git status --short aus. Erzeuge nur eine reproduzierbare, datenschutzsichere
Baseline, Charakterisierungstests und einen vollstaendig sanitisierten
Rechnungskorpus. Uebernimm keine produktiven PDFs, Dateinamen, Message-IDs, Hashes,
Pfade oder Lieferantendaten. Friere insbesondere Backfill-Auswahl,
Jahresprioritaet und heutige Extraktionsfehler als Charakterisierung ein, ohne sie
zu beheben. Definiere Feldpraezision, Feldabdeckung, False-confirmed, Reviewquote
und Rechenfehler als getrennte Messwerte. Veraendere keine Produktivdatei, keine
Datenbank, keinen Toolvertrag und keine Extraktionsentscheidung. Aktualisiere nur
passende Test-/Baseline-Dokumentation und das Quellmanifest. Fuehre den
vollstaendigen Repository-Check aus, berichte Testzahl und Baseline und stoppe nach
M10.0. Beginne nicht mit M10.1.
```

## M10.1 – Wahrer Tool- und Effektvertrag

### Scope

- `invoices export`, produktives `backfill` und `correct` gegen ihre tatsaechlichen
  SQLite- und Nextcloud-Effekte charakterisieren.
- Read-only Vorschau, lokale Datenbankaenderung und externe
  Registeraktualisierung in CLI und Toolkatalog eindeutig trennen.
- `mode`, `writes_external_data`, Approval-Label, Hilfe und Skilltext korrigieren,
  ohne still eine neue Berechtigung einzufuehren.
- Jeder Pfad, der das verwaltete Nextcloud-Jahresregister ersetzt, braucht
  ausdrueckliche Freigabe, ETag-/SHA-/Schema-Schutz und einen Verhaltensnachweis.
- Bestehende CLI-Kompatibilitaet erhalten oder eine klar dokumentierte,
  fail-closed Migration mit Deprecation-Test vorsehen.

### Pflichttests und Abnahme

- Ein als `read` oder `local-write` registrierter Befehl kann kein externes
  Register veraendern.
- Ohne erforderliches `--yes` bleiben SQLite und Nextcloud unveraendert.
- Vorschaupfade schreiben weder Datenbank noch Register.
- Toolkatalog, `tools list`, Capability-Ausgabe, Befehlsreferenz und Skillvertrag
  stimmen deterministisch ueberein.
- Negative Tests belegen ETag-Konflikt, SHA-Abweichung, Schemafehler und
  fehlgeschlagene Registeraktualisierung.

### Entwicklungsprompt

```text
Setze nur M10.1 um und fuehre zuerst die M10.0-Abnahme aus. Pruefe die realen
Seiteneffekte aller Rechnungsbefehle vom CLI-Handler bis zur Nextcloud-Bridge.
Korrigiere den typisierten Toolvertrag so, dass jede Registeraenderung als externe
Schreibwirkung mit expliziter Freigabe ausgewiesen wird. Trenne echte read-only
Vorschau von SQLite- und Nextcloud-Schreiben. Bewahre CLI-Kompatibilitaet, soweit
sie nicht einen falschen Sicherheitsvertrag erhaelt; alte unsichere Formen muessen
fail-closed ablehnen oder klar migriert werden. Teste Seiteneffektfreiheit ohne
Freigabe sowie ETag-, SHA-, Schema- und Remote-Fehler. Fuehre keine produktive
Korrektur, keinen Backfill und keinen Export aus. Regeneriere Tool-/Skill- und
Befehlsdokumentation ausschliesslich aus dem Katalog, aktualisiere Manifest und
stoppe nach M10.1. Beginne nicht mit M10.2.
```

## M10.2 – Belegte Rechnungsnummern und sichere Datumsfelder

### Scope

- Feldkandidaten fuer Rechnungsnummer und Rechnungsdatum typisieren und Evidenz,
  Normalisierung sowie Ausschlussgrund erhalten.
- Deutsche und englische Anker wie `Rechnungsnummer`, `Rechnung Nr.`,
  `Invoice No.` und belegte Varianten mit begrenztem Kontext auswerten.
- Kunden-, Bestell-, Liefer-, Vertrags-, Telefon-, Steuer- und Trackingnummern
  explizit von Rechnungsnummern trennen.
- Dateinamen nur als stuetzende Evidenz nutzen. Eine unbeschriftete Zahl im
  Dateinamen bestaetigt allein keine Rechnungsnummer.
- Rechnungsdatum weiterhin von Leistungs-, Liefer-, Bestell-, Zahlungs- und
  Faelligkeitsdatum trennen; Konflikte bleiben `review`.
- Noch keine Datenbankmigration und kein Reprocessing bestehender Zeilen.

### Pflichttests und Abnahme

- Positive und negative Golden-Tests fuer deutsche/englische Labels,
  Bindestriche, Schraegstriche, alphanumerische Nummern, Unicode und OCR-Abstaende.
- Dateiname plus beschriftetes PDF-Feld kann bestaetigen; Dateiname allein bleibt
  unsicher.
- `Rechnung NR. <Wert>` darf nicht im Datumsevidenzfeld verschwinden, ohne als
  Nummernkandidat betrachtet zu werden.
- Keine Verwechslung mit Kundennummer, Telefonnummer, IBAN, USt-ID oder Datum.
- Baselinevergleich berichtet Nummern- und Datumspraezision sowie
  False-confirmed getrennt.

### Entwicklungsprompt

```text
Setze ausschliesslich M10.2 um. Fuehre die Abnahmen M10.0 und M10.1 aus. Verbessere
Rechnungsnummer und Rechnungsdatum ueber typisierte, belegte Feldkandidaten.
Verwende nur PDF-/OCR-Text als primaere Evidenz; der Dateiname darf einen bereits
plausiblen Kandidaten stuetzen, aber nie allein bestaetigen. Trenne Kunden-,
Bestell-, Liefer-, Vertrags-, Telefon-, Steuer- und Trackingnummern sowie
Leistungs-, Liefer-, Zahlungs- und Faelligkeitsdaten. Bei Konflikten oder nur
unbeschrifteten Zahlen bleibe fail-closed in review. Nutze ausschliesslich den
sanitisierten Korpus und ergaenze negative Golden- und Regressionstests. Aendere
noch keine SQLite-Schemata, Backfill-Auswahl, produktiven Register oder
Nextcloud-Dateien. Berichte Feldpraezision, Abdeckung und False-confirmed und
stoppe nach M10.2.
```

## M10.3 – Typisierte Betraege und rechnerische Plausibilitaet

### Scope

- Betragskandidaten nach Rolle unterscheiden: Zahlbetrag/Brutto, Netto,
  Steuerbetrag, Steuersatz, Zwischensumme, Rabatt, Gutschrift und Einzelpreis.
- Deutsche und englische Dezimal-/Tausenderformate sowie ISO-Waehrungen
  deterministisch normalisieren.
- Prozentwerte niemals als Geldbetrag interpretieren.
- Gelabelten Gesamt-/Zahlbetrag priorisieren, aber nicht pauschal den groessten
  Wert waehlen.
- Brutto, Netto und Steuer mit dokumentierter Cent-Toleranz pruefen. Konflikte,
  mehrere unvereinbare Summen oder Steuer groesser als Brutto fuehren zu
  typisiertem `review`-Grund.
- Gutschriften und negative Werte gesondert behandeln; keine stillen Vorzeichen-
  oder Waehrungskorrekturen.

### Pflichttests und Abnahme

- `19 %` und `7 %` werden nie zu 19,00 EUR oder 7,00 EUR Steuerbetrag.
- `Brutto = Netto + Steuer` gilt innerhalb der dokumentierten Rundungstoleranz;
  unplausible Dreierkombinationen koennen nicht `confirmed` werden.
- Mehrere Gesamtbetraege, Abschlag, Guthaben, Rabatt und Fremdwaehrung sind
  positiv und negativ getestet.
- Kein Betrag wird aus Mailtext, Dateiname oder Ollama erfunden.
- Evaluationsbericht nennt Betragspraezision, Abdeckung, Rechenfehler und
  False-confirmed vor/nach der Aenderung.

### Entwicklungsprompt

```text
Setze nur M10.3 um. Fuehre die vorherigen M10-Abnahmen aus. Ersetze untypisierte
Betragsauswahl durch belegte Kandidatenrollen fuer Brutto/Zahlbetrag, Netto,
Steuerbetrag, Steuersatz, Zwischensumme, Rabatt, Gutschrift und Einzelpreis.
Interpretiere Prozentwerte niemals als Geld und waehle nicht automatisch den
groessten Zahlenwert. Validiere Brutto, Netto und Steuer mit einer dokumentierten
Cent-Toleranz; unvereinbare Werte muessen fail-closed einen typisierten
Review-Grund erzeugen. Teste deutsche und englische Formate, mehrere Summen,
Rundung, Gutschrift, negative Werte und Fremdwaehrung mit sanitisierten Fixtures.
Ollama darf keine Werte erzeugen oder Rechenregeln ueberstimmen. Veraendere noch
keine Datenbank und fuehre kein Reprocessing aus. Berichte den direkten
Baselinevergleich und stoppe nach M10.3.
```

## M10.4 – Begrenzte OCR-Auswahl und versionierte Feldfusion

### Scope

- Native PDF-Texte weiter zuerst lesen und ihre Nutzbarkeit feldbezogen bewerten.
- OCR nur fuer fehlende/unbrauchbare Pflichtfelder und innerhalb eines
  dokumentierten Seiten-, Zeit-, Groessen- und Ressourcenbudgets ausfuehren.
- Bei mehrseitigen Rechnungen anhand des sanitisierten Korpus pruefen, ob erste
  Seiten plus letzte Seite sicherer sind als eine pauschale Erhoehung von
  `max_pages`. Keine willkuerliche Grenzaenderung.
- Native und OCR-Kandidaten feldweise fusionieren. Widerspruch wird sichtbar und
  nicht durch Gesamt-Konfidenz verdeckt.
- Extraktor-/Regelversion, OCR-Engine, Sprachen und Scanneridentitaet im technischen
  Ergebnis erfassen, ohne Dokumentinhalt in Telemetrie zu schreiben.

### Pflichttests und Abnahme

- Native Pflichtfelder verhindern unnoetige OCR; fehlende Felder erlauben nur den
  begrenzten Fallback.
- Mehrseitige, bildbasierte, gemischte und korrupte PDFs sowie OCR-Timeout,
  fehlende Sprache und fehlendes Binary sind getestet.
- Konflikte zwischen Native und OCR koennen nicht still `confirmed` werden.
- Ressourcenbudget und Laufzeitbaseline werden gemessen und dokumentiert.
- Keine externe OCR-Verbindung und keine Abschwaechung des ClamAV-Gates.

### Entwicklungsprompt

```text
Setze ausschliesslich M10.4 um. Fuehre M10.0 bis M10.3 erneut aus. Implementiere
eine feldbezogene, lokal begrenzte OCR-Strategie: nativer Text bleibt primaer, OCR
wird nur fuer fehlende oder unbrauchbare Pflichtfelder innerhalb klarer Seiten-,
Zeit-, Groessen- und Ressourcenbudgets verwendet. Untersuche mit sanitisierten
mehrseitigen Fixtures erste Seiten plus letzte Seite, statt max_pages willkuerlich
zu erhoehen. Fusioniere native und OCR-Kandidaten je Feld und behandle Widerspruch
als Review. Versioniere Extraktor und Regeln; speichere nur technische Identitaet,
keinen Dokumentinhalt in Telemetrie. Teste Fehler, Timeout, Sprachen, korrupte PDFs
und ClamAV fail-closed. Fuehre kein produktives OCR oder Reprocessing aus und
stoppe nach M10.4.
```

## M10.5 – Read-only Reprocessing-Vorschau

### Scope

- Ein registriertes read-only Werkzeug fuer bestehende unklassifizierte oder
  `review`-Zeilen einfuehren; der Legacy-Backfill bleibt semantisch getrennt.
- Quelljahr (bestehender Archivpfad/Datensatz) und neu erkanntes Rechnungsjahr als
  getrennte Felder ausgeben. `--year` darf nicht mehr still mehrere Bedeutungen
  haben.
- Fuer jeden Kandidaten Altwert, Neuwert, Evidenztyp, Konflikte, Extraktorversion
  und Bewertung `improved`, `unchanged`, `regressed` oder `still-review` liefern.
- Bestaetigte und manuell korrigierte Datensaetze hart ausschliessen.
- Einen deterministischen Vorschau-Digest aus PDF-Hash, aktuellem Datensatz,
  Extraktorversion und Neuvorschlag berechnen.
- Vorschau veraendert weder SQLite noch Nextcloud und bewegt kein PDF.

### Vorgesehener Toolvertrag

```text
Tool-ID: assistant.invoices.reprocess-preview
Modus: read
Externe Wirkung: nein
Approval: none
Kommando: ./scripts/assistant.sh invoices reprocess --status "<review|unclassified>" --source-year <YYYY> --limit 100 --dry-run
```

Die exakte CLI darf waehrend M10.5 nur aus begruendeten Parser-/Kompatibilitaets-
Gruenden angepasst werden; Toolkatalog, Skill, Dokumentation und Tests muessen
anschliessend dieselbe Form verwenden.

### Pflichttests und Abnahme

- SQLite-Datei, Register-ETag und PDF-Bestand bleiben bytegleich.
- `review` und `unclassified` sind getrennt waehlbar; `confirmed` und
  `confirmed-manual` werden auch bei manipulierten Argumenten ausgeschlossen.
- Source-Year, Pfadjahr, Empfangsjahr und erkanntes Rechnungsjahr werden nicht
  verwechselt.
- Gleicher Eingang und gleiche Extraktorversion liefern denselben Digest.
- Geaenderter PDF-Hash, Altzustand oder Extraktor erzeugt einen anderen Digest.
- Ausgabe enthaelt keine PDF-Texte oder Secrets; Evidenz ist begrenzt und
  datenschutzbewusst.

### Entwicklungsprompt

```text
Setze nur M10.5 um. Fuehre die Abnahmen M10.0 bis M10.4 aus. Implementiere ein
registriertes read-only invoices reprocess fuer exakt review oder unclassified.
Trenne Quelljahr, Pfadjahr, Empfangsjahr und neu erkanntes Rechnungsjahr in der
Ausgabe. Zeige pro Feld begrenzte Alt-/Neu-Evidenz und klassifiziere den Vorschlag
als improved, unchanged, regressed oder still-review. Schliesse bestaetigte und
manuell korrigierte Zeilen hart aus. Erzeuge einen deterministischen Digest ueber
PDF-Hash, Altzustand, Extraktorversion und Vorschlag. Der Befehl darf weder SQLite
noch Nextcloud, Register, PDF-Pfad oder Audit veraendern. Ergaenze CLI,
Toolkatalog, generierte Dokumentation und echte Seiteneffektfreiheitstests. Fuehre
keinen Apply aus und stoppe nach M10.5.
```

## M10.6 – Auditierbare, explizite Einzeluebernahme

### Scope

- Ein additives, migrationssicheres Extraktionsaudit fuer Altzustand,
  Vorschau-Digest, Neuzustand, Extraktorversion, Freigabe und Ergebnis einfuehren.
- Eine Uebernahme fuer genau einen PDF-Hash und genau einen unveraenderten
  Vorschau-Digest erlauben. Keine Bulk-Option.
- Vor Apply PDF-Hash, aktuelles Datensatzfingerprint, Status, manuelle
  Korrekturgrenze und Extraktorversion erneut pruefen.
- Bestaetigte/manuell korrigierte Zeilen, Regressionen und weiterhin
  rechnerisch unplausible Vorschlaege ablehnen.
- Registeraktualisierung ueber bestehenden ETag-/SHA-/Schema-Vertrag ausfuehren
  und lokale/remote Teilfehler sichtbar sowie wiederaufnehmbar behandeln.
- Original-PDF und bestehender Archivpfad bleiben unveraendert.

### Vorgesehener Toolvertrag

```text
Tool-ID: assistant.invoices.reprocess-apply
Modus: write
Externe Wirkung: ja
Approval: explicit-user-single-invoice-reprocess
Kommando: ./scripts/assistant.sh invoices reprocess-apply --hash "<SHA256>" --expected-preview-sha256 "<Digest>" --yes
```

### Pflichttests und Abnahme

- Ohne `--yes`, bei falschem Hash/Digest, Drift, Statuswechsel oder manueller
  Korrektur findet keine Aenderung statt.
- Migration erhaelt alle bisherigen Rechnungs-, Register- und Korrekturdaten und
  ist wiederholbar.
- Genau-eine-Datensatz-Grenze, Idempotenz und konkurrierender Apply sind getestet.
- Remote-Konflikt oder Ausfall erzeugt keinen behaupteten Gesamterfolg; ein
  sicherer Wiederaufnahme-/Abgleichpfad ist vorhanden.
- Audit enthaelt keine PDF-Texte, Mailinhalte oder Zugangsdaten.
- Kein Move, Rename, Delete oder Overwrite des Original-PDFs.

### Entwicklungsprompt

```text
Setze ausschliesslich M10.6 um. Fuehre M10.0 bis M10.5 ab. Ergaenze eine additive,
wiederholbare SQLite-Migration fuer ein inhaltsfreies Extraktionsaudit. Implementiere
Apply fuer genau einen PDF-Hash und den exakt zuvor angezeigten
expected-preview-sha256. Verlange --yes nach ausdruecklichem Nutzerauftrag und
pruefe vor jeder Aenderung PDF-Hash, Datensatzfingerprint, Status,
Extraktorversion und Schutz manueller/bestaetigter Werte. Lehne Regressionen und
unplausible Betraege ab. Aktualisiere das Nextcloud-Jahresregister nur ueber ETag,
SHA und Schemavalidierung; Teilfehler muessen sichtbar und sicher wiederaufnehmbar
sein. Verschiebe oder ueberschreibe kein PDF. Teste Migration, Drift, Konflikt,
Idempotenz, Remote-Ausfall und Konkurrenz hermetisch. Fuehre keinen produktiven
Apply aus und stoppe nach M10.6.
```

## M10.7 – Register-/Backlog-Audit und agentengerechte Bedienung

### Scope

- Read-only Aggregate fuer Statusverteilung, fehlende Pflichtfelder,
  Plausibilitaetsfehler, Extraktorversionen und Pfadabweichungen bereitstellen.
- Die 19 historischen `review`-Zeilen ausserhalb von `Pruefen` sichtbar machen,
  aber keine automatische Verschiebung anbieten.
- Unklassifizierte Legacy-Zeilen, Review-Zeilen und manuelle Korrekturen getrennt
  darstellen.
- Skillbeschreibung so praezisieren, dass der Agent zuerst Status/Audit liest,
  dann Vorschau verwendet und Apply nur nach Darstellung der exakten Aenderung
  und ausdruecklicher Freigabe ausfuehrt.
- Der Agent darf fehlende Metadaten weder aus Erinnerung noch aus Dateinamen oder
  Mailtext erfinden und keine `--yes`-Form autonom ausfuehren.
- Evaluationsbericht und Betriebsanleitung fuer eine begrenzte Backlog-Triage
  dokumentieren.

### Pflichttests und Abnahme

- Aggregate stimmen auf kontrollierten Fixtures exakt und enthalten keine
  Dokumentinhalte.
- Toolrouting verwendet reale CLI-Kommandos statt Tool-IDs oder erfundener Syntax.
- Skilltests pruefen Status -> Audit -> Preview -> explizite Einzeluebernahme.
- Pfadabweichungen bleiben read-only; kein allgemeines Nextcloud-Move-Werkzeug wird
  freigeschaltet.
- Vollstaendige Testanleitung nennt Datenschutz-, Backup- und Rollbackgrenzen.

### Entwicklungsprompt

```text
Setze nur M10.7 um. Fuehre alle bisherigen M10-Abnahmen aus. Ergaenze
datenschutzsichere read-only Rechnungsaggregate fuer Status, fehlende
Pflichtfelder, Plausibilitaetsfehler, Extraktorversion und Pfadabweichung. Melde
historische review-PDFs ausserhalb von Pruefen, verschiebe sie aber nicht und
schalte kein allgemeines Move frei. Aktualisiere den Personal-Assistant-Skill so,
dass er ausschliesslich die registrierte Reihenfolge Status/Audit, Reprocess-
Vorschau und nach expliziter Einzelfreigabe Apply nutzt. Er darf keine Werte aus
Dateiname, Erinnerung, Mailtext oder Ollama erfinden und keine --yes-Form autonom
ausfuehren. Ergaenze Toolrouting-, Datenschutz- und Verhaltensregressionstests,
aktualisiere Betriebs-/Testdokumentation und stoppe nach M10.7.
```

## M10.8 – Gesamt-Abnahme und separater Rolloutvertrag

### Scope

- Einzelabnahmen M10.0 bis M10.7 wiederholen und Baselinevergleich erstellen.
- Zentrale Test-, Rechnungs-, Skill-, Build-, Deployment- und Changelog-
  Dokumentation konsistent aktualisieren.
- Generierte Tool-/Skillvertraege, Befehlsreferenz, Architektur-Inventar und
  Quellmanifest deterministisch pruefen.
- Wheel und Rollenimages bauen, auf private Daten pruefen und hermetische
  Nextcloud-/Register-Konflikte sowie Recovery testen.
- Einen separaten produktiven Rolloutplan dokumentieren: verifiziertes Backup,
  read-only Baseline, Canary-Vorschau, explizite Einzeluebernahme, Nachmessung und
  Rollback. Den Rollout nicht ausfuehren.

### Gesamt-Abnahme

- Kein bestaetigter/manuell korrigierter Wert wurde automatisch geaendert.
- Rechnungsnummern und Betraege sind belegt; False-confirmed und Rechenfehler sind
  gegenueber der sanitisierten Baseline nicht schlechter.
- Prozentwerte erscheinen nie als Geldbetrag und unplausible Dreierkombinationen
  koennen nicht `confirmed` werden.
- Reprocessing-Preview ist byteweise seiteneffektfrei und trennt Jahresbegriffe.
- Apply ist genau-ein-Datensatz, digest-/driftgeschuetzt, auditierbar und explizit
  freigegeben.
- Toolmodus, externe Wirkung und Approval stimmen mit SQLite- und
  Nextcloud-Effekten ueberein.
- `version --verify`, `git diff --check`, `check-repo.sh`, Compose-Validierung,
  Wheel-Pruefung, Image-Build und hermetische Containerintegration sind gruen.
- Kein Secret, PDF, Mailinhalt, produktiver Hash, Dateiname, Register, Datenbank,
  Log oder Laufzeitzustand liegt in Git, Wheel, Image oder CI-Artefakten.

### Entwicklungsprompt

```text
Setze ausschliesslich M10.8 um und aendere keine fachliche Funktion mehr ausser
fuer einen durch Regressionstest belegten M10-Fehler. Fuehre die Einzelabnahmen
M10.0 bis M10.7 sowie den sanitisierten Feldqualitaetsvergleich erneut aus.
Aktualisiere zentrale Rechnungs-, Test-, Build-, Deployment-, Skill- und
Changelog-Dokumentation. Regeneriere abgeleitete Tool-/Skillvertraege,
Befehlsreferenz, Architektur-Inventar und SOURCE_MANIFEST.sha256 nur mit den
vorhandenen Generatoren. Baue und pruefe Wheel und alle Rollenimages, rendere
Compose und teste Registerkonflikt, Teilfehler und Recovery hermetisch. Veraendere
keine Datei unter /srv/openclaw, keinen produktiven Job, kein Register und kein
PDF. Erstelle nur den separaten backup- und rollbackgesicherten Rolloutplan; fuehre
ihn ohne neuen ausdruecklichen Nutzerauftrag nicht aus. Berichte Testzahlen,
Feldmetriken, Sicherheitsfehler, Artefakte, Einschraenkungen und ein eindeutiges
Urteil M10 ABGENOMMEN oder M10 NICHT ABGENOMMEN. Stoppe nach M10 und beginne keinen
produktiven Rollout.
```

## Produktiver Rollout nach M10

Dieser Abschnitt ist nur ein Vertrag fuer einen spaeteren Auftrag, keine aktuelle
Freigabe.

1. Signiertes Image und exakte OCI-Revision pruefen.
2. Alle Writer stoppen und ein verifiziertes lokales Release-Backup erstellen.
3. Fuer Registeraenderungen einen verifizierten externen Nextcloud-Snapshot-Hook
   verlangen.
4. Aktuelle read-only Status-/Audit-Baseline erfassen.
5. Zuerst einen einzelnen datenschutzrechtlich unkritischen `review`-Fall als Canary
   vorschlagen und Jan Altwert, Neuwert, Evidenz und Digest zeigen.
6. Nur nach erneuter ausdruecklicher Freigabe genau diesen Fall uebernehmen.
7. SQLite-Integritaet, Register-ETag/SHA/Schema, Rechnungsstatus und Container-
   Health verifizieren.
8. Weitere Faelle bleiben einzelne, jeweils neu freizugebende Aktionen. Kein
   automatisches Abarbeiten der 48 Prueffaelle.
9. Bei Unsicherheit oder Teilfehler Writer stoppen, Zustand erneut lesen und den
   dokumentierten Recovery-Pfad verwenden. Ein Image-Rollback allein stellt keine
   externe Nextcloud-Aenderung wieder her.

## Reihenfolge der Commits

1. `docs(invoices): establish quality and reprocessing baseline`
2. `fix(invoices): align tool effects and approvals`
3. `feat(invoices): extract evidenced numbers and dates`
4. `feat(invoices): validate typed monetary fields`
5. `feat(invoices): bound OCR and version field fusion`
6. `feat(invoices): add read-only reprocessing preview`
7. `feat(invoices): add audited single-record reprocessing`
8. `docs(invoices): expose backlog audit and agent workflow`
9. `docs(invoices): complete M10 acceptance and rollout contract`

Jeder Commit muss fuer sich `git diff --check` und die jeweils betroffenen Tests
bestehen. Pakete werden nicht zusammengezogen. Ein Fehler wird im verursachenden
Paket oder als eigener klar benannter Fix-Commit korrigiert.

## Globale Definition of Done

- Alle neuen Befehle sind registriert, dokumentiert und verhaltensgetestet.
- Jede behauptete Qualitaetsverbesserung ist durch den sanitisierten Korpus und
  getrennte Feldmetriken belegt.
- False-confirmed, Plausibilitaetsfehler und Datenschutzverletzungen werden nie
  gegen hoehere Abdeckung eingetauscht.
- Vorschau, Apply, Audit und Registersync haben eindeutige, unterschiedliche
  Effekte und Freigaben.
- Manuelle Korrekturen, Original-PDFs und unbekannte Metadaten bleiben erhalten.
- Vollstaendige Repository-, Artefakt-, Container- und Recovery-Abnahme ist gruen.
- Produktive Aktivierung bleibt bis zu einem neuen expliziten Auftrag ausserhalb
  des Entwicklungsabschlusses.
