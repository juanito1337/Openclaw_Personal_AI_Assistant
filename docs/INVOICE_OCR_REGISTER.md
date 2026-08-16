# Rechnungs-Texterkennung und Jahresregister (R26)

## Ziel

Das Rechnungswerkzeug archiviert erkannte Rechnungs-PDFs create-only in Nextcloud. Fuer die Ordnerstruktur ist allein ein belastbar aus dem Dokument erkanntes Rechnungsdatum entscheidend. Firma, Betrag, Rechnungsnummer oder Kategorie duerfen unvollstaendig sein, ohne die Rechnung deshalb in einen falschen Monatsordner zu verschieben.

Das produktive Jahresregister existiert ausschliesslich im jeweiligen Nextcloud-Jahresordner:

```text
Assistent/Rechnungen/YYYY/Rechnungen_YYYY.csv
```

Es wird keine dauerhafte lokale CSV-Kopie gefuehrt. Kurzlebige, geschuetzte Action-Payloads werden unmittelbar nach dem Transfer geloescht.

## Erkennungsstufen

1. `pdftotext` liest zuerst die vorhandene PDF-Textschicht mit Layout-Erhalt.
2. Rechnungsdatum, Rechnungsnummer, Bruttobetrag und ein dokumentbelegter
   Rechnungssteller werden getrennt auf Nutzbarkeit geprueft. Sind alle vier
   Pflichtfelder brauchbar, wird keine OCR ausgefuehrt; optionale Felder allein
   loesen sie nicht aus.
3. Lokale OCR mit `pdfinfo`, `pdftoppm` und Tesseract ist nur der Fallback fuer
   die konkret fehlenden oder unbrauchbaren Pflichtfelder. Sie verwendet keine
   externe OCR- oder Dokumenten-API.
4. Native Textwerte bleiben vorrangig. OCR darf ein unbrauchbares Feld ergaenzen,
   aber keinen brauchbaren nativen Wert still ersetzen.
5. Glaubwuerdige Widersprueche zwischen nativer Textschicht und OCR werden
   feldweise geleert und mit `fusion:<feld>-conflict` als `Pruefen`
   gekennzeichnet. Eine hohe Gesamtkonfidenz darf diesen Konflikt nicht verdecken.
6. Rechnungsnummer und Rechnungsdatum entstehen aus typisierten Kandidaten mit
   Quelle, Rohwert, normalisiertem Wert, begrenztem Evidenztyp, Konfidenz und
   Ausschlussgrund. Die Evidenzzeile ist auf 300 Zeichen begrenzt.
7. Nummernanker sind unter anderem `Rechnungsnummer`, `Rechnung Nr.`, `Invoice
   Number`, `Invoice No.` und `Beleg-Nr.`. Der Wert muss auf derselben oder exakt
   der naechsten unbeschrifteten Zeile stehen. Unicode, Bindestrich,
   Schraegstrich, alphanumerische Werte und typische OCR-Zeichenabstaende werden
   deterministisch normalisiert.
8. Kunden-, Bestell-, Liefer-, Vertrags-, Telefon-, Steuer- und
   Trackingnummern sowie IBAN werden als eigene ausgeschlossene Rollen erfasst.
   Ein datumsfoermiger Wert hinter `Rechnung NR.` bleibt sichtbarer, aber
   ausgeschlossener Nummernkandidat.
9. Rechnungsdaten werden unter anderem an `Rechnungsdatum`, `Datum der
   Rechnung`, `Rechnung vom`, `Datum`, `Invoice date`, `Belegdatum` und
   `Ausstellungsdatum` erkannt. Leistungs-, Liefer-, Bestell-, Zahlungs- und
   Faelligkeitsdaten sind typisiert ausgeschlossen. Mehrere gleich plausible
   Rechnungsnummern oder Rechnungsdaten fuehren fail-closed zu `review`.
10. Betragskandidaten behalten die getrennten Rollen Zahlbetrag, Brutto, Netto,
    Steuerbetrag, Steuersatz, Zwischensumme, Rabatt, Abschlag, Gutschrift und
    Einzelpreis. Prozentwerte sind immer ausgeschlossen und ein Wert wird nie
    allein deshalb ausgewaehlt, weil er der groesste ist.
