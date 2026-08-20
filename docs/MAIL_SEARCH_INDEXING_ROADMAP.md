# M11-Roadmap: Schnelle, vollstaendige und kontextuelle Mail-Suche

Stand: 2026-08-20
Arbeitsbranch: `development/mail-search-indexing-m11`
Status: M11.0 bis M11.5 abgeschlossen; M11.6 bis M11.8 nicht begonnen

## Ziel

M11 macht das gesamte freigegebene Mailkonto schnell, nachvollziehbar und
kontextbezogen durchsuchbar. Der Agent soll eine Nachricht ueber exakte Begriffe,
Absender, Zeitraum, Ordner, Anhangsmerkmale, lokale Tags, Gespraechszusammenhang
und inhaltlich aehnliche Formulierungen finden koennen. Ein vollstaendiger lokaler
Index ersetzt dabei die heutige lineare Suche ueber jeden IMAP-Ordner fuer den
Normalfall. Eine aktuelle Serverabfrage bleibt als klar ausgewiesener
Frische-/Fallbackpfad erhalten.

Der Index ist keine zweite Wahrheit ueber das Postfach. Die aktuelle Mail auf dem
IMAP-Server bleibt fuer Lesen oder eine spaetere Einzelaktion autoritativ. Ein
Suchtreffer muss deshalb seine Quellgeneration, Aktualitaet, Abdeckung und einen
erneut pruefbaren aktuellen Mail-Locator ausweisen. Ein unvollstaendiger oder
veralteter Index darf niemals die Aussage begruenden, eine Mail existiere nicht.

Semantische Suche dient ausschliesslich dem Retrieval. Sie darf keine Nachricht,
keinen Absender, keinen Inhalt und keine Beziehung erfinden. Antworten des
Agenten muessen auf tatsaechlichen Treffern, begrenzten Textausschnitten und
aktuellen Quellnachweisen beruhen. Mailinhalte bleiben unvertrauenswuerdige Daten
und niemals Anweisungen.

## Nicht-Ziele

- M11 verschiebt, loescht, versendet oder markiert keine Mail auf dem IMAP-Server.
- Lokale Such-Tags sind keine IMAP-Flags und keine Provider-Labels.
- Es gibt keinen unkontrollierten Volltext- oder Mailupload zu einem externen
  Embedding-, Such- oder KI-Dienst.
- M11 senkt keine Spam-, Antivirus-, Weiterleitungs- oder
  Klassifikationsschwellen.
- M11 fuehrt keine automatische Antwort, keinen Versand und keine Mailaktion aus.
- M11 interpretiert semantische Aehnlichkeit nicht als belegte Tatsache.
- M11 startet, aktiviert oder repariert beim Entwicklungsrollout keine
  produktiven Jobs und verarbeitet keinen produktiven Backlog ohne separaten
  Auftrag.

## Verifizierter Ist-Stand

Der Stand `3.4.0-r28` besitzt bereits zwei verschiedene Suchpfade:

1. `mail search` fragt ueber Himalaya jeden lesbaren IMAP-Ordner einzeln ab. Die
   Suche kombiniert bis zu zwoelf Begriffe ueber Absender, Betreff und Body,
   sammelt bis zu 200 Treffer pro Ordner und wendet das globale Limit erst danach
   an. Das ist fuer aktuelle Serverdaten korrekt, skaliert aber in Laufzeit und
   Netzlast mit der Zahl der Ordner. Bei heute mehr als zwanzig Ordnern entstehen
   viele serielle Backend-Aufrufe.
2. `assistant search` verwendet den lokalen Wissensindex mit SQLite FTS5. Der
   Mailworker publiziert dafuer seit M9.6 unveraenderliche JSON-Datensaetze und
   ein atomares `_projection.json`-Manifest. Der Sync-Worker prueft die komplette
   Generation und indiziert sie in `knowledge.sqlite3`.

Erhaltenswerte Grundlagen:

- Mail bleibt alleiniger Owner von `mail_agent.sqlite3` und der
  Mail-Suchprojektion.
- Der Sync-Worker liest Mail-State nur read-only und schreibt ausschliesslich den
  Wissensindex.
- Projektionsdatensaetze sind ueber Stable-Key, Quellzeitpunkt und SHA-256 an eine
  atomar veroeffentlichte Generation gebunden.
- Fehlende, veraltete, teilweise oder korrupte Projektionen werden vor dem ersten
  Indexwrite abgelehnt.
- SQLite FTS5, Dokument-/Chunk-Tabellen und strukturierte Metadaten existieren.
- Der Ollama-Koordinator unterstuetzt Embedding-Endpunkte; der semantische Provider
  ist absichtlich noch `disabled`.
- `mail search` berichtet bereits `complete`, `folder_errors`,
  `results_may_be_truncated` und begrenzte Ordnerfehler.

Nachgewiesene Luecken:

- Die Projektion enthaelt nur Mails, die der Mailworker bereits verarbeitet hat;
  ein kontrollierter Vollkonto-Backfill fehlt.
- Der lokale Index kann seine Abdeckung gegen den aktuellen IMAP-Bestand noch
  nicht beweisen.
- Der aktuelle Ein-Manifest-Ansatz schreibt bei jedem neuen Datensatz die gesamte
  Referenzliste neu und ist fuer ein grosses Postfach nicht die endgueltige
  inkrementelle Struktur.
- Verschobene oder auf dem Server entfernte Nachrichten besitzen noch keinen
  vollstaendigkeitsgesicherten Locator-/Tombstone-Vertrag.
- Lokale FTS-Ergebnisse koennen mehrere Chunks derselben Mail liefern, zeigen nur
  den Anfang eines Chunks und besitzen keine robuste strukturierte Query-Syntax.
- Der Wissensindex kennt noch keinen sicheren Weg von einem Treffer zur aktuell
  auf dem Server gueltigen Kombination aus Ordner und Mailbox-ID.
- Gespraechsfaeden aus `Message-ID`, `In-Reply-To` und `References` werden nicht
  modelliert.
- Ein typisiertes, herkunftsbelegtes lokales Tagging fuer Suche und Filterung
  fehlt.
- `semantic_provider = "ollama"` ist nur ein vorbereiteter Konfigurationswert;
  Modellwahl, Embedding-Schema, Ranking und Qualitaetsnachweis fehlen.
- Der Agentvertrag fordert fuer aktuelle Mailfragen den Serverpfad, obwohl ein
  vollstaendiger, frischer lokaler Index der schnellere Normalpfad sein soll.

## Zielarchitektur

```text
                    IMAP (read-only fuer M11)
                              |
                              v
                  Mail-Owner / Inventory-Crawler
                  - Ordnerinventar und Checkpoints
                  - IMAP-Deltas und periodische Reconciliation
                  - ClamAV-Gate und Parser
                  - Content-/Occurrence-Identitaet und Thread-Header
                  - belegte lokale Tags
                              |
                              v
              partitionierte, atomare Suchprojektion v2
              - immutable content-addressed records
              - Ordner-/Shard-Manifeste mit Digests
              - Root-Generation mit Coverage-Nachweis
                              |
                       read-only mount
                              v
                         Sync-Worker
                  - komplette Validierung
                  - transaktionaler Delta-Import
                  - FTS / Tags / Threads / Locator
                  - versionierte lokale Embeddings
                              |
                              v
                         Gateway-Suche
              lexical + structured + semantic + context
                              |
              Frische-/Coverage-Gate und Server-Fallback
                              |
                              v
             belegte Treffer mit aktuellem Live-Locator
```

### Daten- und Rollenvertrag

- Der Mailworker beziehungsweise ein von ihm kontrollierter Read-only-Crawler
  ist alleiniger Publisher der Mail-Suchquelle.
- Der Sync-Worker erhaelt weiterhin kein Schreibrecht auf Mail-State. Er darf den
  Index nur aus einer vollstaendig validierten Quellgeneration aktualisieren.
- Der Gateway-Prozess liest den Wissensindex read-only und schreibt weder
  Mailprojektion noch Wissensindex.
