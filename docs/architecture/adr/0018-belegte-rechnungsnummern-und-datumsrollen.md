# ADR-0018: Rechnungsnummern und Rechnungsdaten entstehen aus belegten Kandidaten

- Status: Accepted
- Datum: 2026-08-15
- Entscheider: Data Maintainers, Security Maintainers
- Betroffene Milestones: M10.2

## Kontext

Die bisherige Rechnungsextraktion gab fuer Nummer und Datum unmittelbar einen
Regex-Treffer zurueck. Ein nicht ausgewaehlter Wert besass dadurch keine
maschinenlesbare Rolle und keinen Ausschlussgrund. Varianten wie `Rechnung Nr.`,
mehrzeilige Felder und OCR-Zeichenabstaende blieben teilweise unerkannt. Ein
spaeteres Reprocessing koennte deshalb weder belegen, welche Werte betrachtet
wurden, noch sicher zwischen Rechnungs-, Kunden-, Bestell- oder anderen Nummern
und Datumsarten unterscheiden.

Dateinamen enthalten in der Praxis oft plausible Nummern, stammen aber aus einer
weniger verlaesslichen Quelle. Sie duerfen keinen fehlenden Dokumentwert ersetzen.

## Entscheidung

Rechnungsnummer und Rechnungsdatum werden aus typisierten `FieldCandidate`-
Objekten gewaehlt. Jeder Kandidat enthaelt Feld, Rolle, Rohwert, normalisierten
Wert, Quelle, begrenzten Evidenztyp, begrenzte Evidenz, Konfidenz und einen leeren
oder expliziten Ausschlussgrund.

Nur ein deutscher oder englischer Rechnungsanker auf derselben oder exakt der
naechsten unbeschrifteten Zeile liefert primaere Nummernevidenz. Kunden-,
Bestell-, Liefer-, Vertrags-, Telefon-, Steuer- und Trackingnummern sowie IBAN
werden als ausgeschlossene Rollen erhalten. Datumsfoermige Werte sind keine
Rechnungsnummern. Rechnungsdaten werden getrennt von Leistungs-, Liefer-,
Bestell-, Zahlungs- und Faelligkeitsdaten erfasst. Mehrere gleich plausible
Rechnungsnummern oder Rechnungsdaten leeren das Ergebnis und fuehren zu Review.

Unicode und typische OCR-Abstaende werden deterministisch normalisiert. Ein
physischer PDF-Dateiname darf die Konfidenz nur erhoehen, wenn er einen bereits
beschrifteten und normalisierten Dokumentwert wiederholt. Ein Dateiname allein
bleibt ein ausgeschlossener Stuetzkandidat.

Die Kandidaten werden im vorhandenen Extraktions-JSON serialisiert. M10.2 fuehrt
keine SQLite-Migration, keine neue Backfill-Auswahl und keine produktive
Neubewertung aus.

## Konsequenzen

Spaetere Vorschau- und Auditpakete koennen Auswahl und Ausschluss erklaeren, ohne
Werte aus Dateinamen, Mailtext oder einem Modell zu erfinden. Bestehende
Feldschwellen und Ablagegrenzen bleiben kompatibel. Das technische
Extraktions-JSON wird umfangreicher und kann weiterhin begrenzte Dokumentevidenz
enthalten; es bleibt deshalb im geschuetzten Rechnungszustand und darf nicht als
inhaltsfreie Telemetrie exportiert werden.

Die Betragslogik, native/OCR-Fusion, historische Neubewertung und Datenbankaudit
sind ausdruecklich nicht Teil dieser Entscheidung und bleiben spaeteren
M10-Paketen vorbehalten.

## Verifikation

Ein versionierter, vollstaendig synthetischer 12-Faelle-Korpus misst Nummern- und
Datumspraezision, Abdeckung und False-confirmed vor und nach der Umstellung.
Verhaltensregressionen pruefen positive und negative Rollen, Unicode,
OCR-Abstaende, Folgezeilengrenze, Dateinamen, Konflikte und serialisierte
Kandidaten. Die M10.0-Baseline bleibt separat unveraendert.