11. Deutsche und englische Dezimal-/Tausenderformate werden in Cent, EUR, USD,
    GBP und CHF in ISO-Waehrungscodes normalisiert. Vorzeichen und Waehrungen
    werden nicht still korrigiert. Mailbetreff, Dateiname und Ollama sind keine
    Betragsquellen.
12. Vollstaendige Brutto/Netto/Steuer-Tripel duerfen um hoechstens zwei Cent
    abweichen. Rechen-, Summen-, Vorzeichen- oder Waehrungskonflikte sowie Steuer
    groesser als Brutto erzeugen einen typisierten `amount:*`-Reviewgrund.

## OCR-Budgets und Seitenauswahl (M10.4)

Die Standardgrenzen sind Sicherheitsbudgets und keine Aussage ueber die fachliche
Erkennungsqualitaet:

| Ressource | Standardgrenze |
| --- | ---: |
| PDF-Eingabe | 25.000.000 Byte |
| OCR-Seiten | 2 |
| Aufloesung | 300 DPI |
| gesamter OCR-Lauf einschliesslich Seitenpruefung, Rendering und Erkennung | 180 Sekunden |
| gerenderte PNG-Daten zusammen | 50.000.000 Byte |
| OCR-Ausgabe zusammen | 100.000 Zeichen |

Bei mehr Seiten als dem Budget werden zuerst die vorderen Seiten und zuletzt die
letzte Seite gewaehlt. Mit dem unveraenderten Zwei-Seiten-Budget bedeutet das
`[1, letzte Seite]`; bei drei Seiten werden also `[1, 3]` gelesen. Das Budget wird
nicht automatisch vergroessert. `invoices status` zeigt Binaries, Sprachen,
Seitenauswahl und alle aktiven Grenzen.

Das technische Extraktionsergebnis verwendet Schema 1 sowie die versionierten
Identitaeten `extractor_version=m10.4` und `ruleset_version=2026-08-16.1`. Es
enthaelt nur Engine-/Scanneridentitaet, Sprachen, Eingabegroesse, Laufzeiten,
Seitennummern, gerenderte Bytes, OCR-Ausloeser und feldweise Fusionsentscheidungen.
Rechnungsnummer, Lieferant, Betraege, PDF-/OCR-Text und sonstiger Dokumentinhalt
werden in diesem technischen Abschnitt nicht wiederholt.

Der physische PDF-Dateiname darf nur einen bereits beschriftet aus PDF- oder
OCR-Text extrahierten Rechnungsnummernkandidaten stuetzen. Eine unbeschriftete
Nummer im Dateinamen erzeugt keinen Feldwert und keine Bestaetigung. M10.2 und
M10.3 fuehren keine Datenbankmigration und keine Neubewertung historischer Zeilen
aus.

Es wird kein fehlender Dokumentwert aus dem E-Mail-Eingangsdatum geraten. Das Eingangsdatum bleibt ein separates Registerfeld und ist nur der sichere Ablage-Fallback, wenn das Rechnungsdatum selbst nicht belastbar ist.

## Archivierung

Sobald das Rechnungsdatum sicher erkannt ist, wird die PDF nach diesem Datum abgelegt, auch wenn weitere Metadaten fehlen:

```text
Assistent/Rechnungen/YYYY/MM/<Rechnungsdatum>_<Rechnungssteller>_<Rechnungsnummer>_<Hash>.pdf
```

Fehlen Rechnungsnummer oder Rechnungssteller, werden sichere Platzhalter im Dateinamen verwendet. Die Datei bleibt dennoch im richtigen Jahr und Monat.

Nur wenn das Rechnungsdatum selbst unsicher ist, wird die Rechnung unter dem Eingangsjahr und Eingangsmonat zur Pruefung abgelegt:

```text
Assistent/Rechnungen/Pruefen/<Eingangsjahr>/<Eingangsmonat>/PRUEFEN_<Eingangsdatum>_....pdf
```

Unvollstaendige Zusatzdaten fuehren zum Status `invoice-archived-metadata-review`, nicht zur Verschiebung der Mail oder PDF in den allgemeinen Pruefordner.

## Jahresregister

Nach jeder neu archivierten Rechnung wird das Jahresregister zwingend aus der Rechnungsdatenbank neu erzeugt und in Nextcloud synchronisiert. Auch ein Dublettenlauf versucht die Synchronisation erneut, sodass ein zuvor fehlgeschlagener CSV-Transfer repariert werden kann.

