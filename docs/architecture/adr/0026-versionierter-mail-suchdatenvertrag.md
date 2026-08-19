# ADR-0026: Versionierter Mail-Suchdaten-, Identitaets- und Migrationsvertrag

- Status: Accepted
- Datum: 2026-08-19
- Entscheider: Architecture Maintainers, Mail Maintainers, Data Maintainers
- Betroffene Bereiche: Mail, Suchprojektion, Wissensindex, Sync-Worker

## Kontext

ADR-0017 trennt die Mail-SQLite vom read-only Sync-Worker durch eine atomare
Suchprojektion. Das bisherige Projektionsschema v1 ist jedoch eine einzige Liste:
Schon eine neue oder verschobene Mail erzeugt eine neue Gesamtliste. Ausserdem
vermischt der bisherige Stable-Key den durchsuchbaren Inhalt mit seinem damaligen
Ordnerort. Das reicht weder fuer einen begrenzten Vollkonto-Backfill noch fuer die
spaetere inkrementelle Verfolgung externer Client-Moves.

Vier Zustaende muessen dauerhaft getrennt bleiben:

1. Der IMAP-Server ist die fachliche Source of Truth fuer Existenz und aktuellen
   Ort einer Mail.
2. Die Mail-Suchprojektion ist ein validierter, unveraenderlicher Uebergabevertrag
   des alleinigen Mail-Owners; sie ist kein zweites Postfach.
3. `knowledge.sqlite3` ist eine daraus abgeleitete lokale Suchsicht und nie
   autoritativ fuer IMAP-Aktionen.
4. Ein Live-Locator wird vor einer spaeteren Mailaktion erneut read-only gegen
   IMAP aufgeloest. Ein Index-Locator allein erteilt keine Schreibautoritaet.

## Entscheidung

### Partitionierte Publikation v2

Schema v2 besteht aus unveraenderlichen Content- und Occurrence-Dateien,
unveraenderlichen Ordnerpartitionen und genau einem atomar ersetzten
`_projection.json`-Root. Jede Referenz besitzt SHA-256 und eine kanonisch
berechnete Generation. Das Root listet erwartete, vollstaendige und
unvollstaendige Partitionen sowie begrenzte Fehlergruende. Nur wenn erwartete,
referenzierte und vollstaendig autoritativ abgeglichene Partitionen exakt
uebereinstimmen, darf `complete=true` sein.

Eine unveraenderte Partition kann mit identischer Dateireferenz in einer neuen
Root-Generation wiederverwendet werden. Content-, Occurrence- und
Partitionsdateien werden vor dem Root geschrieben. Ein Abbruch vor einem dieser
atomaren Replaces laesst das letzte publizierte Root und damit die letzte
vollstaendige Generation lesbar. Nicht referenzierte Dateien sind nicht
publiziert.

Schema v1 bleibt strikt lesbar. Eine v1-Neupublikation schreibt ausschliesslich
in ein getrenntes Staging-Ziel, ist wiederholbar und wird absichtlich als
`complete=false`, `authoritative=false` markiert: v1 belegt nur die bereits
publizierte Generation, nicht die Vollstaendigkeit des Mailkontos. Der produktive
v1-Writer bleibt in M11.1 aktiv; v2, Backfill und neues Ranking werden noch nicht
aktiviert.

### Identitaeten und Konflikte

- `content_id` ist der Hash aus Vertragsversion, `resource_id` und Raw-SHA-256.
  Eine `Message-ID` ist nur Identitaetsnachweis und Threadsignal, nie alleiniger
  Deduplizierungsschluessel. Gleiche Message-ID mit verschiedenem Raw-Inhalt
  erzeugt zwei Contents; identischer Raw-Inhalt derselben Ressource genau einen.
- `locator_id` bindet Ressource, stabile `folder_id` und entweder
  UIDVALIDITY/UID oder, als dokumentierten Fallback, die Connector-Mailbox-ID.
  Der sichtbare Ordnername gehoert nicht zur Identitaet. Ein Rename behaelt die
  Identitaet; ein UIDVALIDITY-Reset erzeugt eine neue.
