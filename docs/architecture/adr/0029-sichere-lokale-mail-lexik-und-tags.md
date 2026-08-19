# ADR-0029: Sichere lokale Mail-Lexik und belegte Tags

- Status: Accepted
- Datum: 2026-08-20
- Entscheider: Data Maintainers, Security Maintainers, Tool Contract Maintainers
- Bezug: M11.4, ADR-0017, ADR-0026, ADR-0028

## Kontext

Der generische Wissensindex uebergab Suchtext bislang direkt an FTS5, konnte
mehrere Chunks derselben Mail ausgeben und zeigte den Chunkanfang statt des
Trefferbereichs. Strukturierte Mailmerkmale lagen teils in JSON, besassen aber
keine geschlossene, herkunftsbelegte Filtersemantik. Damit war der Pfad weder als
robuste Agentenschnittstelle noch als erklaerbares Ranking fuer ein grosses
Postfach geeignet.

M11.4 darf die bestehende aktuelle IMAP-Suche noch nicht ersetzen. Ihr lokaler
Pfad muss trotzdem sicher mit unvertrauenswuerdigem Suchtext umgehen, nur eine
Mail pro Ergebnis liefern und erkennen lassen, ob ein leerer Treffer eine
Abwesenheit belegen kann.

## Entscheidung

Das read-only Werkzeug `mail.search.local` exponiert den stabilen Befehl
`mail search-local`. Es liest ausschliesslich `knowledge.sqlite3`; es schreibt
weder IMAP-Flags noch Providerlabels oder lokale Tags waehrend einer Suche. Der
bestehende Befehl `mail search` bleibt unveraendert der aktuelle Serverpfad.

Eine typisierte Queryschicht normalisiert NFKC, begrenzt den Suchtext auf 500
Zeichen und 24 Terme und setzt jeden Term selbst als gequoteten FTS-Ausdruck
zusammen. Phrasen und Suffix-Prefixe sind explizit, waehrend Operatoren,
Klammern und Sonderzeichen niemals als rohe FTS-Syntax ausgefuehrt werden.
Filter fuer Absender, Teilnehmer, Zeitraum, aktuellen Ordner, Kategorie,
Review-Grund, Anlagenstatus/-typ und lokale Tags werden in SQL vor Ranking und
Ergebnislimit angewandt.

Ein eigenes FTS5-Schema trennt `subject`, `sender` und `body`. BM25 verwendet
die festen Gewichte 8,0 / 4,0 / 1,0. Eine belegte exakte Phrase erhaelt +2,0,
ein exakter Absender +3,0. Es gibt keinen Recency-Boost; das Datum ist nur ein
deterministischer Gleichstandsaufloser. Alte relevante Treffer werden deshalb
nicht durch ein verborgenes Aktualitaetsmodell verdraengt. Teil-Scores und
Rankingversion stehen im Ergebnis.

Treffende Chunks werden vor dem finalen Limit nach Dokument gruppiert. Der
bestbewertete Chunk erzeugt einen maximal 320 Zeichen langen, query-zentrierten
Plaintext-Snippet. HTML-Tags, ANSI-Sequenzen und Steuerzeichen werden entfernt
und niemals ausgefuehrt. Der Suchpfad begrenzt die interne Auswertung auf 100.000
Chunks und 10.000 gruppierte Dokumente; eine Ueberschreitung setzt sichtbar
`results_may_be_truncated`.

Lokale Tags besitzen einen geschlossenen Namensraum, Wert, Quelle,
Quellversion, optionale Konfidenz, strukturierte Evidenz, Aktivstatus und
Unsicherheitsgrund. Strukturelle Tags entstehen deterministisch aus Parser- und
aktuellen Locatorfeldern. Deklarierte Fach-Tags ohne Evidenz bleiben inaktiv;
ein Modell-Tag ist ausnahmslos `model-proposal` und niemals still aktiv.
Ordner- und Quarantaene-Tags werden beim Locatorabgleich ersetzt, ohne Content,
Chunks oder FTS-Body neu zu schreiben.

Der Projektionswriter akzeptiert deklarierte Tags nur nach Kanonisierung gegen
diesen geschlossenen Vertrag. Backfill und Reconciliation lesen dazu vorhandene
typisierte Kategorie-, Review- und Extraktorentscheidungen ueber eine
`query_only`-Verbindung zur Mail-Owner-Datenbank. Sie erstellen oder migrieren
diese Datenbank nicht und rufen kein Modell auf. Ein gespeichertes Ergebnis des
typisierten Klassifikators ist Quelle `classifier`; freie Modellvorschlaege sind
weiterhin Quelle `model`, inaktiv und als `model-proposal` markiert.

Die Antwort weist Rootgeneration, Coverage, Alter und Autoritaet aus. Nur eine
vollstaendige, autoritative und innerhalb des konfigurierten Alters liegende
Generation setzt `complete=true` beziehungsweise `absence_proven=true`.
Querytext, Adressen und Snippets werden weder protokolliert noch in Metriken
gespeichert; Metriken enthalten nur technische Zaehler und Laufzeit.

## Konsequenzen

- Die lokale Suche kann schnell und deterministisch nach Lexik und strukturierten
  Merkmalen suchen, ohne die aktuelle IMAP-Suche zu veraendern.
- Wissensschema 3 ist additiv. Bestehende Chunks werden einmalig in den
  feldgetrennten Mail-FTS uebernommen; neue Projektionen liefern Absender und
  reinen Body getrennt.
- Ein Locator-Move aktualisiert Folder-/Quarantaene-Tags, aber keine FTS-Zeile.
- Kategorie-, Review- und Fachdatenfilter sind auch im realen
  Backfill-/Reconciliation-Pfad belegt und nicht nur ein synthetisches
  Queryschema.
- Freie Modell-Tags, implizite Providerlabels und IMAP-Schreibzugriff sind keine
  Nebenwirkung dieses Meilensteins.
- Live-Locator-Verifikation, automatischer Fallback und Agenten-Routing bleiben
  M11.7. Threads und Kontext beginnen erst mit M11.5.

## Verifikation

`tests/test_mail_search_lexical_m114.py` deckt Unicode, Akzente, Schreibweise,
Bindestriche, E-Mail-Adressen, Rechnungsnummern, Zitate, Klammern,
FTS-Sonderzeichen, Prefixe, alle Filter, Chunk-Deduplizierung, sichere Snippets,
Ranking, Coverage sowie aktive/inaktive Tagprovenienz ab. Der Move-Test belegt
`fts_rows_changed = 0` bei aktualisierten Locator-Tags.

`scripts/benchmark_mail_search_m114.py` verwendet den rein synthetischen
M11.0-Korpus, misst p50/p95/p99 und Recall/MRR/nDCG und weist jede Differenz zur
M11.0-Messung aus. Der Bericht enthaelt nur Query-IDs, synthetische Treffer-IDs
und Aggregate, nie Suchtext, Adresse, Body oder Snippet.