Pfad:

```text
Assistent/Rechnungen/2026/Rechnungen_2026.csv
```

Format:

- UTF-8 mit BOM fuer Excel,
- Semikolon als Feldtrenner,
- CRLF-Zeilenenden,
- Dezimalkomma,
- eine Zeile je eindeutigem PDF-SHA256,
- keine zweite lokale Registerdatei.

Spalten:

- Status
- Rechnungsdatum
- Eingangsdatum
- Rechnungsnummer
- Rechnungssteller
- Kategorie
- Nettobetrag
- USt-Betrag
- Bruttobetrag
- Waehrung
- Faelligkeitsdatum
- Erkennung
- Konfidenz
- Nextcloud-Pfad
- Originaldatei
- SHA256

Die Kategorie ist eine nachvollziehbare Arbeitshilfe, aber kein verbindliches Buchungskonto, DATEV-Steuerschluessel oder steuerliche Beratung. Unsichere Kategorien bleiben `Ungeklaert` und der Datensatz erhaelt den Status `Pruefen`.

## Sichere Aktualisierung in Nextcloud

PDFs bleiben create-only und werden niemals ueberschrieben. Fuer exakt die verwaltete Datei `.../YYYY/Rechnungen_YYYY.csv` existiert eine eng begrenzte Ausnahme:

- Pfad, Jahr, Dateiname, CSV-Schema und SHA-256 werden geprueft.
- Existiert die CSV, wird ihr aktueller ETag gelesen.
- Der PUT erfolgt mit `If-Match`; eine parallele Aenderung fuehrt zu HTTP 412 und einem sichtbaren Fehler statt zu stillem Datenverlust.
- Existiert die CSV noch nicht, wird sie mit `If-None-Match: *` angelegt.
- Jede andere Datei bleibt vom globalen Ueberschreibverbot geschuetzt.

Kann die Jahres-CSV nicht aktualisiert werden, ist die Rechnungsverarbeitung nicht erfolgreich abgeschlossen. Der Fehler wird protokolliert und die Mail gelangt in den Fehlerpfad. Die bereits create-only archivierte PDF bleibt erhalten; ein erneuter Lauf kann das Register anhand der Dublette reparieren.

## Agentenbefehle

```bash
./scripts/assistant.sh invoices status
./scripts/assistant.sh invoices audit
./scripts/assistant.sh invoices list --year 2026 --limit 100
./scripts/assistant.sh invoices review --limit 100
./scripts/assistant.sh invoices export --year 2026 --dry-run
./scripts/assistant.sh invoices export --year 2026 --yes
./scripts/assistant.sh invoices reprocess --status "review" --source-year 2026 --limit 100 --dry-run
```

`invoices export --dry-run` rendert die CSV nur im Speicher. Es schreibt weder
eine lokale Datei noch SQLite oder Nextcloud. Erst `--yes` gibt das bedingte
Anlegen oder Ersetzen der festen Datei `Rechnungen_2026.csv` im
Nextcloud-Jahresordner frei. Ein Aufruf ohne `--dry-run` und ohne `--yes` endet
fail-closed vor einer Schreibwirkung. Die alte Option `--nextcloud` wird nur noch
aus Kompatibilitaetsgruenden akzeptiert; sie ist keine Freigabe und aendert diese
Grenze nicht.

### Wirkungsvertrag

| Pfad | SQLite | Nextcloud-PDF | verwaltetes Jahresregister | Freigabe |
| --- | --- | --- | --- | --- |
| `invoices audit` | strikt read-only; nur Aggregate | nicht geoeffnet | nicht geoeffnet | keine |
| `invoices export ... --dry-run` | unveraendert | unveraendert | unveraendert; nur In-Memory-Vorschau | keine |
| `invoices export ... --yes` | unveraendert | unveraendert | bedingtes Anlegen/Ersetzen | ausdrueckliches `--yes` |
| `invoices backfill ... --dry-run` | unveraendert | nur gelesen, nie ersetzt | unveraendert | keine |
| `invoices backfill ... --yes` | Extraktionszeilen werden einzeln aktualisiert | nur gelesen, nie ersetzt | betroffene Jahre werden bedingt ersetzt | ausdrueckliches `--yes` |
| `invoices reprocess ... --dry-run` | strikt read-only geoeffnet | nur gelesen, nie ersetzt | nicht geoeffnet | keine |
| `invoices correct ... --yes` | genau der adressierte Hash wird aktualisiert | unveraendert | altes und neues Jahr werden bedingt ersetzt | ausdrueckliches `--yes` |
| konfigurierter Mail-Rechnungslauf | Archiv-/Metadatenstatus wird aktualisiert | create-only | zugehoeriges Jahr wird bedingt synchronisiert | zuvor freigegebene Workflow-/Ressourcenkonfiguration |

