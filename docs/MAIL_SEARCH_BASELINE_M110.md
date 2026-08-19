# M11.0-Baseline: Mail-Suche, Abdeckung und Integritaet

Stand: 2026-08-19

## Zweck und Abgrenzung

M11.0 friert das Verhalten der vorhandenen serverseitigen `mail search`-Suche
und des lokalen SQLite-FTS5-Pfads ein. Es fuehrt weder einen Vollkonto-Crawler
noch ein neues Schema, Ranking, Tagging, Threading, Embedding oder Agentenwerkzeug
ein. Es liest kein produktives Postfach, veraendert keine Datei unter
`/srv/openclaw` und startet keinen Job.

Die Messwerte sind beobachtete Ausgangswerte, keine Qualitaets- oder
Laufzeitgrenzen. Verbindliche Zielwerte werden erst nach dieser Baseline in einem
eigenen dokumentierten Beschluss festgelegt.

## Reproduktion

Die vollstaendige Baseline wird offline aus generierten EML-Nachrichten, einem
Fake-IMAP-Client, einer temporaeren Suchprojektion und einer temporaeren
SQLite-Datenbank erzeugt:

```bash
./scripts/bootstrap-dev.sh
.venv/bin/python scripts/benchmark_mail_search_m110.py \
  --samples 11 \
  --output build/m110-mail-search-baseline.json
.venv/bin/python -m pytest -q tests/test_mail_search_baseline_m110.py
./scripts/check-repo.sh
```

Die JSON-Ausgabe unter `build/` ist ein lokales, nicht versioniertes
Messartefakt. Sie enthaelt nur synthetische Fall-IDs, technische Zaehler und
aggregierte Messwerte; keine Suchtexte, Adressen, Betreffe, Bodies, Snippets oder
Embeddings.

## Goldkorpus

Der versionierte Korpus
`tests/fixtures/mail_search/m110_synthetic_corpus.json` enthaelt ausschliesslich
erfundene Daten unter der reservierten Domain `example.invalid`. Aus seinen
Feldern erzeugt der Benchmark 13 EML-Nachrichten erst im Speicher. Es werden
keine `.eml`-Dateien in Git aufgenommen. Der im Referenzlauf verifizierte
Korpus-SHA-256 lautet
`b0e5e79b06f5b493a50db4047b4d8a0630dd1d827700efb6279f6de9d7d97a7d`.

| Merkmal | Umfang |
| --- | ---: |
| synthetische EML-Nachrichten | 13 |
| freigegebene Fake-IMAP-Ordner | 5 |
| in der heutigen Projektion publizierte Nachrichten | 11 |
| bewusst noch nicht projizierte Nachrichten | 2 |
| deutsche/englische Goldqueries | 13 |
| lexikalische Queries | 2 |
| Absender-, Body- und haeufige-Begriff-Queries | 4 |
| strukturierter Zeitraumfall | 1 |
| Nulltreffer | 1 |
| Anhangsquery | 1 |
| semantische Queries | 2 |
| Kontext-/Threadqueries | 2 |

Die 13 Querydefinitionen enthalten abgestufte Relevanzlabels. Der Report nennt
nur Query-ID, Art, synthetische Treffer-IDs und Metriken. Recall@5/10, MRR und
nDCG@10 werden direkt aus diesen Labels berechnet.

## Gemessene Umgebung

Ein Referenzlauf erfolgte auf Linux 7.0.0-28-generic x86_64 mit 8 logischen CPUs
und 15,46 GiB RAM. Verwendet wurden Python 3.12.3, SQLite 3.45.1 mit FTS5 und 11
Wiederholungen je Query. Der Korpus erzeugte 6.512 Bytes EML-Daten. Zeit- und
Speicherwerte schwanken mit Hardware und Systemlast und werden deshalb nicht als
Grenzwerte interpretiert.

## Abdeckung und Datenzustand

