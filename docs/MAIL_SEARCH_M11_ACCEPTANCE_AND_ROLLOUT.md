# M11-Gesamtabnahme und separater produktiver Rollout

Stand: 2026-08-20. Entwicklungsstand: M11.0 bis M11.8.

## Urteil und Grenze

Die **synthetische M11-Entwicklungsabnahme ist bestanden**. Der lexikalische,
strukturierte und Threadvertrag, der inaktive semantische Vertrag sowie der
hermetische Containerablauf sind reproduzierbar gruen. Dabei wurde ein Fehler
behoben, durch den ein korrekt tombstonter historischer Content nach einem
Delete die aktive Locatorabdeckung dauerhaft als unvollstaendig markierte.

Das ist ausdruecklich **keine produktive Rolloutfreigabe**:

- Der aktuelle Himalaya-Connector belegt weiterhin keine autoritative
  UID-/UIDVALIDITY-/stabile-Ordner-ID-Semantik. Ein produktiver Vollindex darf
  deshalb noch keine vollstaendige Kontoabdeckung behaupten.
- Es wurde kein echtes Embeddingmodell auf der Zielhardware verglichen,
  ausgewaehlt, gepullt oder aktiviert. Der sichere Zustand bleibt
  `semantic_state=disabled`.
- Es wurden kein produktiver Backfill, keine Reconciliation, kein Indexjob,
  keine Mailaktion, kein Main-Merge, kein Tag und keine Datei unter
  `/srv/openclaw` veraendert.

Eine produktive Gegenpruefung am 2026-08-21 zeigte zusaetzlich, dass Himalaya
1.2 fuer mehrere exakte Suchvarianten erfolgreich leere Ergebnisse liefern kann,
obwohl eine passende Mail in einem anderen aktuellen Ordner existiert. Die
Nachbesserung behandelt diesen Providerpfad nicht mehr als autoritativ, rettet
positive Absender-/Adress-/Betrefftreffer ueber einen bounded Metadatenfallback
und laesst Nulltreffer sowie nicht verifizierte Bodyabdeckung fail-closed. Die
Regression verwendet ausschliesslich synthetische `example.invalid`-Daten; sie
aktiviert weiterhin keinen produktiven Vollindex.

## Reproduzierbare Entwicklungsabnahme

```bash
./scripts/assistant.sh version --verify
./scripts/check-repo.sh
.venv/bin/python scripts/benchmark_mail_acceptance_m118.py \
  --samples 11 --output build/m11-acceptance.json
./scripts/check-wheel.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet

OPENCLAW_SOURCE_REVISION="$(git rev-parse HEAD)" \
  ./docker/scripts/build-local.sh \
  openclaw-agent:m11-candidate \
  openclaw-agent:m11-candidate-proxy \
  openclaw-agent:m11-candidate-maintenance

./scripts/check-role-images.sh \
  openclaw-agent:m11-candidate \
  openclaw-agent:m11-candidate-proxy \
  openclaw-agent:m11-candidate-maintenance \
  "$(git rev-parse HEAD)"
OPENCLAW_M11_RUNTIME_IMAGE=openclaw-agent:m11-candidate \
  ./scripts/check-m11-integration.sh
```

Falls die aufrufende Shell die Dockergruppe noch nicht geerbt hat, werden nur
die Dockerbefehle in `sg docker -c '...'` gekapselt. Der M11-Stack verwendet
einen eindeutigen Compose-Projektnamen, ein internes Netz, temporaere Volumes,
keine Hostports, keine Secrets und keinen `/srv/openclaw`-Mount. Bereits laufende
Container gehoeren nicht zu diesem Test und werden weder adressiert noch
gestoppt.

Die CI fuehrt denselben Stack nach dem Build der drei Rollenimages aus und
bewahrt `build/m11-integration.json` sowie `build/m11-acceptance.json` als
inhaltsfreie Nachweise. Der Releaseworkflow wiederholt den Test vor Publikation
und Signatur.

## Gemessener Baselinevergleich

Der lokale M11.8-Referenzlauf nutzt 13 synthetische Nachrichten und 13
Goldqueries aus
`tests/fixtures/mail_search/m110_synthetic_corpus.json` mit SHA-256
`b0e5e79b06f5b493a50db4047b4d8a0630dd1d827700efb6279f6de9d7d97a7d`.
Zeitwerte sind Beobachtungen auf der Entwicklungsmaschine, keine willkuerlichen
Freigabegrenzen.