Die SQLite-Aenderungen von produktivem Backfill und Korrektur werden vor der
anschliessenden Nextcloud-Synchronisation gespeichert. Beide Systeme bilden keine
gemeinsame Transaktion. Schlaegt die Registeraktualisierung fehl, meldet der
Befehl einen Fehler; er behauptet keine automatische Ruecknahme der lokalen
Aenderung. Ein erneuter produktiver Lauf benoetigt weiterhin ausdrueckliche
Freigabe und den dokumentierten Backup-Vertrag.

Metadaten duerfen nur nach ausdruecklichem Auftrag korrigiert werden:

```bash
./scripts/assistant.sh invoices correct \
  --hash "<SHA256>" \
  --date "2026-07-15" \
  --number "RE-4711" \
  --supplier "Beispiel GmbH" \
  --category "Software/IT" \
  --gross "1190,00" \
  --net "1000,00" \
  --tax "190,00" \
  --yes
```

Nach einer Korrektur werden alle betroffenen Jahresregister direkt in Nextcloud
neu synchronisiert. `correct` ist deshalb trotz genau einer lokalen
Metadatenzeile ein extern schreibendes Werkzeug.

## Bestehende Archive nachtragen

Der Backfill liest ausschliesslich PDFs innerhalb des konfigurierten Rechnungsordners. Er benoetigt das `read`-Recht der Nextcloud-Dateiressource und fuehrt vor der Auswertung den Host-Virenscan aus.

Vorschau ohne Datenbankaenderung und ohne CSV-Schreibzugriff:

```bash
./scripts/assistant.sh invoices backfill --year 2026 --limit 500 --dry-run
```

Produktive Uebernahme nach ausdruecklichem Auftrag:

```bash
./scripts/assistant.sh invoices backfill --year 2026 --limit 500 --yes
```

Alte, nicht eindeutig lesbare Rechnungen werden mit Status `Pruefen` in das Jahresregister aufgenommen. Bereits archivierte PDFs werden beim Backfill nicht verschoben oder ersetzt.

Die Vorschau liest PDF-Dateien und fuehrt den Virenscan sowie die Extraktion aus,
speichert das Ergebnis aber nicht. Der produktive Pfad speichert dagegen die
SQLite-Ergebnisse und synchronisiert danach die betroffenen Nextcloud-Register;
sein Toolmodus ist daher `write` mit externer Wirkung.

## Aggregierter Backlog-Audit und Triage (M10.7)

`invoices audit` ist die datenschutzsichere Bestandsaufnahme vor jeder
historischen Neubewertung:

```bash
./scripts/assistant.sh invoices status
./scripts/assistant.sh invoices audit
```

Der Audit oeffnet die Rechnungs-SQLite mit `mode=ro` und `query_only`. Bei einer
geschlossenen Datenbank ohne WAL wird zusaetzlich der unveraenderliche SQLite-
Modus verwendet, damit selbst keine neuen Sidecar-Dateien entstehen. Ein
vorhandenes WAL wird dagegen in die aktuelle konsistente Lesesicht einbezogen.
Nextcloud, PDFs, Jahresregister und der schreibende Extraktionsaudit werden nicht
geoeffnet.

Die Ausgabe enthaelt ausschliesslich Aggregate:

- Archiv- und Extraktionsstatus;
- getrennte Kohorten fuer unklassifizierte Legacy-Zeilen, Review, bestaetigte
  Zeilen, manuelle Korrekturen und andere technische Zustaende;
- fehlende Pflichtfelder sowie Datums- und Betragsplausibilitaet je Kohorte;
- ausschliesslich typisierte `amount:*`-Reviewgruende;
- eng formatgepruefte Extraktor-/Regelversionen und aggregierte Quelljahre;
- Reviewzeilen innerhalb, ausserhalb oder ohne konfigurierten Pruefpfad sowie
  Pfad-/Registerjahresabweichungen.

