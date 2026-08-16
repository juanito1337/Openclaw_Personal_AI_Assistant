# ADR-0019: Rechnungsbetraege bleiben typisiert und rechnerisch plausibel

- Status: Accepted
- Datum: 2026-08-16
- Entscheider: Data Maintainers, Security Maintainers
- Betroffene Milestones: M10.3

## Kontext

Die bisherige Extraktion suchte Brutto, Netto und Steuer unabhaengig anhand
einfacher Textanker. Bei mehreren Treffern bevorzugte sie den betragsmaessig
groessten Wert. Dadurch konnten Zwischensummen, Einzelpreise oder ein dezimal
geschriebener Steuersatz als Geldbetrag erscheinen. Zwischen den drei
Betragsfeldern und ihren Waehrungen bestand kein fail-closed
Plausibilitaetsvertrag.

Eine spaetere Neubewertung muss nicht nur den ausgewaehlten Wert, sondern auch
Rolle, Waehrung, verworfene Alternativen und den Grund einer Reviewentscheidung
belegen koennen. Modellvermutungen, Mailbetreff und Dateiname sind hierfuer keine
zulaessige Evidenz.

## Entscheidung

Jeder beschriftete Betragsfund wird als `FieldCandidate` mit Feld, Rolle, Rohwert,
normalisiertem Wert, ISO-Waehrung, Quelle, begrenzter Evidenz, Konfidenz und
Ausschlussgrund erhalten. Die geschlossene Rollentrennung umfasst Zahlbetrag,
Bruttosumme, Nettosumme, Steuerbetrag, Steuersatz, Zwischensumme, Rabatt,
Abschlagszahlung, Gutschrift und Einzelpreis.

Deutsche und englische Dezimal- und Tausenderformate werden deterministisch in
Cent normalisiert. Die Waehrungssymbole fuer EUR, USD und GBP sowie die ISO-Codes
EUR, USD, GBP und CHF werden in ISO-Codes ueberfuehrt. Prozentwerte sind immer
`tax-rate` und tragen den Ausschlussgrund `percentage-is-not-money`; sie koennen
kein Steuerbetragsfeld fuellen.

Ein expliziter Zahlbetrag besitzt Vorrang vor einer allgemeinen Bruttosumme.
Innerhalb derselben priorisierten Rolle sind unterschiedliche Werte ein Konflikt;
es wird nie auf den groessten Wert ausgewichen. Eine Zwischensumme darf Netto nur
dann ersetzen, wenn sie zusammen mit Steuer und Brutto innerhalb von zwei Cent
rechnerisch aufgeht.

Bei einem vollstaendigen Tripel gilt:

```text
abs(Brutto - Netto - Steuer) <= 2 Cent
```

Eine groessere Abweichung, Steuer groesser als Brutto, unvereinbare Vorzeichen,
mehrere unvereinbare Summen, ein positives mehrdeutiges Guthaben sowie fehlende
oder gemischte Waehrung erzeugen einen geschlossenen `amount:*`-Reviewgrund.
Vorzeichen und Waehrung werden nicht still korrigiert. Mailtext ausserhalb des
PDF-Inhalts, Dateiname und Ollama sind keine Betragsquellen; Ollama darf weder
Werte erzeugen noch Plausibilitaetsregeln ueberstimmen.

Die Kandidaten und Reviewgruende verbleiben im vorhandenen Extraktions-JSON.
M10.3 fuehrt keine Datenbankmigration, kein Reprocessing und keine produktive
Registeraenderung ein.

## Konsequenzen

Die Extraktion ist erklaerbar und spaetere Vorschau-/Auditpakete koennen
Alternativen sowie Ausschlussgruende anzeigen. Ein korrekt beschrifteter
Zahlbetrag wird auch dann gewaehlt, wenn eine Positionssumme groesser ist.
Rechnerisch oder waehrungsseitig unsichere Dokumente bleiben bewusst in Review;
das kann die Reviewquote gegenueber einer ungesicherten Heuristik erhoehen.

Die Zwei-Cent-Grenze ist eine dokumentierte Rundungstoleranz, keine allgemeine
Qualitaetsschwelle. Abschlaege koennen zu einem vom Brutto/Netto/Steuer-Tripel
abweichenden Zahlbetrag fuehren und bleiben bis zu einer spaeteren expliziten
Semantik in Review. M10.4-OCR-Fusion und historische Neubewertung bleiben
ausserhalb dieser Entscheidung.

## Verifikation

Ein vollstaendig synthetischer 15-Faelle-Korpus misst Betragspraezision,
Abdeckung, Rechenfehler und False-confirmed direkt vor und nach M10.3. Golden- und
Regressionstests decken deutsche und englische Zahlenformate, Prozentwerte,
mehrere Summen, Rundung, Abschlag, Rabatt, Einzelpreis, positive und negative
Gutschrift, Fremdwaehrung, Waehrungskonflikt sowie ausgeschlossene Mail-,
Dateinamen- und Modellquellen ab.
