# ADR-0016: Providergebundenes, deterministisches Portfolio-Research

- Status: Accepted
- Datum: 2026-08-13
- Entscheider: Architecture Maintainers, Data Maintainers, Security Maintainers
- Betroffene Bereiche: Portfolio, Toolvertrag, Personal-Assistant-Skill

## Kontext

Der Personal Assistant soll neue Aktien suchen, bekannte Analysemuster konsistent
anwenden, Vorschlaege nachvollziehbar erklaeren und Jans bestaetigte
Investmentphilosophie ueber Zeit reflektieren. Ein Sprachmodell allein ist dafuer
keine verlaessliche Fakten- oder Rechenquelle. Providerdaten koennen unvollstaendig,
veraltet oder tarifbedingt nicht verfuegbar sein. Persoenliche Praeferenzen und
modellseitig vermutete Vorlieben muessen ausserdem klar getrennt bleiben.

## Entscheidung

EODHD ist die einzige Research-Faktenquelle. Ein allowlist-begrenzter Screener
liefert eine kleine Kandidatenmenge; Fundamental- und EOD-Endpunkte belegen
Identitaet, Kennzahlen und Historie. Antworten sind groessenbegrenzt, Ticker und
Filter validiert, Providerfehler redigieren den API-Schluessel.

Ein im Quellcode versioniertes Mehrfaktormodell berechnet Metriken,
Datenabdeckung, Pfeilerscores und Urteil deterministisch. Es publiziert vier feste
Strategien mit insgesamt 100 Prozent Gewicht. Mindestens 200 EOD-Beobachtungen,
hoechstens sieben Kalendertage Kursalter, 70 Prozent relevante
Kennzahlenabdeckung und je 50 Prozent Abdeckung der Pflichtpfeiler Qualitaet und
Risiko sind obligatorisch. Unterschreitung liefert `abstain` und keinen
Gesamtscore. Ein LLM darf dieses strukturierte Ergebnis sprachlich erklaeren,
aber keine Werte ergaenzen, Scores aendern oder Blocker ueberstimmen.

Research-Laeufe und Kandidatenevidenz werden append-only in der bereits dem
Portfolio-Owner gehoerenden SQLite-Datenbank gespeichert. Eigene Positionen und
aktivierte Watchlist-Identitaeten erscheinen nicht als neue Vorschlaege.
`research-candidate` ist ein Schwellenlabel, keine Kaufempfehlung und keine
Orderfreigabe.

Die erklaerte Investmentphilosophie ist ein eigener append-only Vertrag aus
Risikotoleranz, Horizont, Strategie, Positions-/Sektorgrenzen, Praeferenzen und
Ausschluessen. Jede Aenderung benoetigt ausdrueckliche Freigabe und erzeugt eine
neue Version. Feedback verweist auf eine vorhandene Research-Kandidaten-ID und
wird ebenfalls nur nach Freigabe angehaengt. Daraus abgeleitete Beobachtungen
zeigen Stichprobengroesse und Konfidenz, aendern das bestaetigte Profil aber nie.
Kritik und Lob entstehen ausschliesslich aus bestaetigten Grenzen, vollstaendiger
EUR-Bewertung und ausreichend belegten Sektordaten.

## Konsequenzen

Die Analyse bleibt reproduzierbar und auditierbar, kann bei duennen oder alten
Daten jedoch bewusst keinen Vorschlag liefern. EODHD-Tarife muessen Screener,
Fundamentals und EOD-Historie erlauben; erst ein erfolgreicher Lauf belegt das in
der konkreten Instanz. Branchenvergleiche, Nachrichten, Konsensschaetzungen,
Steuern, individuelle Liquiditaet und Orderausfuehrung sind nicht Teil dieses
Vertrags. Neue Modelle brauchen eine neue Modellversion, Dokumentation und
Regressionstests; sie duerfen historische Ergebnisse nicht still umdeuten.

Research schreibt nur lokale Auditdaten. Watchlist-Aenderungen, Jobaktivierung
und jede denkbare Brokeraktion bleiben separate Werkzeuge und Freigaben. Das
System bietet damit fundierte Entscheidungshilfe, aber keine Garantie, Rendite-
prognose oder automatische Vermoegensverwaltung.

## Verifikation

Provideradaptertests pruefen Filter-Allowlist, Groessenlimit, Eingabevalidierung
und Secret-Redaktion. Verhaltensregressionen belegen identische Scores fuer
identische Evidenz, Enthaltung bei fehlenden Daten, Identitaetsbindung,
Ranking/Ausschluss vorhandener Werte, append-only Profil/Feedback und fehlende
automatische Profilmutation. Tool- und Skilltests gleichen Befehle, Approvals und
die Pflicht zur belegten Erklaerung gegen den generierten Vertrag ab.
