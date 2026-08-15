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
2. Solange die native Textschicht brauchbar ist und ein belastbares Rechnungsdatum liefert, wird keine OCR ausgefuehrt. Fehlende Zusatzfelder allein loesen keine OCR aus.
3. OCR mit `pdftoppm` und Tesseract ist nur der Fallback, wenn die Textschicht unbrauchbar ist oder das Rechnungsdatum nicht sicher erkannt wurde.
4. Native Textwerte bleiben vorrangig. OCR darf fehlende oder sehr schwache Felder ergaenzen, aber keine guten Textwerte still ersetzen.
5. Widersprechen sich native Textschicht und OCR bei Rechnungsdatum, Rechnungsnummer oder Bruttobetrag mit hoher Konfidenz, wird das betroffene Feld geleert und als `Pruefen` gekennzeichnet.
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

Der physische PDF-Dateiname darf nur einen bereits beschriftet aus PDF- oder
OCR-Text extrahierten Rechnungsnummernkandidaten stuetzen. Eine unbeschriftete
Nummer im Dateinamen erzeugt keinen Feldwert und keine Bestaetigung. M10.2 fuehrt
keine Datenbankmigration und keine Neubewertung historischer Zeilen aus.

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
./scripts/assistant.sh invoices list --year 2026 --limit 100
./scripts/assistant.sh invoices review --limit 100
./scripts/assistant.sh invoices export --year 2026 --dry-run
./scripts/assistant.sh invoices export --year 2026 --yes
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
| `invoices export ... --dry-run` | unveraendert | unveraendert | unveraendert; nur In-Memory-Vorschau | keine |
| `invoices export ... --yes` | unveraendert | unveraendert | bedingtes Anlegen/Ersetzen | ausdrueckliches `--yes` |
| `invoices backfill ... --dry-run` | unveraendert | nur gelesen, nie ersetzt | unveraendert | keine |
| `invoices backfill ... --yes` | Extraktionszeilen werden einzeln aktualisiert | nur gelesen, nie ersetzt | betroffene Jahre werden bedingt ersetzt | ausdrueckliches `--yes` |
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

## Abhaengigkeiten

Der Statusbefehl zeigt, ob folgende Programme vorhanden sind:

```text
pdftotext
pdftoppm
tesseract
Tesseract-Sprachen deu und eng
```

Auf Debian/Ubuntu koennen fehlende Pakete durch einen Administrator installiert werden:

```bash
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng
```

Der Agent installiert keine Systempakete selbststaendig. Fehlt OCR, werden unsichere Dokumente nicht geraten, sondern zur Pruefung markiert.