| Messwert | M11.0 lokales FTS | M11.8/M11.4 Lexik | Delta |
| --- | ---: | ---: | ---: |
| Recall@5 | 0,4833 | 0,6500 | +0,1667 |
| Recall@10 | 0,4833 | 0,6500 | +0,1667 |
| MRR | 0,5000 | 0,6667 | +0,1667 |
| nDCG@10 | 0,4766 | 0,6368 | +0,1602 |
| doppelte Treffer | 0 | 0 | 0 |

Der M11.8-Lauf sammelte 143 Lexiksamples. Gemessen wurden p50 0,5702 ms,
p95 2,9217 ms und p99 5,8545 ms; diese Werte schwanken mit Hostlast. Der
Threadkorpus erreicht Pair-Precision und Pair-Recall 1,0 bei Mislink-Rate 0,0.

Die beiden deterministischen Embeddingprofile pruefen nur die Mess- und
Cachepipeline. Sie bleiben `eligible_for_activation=false`. Der 8D-Vertrag
erreicht Recall@5/10 0,7400/0,7800, MRR 0,6333 und nDCG@10 0,6406; der
6D-Vertrag 0,7400/0,8800, 0,6458 und 0,6702. Diese Zahlen sind keine reale
Modellabnahme.

Der hermetische Stack belegte in einem Referenzlauf 7,151984 s bis zum
betriebsbereiten Fake-IMAP/Projektions-/Sync-Pfad und 2,902854 s fuer
SIGKILL plus Restart des Sync-Workers. Der Bericht enthaelt zusaetzlich die
gemessenen Backfill-/Incrementalzaehler. Reine Moves, Quarantaenewechsel,
Ordnerrename und UIDVALIDITY-Locatorwechsel haben jeweils Delta null fuer
Raw-Fetch, ClamAV und Embeddingdienst; die Reconcilemetriken belegen ferner null
Parser-, OCR-, Modell- und FTS-Arbeit.

## Inhalt des hermetischen End-to-End-Stacks

Der Test verwendet echten Produktcode fuer Backfill, partitionierten
Projektionspublisher, autoritative Reconciliation, Wissensmigration,
SQLite-Transaktion, feldgetrenntes FTS, Embeddingcache und Hybridrouting. Die
synthetischen Dienste pruefen:

- IMAP-Login, LIST, SELECT, SEARCH und FETCH gegen `example.invalid`-Mails,
- ClamAV clean, EICAR-artigen Fund und Scannerfehler fail-closed,
- exakten Projektions-/Serverzaehler bei vollstaendiger Coverage,
- neue Mail ohne Voll-Reexport,
- Move, Copy, Delete erst nach komplettem Abgleich, Quarantaenewechsel,
  Ordnerrename und UIDVALIDITY-Reset,
- mehrere Locator bei genau einem deduplizierten Mailtreffer,
- semantischen Dienstausfall mit belegtem lexikalischem Ergebnis,
- Netztrennung und Wiederherstellung,
- SIGKILL/Restart des Sync-Workers mit erhaltenem Indexzustand.

Prompt-Injection, korrupte Projektion/SQLite/FTS/Vektoren, Stale/Partial State,
Tombstones, Locator-Konflikte, Queue-/Ollamafehler und Crashgrenzen bleiben
zusaetzlich in den M11.0- bis M11.7-Verhaltensregressionen abgedeckt.

## Artefakt- und Datenschutzabnahme

`scripts/check-wheel.sh` baut aus einem frischen Quellsnapshot, scannt das Wheel,
installiert es in eine leere virtuelle Umgebung und fuehrt dort CLI-Hilfe,
Releaseverifikation und Gesamttests aus. Die Rollenabnahme exportiert jeden
produkt-eigenen Imagebereich und verwirft Secrets, nicht beispielhafte
Konfiguration, PDFs/EMLs, SQLite/WAL, Logs, Runtimeverzeichnisse sowie nun auch
`.npy`, `.npz`, `.vec`, `.vector`, `embeddings.json`,
`embedding-cache.json` und `mail-index.json`. Syft/Trivy erzeugen SBOM,
Vulnerability- und Secretscan pro Rolle. Test- und CI-Artefakte enthalten nur
synthetische IDs, Aggregate und technische Zaehler, nie Querytext, Adresse,
Betreff, Body, Snippet oder Vektorwerte.

