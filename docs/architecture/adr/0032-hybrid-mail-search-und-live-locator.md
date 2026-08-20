# ADR-0032: Hybrid-Mail-Suche mit fail-closed Live-Locator

- Status: Accepted
- Datum: 2026-08-20
- Entscheider: Data Maintainers, Security Maintainers, Tool Contract Maintainers
- Bezug: M11.7, ADR-0017, ADR-0026 bis ADR-0031

## Kontext

Die bisherige serverseitige Suche liest jeden IMAP-Ordner und ist dadurch fuer
haeufige inhaltliche Fragen langsam. Der lokale Index liefert schnelle Lexik,
Filter, Tags, Threads und vorbereitete semantische Kandidaten, ist aber nur ein
Snapshot. Ein Mailclient kann zwischen Indexlauf, Suche und `mail read` eine
Nachricht verschieben oder kopieren. Ein lokaler Treffer darf daher weder einen
veralteten Locator als aktuell ausgeben noch eine externe Mailaktion autorisieren.

Ein stiller Wechsel zwischen lokalem Index und Server wuerde ausserdem falsche
Abwesenheitsbehauptungen und schwer erklaerbares Ranking ermoeglichen. Coverage,
Frische, semantische Degradation und Fallback muessen Teil jedes Ergebnisses sein.

## Entscheidung

Der kompatible Einstieg `mail search --query ... --limit ...` verwendet im
Standardmodus `auto` den lokalen Pfad nur, wenn die letzte Generation
vollstaendig, autoritativ und innerhalb der konfigurierten Altersgrenze liegt,
FTS vorhanden ist und jeder Content einen aktuellen Locator besitzt. Fehlt eine
dieser Voraussetzungen, wird vor der lokalen Query sichtbar auf die bestehende
Serversuche gewechselt. `--mode local` und `--mode server` dienen der Diagnose;
der lokale Modus kann bei fehlendem Nachweis niemals `complete=true` liefern.

Lokale Treffer werden mit `mail-hybrid-rrf-v1` fusioniert. Das Verfahren ist
gewichtetes Reciprocal Rank Fusion mit `k=60`: Lexik 1,0, Semantik 0,7,
strukturierter Filterbeleg 0,10 und vorhandener Threadkontext 0,05. Jede
Komponente, ihr Rang und der Matchgrund werden ausgegeben. Ein nur semantischer
Treffer bleibt `role=semantic-candidate`, `query_match=false` und
`evidence_for_query=false`; Fusion macht aus Aehnlichkeit keine Tatsache.

Vor Rueckgabe positiver lokaler Treffer validiert der Mailadapter nur deren
indexierte physische Ordner und Mailbox-IDs am Server. Bei mehreren gueltigen
Occurrences wird deterministisch zuerst eine nicht quarantinierte Kombination
aus Ordner, ID und Occurrence gewaehlt. Ist der alte Locator verschwunden, darf
eine begrenzte ordnerweite Neuaufloesung nur exakt passenden Betreff und Absender
akzeptieren. Genau ein Treffer wird als `resolved-after-move` markiert; null oder
mehrere Treffer sind `missing` beziehungsweise `conflict`. Im Automatikmodus
fuehrt jeder unvollstaendige Locatornachweis zum sichtbaren Server-Fallback.

`mail read` verlangt im Agenten-CLI den erwarteten Betreff und prueft aktuellen
Ordner, Mailbox-ID und Betreff erneut. Eine verschwundene Kombination endet mit
`mail-locator-conflict`; es gibt keinen Export anhand einer ungeprueften alten
ID. Der Index autorisiert weiterhin kein Lesen, Verschieben, Antworten oder
Senden.

Die neuen read-only Werkzeuge `mail index status` und `mail index doctor`
berichten Coverage, Frische, Generation, SQLite, FTS, Locator und Embeddings.
`mail index plan`, der explizit freizugebende Backfill und die explizit
freizugebende Reconciliation bleiben getrennte Vertraege. M11.7 startet keinen
Job, fuehrt keinen Backfill aus und aktiviert kein Embeddingmodell.

## Konsequenzen

- Ein frischer vollstaendiger Nulltreffer braucht keine ordnerweise IMAP-Suche
  und darf Abwesenheit belegen.
- Positive lokale Treffer verursachen nur eine begrenzte Live-Pruefung ihrer
  Kandidatenordner; eine Vollkontosuche erfolgt erst nach einem Locatorbruch.
- Teil-, Stale-, FTS-, Korruptions- und Locatorfehler bleiben sichtbar und koennen
  kein gruenes lokales Negativergebnis erzeugen.
- Ein Semantikausfall behaelt belegte FTS-Treffer mit
  `degraded-lexical-only`. Ohne freigegebenes Modell lautet der Zustand
  `disabled`; es wird nichts gepullt oder gestartet.
- Server-Fallback kann lokale Kategorie-, Review-, Anhangs- und freie Tagfilter
  nicht beweisen. Solche Antworten nennen `filter_limitations` und bleiben
  `complete=false`.
- Suchquery und Mailbody bleiben Daten. Die Orchestrierung erzeugt weder
  ActionPlan noch Toolaufruf und schreibt weder IMAP noch lokale Tags.

## Verifikation

`tests/test_mail_hybrid_search_m117.py` prueft frische, veraltete, teilweise,
nicht autoritative, locatorlose und FTS-lose Zustaende, Backend-Aufrufzahlen,
semantische Degradation, RRF-Provenienz, Move-Neuaufloesung, Kopiekonflikte,
deterministische Mehrfach-Locatorwahl, Read-Konflikt, Prompt-Injection und
Seiteneffektfreiheit ausschliesslich mit synthetischen Daten.