| Messwert | Ausgangswert |
| --- | ---: |
| Fake-IMAP-Nachrichten | 13 |
| Projektionsrecords | 11 |
| Projektionsabdeckung | 84,62 % |
| FTS-Dokumente / Chunks | 11 / 11 |
| Dokumente ohne verifizierbaren Live-Locator | 11 |
| Alter des frisch erzeugten Fixture-Indexes im Referenzlauf | 0,156 s |
| blockierte Inhalte | nicht messbar: noch kein Vollkonto-Crawler |
| gegen IMAP belegbar veraltete Inhalte | nicht messbar: noch kein Coverage-Vertrag |

`complete` der heutigen Projektion bedeutet nur, dass die veroeffentlichte
Recordliste atomar und intern vollstaendig ist. Es beweist nicht, dass alle Mails
des Kontos enthalten sind. Diese begriffliche Trennung ist eine zentrale Vorgabe
fuer M11.1 und M11.2.

## Suchqualitaet

Die Gesamtwerte mischen absichtlich bereits funktionierende exakte Suche und
noch nicht implementierte Anhangs-, Kontext- und Semantikfaelle. Sie sind kein
Produkturteil, sondern der Vergleichspunkt fuer die spaeteren M11-Pakete.

| Pfad | Recall@5 | Recall@10 | MRR | nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| serverseitige Ordnersuche, alle bewerteten Queries | 0,5000 | 0,5000 | 0,5000 | 0,4641 |
| lokaler FTS5-Pfad, alle bewerteten Queries | 0,4833 | 0,4833 | 0,5000 | 0,4766 |
| Server, nur exakte lexikalische Queries | 1,0000 | 1,0000 | 1,0000 | 0,9170 |
| FTS5, nur exakte lexikalische Queries | 1,0000 | 1,0000 | 1,0000 | 1,0000 |
| Server/FTS5, Bodyqueries | 1,0000 | 1,0000 | 1,0000 | 1,0000 |
| Server/FTS5, semantische Queries | 0,0000 | 0,0000 | 0,0000 | 0,0000 |
| Server/FTS5, Kontextqueries | 0,0000 | 0,0000 | 0,0000 | 0,0000 |
| Server/FTS5, Anhangsquery | 0,0000 | 0,0000 | 0,0000 | 0,0000 |
| Server/FTS5, strukturierter Zeitraumfall | 0,0000 | 0,0000 | 0,0000 | 0,0000 |

Der vollstaendige Nulltreffer wurde auf beiden Pfaden korrekt leer beantwortet.
Die lokale haeufige-Begriff-Query erreicht nur 0,8 Recall, weil eine der
relevanten Nachrichten bewusst nicht zur heutigen Projektion gehoert.

## Latenz, Backend- und Ressourcenwerte

| Messwert | serverseitiger Fake-IMAP-Pfad | lokaler FTS5-Pfad |
| --- | ---: | ---: |
| Suchsamples | 143 | 143 |
| erster Lauf | 1,1948 ms | 0,6711 ms |
| p50 | 0,5747 ms | 0,2073 ms |
| p95 | 0,9573 ms | 0,5356 ms |
| p99 | 1,1948 ms | 0,6020 ms |

Die Fake-IMAP-Latenz enthaelt keine reale Netz- oder Providerwartezeit. Relevant
ist vor allem die Aufrufstruktur: 143 Suchlaeufe erzeugten 143 Ordnerlisten und
715 serielle Ordnersuchen. Bei fuenf Ordnern entstehen somit pro Query eine
Ordnerliste und exakt fuenf Suchaufrufe. Die synthetischen Envelope-Antworten
umfassten 41.745 Bytes; Raw- und Body-Fetches waren fuer diesen Suchpfad null.

Der gesamte Referenzlauf benoetigte 304,934 ms Wandzeit und 203,984 ms CPU-Zeit.
`tracemalloc` meldete 198.295 Bytes maximale zusaetzliche Python-Allokation, der
Prozess 29.508 KiB Max-RSS und die temporaeren SQLite-Dateien 114.688 Bytes.
Diese Werte sind reine Beobachtungen auf dem oben genannten System.

## Charakterisiertes Fehler- und Aenderungsverhalten