- Ein produktiver Backfill ist ein begrenzter, wiederaufnehmbarer Lesevorgang mit
  lokaler Indexwirkung. Er benoetigt eine eigene explizite Freigabe und darf
  niemals mit einem zweiten Mailwriter verwechselt werden.
- Die Suchprojektion und der Wissensindex enthalten private Maildaten. Sie liegen
  nur unter geschuetztem `/srv/openclaw`-State, nie in Git, Wheel, Image,
  Telemetrie oder oeffentlichen CI-Artefakten.
- Backups und Migrationen folgen dem bestehenden Data-Owner- und
  SQLite-Integritaetsvertrag. Eine produktive Datenbank wird nie zur Reparatur
  geloescht oder leer neu erstellt.

### Identitaet, Vollstaendigkeit und Aktualitaet

Die Suchdaten trennen drei Ebenen, damit Aenderungen durch Webmail, Smartphone,
Desktop-Mailclient, Provider-Spamfilter oder serverseitige Regeln nicht als neue
Mailinhalte verarbeitet werden:

- `content_id`: inhaltsbezogene Identitaet fuer Parsertext, Chunks, Threads und
  Embeddings. Sie ist konto-/ressourcengebunden und verwendet kanonische
  `Message-ID`, Raw-SHA-256 und weitere belegte Konfliktmerkmale nach dem in M11.1
  festgelegten Vertrag. Eine `Message-ID` allein ist weder eindeutig noch
  ausreichend.
- `occurrence_id`: Identitaet einer physischen IMAP-Auspraegung. Kopien derselben
  Mail duerfen mehrere Occurrences besitzen, ohne den Inhalt mehrfach zu
  analysieren.
- `locator`: aktueller Fundort einer Occurrence aus Ressourcen-/Konto-ID, Ordner,
  UIDVALIDITY und UID beziehungsweise einem dokumentierten Connector-Fallback.
  Ein Content kann gleichzeitig mehrere aktuelle Locator besitzen.

Jeder indizierte Datensatz benoetigt mindestens:

- Ressourcen-/Konto-ID,
- `content_id`, `occurrence_id` und einen oder mehrere aktuelle
  Mailbox-Locator,
- kanonische `Message-ID`, Raw-SHA-256 und den angewandten Identitaetsnachweis,
- sofern vom Connector belastbar lieferbar: UIDVALIDITY und UID,
- Quellzeitpunkt, empfangenen/gesendeten Zeitpunkt und Indexzeitpunkt,
- Record-, Partition- und Root-Generationsdigest,
- Parser-, Normalisierungs-, Tag- und optional Embedding-Version.

`complete = true` ist nur erlaubt, wenn alle freigegebenen Ordner erfolgreich
inventarisiert wurden und die Root-Generation genau diese Ordnergenerationen
bindet. Ein Fehler in einem Ordner macht die Gesamtgeneration unvollstaendig.
Fehlende Mails werden nur nach einem vollstaendigen autoritativen Abgleich als
verschoben oder entfernt markiert. Ein abgebrochener Lauf darf keine Tombstones
erzeugen. Das Auftauchen eines zweiten Locator ist zunaechst eine Kopie und erst
nach bestaetigtem Verschwinden des bisherigen Locator eine Verschiebung. Das
Entfernen einer einzelnen Occurrence tombstoned nicht den Content, solange ein
weiterer belegter Locator existiert. Ein UIDVALIDITY-Reset, Ordnerrename oder
eine kurzzeitig ueberlappende Copy/Delete-Folge wird als eigener Konfliktfall
behandelt und niemals aus einer einzelnen Message-ID erraten.

### Lokaler Tagging-Vertrag

Tags dienen Filterung, Ranking und Erklaerung. Sie werden nicht auf den
Mailprovider zurueckgeschrieben. Der Kern verwendet geschlossene Namensraeume,
zum Beispiel:

- `folder:<kanonischer-ordner>`,
- `sender-domain:<domain>`,
- `participant:<normalisierte-adresse>` nur im geschuetzten Index,
- `has:attachment`, `attachment-type:pdf`,
- `category:<typisierte-kategorie>`,
- `review:<typisierter-grund>`,
- `thread:<stabile-thread-id>`,
- `year:<yyyy>` und `month:<yyyy-mm>`,
- belegte Fachdaten wie `invoice`, `order`, `calendar` nur aus bestehenden
  typisierten Extraktoren.

Jeder nicht rein strukturelle Tag speichert Quelle, Regel-/Modellversion,
Konfidenz und gegebenenfalls belegte Textspanne. Ein Modell darf keine freien
Tags still zur Wahrheit machen. Unsichere Vorschlaege bleiben getrennt von
aktiven Such-Tags; Nutzer-Tags oder eine spaetere Provider-Synchronisierung
brauchen einen eigenen expliziten Werkzeug- und Freigabevertrag.

### Such- und Antwortvertrag

- Lexikalische Suche ist der schnelle, deterministische Basispfad.
- Strukturierte Filter werden vor Ranking und Limit angewendet.
- Semantische Suche erweitert den Recall, ersetzt aber weder exakte Treffer noch
  Quellenpruefung.
- Hybridranking weist Teil-Scores und angewandte Boosts aus und gruppiert mehrere
  Chunks derselben Mail.
- Ein Treffer zeigt query-zentrierten Snippet, Datum, Absender, Ordner, Tags,
  Match-Grund, Indexalter und Quellgeneration.
- Thread-Kontext wird begrenzt und als Kontext gekennzeichnet; Kontextnachrichten
  werden nicht als eigene Query-Treffer ausgegeben.
- Vor `mail read` oder einer spaeteren Aktion wird der aktuelle Server-Locator
  erneut verifiziert. Ein veralteter Locator ist ein Konflikt, kein Erfolg.
- Nur ein vollstaendiger und ausreichend frischer Index oder eine erfolgreiche
  vollstaendige Serverabfrage darf Abwesenheit belegen.

## Messbare Qualitaetskriterien

M11.0 misst zuerst den Ausgangswert. Vor diesen Messungen werden keine
willkuerlichen Grenzwerte als angebliche Baseline festgeschrieben. Die spaetere
Abnahme muss mindestens folgende Dimensionen reproduzierbar berichten:

- Zahl freigegebener IMAP-Ordner und Nachrichten,
- Zahl und Anteil indizierter, blockierter, veralteter und locatorloser Mails,
- Projektions-/Indexgeneration und Alter,
- Erst-Backfill-Dauer, Durchsatz, CPU-, RAM-, Netz- und Plattenbedarf,
- inkrementelle Aktualisierungsdauer ohne und mit Aenderungen,
- inkrementelle Aktualisierungsdauer fuer einzelnen Move, Copy/Delete-Folge,
  Quarantaenewechsel und groessere Move-Gruppe,
- Zahl gelesener Header/Bodies, uebertragene Bytes, geaenderte FTS-Dokumente und
  wiederverwendete beziehungsweise neu berechnete Parser-/Embedding-Ergebnisse,
- Suchlatenz p50/p95/p99 fuer lexikalisch, strukturiert, semantisch und hybrid,
- Cold-/Warm-Start und Ollama-Queue-Wartezeit getrennt,
- Recall@5/10, MRR und nDCG@10 auf einem sanitisierten Goldkorpus,
- Trefferduplikate, veraltete Locator und falsch gebildete Threads,
- Server-/Index-Uebereinstimmung fuer Stichproben und Negativsuchen,
- Zahl unvollstaendiger oder fallbackbeduerftiger Suchen,
- Embedding-Abdeckung nach Modell-/Normalisiererversion.

Nach M11.0 werden Zielgrenzen aus gemessener Postfachgroesse und Nutzererwartung
als eigener dokumentierter Beschluss festgelegt. Jede spaetere Verschlechterung
gegen die eingefrorene Baseline muss in CI oder dem hermetischen Lasttest sichtbar
werden.

## Sicherheits- und Arbeitsvertrag

