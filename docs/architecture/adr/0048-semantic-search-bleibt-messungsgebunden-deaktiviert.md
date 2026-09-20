# ADR-0048: Semantic Search bleibt messungsgebunden deaktiviert

- Status: Accepted
- Datum: 2026-09-21
- Entscheider: Data Maintainers, Security Maintainers, Operations Maintainers
- Bezug: M16.9, ADR-0029 bis ADR-0033

## Kontext

Lexik, Tags und Threadkontext sind schnell und erklärbar, verfehlen aber
potenziell Synonyme. Lokale Embeddings können Recall erhöhen, bringen zugleich
zusätzliche Fehlklassifikation, Modell-, Speicher- und Rechenkosten sowie einen
weiteren aus unvertrauenswürdigem Inhalt abgeleiteten Datenbestand mit.

Die M11-Prototypen belegten technische Machbarkeit, aber keine
Zielhardwarequalität, die eine Aktivierung rechtfertigt. Eine Architekturwahl
darf deshalb weder durch vorhandenen Code noch durch ein gewünschtes Ergebnis
vorweggenommen werden.

## Entscheidung

M16.9 friert ein synthetisches Eval mit exakten, kontextuellen, synonymen und
negativen Suchintentionen ein. Verglichen werden Lexik, Thread/Tags, lokales
digestgebundenes Embedding und Hybrid-Retrieval. Precision, Recall, Abstention,
Fehlklassifikation, p50/p95, Rechenoperationen und Speicher sind verpflichtend.

Der gemessene Hybridprototyp erhöht Recall von 0,50 auf 0,666667, senkt aber
Precision von 0,75 auf 0,666667. Damit ist die vorab definierte
qualitätsneutrale Mehrwertbedingung nicht erfüllt. Semantic Search bleibt
deaktiviert; die synthetische Messung wäre auch bei besserem Ergebnis nicht zur
Aktivierung berechtigt.

Der abgeleitete Index besitzt eine eigene entfernbare Wurzel und ist vollständig
reproduzierbar. Quellmails und lexikalischer Index werden nicht verändert.
Vektoren autorisieren weder Lesen noch Schreiben und jede spätere positive
Mailaktion benötigt weiterhin eine aktuelle serverseitige Revalidierung.

## Alternativen

- Automatische Aktivierung aufgrund höherem Recall: verworfen, weil Precision
  sinkt und Zielhardwareevidenz fehlt.
- Externe Embedding-API: verworfen wegen Inhaltsabfluss und fremder
  Datenverarbeitung.
- Ersatz der Lexik durch Vektorsuche: verworfen, weil exakte Belege,
  Negativergebnisse und Rückbaugrenze verloren gingen.
- Dauerhafte Deinstallation sämtlichen Prototypcodes: derzeit verworfen; der
  hermetische Vergleich bleibt als Regressionsevidenz nützlich.

## Rollout- und Rückbaugrenze

Aktivierung verlangt ein separates Review, lokale digestgebundene Modelle,
Zielhardwaremessung, vorab definierte Schwellen, Canary, Rebuild und Rollback.
Bis dahin bleibt die Runtimekonfiguration unverändert. Der vollständige Rückbau
löscht nur die eigene Embeddingwurzel; Source und Lexik bleiben bytegleich.

