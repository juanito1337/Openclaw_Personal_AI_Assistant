# ADR-0020: Rechnungs-OCR bleibt lokal, begrenzt und feldweise

- Status: Accepted
- Datum: 2026-08-16
- Entscheider: Data Maintainers, Security Maintainers
- Betroffene Milestones: M10.4

## Kontext

Der bisherige Fallback startete OCR aufgrund einer globalen Textqualitaet oder
eines unsicheren Datums und renderte pauschal die ersten `max_pages`. Ein bereits
gut lesbarer Beleg konnte dadurch unnoetig verarbeitet werden; bei langen
Rechnungen blieb gerade die haeufig summenrelevante Schlussseite ausserhalb der
Auswahl. Laufzeit galt je Teilprozess statt als Gesamtbudget. Engine-, Regel- und
Scanneridentitaet waren im Extraktionsergebnis nicht gemeinsam nachvollziehbar.

Die bisherige Fusion konnte ausserdem einen abweichenden OCR-Wert zugunsten einer
hoeher bewerteten nativen Textschicht still ignorieren. Eine Gesamtpunktzahl ist
kein geeigneter Nachweis dafuer, welcher von zwei widerspruechlichen Feldwerten
richtig ist.

## Entscheidung

`pdftotext` bleibt der erste Pfad. OCR wird nur gestartet, solange mindestens
eines der Pflichtfelder Rechnungsdatum, Rechnungsnummer, Bruttobetrag oder
dokumentbelegter Rechnungssteller unbrauchbar ist. Ein nur aus Mailabsender oder
Absenderdomain abgeleiteter Lieferant gilt dabei nicht als Dokumentbeleg.
Optionale Felder allein aktivieren keine OCR.

Der lokale OCR-Pfad ist auf `pdfinfo`, `pdftoppm` und Tesseract begrenzt. Er
besitzt unabhaengige Budgets fuer PDF-Bytes, Seiten, DPI, den gesamten Lauf,
gerenderte Bytes und Ausgabezeichen. Laengere Dokumente verwenden innerhalb des
bestehenden Seitenbudgets die vorderen Seiten plus die letzte Seite. Fehlendes
Binary, fehlende Sprache, unlesbare PDF, Timeout oder Budgetverletzung endet
fail-closed in Review; eine externe OCR ist kein Ersatzpfad.

Native und OCR-Ergebnisse werden pro Feld fusioniert. Ein unbrauchbarer nativer
Wert darf durch einen brauchbaren OCR-Wert ergaenzt werden. Zwei glaubwuerdige,
abweichende Werte leeren dagegen das Feld und erzeugen
`fusion:<feld>-conflict`. Die Gesamt-Konfidenz kann diesen Reviewgrund nicht
ueberstimmen.

Das technische Teilergebnis traegt Schema-, Extraktor- und Regelversion, lokale
Engine, Sprachen, Scanneridentitaet, Eingabegroesse, Laufzeiten, OCR-Ausloeser,
Seitenauswahl, Renderumfang und inhaltsfreie Fusionsentscheidungen. Dokumenttext,
OCR-Text und extrahierte Feldwerte werden dort nicht dupliziert. Der bestehende
ClamAV-Gate bleibt vor jeder produktiven Extraktion und wird nicht abgeschwaecht.

## Konsequenzen

Vollstaendige Text-PDFs sparen OCR-Zeit. Bild- und Misch-PDFs koennen fehlende
Pflichtfelder lokal ergaenzen, ohne das Seitenbudget zu erhoehen. Schlussseiten
werden bei langen Rechnungen reproduzierbar erreicht. Widersprueche erzeugen
bewusst mehr Review statt scheinbarer Bestaetigung.

Die festen Budgets sind Sicherheitsgrenzen, keine neuen Qualitaetsziele. Die
gemessene Laufzeit bleibt hardwareabhaengig und wird deshalb dokumentiert, aber
nicht als willkuerliches Performance-Gate verwendet. M10.4 aendert weder
SQLite-Schema noch vorhandene Datensaetze, Nextcloud-Dateien oder Jobs und fuehrt
kein historisches Reprocessing aus.

## Verifikation

Ein vollstaendig synthetischer Korpus und Verhaltensregressionen decken
Textschicht-, bildbasierte, gemischte, mehrseitige und korrupte PDFs,
Seitenauswahl, Binary-/Sprachfehler, Timeout, Groessenbudget, Konflikt-Review,
inhaltsfreie technische Identitaet und den ClamAV-Stopp vor dem Extraktor ab. Ein
separater Benchmark erzeugt sein Drei-Seiten-PDF temporaer und meldet nur
Werkzeug-, Budget-, Laufzeit- und Ressourcendaten.