- Jedes Paket wird einzeln implementiert, getestet, dokumentiert und committet.
- Ein Paket beginnt erst nach gruener Abnahme seines Vorgaengers.
- Entwicklung verwendet synthetische EML-Fixtures, temporaere SQLite-Datenbanken,
  einen Fake-IMAP-Server und Fake-Embeddings. Produktive Mails und
  `/srv/openclaw` bleiben unveraendert.
- Mailinhalte, Adressen, Betreffe, Snippets und Embeddings gelangen nicht in Git,
  Testlogs, Metriklabels oder oeffentliche Artefakte.
- Komplette Raw-Mails und physische Anhaenge passieren das bestehende
  fail-closed ClamAV-Gate. Scannerfehler duerfen nicht durch reines Indexing
  umgangen werden.
- Mailinhalt wird niemals als Anweisung ausgefuehrt. Suchtext wird fuer FTS,
  Queryparser, Logs und Modellprompts als unvertrauenswuerdige Eingabe behandelt.
- Neue Faehigkeiten existieren nur mit stabiler CLI, typisiertem Toolkatalog,
  Skill-/Mailreferenz und echtem Verhaltensregressionstest.
- Starts, Jobaktivierung, produktiver Backfill, Embedding-Modellinstallation und
  produktiver Rollout bleiben getrennte explizite Betriebsschritte.
- Keine Mailbewegung, kein Versand, Delete, EXPUNGE, Flag-/Label-Write oder
  Rechteausweitung wird in M11 implizit genehmigt.
- Ein semantischer Fehler oder Ollama-Ausfall degradiert sichtbar auf lexikalische
  Suche. Er darf nicht die gesamte belegte Mail-Suche unnoetig blockieren.
- Ein fehlender oder unvollstaendiger lexikalischer Index degradiert auf den
  bestehenden Serverpfad und meldet die Einschraenkung. Er darf nicht leer als
  vollstaendiges Ergebnis erscheinen.

## Paketuebersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M11.0 | Reproduzierbare Such-, Coverage- und Performance-Baseline | `3.4.0-r28` |
| M11.1 | Versionierter Suchdaten-, Identitaets- und Migrationsvertrag | M11.0 |
| M11.2 | Begrenzter, wiederaufnehmbarer Vollkonto-Backfill | M11.1 |
| M11.3 | Effiziente inkrementelle Aktualisierung und Reconciliation | M11.2 |
| M11.4 | Schnelle lexikalische Suche und belegte lokale Tags | M11.3 |
| M11.5 | Konservativer Thread- und Kontextindex | M11.4 |
| M11.6 | Evaluierte lokale Embeddings und semantisches Retrieval | M11.5 |
| M11.7 | Agentengerechte Hybrid-Suche mit Live-Locator und Fallback | M11.6 |
| M11.8 | Gesamt-Abnahme, Dokumentation und kontrollierter Rollout | M11.7 |

## M11.0 – Baseline und sanitierter Suchkorpus

Status: abgeschlossen am 2026-08-19. Reproduktionsbefehle, Messwerte,
Datenschutzgrenzen und bekannte Luecken stehen in
[`MAIL_SEARCH_BASELINE_M110.md`](MAIL_SEARCH_BASELINE_M110.md).

### Scope

- Bestehende Pfade `mail search` und `assistant search --source-type email`
  charakterisieren, ohne ihre Semantik zu aendern.
- Reproduzierbare, inhaltsfreie Bestandsaggregate fuer Ordnerzahl,
  Nachrichtenzahl, Projektionsrecords, FTS-Dokumente, Indexalter und bekannte
  Luecken definieren.
- Laufzeit, Backend-Aufrufe, CPU, RAM und Ergebnisvollstaendigkeit fuer typische
  Suchmuster messen: einzelner exakter Begriff, mehrere Begriffe, Absender,
  Zeitraum, Bodybegriff, Nulltreffer und sehr haeufiger Begriff.
- Den heutigen Aufwand beziehungsweise die noch fehlende Unterstuetzung fuer
  No-op, neue Mail, Kopie, externen Move, Move nach/aus Quarantaene und
  UIDVALIDITY-Wechsel mit Fake-IMAP charakterisieren, ohne bereits eine
  Synchronisierung zu implementieren.
- Einen sanitisierten Goldkorpus aus vollstaendig synthetischen EMLs und
  realistischen deutschen/englischen Suchfragen anlegen. Keine produktiven
  Betreffe, Adressen, Bodies oder Message-IDs in Git uebernehmen.
- Relevanzlabels und erwartete Treffer fuer lexikalische, kontextuelle und
  semantische Fragen definieren.
- Noch keine Datenbank, Projektion, CLI, Suche oder produktive Konfiguration
  veraendern.

### Pflichttests und Abnahme

- Charakterisierungstests belegen aktuelle Mehrordner-Suche, Limits,
  Nulltreffer, Ordnerfehler und Trunkierung.
- Der lokale FTS-Pfad ist fuer Chunk-Duplikate, Queryfehler, Snippets und
  fehlende Mail-Locator charakterisiert.
- Baseline-Benchmark kann offline mit Fake-IMAP reproduziert werden.
- Die Baseline weist fuer No-op, neue Mail, Copy/Move und UIDVALIDITY-Wechsel
  Backendaufrufe, Header-/Body-Fetches und uebertragene Bytes getrennt aus oder
  markiert eine heute nicht vorhandene Faehigkeit ausdruecklich als solche.
- Produktive Diagnosebefehle geben nur Aggregate aus und sind gesondert als
  optionaler, read-only Betriebscheck dokumentiert.
- Goldkorpus und Benchmarkreport enthalten keine Geheimnisse oder privaten
  Mailinhalte.
- `check-repo.sh`, `git diff --check` und Quellmanifestpruefung bleiben gruen.

### Entwicklungsprompt

```text
Setze ausschliesslich M11.0 aus docs/MAIL_SEARCH_INDEXING_ROADMAP.md um. Lies
AGENTS.md, skills/personal-assistant/references/mail.md, den generierten
Toolvertrag, docs/SEARCH.md und ADR-0017 vollstaendig. Fuehre zuerst
./scripts/assistant.sh version --verify und git status --short aus. Veraendere
keine Datei unter /srv/openclaw, kein produktives Postfach und keinen Jobzustand.
Charakterisiere die heutige ordnerweise IMAP-Suche und den lokalen FTS-Pfad mit
Fake-IMAP und temporaeren Datenbanken. Vermesse dabei auch No-op, neue Mail,
Kopie, externen Move, Quarantaenewechsel und UIDVALIDITY-Reset, ohne fehlende
Faehigkeiten vorzutaeuschen. Lege einen rein synthetischen deutschen und
englischen Goldkorpus samt erwarteten Trefferlisten an und erfasse Coverage-,
Qualitaets-, Latenz- und Ressourcen-Baselines reproduzierbar. Definiere noch keine
willkuerlichen Zielgrenzen und aendere weder Schema, Projektion, Suchranking, CLI
noch Konfiguration. Dokumentiere Befehle und Datenschutzgrenzen, aktualisiere das
Quellmanifest, fuehre den vollstaendigen Repository-Check aus und stoppe nach
M11.0.
```

## M11.1 – Suchdaten-, Identitaets- und Migrationsvertrag

Status: abgeschlossen am 2026-08-19. Die Vertragsentscheidung steht in
[ADR-0026](architecture/adr/0026-versionierter-mail-suchdatenvertrag.md). Es wurde
weder ein produktiver Backfill noch v2-Publikation oder neues Ranking aktiviert.

### Scope

- Eine ADR fuer die vollstaendige Mail-Sucharchitektur und den Unterschied
  zwischen Mail-Source-of-Truth, Suchprojektion, Wissensindex und Live-Locator
  erstellen.
- Projektionsschema v2 entwerfen: versionierte Records, partitionierte
  Ordner-/Shard-Manifeste und atomare Root-Generation. Unveraenderte Partitionen
  duerfen wiederverwendet werden, ohne die gesamte Recordliste bei jeder Mail neu
  zu schreiben.
- V1-Projektionen weiterhin sicher lesen und eine additive, wiederholbare
  Migration beziehungsweise Neupublikation vorbereiten.