Der lokale Referenzlauf baute das Wheel in 2,874 s auf 552.534 Bytes und fuehrte
in der frischen Umgebung alle 879 pytest-Items beziehungsweise 966 JUnit-Faelle
einschliesslich 87 Subtests aus. Zeit und Groesse charakterisieren nur diesen
Checkout und Host; `build/wheel-baseline.json` ist der reproduzierbare Nachweis.

## Separater produktiver Rolloutplan – nicht ausgefuehrt

Dieser Ablauf benoetigt einen neuen ausdruecklichen Auftrag und einen Connector,
dessen `mail index plan` die autoritativen Identitaetsfaehigkeiten wirklich
belegt. Jeder rote Schritt beendet den Rollout; er wird nicht durch Shellzugriff
oder herabgesetzte Sicherheitsregeln umgangen.

1. Signierte unveraenderliche Digests, Release und Quellrevision verifizieren.
2. Read-only `mail status`, `mail doctor`, `mail index status`, `mail index
   doctor`, `mail index plan` und `jobs check --target all --deep` erfassen.
3. Aus dem Plan Mailanzahl, Bytes, erwartete Chunks/Vektoren, freie Platte,
   Backfilldauer, Peak-RAM und Requestbudget ableiten. Fehlende UIDVALIDITY- oder
   stabile Ordneridentitaet blockiert.
4. Alle Writer kontrolliert stoppen und mit dem Deployment-Backupvertrag ein
   lokales Releasebackup erstellen; `verify-backup.sh` und `restore-test.sh`
   muessen erfolgreich sein. Fuer externe Mailaenderungen bleibt ein
   verifizierter IMAP-Snapshot-/Restore-Hook separat erforderlich.
5. Schema additiv in Staging migrieren, `PRAGMA quick_check`, Foreign Keys,
   Projektionsdigests und vorherige Indexgeneration pruefen. Nie eine produktive
   SQLite zur Reparatur loeschen oder neu anlegen.
6. Vorherige Laufzeit starten und genau einen kleinen, zeitlich oder nach
   freigegebenem Testordner begrenzten Canary-Backfill ausfuehren. Er bleibt
   lokaler Write und benoetigt die unveraenderte explizite Freigabe.
7. Serverinventar und Index nach Anzahl und stabilen Identitaeten vergleichen;
   blockierte Inhalte, Teilordner, Frische, FTS, Locator, Scannerstatus,
   Laufzeit, Bytes und RAM dokumentieren. Bis 100 % belegter Coverage bleibt die
   Gesamtsuche sichtbar unvollstaendig.
8. Erst nach neuer ausdruecklicher Freigabe den resumierbaren Vollbackfill mit
   den geprueften Grenzen ausfuehren. Semantik bleibt aus.
9. Lokale und Serversuche im Shadow-Canary mit denselben inhaltsfreien
   Goldquery-IDs vergleichen; Recall/MRR/nDCG, Latenz, Fallback und Konflikte
   nachmessen.
10. `auto` nur bei gruenem Shadowvergleich aktivieren. Ein reales
    Embeddingmodell erfordert danach eine eigene Zwei-Modell-Zielhardwareabnahme,
    vollständige Digests und separate Freigabe.
11. Den inkrementellen Canary separat aktivieren und mindestens sieben Tage
    Coverage, externe Moves, Copy/Delete, Quarantaene, UIDVALIDITY, Bodyfetches,
    Wiederverwendung, ClamAV-/Embeddingaufrufe, Fallbackrate und Ressourcen
    beobachten.
12. Bei Verschlechterung Indexjob stoppen, Serversuche erzwingen, vorherige
    verifizierte lokale Generation und Runtime gemaess Rollbackvertrag
    wiederherstellen. Ein Image-/Indexrollback veraendert oder ersetzt keine
    externe Mail und darf das auch nicht behaupten.

## Freigabestatus

| Bereich | Status |
| --- | --- |
| M11.0–M11.7 Regression und synthetischer Vergleich | bestanden |
| M11.8 hermetischer Containerstack | bestanden |
| Wheel/Rollenimage/Artefaktvertrag | Bestandteil der Gesamtpruefung und CI |
| produktive autoritative Connectorfaehigkeit | offen/blockierend |
| reales semantisches Zielhardwaremodell | nicht abgenommen, nicht aktiviert |
| produktiver Backfill/Indexjob | nicht ausgefuehrt |
| Main-Promotion/Tag/Installation | nicht ausgefuehrt |
