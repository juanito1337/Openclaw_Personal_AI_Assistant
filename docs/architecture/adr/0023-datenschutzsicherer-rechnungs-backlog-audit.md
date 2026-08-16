# ADR-0023: Rechnungs-Backlog wird nur aggregiert und read-only auditiert

- Status: Accepted
- Datum: 2026-08-16
- Entscheider: Data Maintainers, Security Maintainers
- Betroffene Milestones: M10.7

## Kontext

Die Reprocessing-Vorschau aus ADR-0021 benoetigt einen exakten Status und ein
Quelljahr, zeigt danach aber einzelne Rechnungswerte und liest das zugehoerige PDF.
Fuer eine erste Bestandsaufnahme ist dieser Zugriff zu breit. Gleichzeitig waren
historische `review`-Zeilen ausserhalb des heutigen `Pruefen`-Pfads, leere
Legacy-Status und manuelle Korrekturen bisher nur ueber eine vollstaendige Liste
und nachgelagerte lokale Filter sichtbar.

Ein Agent darf aus solchen Listen keine fehlenden Werte erraten, keine
Pfadbereinigung ableiten und keine schreibende Einzeluebernahme als Teil einer
reinen Bestandsaufnahme ausfuehren.

## Entscheidung

`invoices audit` ist ein registriertes Read-tool ohne Approval und externe
Wirkung. Es liest aus `invoices` nur die fuer Aggregate notwendigen Spalten und
oeffnet SQLite read-only mit `query_only`. Ohne vorhandenes WAL wird die Datei
zusaetzlich unveraenderlich geoeffnet; ein vorhandenes WAL bleibt Teil der
aktuellen konsistenten Lesesicht. Der Befehl oeffnet weder Nextcloud noch PDFs,
Register oder den schreibenden Reprocessing-Audit.

Die Ausgabe trennt unklassifizierte Legacy-Zeilen, Review, bestaetigte Werte,
manuelle Korrekturen und andere Zustaende. Sie aggregiert Pflichtfeldluecken,
Datums-/Betragsplausibilitaet, typisierte Betragsgruende, eng formatgepruefte
Extraktor-/Regelversionen, Quelljahre und Pfadabweichungen. Freie Versionswerte
werden nicht wiedergegeben, sondern als `invalid-redacted` gezaehlt.

Hash, Dateiname, Pfad, Message-ID, Rechnungsnummer, Lieferant, Mail- oder
Dokumentinhalt bleiben ausgeschlossen. Eine Reviewzeile ausserhalb des
Pruefunterordners ist nur ein Zaehler. Es gibt keinen Invoice-Move und keine neue
allgemeine Nextcloud-Move-Berechtigung.

Der Agentenablauf ist verbindlich: Status, Audit, exakt eine read-only Vorschau,
Darstellung von Hash/Digest/Aenderung/Konflikten und erst nach einem separaten
ausdruecklichen Auftrag die gebundene Einzeluebernahme. Erinnerung, Dateiname,
Mailtext und Ollama sind keine Ersatzquelle fuer fehlende Rechnungswerte;
`--yes` wird nie autonom ergaenzt.

## Konsequenzen

Backloggroesse, historische Pfadabweichungen und Extraktorheterogenitaet sind
sichtbar, ohne private Einzelwerte in Agentenantwort, Telemetrie oder Tests zu
ziehen. Die Aggregate koennen die naechste Quelljahr-Vorschau steuern, erteilen
aber keinerlei Schreib- oder Verschiebefreigabe. Ein produktiver Apply behaelt
den Backup-, ETag-, SHA-, Schema-, Audit- und Rollbackvertrag aus ADR-0022.

## Verifikation

Hermetische Tests verwenden nur temporaere SQLite-Zeilen mit erfundenen Werten.
Sie pruefen exakte Aggregate, Kohortentrennung, Plausibilitaet,
Versionsredaktion, Quelljahre, Pfadabweichungen, byteweisen Read-only-Zustand,
Abwesenheit von Remotezugriff und privaten Ausgabewerten, exaktes CLI-Routing,
fehlende Move-Werkzeuge sowie den Approval-Stopp vor Apply.
