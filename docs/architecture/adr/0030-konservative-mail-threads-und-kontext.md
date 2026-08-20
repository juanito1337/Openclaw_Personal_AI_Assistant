# ADR-0030: Konservative Mail-Threads und begrenzter Kontext

- Status: Accepted
- Datum: 2026-08-20
- Entscheider: Data Maintainers, Security Maintainers, Tool Contract Maintainers
- Bezug: M11.5, ADR-0017, ADR-0026, ADR-0029

## Kontext

Die M11.4-Suche liefert deduplizierte Einzelnachrichten, modelliert aber noch
keinen Gespraechszusammenhang. Betreffgleichheit allein ist dafuer ungeeignet:
wiederkehrende Newsletter, Rechnungen und leere Betreffe koennen voellig
unabhaengige Nachrichten verbinden. Gleichzeitig enthalten Antwortketten,
Signaturen und Disclaimer viel wiederholten Text, der das Ranking verzerren kann,
ohne neue zitierbare Evidenz zu liefern.

Der Threadindex darf die Content-/Occurrence-/Locator-Trennung nicht aufheben.
Insbesondere darf ein Move durch Webmail oder Mailclient weder eine neue
Threadidentitaet noch eine erneute Inhaltsanalyse erzwingen. Kontext darf den
Treffer erlaeutern, aber niemals selbst als Querytreffer erscheinen.

## Entscheidung

Primaere Elternkanten entstehen ausschliesslich aus kanonischen `Message-ID`,
`In-Reply-To` und `References`. Eine eindeutige `In-Reply-To`-Beziehung hat
Vorrang; andernfalls gilt der letzte eindeutig aufloesbare `References`-Eintrag.
Fehlende, mehrdeutige, selbstbezogene und kaputte IDs bleiben als diagnostische
Kanten ohne Elternbeziehung sichtbar. Zyklen werden deterministisch gebrochen,
sodass kein Content sein eigener Vorfahr sein kann.

Nur ohne jegliche vorhandene Header-Evidenz darf ein Fallback verbinden. Er
fordert einen erkannten deutschen oder englischen Antwort-/Weiterleitungsprefix,
einen normalisierten nichtgenerischen Betreff, bekannte reziproke Teilnehmer,
mindestens zwei uebereinstimmende bekannte Adressen und hoechstens 21 Tage
Abstand. Newsletter-, Digest-, Rechnungs-, Zahlungs- und leere Betreffe sind
ausgeschlossen. Die Verbindung und der gesamte betroffene Thread bleiben
`uncertain`. Fehlende BCC-Information wird nicht erraten.

`mail_search_threads` und `mail_search_thread_members` speichern Threadwurzel,
Version, Zeitraum, Mitgliederzahl, Position, Elternbeziehung und Sicherheit
getrennt von `documents`. Die stabile Thread-ID wird aus Threadversion,
Mailressource und Root-`content_id` abgeleitet. Locator und Occurrence sind kein
Bestandteil; reine Moves behalten deshalb die Threadidentitaet.

`mail search-local --context-limit N` darf pro Querytreffer hoechstens sechs
direkt benachbarte Threadmitglieder ausgeben. Diese stehen chronologisch und
dedupliziert im separaten Feld `context`, tragen `role=thread-context`,
`query_match=false` und `evidence_for_query=false` und erhoehen weder
Trefferanzahl noch Matchmetriken. Andere Querytreffer werden nicht als Kontext
dupliziert. Ohne Option bleibt das Kontextfenster leer.

Der versionierte Retrievaltext `mail-retrieval-text-v1` entfernt nur starke
zeilenbasierte Marker fuer Zitatverlauf, `>`-Zitatzeilen, RFC-Signaturtrenner und
bekannte Disclaimergrenzen. Nur dieser Text wird in Mail-FTS gerankt. Der
unveraenderte Chunk bleibt die zitierbare Quelle und erzeugt den Snippet. Eine
Normalisiererversion ist an jedem Mailcontent und im Suchergebnis sichtbar.

## Konsequenzen

- Headerbeziehungen bleiben die belastbare Primaerevidenz; Betreffaehnlichkeit
  kann keine widersprechenden oder defekten Header ueberschreiben.
- Wissensschema 4 fuegt Thread-/Membermetadaten und Retrievaltextversion additiv
  hinzu. Der bestehende FTS wird bei der Migration einmal reproduzierbar neu
  normalisiert; die Originalchunks bleiben unangetastet.
- Ein Locatorwechsel baut die kleine deterministische Graphsicht neu auf, schreibt
  aber weder Content noch FTS-Body um.
- Kontext erweitert nur die Anzeige eines belegten Treffers. Er darf nicht zum
  Beweis fuer den Suchbegriff oder eine Abwesenheitsaussage werden.
- Semantische Suche, Embeddings, Live-Locator-Fallback und produktive
  Suchpraeferenz bleiben ausserhalb M11.5.

## Verifikation

`tests/test_mail_threads_m115.py` prueft vollstaendige, fehlende, kaputte,
ueberlange, zyklische und selbstbezogene Header, deutsche und englische
Reply-/Forward-Prefixe, geaenderte Betreffe, mehrere Teilnehmer, unbekanntes BCC,
identische Newsletter/Rechnungen, stabile Moves, Kontextgrenzen und die
unveraenderte zitierbare Quelle.

`scripts/benchmark_mail_threads_m115.py` misst den Graphen gegen die synthetischen
M11.0-Threadlabels. Die eingefrorene Referenz unter
`docs/architecture/mail-thread-baseline-m115.json` umfasst 13 Nachrichten und
10 Threads: alle 3 erwarteten Paare wurden gefunden, es gab 0 Fehlverknuepfungen,
Pair-Precision/Recall 1,0 und Mislink-Rate 0,0. Das kleine synthetische Ergebnis
ist eine Regressionbaseline, keine behauptete Produktivqualitaet.