- `occurrence_id` bindet den primaeren Locator. Beim Mailbox-ID-Fallback wird
  zusaetzlich Raw-SHA-256 gebunden. Eine physische Kopie ist eine weitere
  Occurrence. Mehrere aktuelle oder beobachtete Locator koennen derselben
  Occurrence zugeordnet werden; Copy/Delete-Ueberlappungen duerfen daher nicht
  vorzeitig zusammenfallen.
- Widerspruechliche Contentdateien zur selben `content_id`, doppelte Occurrences,
  unsichere Dateinamen, fehlende Dateien, unbekannte Versionen und falsche Digests
  sind harte Vertragsfehler.

Contentdateien enthalten Parser-/Normalisierungs-/Tagversion, Raw-Nachweis,
normalisierten Text und begrenzte Threadheader. Sie enthalten weder Ordnername,
Quarantaenestatus noch UID. Diese veraenderlichen Werte liegen ausschliesslich in
Locator-/Occurrence-Daten. Ein reiner Move, Rename oder Quarantaenewechsel aendert
deshalb den Content-Digest nicht.

`In-Reply-To` und `References` werden tolerant normalisiert, dedupliziert und auf
20 beziehungsweise 50 syntaktisch brauchbare Message-IDs begrenzt. Sie sind
belegte Kantenhinweise, keine Erlaubnis, unterschiedliche Inhalte zu verschmelzen.

### Coverage, Tombstones und Wissensschema

Ein Tombstone ist nur in einer vollstaendigen, autoritativen Ordnerpartition
zulaessig. Ein fehlgeschlagener, abgebrochener oder nur teilweise gelesener Ordner
darf weder globale Vollstaendigkeit behaupten noch ein Verschwinden belegen. Der
Wissensindex lehnt unvollstaendige v2-Roots vor dem ersten Indexwrite ab.

Das Wissensschema v2 fuegt additiv Generationen, Contents, Occurrences, Locator,
Tags und Threadkanten sowie Dokumentspalten fuer `content_id`, Indexgeneration,
Quellstatus und Embeddingversion hinzu. Bestehende Dokumente, Chunks, FTS-Daten
und Sync-Historie bleiben erhalten; Altdokumente erhalten den expliziten Status
`legacy`. Wiederholte Migration ist idempotent. Vor einer produktiven
Schemamigration gilt weiterhin der verifizierte Releasebackup-Vertrag. Ein
Rollback verwendet die gesicherte v1-Datenbank; es gibt keinen verlustbehafteten
Down-Migrationspfad.

## Konsequenzen

Der Datenvertrag kann M11.2 und M11.3 spaeter begrenzte Backfills und kleine
Reconciliations publizieren lassen, ohne unveraenderten Inhalt erneut zu
verarbeiten. Er garantiert noch keine Vollkonto-Coverage und verbessert weder
Recall noch Ranking. Eine v2-Datei auf Disk ist ohne ein valides Root nicht
sichtbar.

Rollen und Mounts bleiben unveraendert: nur der Mailworker publiziert unter
`domains/mail/search_documents`; Sync liest Mail-State read-only und schreibt nur
den Wissens-State. Gateway und Suchindex werden nicht zu Mailwritern. Indexdaten,
Locator und Projektionen bleiben private Maildaten und duerfen weder in Builds
noch Telemetrie gelangen.

## Verifikation

Hermetische Tests pruefen v1/v2, unbekannte Zukunftsversionen, fehlende
Partitionen, falsche Digests, doppelte Identitaeten und unsichere Dateinamen.
Weitere Tests decken gleiche Message-ID mit unterschiedlichem Raw-Inhalt,
Raw-Deduplizierung ueber Ordner, fehlende Message-ID, Copy/Delete-Ueberlappung,
UIDVALIDITY-Reset, Ordnerrename, Tombstone- und Coverage-Gates ab. Simulierte
Abbrueche vor Content-, Partitions- und Root-Replace erhalten das alte Root.
Eine realistische v1-Wissensdatenbank wird zweimal ohne Verlust von Dokumenten,
Chunks oder Sync-Status migriert. Die bestehenden Compose-/Rollenregressionen
belegen weiterhin Single-Writer und read-only Mail-Mount des Sync-Workers.