Hashes, Message-IDs, Dateinamen, Pfade, Rechnungsnummern, Lieferanten,
Dokumenttexte und freie Fehlertexte werden nie ausgegeben. Unbekannte oder frei
formatierte Versionswerte erscheinen nur als `invalid-redacted`. Der operative
M10-Ausgangswert von 19 Reviewzeilen ausserhalb `Pruefen` wird damit ueber
`review_outside_review_subfolder` sichtbar, aber nicht hart kodiert.

### Begrenzte Bedienreihenfolge

1. `invoices status` und danach `invoices audit` lesen.
2. Genau eine Auditkohorte `review` oder `unclassified` und genau ein dort
   ausgewiesenes Quelljahr fuer `invoices reprocess ... --dry-run` auswaehlen.
3. Fuer genau einen Vorschlag Hash, `preview_sha256`, Klassifikation,
   Feldaenderungen und Konflikte vollstaendig darstellen.
4. Ohne ausdrueckliche Einzelfreigabe stoppen. Erst ein neuer Nutzerauftrag darf
   denselben unveraenderten Vorschlag mit `invoices reprocess-apply ... --yes`
   ausfuehren.

Pfadabweichungen sind nur ein Triagehinweis. M10.7 bietet weder einen Invoice-
Move noch eine allgemeine Nextcloud-Verschiebefreigabe. Fehlende Werte werden
nicht aus Erinnerung, Dateiname, Mailtext oder Ollama ergaenzt. Vor einem
produktiven Apply gelten weiterhin verifiziertes lokales Backup und fuer
vollstaendige externe Ruecknahme ein verifizierter Nextcloud-Snapshot-Hook. Ein
Image-Rollback allein stellt keine bereits ersetzte Registerdatei wieder her.

## Read-only Reprocessing-Vorschau (M10.5)

Der neue Reprocessing-Pfad ist kein Alias fuer den Legacy-Backfill. Er waehlt
entweder exakt `review` oder exakt einen leeren, als `unclassified` dargestellten
Extraktionsstatus. `confirmed` und `confirmed-manual` sind sowohl in der
read-only SQL-Auswahl als auch unmittelbar vor dem Erzeugen eines Vorschlags
ausgeschlossen. Pro Aufruf werden hoechstens 100 Ergebnisse ausgegeben:

```bash
./scripts/assistant.sh invoices reprocess \
  --status "<review|unclassified>" \
  --source-year 2026 \
  --limit 100 \
  --dry-run
```

`source_year` ist bewusst kein Rechnungsjahr. Es bezeichnet das bestehende
`register_year`; fehlt dieses, gilt das vierstellige Jahrsegment des vorhandenen
Archivpfads. Ein Datensatz ohne beide Angaben wird nicht still ueber sein
Empfangsjahr zugeordnet. Die Ausgabe weist `register_year`, `path_year`,
`received_year` und das neu erkannte `recognized_invoice_year` getrennt aus und
nennt die verwendete Quelljahrbasis.

Jedes Feld zeigt begrenzten Alt-/Neuwert, Konfidenz, Evidenztyp und Quelle. Rohes
PDF-/OCR-Zeilenmaterial, allgemeine Extraktionsfehler, Zugangsdaten und
Nextcloud-Antwortinhalte werden nicht ausgegeben. Nur typisierte Konfliktcodes
gelangen in die Vorschau. Die Bewertung bedeutet:

- `unchanged`: Werte und fachlicher Status sind identisch;
- `improved`: bestaetigter Status, Pflichtfeldabdeckung oder belegte Konfidenz
  steigen ohne Verlust und Konflikt;
- `regressed`: ein Pflichtwert geht verloren, die Qualitaet sinkt oder ein
  typisierter Konflikt bleibt;
- `still-review`: der Vorschlag aendert sich, erreicht aber keine belegte
  Verbesserung und bleibt ein Prueffall.

Der `preview_sha256` bindet Schema, tatsaechlichen PDF-SHA-256, den vollstaendigen
aktuellen Datenbank-/Nachrichtenstand, die Extraktorversion und den stabilen
Neuvorschlag. Laufzeiten und temporaere Scannerwerte sind nicht Teil des
Vorschlags, sodass identische Eingaben deterministisch bleiben. Eine Aenderung an
PDF-Hash, Altzustand, Extraktor oder Vorschlag erzeugt einen anderen Digest.

