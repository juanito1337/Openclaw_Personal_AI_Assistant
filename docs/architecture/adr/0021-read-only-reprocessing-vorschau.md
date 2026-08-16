# ADR-0021: Rechnungs-Reprocessing beginnt mit einer gebundenen Read-only-Vorschau

- Status: Accepted
- Datum: 2026-08-16
- Entscheider: Data Maintainers, Security Maintainers
- Betroffene Milestones: M10.5

## Kontext

Der Legacy-Backfill kennt nur Zeilen ohne Extraktionsstatus und verwendet
`--year` fuer eine historische Mischung aus Rechnungs-, Empfangs-, Erstellungs-
und Pfadjahr. Er ist deshalb weder eine vollstaendige Auswahl bestehender
Prueffaelle noch eine sichere Grundlage fuer eine spaetere explizite
Neubewertung. Der normale `Storage` initialisiert und migriert SQLite; die
bisherige Nextcloud-Bridge erzeugt den vollstaendigen Assistant inklusive
Action- und Audit-Storage. Beides widerspricht einem Vertrag, nach dem eine
Vorschau den bestehenden Zustand bytegleich lassen muss.

Eine reine Alt-/Neu-Anzeige ohne Bindung an PDF und aktuellen Datensatz waere
ausserdem kein belastbarer Freigabebezug. Zwischen Vorschau und einer spaeteren
Uebernahme koennten PDF, Datenbankzustand, Regeln oder Extraktor wechseln.

## Entscheidung

`invoices reprocess` ist ein eigenes registriertes Read-tool und kein Alias fuer
Backfill. Es verlangt `--dry-run`, genau einen Status `review` oder
`unclassified`, ein `--source-year` und ein Limit zwischen 1 und 100.
`confirmed` und `confirmed-manual` werden in der SQL-Auswahl und erneut bei der
Vorschlagserzeugung ausgeschlossen.

Das Quelljahr ist das vorhandene `register_year`; nur wenn es fehlt, wird das
Jahrsegment des bestehenden Nextcloud-Archivpfads verwendet. Empfangsjahr und
neu erkanntes Rechnungsjahr sind reine, getrennt benannte Ausgabeinformationen
und beeinflussen die Auswahl nicht. Datensaetze ohne Register- und Pfadjahr
werden nicht geraten.

Die Invoice-SQLite wird ueber SQLite `mode=ro` und `query_only` ohne den
migrierenden `Storage` geoeffnet. Der PDF-Reader laedt die exakte Datei direkt
ueber das konfigurierte Nextcloud-`read`-Recht und prueft Rechnungsroot sowie
Ressourcenwurzel, ohne PersonalAssistant, ActionPlan, Audit oder Registerdienst
zu erzeugen. ClamAV bleibt vor der Extraktion fail-closed; Cache und Scanpfad
liegen ausschliesslich in einem nach dem Lauf entfernten temporaeren Verzeichnis.

Die Ausgabe enthaelt pro Feld begrenzte Alt-/Neudaten, Konfidenz, Evidenztyp und
Quelle, aber keinen PDF-/OCR-Rohtext, keine freien Issue-Texte, keine Remote-
Antwort und kein Geheimnis. Konflikte muessen der geschlossenen typisierten Form
entsprechen. Die Klassifikation ist deterministisch: identischer Fachzustand ist
`unchanged`, nachweisbar hoeherer Status/Pflichtfeldscore `improved`, Verlust,
schlechterer Score oder Konflikt `regressed`; sonst bleibt die Aenderung
`still-review`.

Ein `preview_sha256` bindet Schemaversion, tatsaechlichen PDF-SHA-256, den
vollstaendigen aktuellen Invoice-/Nachrichten-Datensatz, Extraktorversion und den
kanonischen Vorschlag. Volatile Laufzeiten und temporaere Scannerwerte sind nicht
Bestandteil des Vorschlags. M10.5 definiert keinen Apply-Pfad.

## Konsequenzen

Review- und unklassifizierte Altzeilen lassen sich getrennt und nachvollziehbar
neu bewerten, ohne SQLite, PDF, Archivpfad, Register-ETag oder Audit zu aendern.
Jahresabweichungen werden sichtbar statt durch einen ueberladenen Parameter
verdeckt. Der Digest schafft den spaeter benoetigten Vergleichswert, erteilt aber
selbst keine Schreibfreigabe.

Ein manueller Vorschauaufruf ist fachlich read-only, benoetigt jedoch weiterhin
Nextcloud-Lesezugriff, ClamAV und gegebenenfalls lokale OCR-Rechenzeit. Fehler
werden pro Hash mit inhaltsfreien Codes gemeldet. Ein spaeterer Apply benoetigt
einen separaten Tool-, Audit-, Konflikt- und Freigabevertrag in M10.6.

## Verifikation

Synthetische Verhaltensregressionen belegen getrennte Statusauswahl, zweistufigen
Schutz bestaetigter Werte, vier gleichzeitig abweichende Jahresangaben, alle vier
Klassifikationen, deterministische Digestwiederholung und Digestdrift fuer PDF,
Altzustand, Extraktor und Vorschlag. Vorher-/Nachhervergleiche sichern SQLite-
Bytes, PDF-Bestand, Register-ETag und Audit-Abwesenheit; ein Marker im
synthetischen PDF-Evidenztext darf in keiner JSON-Ausgabe erscheinen.