- Wissensschema additiv um Locator, Tags, Threadkanten, Indexgeneration,
  Quellstatus und Embeddingversion ergaenzen. Bestehende Dokumente und
  Sync-Historie bleiben erhalten.
- `content_id`, `occurrence_id`, Locator-Menge, Message-ID-/Raw-SHA-Nachweis,
  Ordner-/Mailbox-Locator, optionale UIDVALIDITY/UID-Semantik und Konfliktfaelle
  exakt definieren. Eine Message-ID darf Kopien oder verschiedene physische Mails
  nicht still zusammenfuehren.
- Content-addressed Parser-, FTS- und Embeddingdaten von veraenderlichen Locator-,
  Ordner- und Quarantaenemetadaten trennen, damit ein reiner Locatorwechsel den
  Content-Digest nicht aendert.
- Tombstones nur als Ergebnis eines vollstaendigen autoritativen Ordnerabgleichs
  erlauben.
- Parserfelder `In-Reply-To` und `References` tolerant und begrenzt aufnehmen,
  ohne strikte Realwelt-Mailheader zum Absturz zu bringen.

### Pflichttests und Abnahme

- Golden-Schema-Tests fuer v1, v2, unbekannte Zukunftsversion, fehlende Partition,
  falschen Digest, doppelte Stable-Keys und unsichere Dateinamen.
- Identitaetstests fuer gleiche Message-ID mit verschiedenem Raw-Inhalt,
  identischen Raw-Inhalt in mehreren Ordnern, fehlende Message-ID, Copy/Delete-
  Ueberlappung, UIDVALIDITY-Reset und Ordnerrename.
- Migration einer realistischen v1-Wissensdatenbank ohne Datenverlust; wiederholte
  Migration liefert denselben Zustand.
- Crash vor Record-, Partitions- oder Root-Manifest-Replace laesst die letzte
  vollstaendige Generation lesbar.
- Ein unvollstaendiger Ordner darf keine globale Vollstaendigkeit und keinen
  Tombstone erzeugen.
- Rollen-/Mounttests beweisen unveraenderte Single-Writer- und read-only-Grenzen.
- Keine Such- oder Rankingsemantik wird in diesem Paket aktiviert.

### Entwicklungsprompt

```text
Setze nur M11.1 um und fuehre zuerst die M11.0-Abnahme aus. Erstelle eine ADR und
einen versionierten Suchdatenvertrag fuer eine partitionierte atomare
Mailprojektion v2. Definiere getrennte Content- und Occurrence-Identitaeten,
mehrere aktuelle Locator, Copy-/Move-/Delete-Semantik, UIDVALIDITY-Reset,
Ordnerrename, Ordnergenerationen, Coverage und sichere Tombstones. Stelle sicher,
dass Locator-, Ordner- oder Quarantaenemetadaten keinen unveraenderten
Content-Digest aendern. Erweitere Projektion,
Parser und Wissensschema nur additiv und migrationssicher; v1 bleibt lesbar und
eine wiederholte Migration verliert keine Dokumente oder Sync-Historie. Bewahre
Mail-Owner-, Sync-read-only- und Gateway-read-only-Grenzen. Teste alle Crash-,
Korruptions-, Versions-, Identitaets- und Migrationspfade mit Fixtures. Fuehre
noch keinen Backfill, keine produktive Migration und kein neues Ranking aus.
Aktualisiere Architektur-, Datenkatalog- und Testdokumentation sowie Manifest und
stoppe nach M11.1.
```

## M11.2 – Begrenzter Vollkonto-Backfill

Status: abgeschlossen am 2026-08-19. Der produktive Backfill bleibt eine
separate, explizit freizugebende Betriebsaktion und wurde in M11.2 nicht
ausgefuehrt. Architekturentscheidungen und der ehrliche Himalaya-Fallback stehen
in [ADR-0027](architecture/adr/0027-begrenzter-mail-vollkonto-backfill.md).

### Scope

- Alle freigegebenen Mailordner read-only inventarisieren, statt nur bereits vom
  Mailworker verarbeitete Nachrichten zu kennen.
- Connectorfaehigkeiten fuer Paging, UID/UIDVALIDITY, UIDNEXT, MODSEQ,
  CONDSTORE/QRESYNC, optional IDLE und Body-/Raw-Fetch explizit erkennen.
  Fehlende Providerfaehigkeiten erhalten einen langsameren, aber korrekten
  Fallback statt erfundener Cursorsemantik.
- Ordnerbestand einschliesslich neu angelegter, entfernter oder umbenannter
  freigegebener Ordner erfassen. Provider-Spam-/Quarantaeneordner bleiben den
  bestehenden Rescue-only- und Untrusted-Content-Regeln unterworfen.
- Backfill nach Ordner und Cursor begrenzen, persistent fortsetzen und nach Crash,
  Timeout oder Neustart idempotent wiederaufnehmen.
- Nie das gesamte Konto gleichzeitig in RAM laden. Paging, Bytebudgets,
  Laufzeitbudget, Rate-Limit und kontrollierten Abbruch vorsehen.
- Raw-Mail und physische Anhaenge ueber das bestehende ClamAV-Gate verarbeiten.
  Ein Scannerfehler oder Fund blockiert Bodyindexierung fail-closed und wird als
  inhaltsfreier Status dokumentiert.
- Bodytext, strukturierte Header und Anhangsmetadaten indizieren; Anhaenge selbst
  werden in M11 nicht pauschal volltextindiziert oder extern versendet.
- Einen read-only Plan und einen getrennten explizit freizugebenden lokalen
  Backfillvertrag vorbereiten. In diesem Entwicklungspaket keinen produktiven
  Backfill ausfuehren.

### Pflichttests und Abnahme

- Mehrordner-Fake-IMAP mit mehreren Seiten, leeren Ordnern, Unicode, sehr grossen
  Mails, doppelter Message-ID und fehlender Message-ID.
- Crash und Wiederaufnahme an jeder Seitengrenze ohne doppelte Records.
- Timeout, Rate-Limit, Ordnerfehler und Connector ohne UIDVALIDITY melden
  `complete = false` und erhalten den letzten sicheren Checkpoint.
- Capability-Matrix und Fake-IMAP testen Deltafaehigkeiten, fehlendes
  CONDSTORE/QRESYNC, IDLE-Abbruch, UIDVALIDITY-Reset und Ordnerrename jeweils mit
  korrektem gebundenem Fallback.
- ClamAV-Fund, Scannerfehler und Cacheidentitaetswechsel verhindern
  ungesicherte Bodyindexierung.
- Dry-run/Plan schreibt weder IMAP noch Index; Backfill schreibt nur lokale
  Projektion/Checkpoint und keine Providerflags.
- Spitzenverbrauch und Backend-Aufrufzahl sind im synthetischen Lasttest
  reproduzierbar dokumentiert.

### Entwicklungsprompt

```text
Setze nur M11.2 um. Baue auf dem M11.1-Datenvertrag einen paginierten,
wiederaufnehmbaren Read-only-Vollkonto-Crawler. Er muss alle freigegebenen Ordner
inventarisieren, Connectorfaehigkeiten explizit erkennen, mit festen Laufzeit-,
Byte-, Seiten- und Rate-Limits arbeiten und bei Fehlern unvollstaendig bleiben.
Nutze UIDNEXT/MODSEQ/CONDSTORE/QRESYNC oder optional IDLE nur nach belegter
Serverfaehigkeit und implementiere einen korrekten Fallback. Erfasse neue,
entfernte und umbenannte freigegebene Ordner sowie Quarantaeneordner, ohne deren
Sicherheitsvertrag zu lockern.
Fuehre komplette Raw-Mails und physische Anhaenge durch das bestehende
fail-closed ClamAV-Gate; umgehe keinen Scanner fuer Suchzwecke. Implementiere
Plan/Dry-run und lokalen Checkpointvertrag, aber starte keinen produktiven
Backfill und veraendere keine IMAP-Mail, Flag oder Ordnerstruktur. Teste Paging,
Resume, Crash, doppelte/fehlende IDs, grosse Mails, Rate-Limit, Scannerfehler und
Nullbestand hermetisch. Aktualisiere Dokumentation und Manifest und stoppe nach
M11.2.
```