Die Mail-SQLite wird mit SQLite `mode=ro` und `query_only` geoeffnet. Der
Nextcloud-Zugriff verwendet nur das konfigurierte `read`-Recht und einen direkten
GET innerhalb des Rechnungsroots; ActionPlan-, Audit- und Registerdienste werden
nicht geoeffnet. ClamAV bleibt fail-closed, verwendet fuer die Vorschau aber nur
einen nach dem Lauf entfernten temporaeren Cache. Original-PDF, Archivpfad,
Register-ETag, SQLite und Audit bleiben unveraendert.

## Explizite Einzeluebernahme (M10.6)

Nur ein zuvor angezeigter, weiterhin unveraenderter Vorschlag darf uebernommen
werden. Die Freigabe gilt gleichzeitig fuer genau einen PDF-Hash und genau einen
`preview_sha256`; es gibt bewusst weder Limit noch Bulk-Option:

```bash
./scripts/assistant.sh invoices reprocess-apply \
  --hash "<SHA256>" \
  --expected-preview-sha256 "<Digest>" \
  --yes
```

`--yes` ist die Approval-Grenze
`explicit-user-single-invoice-reprocess` und darf nur nach einem ausdruecklichen
Nutzerauftrag gesetzt werden. Vor der ersten SQLite-Aenderung liest der Befehl
das archivierte PDF erneut read-only, prueft seinen SHA-256, fuehrt ClamAV
fail-closed aus, extrahiert mit der aktuellen Version und berechnet dieselbe
Vorschau erneut. Hash-, Datensatz-, Status-, Extraktor- oder Vorschlagsdrift
bricht ohne Aenderung ab. Dasselbe gilt fuer bestaetigte/manuell korrigierte
Zeilen, `unchanged`, `regressed`, `still-review`, offene Konflikte und
unplausible Betragsarithmetik.

Der erfolgreiche lokale Schritt aendert genau eine `invoices`-Zeile und erzeugt
in derselben SQLite-Transaktion genau einen inhaltsfreien
`invoice_reprocess_audit`-Datensatz. Das Audit enthaelt nur Hashes,
Extraktorversion, Approval, Status, betroffene Jahre, Versuchszahl und Ergebnis;
keinen PDF-/OCR-Text, Mailinhalt, Pfad oder Zugangsdaten. Die additive Migration
auf Schema 4 behaelt bestehende Rechnungs-, Register- und Korrekturdaten.

Anschliessend rendert der bestehende Registerpfad jedes betroffene alte/neue Jahr
aus dem aktuellen SQLite-Stand und schreibt ausschliesslich das feste verwaltete
CSV ueber ETag, erwarteten SHA-256 und Schemavalidierung. Ein Konflikt oder
Ausfall liefert `local-applied-register-failed` und `retry_safe=true`; er darf
nicht als Gesamterfolg oder automatischer lokaler Rollback beschrieben werden.
Eine Wiederholung mit demselben unveraenderten Hash/Digest setzt nur diesen
Auditvorgang idempotent fort. `register-sync-in-progress` zeigt einen aktiven
konkurrierenden Abgleich an.

| Reprocessing-Befehl | SQLite | Original-PDF/Pfad | Register | Approval |
| --- | --- | --- | --- | --- |
| `invoices reprocess ... --dry-run` | strikt read-only | nur gelesen, nie ersetzt | nicht geoeffnet | keine |
| `invoices reprocess-apply ... --yes` | exakt eine Zeile plus inhaltsfreies Audit | nur gelesen, nie bewegt/ersetzt | ETag-/SHA-/Schemageschuetzter Abgleich | expliziter Einzelauftrag |

## Abhaengigkeiten

Der Statusbefehl zeigt, ob folgende Programme vorhanden sind:

```text
pdftotext
pdfinfo
pdftoppm
tesseract
Tesseract-Sprachen deu und eng
```

Auf Debian/Ubuntu koennen fehlende Pakete durch einen Administrator installiert werden:

```bash
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng
```

Der Agent installiert keine Systempakete selbststaendig. Fehlt OCR, werden unsichere Dokumente nicht geraten, sondern zur Pruefung markiert.