- Ein Nulltreffer ist bei fehlerfreien Ordnern `complete = true`.
- Ein einzelner Ordnerfehler liefert Treffer aus den uebrigen Ordnern, aber
  `complete = false` und den betroffenen `folder_errors`-Eintrag.
- Fehler in allen Ordnern brechen mit `RuntimeError` fail-closed ab.
- Ein haeufiger Begriff mit Limit 2 setzt
  `results_may_be_truncated = true` und weist begrenzte Ordner aus.
- Mehrere passende Chunks derselben Mail werden heute als mehrere Treffer
  ausgegeben.
- Ungueltige FTS-Syntax faellt auf die bestehende literale LIKE-Suche zurueck.
- Ein Snippet besteht aus den ersten 500 Zeichen des Chunks und ist nicht
  query-zentriert.
- Zwei Datumsgrenzen werden heute wie normale Body-/Betreff-/Absenderwoerter
  behandelt; ein strukturierter Zeitraumfilter ist nicht vorhanden.
- Die Projektionsmetadaten enthalten einen Quellordner, aber keine aktuelle
  Mailbox-ID, UID oder UIDVALIDITY.

No-op, neue Mail, Kopie, externer Move und Quarantaenewechsel verursachen bei der
serverseitigen Suche jeweils erneut eine Ordnerliste plus fuenf Ordnersuchen. Der
Serverpfad sieht den aktuellen Zustand, der lokale Index bleibt jedoch
unveraendert: Eine verschobene synthetische Mail wird serverseitig in
`Archiv/2025`, lokal weiterhin in `Gesendet` gefunden. Kopierbeziehung,
Locatorwechsel und UIDVALIDITY-Reset werden heute nicht getrackt. Der Benchmark
markiert diese Faehigkeiten deshalb explizit als nicht implementiert, statt
erfundene Delta- oder Ressourcenwerte auszugeben.

## Optionaler produktiver Read-only-Betriebscheck

M11.0 fuehrt diesen Check nicht aus. Falls er spaeter separat beauftragt wird,
duerfen ausschliesslich die registrierten read-only Befehle verwendet werden:

```bash
./scripts/assistant.sh version --verify
./scripts/assistant.sh mail status
./scripts/assistant.sh mail doctor
./scripts/assistant.sh status
```

Der aktuelle Werkzeugvertrag besitzt noch keinen inhaltsfreien Nachweis fuer die
vollstaendige Nachrichtenzahl, Projektionsabdeckung gegen IMAP oder aktuelle
Locator. Deshalb werden diese Werte fuer ein produktives Postfach nicht aus
generischem Shell-, SQLite- oder ungeprueftem IMAP-Zugriff erraten. Der geplante
registrierte Statuspfad entsteht erst in M11.7.

## Datenschutz- und Sicherheitsgrenzen

- Korpus, Tests und Benchmark verwenden ausschliesslich `example.invalid`.
- EMLs und SQLite-Datenbanken existieren nur temporaer.
- Der Report enthaelt keine Querytexte, Bodies, Betreffe, Adressen oder Snippets.
- Es gibt keine Verbindung zu IMAP, Ollama, Nextcloud oder `/srv/openclaw`.
- Es werden keine Mail, kein Flag, kein Ordner und kein produktiver Job geaendert.
- Der Benchmark umgeht keinen ClamAV-Gate eines produktiven Crawlers; ein solcher
  Crawler existiert in M11.0 noch nicht.

## Ergebnis und naechster erlaubter Schritt

M11.0 ist mit dieser Baseline messbar. Exakte lexikalische Suche funktioniert auf
dem kleinen synthetischen Bestand, waehrend Vollkontoabdeckung, Live-Locator,
inkrementelle Move-/Copy-/Delete-Erkennung, Threadkontext, Anhangsfilter und
Semantik nachweislich fehlen. Der naechste erlaubte Entwicklungsschritt ist
ausschliesslich M11.1, der den Daten-, Identitaets- und Migrationsvertrag
definiert. M11.1 ist nicht Bestandteil dieser Abnahme.