## M11.3 – Inkrementelle Aktualisierung und Reconciliation

Status: abgeschlossen am 2026-08-20. Der autoritative Delta-, Tombstone-,
Retention- und transaktionale Wissensimportvertrag steht in
[ADR-0028](architecture/adr/0028-transaktionale-mail-reconciliation.md). Die
Allowlist-Policy ist vorbereitet, aber weder als Job aktivierbar noch gestartet.
Der aktuelle Himalaya-Connector bleibt mangels belegter UID-/UIDVALIDITY- und
stabiler Ordner-ID-Semantik fail-closed; produktiver Connector-Rollout und
Erstlauf sind separate spaetere Betriebsauftraege.

### Scope

- Nach dem Erst-Backfill nur neue oder nachweislich geaenderte Mails und
  Ordnerpartitionen verarbeiten.
- Ordner-Cursor und Quellgenerationen transaktional publizieren; ein Checkpoint
  darf erst nach verifizierter Partitionspublikation fortgeschrieben werden.
- Moves durch Webmail, Smartphone, Desktop-Mailclient, Provider-Spamfilter oder
  serverseitige Regeln sowie entfernte Nachrichten durch Deltaabgleich und einen
  vollstaendigen periodischen Abgleich erkennen. Stable Content, physische
  Occurrence und aktuelle Locator-Menge bleiben getrennte Konzepte.
- Copy, Move, Copy/Delete-Ueberlappung, Entfernen nur einer von mehreren
  Occurrences, Ordnerrename und UIDVALIDITY-Reset deterministisch unterscheiden.
- Bei unveraendertem Content Parsertext, Thread, fachliche Tags, Chunks, FTS und
  Embeddings wiederverwenden. Ein Locatorwechsel aktualisiert nur Locator-,
  Ordner-, Quarantaene- und davon abgeleitete strukturierte Suchmetadaten.
- Tombstones beziehungsweise Locatorwechsel nur nach vollstaendigem Ordnerabgleich
  anwenden. Fehlerhafte Teilscans bewahren den letzten vollstaendigen Indexstand.
- Den Sync-Worker Deltas transaktional anwenden lassen. Eine fehlgeschlagene
  Zieltransaktion darf weder Root-Generation noch Sync-Cursor als erfolgreich
  markieren.
- Orphan-Records und alte unveraenderliche Projektionsgenerationen mit einer
  sicheren, generationserhaltenden Retention verwalten; keine aktive oder letzte
  verifizierte Rollbackgeneration entfernen.
- Scheduling als begrenzten allowlisteten Indexjob vorbereiten. Aktivierung bleibt
  ein separater Nutzerauftrag.

### Pflichttests und Abnahme

- Keine-Aenderung-Lauf verursacht keine Body-Reexports und keinen kompletten
  FTS-Neuaufbau.
- Ein eindeutig aus Delta-/Locatornachweisen erkennbarer externer Move verursacht
  keinen Raw-/Body-Fetch, kein erneutes Parsing/OCR, keine Modellanfrage, keine
  Embeddingberechnung und keinen kompletten FTS-Neuaufbau. Ist die Identitaet ohne
  Inhaltsnachweis mehrdeutig, ist ein begrenzter Raw-Fetch zur SHA-Verifikation
  erlaubt; bestaetigt er unveraenderten Content, werden Parser-, FTS- und
  Embeddingergebnisse wiederverwendet. Ein ClamAV-Rescan ist nur bei geaendertem
  Inhalt oder nicht mehr passender Scanner-/Signaturidentitaet erlaubt.
- Neue Mail, geaenderter Locator, Copy, Move, Copy/Delete-Ueberlappung, geloeschte
  Occurrence, letzter geloeschter Locator und wiederaufgetauchte Mail sind
  getrennt getestet.
- Ein Move nach oder aus dem Provider-Spam-/Quarantaeneordner aktualisiert Locator
  und belegte Ordner-/Quarantaenemetadaten, behaelt Content-, Thread- und
  Embeddingidentitaet und fuehrt keine Rettung, Mailaktion oder Aenderung der
  Spamregeln aus.
- Inkrementelle Tests zaehlen Header-/Body-Fetches, uebertragene Bytes,
  Parser-/OCR-/ClamAV-/Modellaufrufe, geaenderte FTS-Zeilen und
  wiederverwendete/neue Embeddings fuer No-op, einzelne und gebuendelte Moves.
- Teilscan, Netzverlust und Crash vor/nach Projektion oder Indexcommit erzeugen
  keine falschen Tombstones und keine vorgezogene Generation.
- Paralleler produktiver Mailwriter und Indexcrawler verletzen keine Locks,
  Mountrechte oder Datenowner.
- Retention behaelt aktive und letzte verifizierte Generation und loescht nie
  unaufgefordert Mailquelle oder Wissensdatenbank.
- Inkrementeller Lauf berichtet gesehen, neu, geaendert, verschoben, entfernt,
  unveraendert, blockiert und fehlgeschlagen ohne Mailinhalte in Telemetrie.

### Entwicklungsprompt

```text
Setze nur M11.3 um. Implementiere einen effizienten inkrementellen
Projektions-/Indexpfad auf Basis vollstaendiger Ordnergenerationen. Trenne Content,
Occurrence und Locator-Menge. Erkenne Aenderungen externer Mailclients,
Provider-Spamfilter und Serverregeln; unterscheide Copy, Move, Delete,
Ordnerrename und UIDVALIDITY-Reset. Ein belegter reiner Locatorwechsel muss
Content, Parsertext, Thread, FTS-Text und Embeddings wiederverwenden. Er darf nur
bei mehrdeutiger Identitaet einen begrenzten Raw-Fetch zur SHA-Verifikation und
bei geaenderter Scanneridentitaet einen ClamAV-Rescan anstossen, aber keine
erneute teure Inhaltsverarbeitung. Erzeuge Moves oder Tombstones nur nach
vollstaendig erfolgreichem autoritativem Abgleich; ein Teilscan darf den letzten
vollstaendigen Stand nicht beschaedigen. Wende Deltas im Wissensindex
transaktional an und schreibe Cursor erst nach verifiziertem Commit fort. Teste
No-op, neue Mail, Copy, Move, Quarantaenewechsel, Delete, Wiederkehr,
UIDVALIDITY-Reset, Netzverlust, Crash an jeder Publikationsgrenze, parallelen
Writer und Retention. Messe Backend-, Fetch-, Byte-, FTS-, Scanner- und
Modellaufwand. Bereite den allowlisteten Job
vor, aktiviere oder starte ihn aber nicht. Aendere keine produktiven Rechte oder
Postfachdaten und stoppe nach M11.3.
```

## M11.4 – Schnelle lexikalische Suche und lokale Tags

Status: abgeschlossen am 2026-08-20. Query-, Ranking- und Tagentscheidung stehen
in [ADR-0029](architecture/adr/0029-sichere-lokale-mail-lexik-und-tags.md); der
produktive Suchpfad und Jobs wurden nicht umgeschaltet. Vorhandene typisierte
Kategorie-, Review- und Extraktorentscheidungen werden im Backfill und in der
Reconciliation read-only als belegte lokale Tags uebernommen.

### Scope

- Eine sichere Queryschicht vor FTS5 setzen. Nutzereingaben werden nicht als rohe
  FTS-Syntax ausgefuehrt; Phrasen, Prefixe und Sonderzeichen sind begrenzt und
  deterministisch geparst.
- Strukturierte Filter mindestens fuer Absender/Teilnehmer, Zeitraum, Ordner,
  Kategorie, Review-Grund, Anhang vorhanden/Typ und lokale Tags anbieten.
- BM25 mit dokumentierten Feldgewichten fuer Betreff, Absender und Body verwenden.
  Exakte Phrase und exakter Absender duerfen belegte Boosts erhalten; Recency darf
  alte relevante Treffer nicht unerklaert verdraengen.
