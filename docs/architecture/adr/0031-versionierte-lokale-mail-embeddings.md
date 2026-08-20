# ADR-0031: Versionierte lokale Mail-Embeddings ohne automatische Aktivierung

- Status: Accepted
- Datum: 2026-08-20
- Entscheider: Data Maintainers, Security Maintainers, Operations Maintainers
- Bezug: M11.6, ADR-0008, ADR-0026, ADR-0028, ADR-0029, ADR-0030

## Kontext

Lexikalische Suche findet exakte Begriffe schnell, kann aber semantisch verwandte
deutsche und englische Formulierungen verfehlen. Ein Embedding darf diese Luecke
nur als Retrievalsignal schliessen. Es ist kein Beleg fuer eine fachliche
Behauptung und darf weder die unveraenderte Mailquelle noch den autoritativen
IMAP-Locator ersetzen.

Modellname, Dimension oder ein frei veraenderbares `latest`-Tag reichen nicht als
reproduzierbare Identitaet. Ausserdem duerfen Mailclient-Moves, weitere Kopien und
Quarantaenewechsel keine erneute teure Inhaltsberechnung ausloesen. Modellfehler
duerfen die unabhaengige FTS-Suche nicht ausfallen lassen.

## Entscheidung

`mail-embedding-v1` bindet jeden Vektor an Raw-SHA-256, SHA-256 des mit
`mail-retrieval-text-v1` normalisierten Chunks, Retrievaltextversion,
Chunkposition, Modellname, vollstaendigen Ollama-SHA-256-Digest und Dimension.
Occurrence, UID, Ordner, Locator und Quarantaenestatus fehlen absichtlich im
Schluessel. Ein Content mit mehreren Occurrences besitzt deshalb genau denselben
Vektorsatz.

Die Wissensdatenbank verwendet Schema 5 und speichert die Vektoren als
little-endian Float32 in `mail_search_embeddings`. Fremdschluessel zu Dokument
und Chunk entfernen veraltete Vektoren transaktional bei einer echten
Inhaltsaenderung. Modellwechsel erzeugen einen getrennten Cache. Wiederaufnahme
scannt deterministisch und ueberspringt bereits vorhandene Schluessel; ein
begrenzter Lauf muss seinen Rest sichtbar melden.

Jede reale Embedding- und Queryanfrage laeuft ueber `/api/embed` des vorhandenen
Ollama-Prioritaetsproxies. Hintergrundaufbau verwendet `background`, interaktive
Suche `interactive`; Queue- und Upstream-Timeouts werden explizit uebergeben.
Vor einer realen Messung muss `/api/tags` Name und vollstaendigen Digest des
bereits installierten Modells bestaetigen. Der Vertrag zieht, startet oder
aktiviert kein Modell und registriert keinen produktiven Job.

Die erste Suchimplementierung berechnet exakte Kosinusaehnlichkeit in Python.
Sie ist fuer den elf Chunks kleinen M11.0-Messbestand korrekt, deterministisch
und unabhaengig von einer optionalen SQLite-ANN-Erweiterung. Eine ANN-Auswahl
erfolgt erst nach einem realen Vollkonto-/Zielhardwarebenchmark. Fehlt die
Erweiterung oder scheitert Semantik, bleibt FTS explizit verfuegbar. Es gibt
keinen stillen ungetesteten ANN-Fallback.

Ein semantisches Ergebnis traegt Score, Distanz, Rankingversion, Modellname,
Digest und Dimension sowie `role=semantic-candidate` und
`evidence_for_query=false`. Es ist damit ein Kandidat fuer die spaetere
M11.7-Fusion, keine gespeicherte inhaltliche Wahrheit.

## Modellvergleich und Freigabegrenze

Als lokal betreibbare mehrsprachige Kandidaten werden
`nomic-embed-text-v2-moe` und `bge-m3` fuer eine spaetere Messung vorgemerkt.
Die Ollama-Kataloge nennen 768 beziehungsweise 1024 Dimensionen, 512
beziehungsweise 8192 Token Kontext und etwa 958 MB beziehungsweise 1,2 GB
Modellgroesse. Diese Hersteller-/Katalogwerte sind nur Kandidatenmetadaten und
keine lokale Qualitaetsmessung.

Der Entwicklungs-Koordinator war beim M11.6-Lauf nicht erreichbar. Deshalb
enthaelt die eingefrorene Baseline nur zwei klar als `synthetic-contract`
markierte Fake-Profile. Sie prueft Messschema, Recall@5/10, MRR, nDCG@10,
p50/p95, Cold/Warm-Zeit, Queuewartezeit, RAM- und Plattenfelder, ist aber
`eligible_for_activation=false`. Kein echtes Modell wurde gepullt oder gewaehlt.

Eine spaetere Zielhardwaremessung muss zwei bereits installierte Modelle ueber
den Proxy mit vollem Digest vergleichen. Erst danach und nach Jans separater
Freigabe darf eine Konfiguration oder ein Job aktiviert werden.

Kandidatenquellen:

- <https://ollama.com/library/nomic-embed-text-v2-moe>
- <https://ollama.com/library/bge-m3>

## Konsequenzen

- Reine Locatorereignisse kosten exakt null neue Embeddinganfragen.
- Ein geaenderter Chunk oder Modelldigest kann keinen alten Vektor als Treffer
  wiederverwenden.
- Falsche Dimensionen, NaN, Infinity, Nullvektoren, Queue-Full, Timeout und
  Proxy-Ausfall werden sichtbar und lassen FTS intakt.
- Exakte Suche skaliert linear mit Vektorzahl und Dimension. Diese bewusste
  Ausgangsgrenze wird vor einem Vollkonto-Rollout neu gemessen.
- M11.6 aendert weder `mail search-local` noch die produktive Suchpraeferenz;
  Hybridrouting und Live-Locator folgen fruehestens in M11.7.

## Verifikation

`tests/test_mail_embeddings_m116.py` prueft Schema, Cache, Resume,
Modellwechsel, Chunkwechsel, Move/Kopie/Quarantaene, Prioritaetsheader,
Vektorvalidierung, Ausfallpfade, FTS-Isolation und Ergebnisprovenienz mit rein
synthetischen Daten.

`scripts/benchmark_mail_embeddings_m116.py` erzeugt ohne Optionen die
hermetische Vertragsbaseline unter
`docs/architecture/mail-embedding-baseline-m116.json`. Reale Ausfuehrungen
verlangen `--base-url` und mindestens zwei vollstaendige
`NAME|sha256:DIGEST|DIMENSION|KONTEXTZEICHEN`-Angaben; der Client bestaetigt
jeden Digest am Koordinator, bevor er den Goldkorpus einbettet.