- Mehrere Chunks derselben Mail vor dem finalen Limit gruppieren und den besten
  query-zentrierten Snippet liefern.
- Geschlossene lokale Tag-Namensraeume, Herkunft, Version, Konfidenz und
  Evidenzspanne implementieren. Keine Tags auf IMAP schreiben.
- Ordner-/Quarantaene-Tags aus der aktuellen Locator-Menge ableiten und bei einem
  Move ohne erneute inhaltliche Klassifikation aktualisieren.
- Index- und Querymetriken nur mit technischen Zaehlern und Latenzen erfassen;
  keine Suchbegriffe, Mailadressen oder Snippets als Labels/Logs speichern.

### Pflichttests und Abnahme

- Golden-Queries fuer Umlaute, Akzente, Gross-/Kleinschreibung, Bindestriche,
  E-Mail-Adressen, Rechnungsnummern, Zitate, Klammern und FTS-Sonderzeichen.
- Strukturierte Filter werden vor Limit und Ranking angewandt.
- Jede Mail erscheint hoechstens einmal, obwohl mehrere Chunks treffen.
- Snippets enthalten den Matchbereich, sind laengenbegrenzt und fuehren keinen
  HTML-/Terminal-Markup-Inhalt aus.
- Tagtests pruefen geschlossene Typen, Herkunft, Version, Unsicherheit und
  fehlende Evidenz. Ein Modellvorschlag wird nicht still aktiver Tag.
- Benchmark vergleicht p50/p95/p99 und Recall gegen M11.0; Verschlechterungen sind
  sichtbar und begruendungspflichtig.

### Entwicklungsprompt

```text
Setze nur M11.4 um. Implementiere eine sichere lokale lexikalische Mail-Suche mit
typisierter Queryschicht, strukturierten Filtern, dokumentiertem BM25-Ranking,
Dokument-Deduplizierung und query-zentrierten Snippets. Fuehre geschlossene lokale
Tag-Namensraeume mit Quelle, Version, Konfidenz und Evidenz ein; schreibe keine
IMAP-Flags oder Providerlabels. Behandle Suchtext als unvertrauenswuerdig und
speichere weder Query noch Snippet in Telemetrie. Teste Unicode, deutsche und
englische Begriffe, FTS-Sonderzeichen, Filterreihenfolge, Chunk-Deduplizierung,
Tagprovenienz und Nulltreffer. Vergleiche Qualitaet und Latenz reproduzierbar mit
M11.0 und stoppe nach M11.4.
```

## M11.5 – Gespraechsfaeden und begrenzter Kontext

### Scope

- Primaere Threadkanten ausschliesslich aus kanonischen `Message-ID`,
  `In-Reply-To` und `References` ableiten.
- Einen konservativen Fallback aus normalisiertem Antwortbetreff, Teilnehmern und
  Zeitfenster nur dann verwenden, wenn keine widersprechende Header-Evidenz
  existiert. Unsichere Fallbackthreads bleiben als unsicher markiert.
- Gleichlautende Newsletter, Rechnungsbetreffe oder leere Betreffe nicht pauschal
  zu einem Thread verschmelzen.
- Threadmetadaten getrennt von einzelnen Maildokumenten speichern. Ein Move oder
  Locatorwechsel aendert nicht automatisch die Threadidentitaet.
- Suchtreffer optional um eine kleine Zahl direkt benachbarter Nachrichten
  erweitern. Kontext wird separat gekennzeichnet und nie als eigener Treffer oder
  Beleg fuer die Query ausgegeben.
- Zitatketten, Signaturen und wiederholte Disclaimer fuer Ranking/Embedding
  versioniert reduzieren, den unveraenderten Body aber fuer Quellenansicht
  erhalten.

### Pflichttests und Abnahme

- Vollstaendige, fehlende, zirkulaere, kaputte und extrem lange
  References-Header.
- Reply/Forward in Deutsch und Englisch, geaenderter Betreff, mehrere Teilnehmer,
  BCC-unbekannt und identische Newsletterbetreffe.
- Kein Thread kann sich selbst zyklisch als Vorfahr enthalten.
- Kontextfenster ist begrenzt, chronologisch, dedupliziert und klar als Kontext
  gekennzeichnet.
- Rankingtext-Normalisierung veraendert nicht die gespeicherte zitierbare Quelle
  und ist ueber eine Version reproduzierbar.
- Threadqualitaet und Fehlverknuepfungen werden auf dem M11.0-Goldkorpus gemessen.

### Entwicklungsprompt

```text
Setze nur M11.5 um. Ergaenze einen konservativen Thread-/Kontextindex. Nutze
Message-ID, In-Reply-To und References als primaere Evidenz und einen klar
markierten, streng begrenzten Betreff-/Teilnehmer-/Zeit-Fallback nur ohne
Widerspruch. Verschmelze keine gleichlautenden Newsletter oder Rechnungen. Trenne
Treffer von Kontextnachrichten und begrenze das Kontextfenster. Reduziere
Zitatketten und Signaturen nur in einem versionierten Retrievaltext; die
zitierbare Quelle bleibt unveraendert. Teste kaputte Header, Zyklen, fehlende IDs,
deutsche/englische Replies, identische Betreffe und Kontextgrenzen. Aendere noch
keine semantische Suche und stoppe nach M11.5.
```

## M11.6 – Lokale Embeddings und semantisches Retrieval

### Scope

- Mindestens zwei lokal betreibbare, fuer deutsche und englische Mailtexte
  geeignete Embeddingmodelle anhand des M11.0-Goldkorpus vergleichen. Modell,
  Digest, Dimension, Kontextgrenze, Speicherbedarf, Latenz und Qualitaet
  dokumentieren.
- Keine Modellwahl allein aus Bekanntheit oder Herstellerbeschreibung ableiten.
  Aktivierung erfolgt nur nach gemessener Recall-/Rankingqualitaet und
  Ressourcenpruefung auf der Zielhardware.
- Alle Embeddings ausschliesslich ueber den bestehenden Ollama-Koordinator und
  seine Prioritaets-/Timeoutregeln erzeugen. Kein direkter Bypass zum Upstream.
- Embeddings an Record-SHA, normalisierten Retrievaltext, Chunkversion,
  Modellname/-digest und Dimension binden. Unveraenderte Chunks werden nicht neu
  berechnet.
- Locator-, Ordner- und Quarantaeneaenderungen explizit aus dem Embedding-Key
  ausschliessen; ein Content mit mehreren Occurrences teilt seine Vektoren.
- Geeignete lokale Vektorsuche nach Benchmark waehlen. Fehlende optionale SQLite-
  Erweiterungen muessen sichtbar auf eine korrekte, fuer den gemessenen Bestand
  geeignete Implementierung oder lexikalisch-only degradieren; keine stille
  ungetestete ANN-Abhaengigkeit.
- Hintergrundberechnung begrenzen, wiederaufnehmen und unter interaktiven
  Modellanfragen priorisieren. Ein Modellfehler laesst FTS verfuegbar.
- Semantische Treffer speichern Distanz/Score und Modellversion, nicht die
  Behauptung einer inhaltlichen Wahrheit.

### Pflichttests und Abnahme

- Deterministische Fake-Embeddings testen Speicherung, Dimensionsfehler,
  Modellwechsel, Resume, Cachetreffer und Chunkaenderung.
- Ein externer Move, eine zusaetzliche Kopie und ein Quarantaenewechsel erzeugen
  bei unveraendertem Retrievaltext exakt null neue Embeddinganfragen.
- Ollama-Timeout, Queue-Full, Proxy-Ausfall, falsche Dimension und ungueltige
  Zahlen degradieren sichtbar ohne FTS-Ausfall.
- Modellvergleich berichtet Recall@5/10, MRR, nDCG@10, p50/p95, RAM,
  Plattenbedarf, Cold-/Warm-Zeit und Queuewartezeit.
- Keine produktive Mail oder Embeddingdatei liegt in Git/CI-Artefakten; Tests
  verwenden nur synthetische Texte und Vektoren.
- Kein Modell wird produktiv gepullt, aktiviert oder in Jobs aufgenommen, bevor
  Jan dem dokumentierten Kandidaten explizit zustimmt.

### Entwicklungsprompt

```text
Setze nur M11.6 um. Implementiere einen versionierten lokalen Embeddingvertrag und
vergleiche mindestens zwei fuer deutsche und englische Mailtexte geeignete
Modelle auf dem synthetischen M11.0-Goldkorpus. Fuehre Modellanfragen
ausschliesslich ueber den Ollama-Koordinator aus und respektiere Prioritaet,
Timeout, Queue und Hintergrundlimits. Binde jeden Vektor an Quell-SHA,
Retrievaltextversion, Chunk und Modelldigest; Locator-, Ordner- und
Quarantaeneaenderungen sind kein Teil dieses Schluessels. Teste, dass externe
Moves und Kopien keine neuen Vektoren erzeugen. FTS muss bei jedem semantischen
Fehler verfuegbar bleiben. Teste mit Fake-Vektoren alle Cache-, Resume-,
Dimensions-, Modellwechsel-, Timeout- und Fehlerpfade. Dokumentiere Qualitaet und
Ressourcen statt ein Modell zu erraten. Pull oder aktiviere kein produktives
Modell und starte keinen Job ohne separate Freigabe. Stoppe nach M11.6.
```

## M11.7 – Agentengerechte Hybrid-Suche und Live-Locator

### Scope

- Den bestehenden kompatiblen Einstieg `mail search --query ... --limit ...`
  beibehalten und um typisierte optionale Filter beziehungsweise Modi erweitern.
- Standardmodus `auto`: einen vollstaendigen, frischen lokalen Hybridindex nutzen;
  bei fehlender Coverage oder Frische sichtbar auf den Serverpfad fallen. Ein
  expliziter Modus darf Diagnosezwecke unterstuetzen, aber keine Sicherheitsgates
  umgehen.
- Lexikalische, strukturierte, semantische und Threadsignale mit einem
  dokumentierten, reproduzierbaren Fusionsverfahren zusammenfuehren. Teil-Scores,
  Match-Grund und angewandte Filter bleiben erklaerbar.
- Neue registrierte Diagnosewerkzeuge vorsehen:
  - `mail index status` fuer Coverage, Generation, Alter und semantischen Zustand,
  - `mail index doctor` fuer Projektion, SQLite, FTS, Locator und Embeddings,
  - `mail index plan` fuer einen read-only Backfill-/Kapazitaetsplan,
  - einen getrennten explizit freizugebenden Backfill/Refresh-Befehl mit rein
    lokaler Indexwirkung.
- Suchantwort mindestens mit `complete`, `coverage`, `freshness`,
  `index_generation`, `semantic_state`, `fallback_used`, `folder_errors` und
  `results_may_be_truncated` versehen.
- Jeder Treffer enthaelt Content-/Occurrence-ID, aktuelle oder als veraltet
  markierte Locator-Menge, einen deterministisch gewaehlten Live-Locator,
  query-zentrierten Snippet, Tags, Threadkontext, Scorekomponenten und eine
  zitierbare Quellreferenz.
- Vor `mail read` aktuellen Ordner, Mailbox-ID und erwarteten Betreff erneut auf
  IMAP validieren. Der Index allein autorisiert keine Aktion.
- Skillbeschreibung anweisen, zuerst den schnellen Pfad zu verwenden, Statusfelder
  immer auszuwerten, bei Unvollstaendigkeit zu verfeinern/fallbacken und keine
  Abwesenheit zu behaupten.

### Pflichttests und Abnahme

- CLI, Handler, Service, Toolkatalog, generierter Skillvertrag, Capabilities und
  Verhaltensregression stimmen fuer jedes neue Werkzeug ueberein.
- Frischer vollstaendiger Index vermeidet die ordnerweise IMAP-Suche.
- Veralteter, teilweiser, korrupter oder locatorloser Index faellt sichtbar auf
  Server oder liefert kein vollstaendiges Negativergebnis.
- Semantic-Ausfall liefert belegte lexikalische Ergebnisse mit degradiertem
  Status; FTS-Ausfall nutzt den sicheren Serverpfad.
- Aktueller Move zwischen Suche und Read wird erkannt und neu aufgeloest oder als
  Konflikt gemeldet.
- Bei mehreren Locatorn wird die konkrete physische Occurrence vor `mail read`
  eindeutig und aktuell aufgeloest; ein verschwundener Locator berechtigt nicht
  zur stillen Auswahl eines ungeprueften Ziels.
- Prompt-Injection in Body oder Suchquery kann weder Toolaufruf noch Mailaktion
  ausloesen.
- Echte Verhaltenspruefungen testen Ergebnismenge, Ranking, Statusfelder,
  Seiteneffektfreiheit und Backend-Aufrufzahl; reine Textsuchen reichen nicht.

### Entwicklungsprompt

```text
Setze nur M11.7 um. Mache den kompatiblen mail-search-Einstieg agentengerecht und
hybrid: frischer vollstaendiger lokaler Index zuerst, klar ausgewiesener
Server-Fallback bei fehlender Coverage oder Frische. Ergaenze typisierte
Index-Status-, Doctor- und Planwerkzeuge sowie einen getrennten lokalen
Backfill/Refresh-Vertrag mit korrekter Approval-Klasse. Jede Suche muss
Vollstaendigkeit, Coverage, Alter, Generation, semantischen Zustand, Fallback und
Ordnerfehler ausgeben. Liefere erklaerbare, deduplizierte Treffer mit Snippet,
Tags, Threadkontext und Live-Locator; validiere vor mail read weiterhin Ordner,
Mailbox-ID und erwarteten Betreff auf dem Server. Aktualisiere den typisierten
Toolkatalog und generiere Skill-/Befehlsreferenzen deterministisch. Teste frischen,
veralteten, korrupten, teilweisen und semantisch degradierten Zustand sowie Move-
Konflikte, Prompt-Injection und Seiteneffektfreiheit. Fuehre keinen produktiven
Backfill oder Jobstart aus und stoppe nach M11.7.
```

## M11.8 – Gesamt-Abnahme und kontrollierter Rollout

### Scope

- Einzelabnahmen M11.0 bis M11.7 erneut ausfuehren und den Baselinevergleich
  reproduzierbar dokumentieren.
- Zentrale Mail-, Such-, Skill-, Architektur-, Datenkatalog-, Test-, Build-,
  Deployment- und Changelog-Dokumentation aktualisieren.
- Wheel und alle Rollenimages bauen; Secrets, produktive Konfiguration,
  Mailinhalte, Indexdatenbanken, Embeddings, Logs und Laufzeitdaten ausschliessen.
- Hermetischen End-to-End-Test mit Fake-IMAP, ClamAV-Fixtures,
  Projektionspublisher, Sync-Worker, FTS, Fake-Embeddingservice, Gateway,
  Netzverlust, Crash und Restart ausfuehren.
- Produktiven Rollout als separaten Ablauf dokumentieren: Kapazitaetsplan,
  verifiziertes lokales Backup, Schema-Migration, begrenzter Canary-Backfill,
  Coveragevergleich, explizite Freigabe fuer Vollbackfill, inkrementeller Canary,
  Suchqualitaetsnachmessung und Rollback auf Server-Suche.
- Rollback bewahrt Mailquelle und den vorherigen verifizierten Index. Ein
  Image-Rollback behauptet nicht, externe Mails zu veraendern oder zurueckzusetzen.
- Release-/Tag-Vorbereitung erst nach gruener Gesamt-Abnahme; keine Main-Promotion
  und keine produktive Installation ohne separaten Auftrag.

### Gesamt-Abnahme

- Jeder freigegebene Ordner besitzt einen aktuellen, erfolgreichen
  Coverage-Nachweis oder die Gesamtsuche ist sichtbar unvollstaendig.
- Vollstaendiger Index und Serverinventar stimmen fuer den Abnahmezeitpunkt in
  Anzahl und stabilen Identitaeten ueberein; blockierte Inhalte sind separat
  erklaert.
- Neue Mail wird inkrementell ohne Voll-Reexport auffindbar; Move/Delete werden
  nur nach vollstaendigem Abgleich wirksam.
- Externe Client-/Provider-Moves, Copy/Delete-Ueberlappung, mehrere Locator,
  Quarantaenewechsel, Ordnerrename und UIDVALIDITY-Reset sind hermetisch getestet.
- Eindeutig belegte reine Locatorwechsel erzeugen keinen Body-/Raw-Reexport, kein
  erneutes Parsing oder OCR, keine unnoetige ClamAV-Arbeit, keine
  Modell-/Embeddinganfrage und keinen Voll-FTS-Neuaufbau. Mehrdeutige Faelle
  duerfen nur den dokumentierten begrenzten Raw-SHA-Nachweis ausloesen; die
  gemessenen Aufruf- und Ressourcenzaehler belegen die anschliessende
  Wiederverwendung.
- Lexikalische, strukturierte, Thread- und semantische Goldqueries erreichen die
  nach M11.0 beschlossenen Qualitaets- und Latenzgrenzen.
- Mehrere Chunks erzeugen keinen doppelten Mailtreffer; Threadkontext wird nicht
  als Querytreffer ausgegeben.
- Jede semantische Aussage bleibt auf reale Treffer und zitierbare Snippets
  begrenzt; bei unzureichender Evidenz enthaelt sich der Agent.
- Index-, Doctor- und Suchwerkzeuge besitzen stabilen CLI-/Tool-/Skillvertrag und
  echte Regressionstests.
- Alle Negativtests fuer Korruption, Stale State, Teilscan, Tombstone, Locator,
  ClamAV, Ollama, Prompt-Injection, Netzverlust und Crash sind gruen.
- `version --verify`, `git diff --check`, `check-repo.sh`, Compose, Wheel,
  Rollenimages, SBOM/Secret-Scan und Containerintegration sind erfolgreich.
- Kein produktiver Mailinhalt, Secret, Index, Embedding oder Laufzeitzustand liegt
  in Git, Wheel, Image oder CI-Artefakten.

### Entwicklungsprompt

```text
Setze nur M11.8 um und aendere keine fachliche Funktion ausser fuer einen durch
Regressionstest belegten M11-Fehler. Fuehre die Abnahmen M11.0 bis M11.7 erneut
aus. Aktualisiere Mail-/Such-/Skill-/Architektur-/Datenkatalog-/Test-/Build-/
Deployment-/Changelog-Dokumentation und das Quellmanifest. Baue und installiere
das Wheel isoliert, baue alle Rollenimages, pruefe Artefakte auf Secrets und
private Laufzeitdaten und fuehre die hermetische End-to-End-Mailindexintegration
mit Netz-, Crash-, ClamAV- und Ollama-Fehlern aus. Dokumentiere Coverage,
Recall/MRR/nDCG, Latenzen, Backfill-/Incremental-Ressourcen und Baselinevergleich.
Erstelle einen separaten backup-, canary- und rollbackgesicherten produktiven
Rolloutplan, fuehre ihn aber nicht aus. Veraendere keine Datei unter /srv/openclaw,
keinen produktiven Job und kein produktives Postfach. Berichte verbleibende
Einschraenkungen und ein eindeutiges M11-Urteil und stoppe nach M11.
```

## Produktiver Rollout nach M11

Der produktive Rollout ist nicht Teil der Entwicklungsabnahme und benoetigt einen
eigenen ausdruecklichen Auftrag. Die vorgesehene Reihenfolge lautet:

1. Release und Image-Digests verifizieren.
2. Aktuelle Mail-, Sync- und Indexgesundheit read-only erfassen.
3. Speicher-, Laufzeit- und Ollama-Kapazitaetsplan aus `mail index plan` pruefen.
4. Lokales verifiziertes Backup der betroffenen OpenClaw-Datenowner erstellen.
5. Alle produktiven Writer gemaess Migrationsvertrag stoppen; Schema staged
   migrieren und SQLite-/Projektionsintegritaet pruefen.
6. Vorherige Laufzeit wieder starten und zunaechst einen kleinen read-only
   Canary-Backfill auf freigegebenen Testordnern oder einem begrenzten Zeitraum
   ausfuehren.
7. Coverage, Scannerstatus, FTS-Treffer, Locator und Ressourcenverbrauch messen.
8. Erst nach Jans expliziter Bestaetigung den Vollkonto-Backfill begrenzt und
   wiederaufnehmbar ausfuehren.
9. Lokale Suche im Shadow-Modus gegen Server-Suche vergleichen; keine
   Negativaussage allein aus einem noch unvollstaendigen Index ableiten.
10. Nach gruener Nachmessung `auto` fuer Suchanfragen aktivieren; semantischen
    Provider nur nach separater Modellfreigabe einschalten.
11. Inkrementellen Job separat freigeben und mindestens sieben Tage Coverage,
    Latenz, Fallbackrate, externe Moves/Locator-Konflikte, Wiederverwendungsrate,
    Body-/Embedding-Fetches, Fehler und Ressourcenverbrauch beobachten.
12. Bei Verschlechterung Jobs stoppen, vorherige Konfiguration/Runtime und
    verifizierten Index wiederherstellen; Server-Suche bleibt der sichere
    Fallback. Externe Mails werden durch diesen Rollback weder ersetzt noch
    veraendert.

## Reihenfolge der Commits

1. `docs(mail-search): capture baseline and evaluation corpus`
2. `feat(mail-search): version projection and index identity contract`
3. `feat(mail-search): add resumable full-account backfill`
4. `feat(mail-search): add incremental reconciliation`
5. `feat(mail-search): add lexical filters and local tags`
6. `feat(mail-search): index conservative thread context`
7. `feat(mail-search): add evaluated local semantic retrieval`
8. `feat(mail-search): expose safe hybrid agent tools`
9. `docs(mail-search): complete M11 acceptance and rollout contract`

Jeder Commit muss fuer sich `git diff --check` bestehen und seine eigenen Tests
enthalten. Fehler werden im verursachenden Paket oder in einem klar benannten
Fix-Commit korrigiert; die Pakete werden nicht zu einem unpruefbaren Gesamtcommit
zusammengefasst.

## Globale Definition of Done

- Scope und Nicht-Ziele des Pakets sind eingehalten.
- Keine produktive Mail, Konfiguration, Berechtigung oder Jobzustand wurde in der
  Entwicklungsabnahme veraendert.
- CLI, Toolkatalog, Skillvertrag und Verhaltenstest stimmen fuer neue Agenttools
  ueberein.
- Datenmigrationen sind additiv, wiederholbar, backupgesichert und
  integritaetsgeprueft.
- Vollstaendigkeit, Frische und Fallback sind maschinenlesbar und fuer den Agenten
  verpflichtend dokumentiert.
- Private Inhalte fehlen in Logs, Telemetrie, Git, Wheel, Images und CI-Artefakten.
- Positive, negative, Crash-, Timeout-, Korruptions-, Idempotenz- und
  Seiteneffekttests sind gruen.
- Collection-Untergrenzen werden mit neuen Tests angehoben; eine kleinere
  Collection kann nicht unbemerkt gruen bleiben.
- Repository-, Wheel-, Compose-, Image- und Containerpruefungen sind fuer den
  jeweiligen Scope erfolgreich.
- Baseline, Messbefehle, Ergebnisse, Einschraenkungen und naechster erlaubter
  Schritt sind dokumentiert.
- Nach jedem Paket wird beendet; das Folgepaket beginnt nur nach separatem Auftrag.

## Naechster erlaubter Schritt

M11.0 ist abgenommen. Nach separatem Auftrag darf ausschliesslich M11.1 beginnen.
M11.1 definiert den versionierten Suchdaten-, Identitaets- und
Migrationsvertrag; ein Crawler, eine produktive Migration, neues Ranking oder
semantische Modellaktivierung bleiben weiterhin ausserhalb dieses Schritts.
