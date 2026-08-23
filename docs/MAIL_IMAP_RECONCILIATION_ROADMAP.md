# M12-Roadmap: Autoritativer IMAP-Connector und produktives Move-Tracking

Stand: 2026-08-23
Vorgesehener Arbeitsbranch: `development/authoritative-imap-reconciliation-m12`
Status: M12.0 bis M12.8 als Entwicklungsstand implementiert; produktiver
Capability-Canary, Vollbackfill, Jobstart und siebentägige Beobachtung offen

## Ausgangslage und Ziel

M11 hat den versionierten Mail-Suchdatenvertrag, einen begrenzten Backfill, die
transaktionale Reconciliation, lokale Lexik, Threads, einen inaktiven
Embeddingvertrag und die agentengerechte Hybridsuche implementiert und mit
synthetischem Fake-IMAP getestet. Im Produktivbetrieb bleibt der M11-Pfad jedoch
fail-closed: Himalaya 1.2 belegt weder UID und UIDVALIDITY noch eine stabile
Ordneridentitaet und kann deshalb keine autoritative Vollkonto-Generation oder
zuverlaessige Deltas liefern. Der produktive Index besitzt folglich keine
vollstaendige aktuelle Locatorabdeckung; externe Verschiebungen durch Mailclient,
Webmail, Smartphone, Provider-Spamfilter oder Serverregeln werden noch nicht
laufend in den lokalen Suchindex uebernommen.

M12 schliesst genau diese Betriebsluecke. Ein interner, strikt read-only
IMAP-Inventory-Connector liefert dem vorhandenen M11-Backfill und Reconciler
vollstaendige Ordnerzustaende und belastbare Locator. Nach einem kontrollierten
Erstaufbau verfolgt ein begrenzter periodischer Lauf neue, kopierte, verschobene
und entfernte Nachrichten. Ein belegter reiner Move aktualisiert nur Locator,
Ordner- und Quarantaenestatus. Parsertext, Threads, Tags, FTS und Embeddings
werden wiederverwendet und nicht erneut berechnet.

Das Betriebsziel ist erreicht, wenn der Agent:

- alle freigegebenen Ordner vollstaendig und schnell durchsuchen kann,
- eine extern verschobene Mail weiterhin ueber Absender, Adresse, Betreff und
  Body findet,
- den aktuellen Ordner und die aktuelle UID vor Lesen oder einer Einzelaktion
  erneut verifiziert,
- eine Mail nur bei belegter vollstaendiger und frischer Coverage als nicht
  vorhanden bezeichnet,
- bei Teilscan, Netzfehler, UIDVALIDITY-Wechsel oder unklarem Locator sichtbar
  `inconclusive` meldet,
- unveraenderte Mailinhalte bei Moves nicht erneut scannt, parst, taggt,
  einbettet oder in FTS neu aufbaut,
- nach einem erfolgreichen kontrollierten Rollout mit der bereits persistenten
  Jobsteuerung automatisch wieder voll funktionsfaehig startet.

## Nicht-Ziele und Sicherheitsgrenzen

- Der neue Connector ist kein allgemeines IMAP-Shellwerkzeug und wird dem Modell
  nicht direkt angeboten.
- Der Connector verwendet nur read-only IMAP-Befehle. `STORE`, `COPY`, `MOVE`,
  `EXPUNGE`, `APPEND`, `CREATE`, `DELETE`, `RENAME`, `SUBSCRIBE` und andere
  Providerwrites sind in diesem Pfad technisch verboten.
- Mailboxen werden mit read-only Semantik geoeffnet; Bodyabrufe verwenden
  `BODY.PEEK[]` oder einen gleichwertig belegten Pfad und setzen niemals das
  `\Seen`-Flag.
- Himalaya bleibt zunaechst der kontrollierte Connector fuer bestehende
  agentenseitige Einzelaktionen wie Lesen, Entwurf, Versand und erlaubtes
  Verschieben. M12 ersetzt nur den Inventory-, Backfill-, Reconcile- und
  Live-Locator-Pfad.
- M12 aendert keine Spam-, Antivirus-, Weiterleitungs-, Klassifikations- oder
  Quarantaenepolicy.
- Mailinhalte, Adressen, Betreffe, Querytexte, Snippets, Vektoren und Zugangsdaten
  gelangen nicht in Git, Logs, Telemetrie, Wheel, Image oder CI-Artefakte.
- TLS-Pruefung, ClamAV, Rollenmounts, Secret-Injection, Prozesslock,
  Single-Writer-Grenzen und Backups werden nicht abgeschwaecht.
- Entwicklung und hermetische Tests veraendern keine Dateien unter
  `/srv/openclaw`, keine produktiven Jobs und kein produktives Postfach.
- Ein produktiver Canary, Vollbackfill oder Jobstart ist eine getrennte
  Betriebsaktion mit expliziter Freigabe und darf nicht aus einer gruene CI
  abgeleitet werden.
- Semantische Suche ist fuer die M12-Betriebsfreigabe optional. Eine nicht
  abgenommene Embeddingkonfiguration darf lexikalische Vollstaendigkeit nicht
  blockieren oder vortaeuschen.

## Zielarchitektur

```text
                   IMAP-Server (Source of Truth)
                              |
                   TLS + read-only IMAP
                              |
                              v
             interner IMAP-Inventory-Connector
             - LIST/STATUS/EXAMINE
             - UID SEARCH und UID FETCH BODY.PEEK
             - UIDVALIDITY/UIDNEXT/HIGHESTMODSEQ
             - optionale CONDSTORE/QRESYNC-Optimierung
             - keine IMAP-Schreibbefehle
                              |
                              v
                    Mail-Owner / M11-Reconciler
             - Content, Occurrence und Locator getrennt
             - vollstaendige Ordnersnapshots
             - Move/Copy/Delete/UIDVALIDITY
             - ClamAV nur fuer neuen/geaenderten Content
                              |
                              v
              atomare Mail-Suchprojektion v2
                              |
                      read-only Mount
                              v
                         Sync-Worker
             - transaktionaler Wissensindex/FTS-Import
                              |
                              v
                         Gateway-Suche
             - vollstaendiger lokaler Normalpfad
             - Live-Locator-Revalidierung
             - maschinenlesbares Negativaussage-Gate
```

### Identitaets- und Move-Vertrag

M12 behaelt die drei M11-Identitaetsebenen bei:

- `content_id` identifiziert den unveraenderten Inhalt anhand des bestehenden
  konto-/ressourcengebundenen Raw-SHA-/Message-ID-Vertrags.
- `occurrence_id` identifiziert eine physische Kopie einer Nachricht.
- `locator` beschreibt den aktuellen IMAP-Fundort mit Ressource, Ordneridentitaet,
  Ordnername, UIDVALIDITY und UID.

Ein Abgleich interpretiert Zustandsaenderungen nur nach vollstaendig erfolgreichem
Ordnerinventar:

| Vorher | Nachher | Ergebnis |
| --- | --- | --- |
| alter Locator vorhanden | derselbe Locator vorhanden | unveraendert |
| alter Locator vorhanden | alter weg, identischer Content an genau einem neuen Locator | Move |
| alter Locator vorhanden | alter und neuer Locator vorhanden | Copy |
| alter Locator vorhanden | kein aktueller Locator mehr | entfernte Occurrence/Tombstone |
| kein bekannter Content | neuer Locator und neuer Raw-Digest | neue Mail |
| UIDVALIDITY geaendert | identischer Content belegt | neue Locator-/Occurrence-Identitaet mit Content-Reuse |
| Identitaet mehrdeutig | kein eindeutiger Nachweis | `inconclusive`, keine Tombstones |

IMAP liefert nicht auf jedem Server ein dauerhaft abrufbares Move-Ereignis. Der
Connector erkennt Moves deshalb aus der Differenz zweier vollstaendiger
Ordnerzustaende. `CONDSTORE`, `QRESYNC`, `VANISHED`, `HIGHESTMODSEQ` oder `IDLE`
duerfen diesen Vergleich beschleunigen, sind aber niemals Voraussetzung fuer
Korrektheit. Ohne Deltaerweiterung vergleicht der Connector begrenzt die
UID-Mengen aller freigegebenen Ordner und laedt nur neue oder mehrdeutige Inhalte.

### Ordneridentitaet

M12.1 muss die bisher zu starre Annahme einer immer serverseitig stabilen
Ordner-ID praezisieren:

1. Eine vom Server belegte stabile Mailbox-ID ist der bevorzugte Vertrag.
2. Falls der Server keine stabile Mailbox-ID anbietet, kann ein vollstaendiger
   aktueller Snapshot aus kanonischem Ordnernamen, UIDVALIDITY und kompletter
   UID-Menge trotzdem autoritative aktuelle Kontoabdeckung belegen.
3. Ohne stabile Mailbox-ID bleibt die Historie eines reinen Ordner-Renames
   gegebenenfalls unsicher. Sie wird dann als konservatives Entfernen/Neuauftreten
   mit Content-Reuse modelliert, nicht als erfundener sicherer Rename.
4. Eine unvollstaendige oder waehrend des Scans wechselnde Ordnerliste darf weder
   globale Coverage noch Tombstones erzeugen.

Diese Abstufung muss maschinenlesbar als `folder_identity_assurance` und getrennt
von `coverage.complete` berichtet werden. Eine unsichere Rename-Historie darf die
vollstaendig belegte aktuelle Existenz einer Mail nicht unnoetig blockieren.

## Paketuebersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M12.0 | Reale read-only Connector- und Betriebsbaseline | M11.8 |
| M12.1 | Autoritativer IMAP-, Ordneridentitaets- und Negativaussagevertrag | M12.0 |
| M12.2 | Strikt read-only nativer IMAP-Inventory-Connector | M12.1 |
| M12.3 | Produktiver Connectoradapter fuer Backfill und Live-Locator | M12.2 |
| M12.4 | Effizientes Move-/Copy-/Delete-Reconcile | M12.3 |
| M12.5 | Kontrollierter Erstindex, Migration und Shadowvergleich | M12.4 |
| M12.6 | Persistenter inkrementeller Job und Betriebsdiagnostik | M12.5 |
| M12.7 | Maschinenfestes Suchurteil und Agentenvertrag | M12.6 |
| M12.8 | Gesamt-Abnahme, Canary, Rollout und Beobachtung | M12.7 |

## M12.0 – Reale read-only Baseline und Capability-Audit

### Ziel

Den aktuellen produktiven Zustand und die tatsaechlichen IMAP-Faehigkeiten
inhaltsfrei messen, ohne Index, Job oder Postfach zu veraendern. Die Baseline
entscheidet, welche Deltaoptimierungen der reale Server erlaubt und welche
Fallbackkosten entstehen.

### Scope

- Bestehende M11-Tests, `mail index status`, `mail index doctor` und `mail index
  plan` reproduzierbar erfassen.
- Einen registrierten read-only Capability-Probe entwerfen, der Protokollversion,
  `UID`, `UIDVALIDITY`, `UIDNEXT`, optionale `CONDSTORE`, `QRESYNC`, `IDLE`,
  `OBJECTID`/Mailbox-ID, UTF-8-Unterstuetzung, Raw-Fetch und TLS-Vertrag meldet.
- Nur technische Aggregate erfassen: Ordnerzahl, UID-Anzahl, UID-Spannen,
  Antwortgroesse, Latenzen, Fehlerkategorien und ungefaehre Backfillkapazitaet.
- Die bestehende Himalaya-Capability-Matrix gegen den geplanten nativen Connector
  abgrenzen.
- Typische reale Operationen messen: No-op-Inventar, neue UID, externer Move,
  Copy, Delete, Ordnerrename und UIDVALIDITY-Wechsel. Produktive Aenderungen
  werden nicht zu Testzwecken erzeugt; Livewerte stammen nur aus bereits
  vorhandenen Zustandsaenderungen oder einem getrennten Testkonto.
- Freie Platte, erwartete Projektions-/FTS-Groesse, Backfilldauer, Peak-RAM und
  Requestrate abschaetzen, ohne willkuerliche Grenzwerte festzulegen.

### Abnahme

- Der Probe ist nachweislich read-only und setzt kein Flag.
- Secrets und Mailinhalte erscheinen in keinem Report.
- Nicht belegte Serverfaehigkeiten werden `false` oder `unknown`, niemals durch
  Clientannahmen `true`.
- Die Baseline nennt einen reproduzierbaren Befehl und die verbleibenden
  Unsicherheiten.
- Es wird kein Backfill, keine Reconciliation und kein Job gestartet.

### Entwicklungsprompt

```text
Setze ausschliesslich M12.0 aus docs/MAIL_IMAP_RECONCILIATION_ROADMAP.md um. Lies
AGENTS.md sowie die Mail-, Runtime- und Toolreferenzen vollstaendig. Pruefe zuerst
Release, Worktree, M11-Indexstatus, Doctor, Plan und Jobs read-only. Implementiere
nur einen registrierten, strikt read-only IMAP-Capability-Probe und eine
inhaltsfreie Baseline. Belege UID, UIDVALIDITY, UIDNEXT, optionale
CONDSTORE/QRESYNC/IDLE/OBJECTID-Faehigkeiten, Raw-Fetch, UTF-8 und TLS einzeln;
erfinde keine Capability aus erfolgreichem Login oder Paging. Messe technische
Aggregate, Latenz und Kapazitaet, aber speichere keine Adresse, keinen Betreff,
Body, Querytext, Secret oder Locator in Git/CI. Erzeuge keine produktive Mail und
starte keinen Backfill, Reconcile oder Job. Ergaenze echte Negativtests fuer
fehlende, widerspruechliche und unerwartete Serverantworten, aktualisiere
Dokumentation und Quellmanifest und stoppe nach M12.0.
```

## M12.1 – IMAP-, Ordneridentitaets- und Negativaussagevertrag

### Ziel

Den fachlichen und sicherheitstechnischen Vertrag festlegen, bevor ein neuer
Connector produktiv codiert wird. Insbesondere werden aktuelle Vollstaendigkeit,
Ordner-Rename-Historie und Negativaussagen getrennt.

### Scope

- ADR fuer den nativen read-only Connector, seine Rollen-, Secret-, TLS-, Lock-
  und Datenownergrenzen erstellen.
- Einen kleinen Connector-Port fuer Capability, Ordnerinventar, Ordnersnapshot,
  Raw-Fetch und Live-Locator definieren; keine allgemeinen IMAP-Befehle oder
  freie Querystrings exponieren.
- `folder_identity_assurance = server-stable | snapshot-stable | unknown`
  definieren.
- Aktuelle Konto-Coverage auch fuer Server ohne stabile Mailbox-ID korrekt
  modellieren, solange LIST und alle Ordnersnapshots vollstaendig und unveraendert
  sind.
- Rename, Move, Copy, Delete, UIDVALIDITY-Reset, temporär fehlenden Ordner und
  widerspruechliche Snapshots exakt unterscheiden.
- Den Suchentscheid auf `matches | no-match | inconclusive` normieren.
  `no-match` ist nur bei frischer, vollstaendiger, autoritativer Coverage und
  vollstaendig unterstuetzten Filtern erlaubt.
- Additive Migrationen fuer Checkpoints und Capabilitystatus definieren; M11-v2-
  Projektionen und bisherige Historie bleiben lesbar.

### Abnahme

- Golden- und Schema-Tests pruefen jede Assurance- und Decision-Ausgabe.
- Ohne stabile Ordner-ID ist die aktuelle Vollstaendigkeit belegbar, aber ein
  Rename wird nicht erfunden.
- LIST-/Snapshot-Race, Teilscan und UIDVALIDITY-Wechsel erzeugen keine falschen
  Tombstones oder `no-match`-Urteile.
- Der Port kann keine IMAP-Schreiboperation ausdruecken.
- Keine Produktivmigration wird ausgefuehrt.

### Entwicklungsprompt

```text
Setze nur M12.1 um. Erstelle eine ADR und einen typisierten read-only
IMAP-Connectorvertrag fuer M11-Backfill, Reconcile und Live-Locator. Trenne
aktuelle Konto-Coverage von historischer Ordner-Rename-Sicherheit und fuehre
folder_identity_assurance mit server-stable, snapshot-stable und unknown ein.
Definiere, wie LIST, UIDVALIDITY, komplette UID-Mengen und optionale Mailbox-IDs
einen konsistenten Snapshot belegen. Normiere Suchurteile auf matches, no-match
und inconclusive; no-match darf nur bei frischer vollstaendiger Coverage und
unterstuetzten Filtern entstehen. Erweitere Daten- und Checkpointschema additiv,
bewahre M11-v2 und Historie und stelle sicher, dass der Port keine STORE-, MOVE-,
COPY-, EXPUNGE-, APPEND- oder Ordnerwrite-Operation ausdruecken kann. Teste
Schema, Migration, Snapshot-Races, fehlende Mailbox-ID, Rename, UIDVALIDITY und
Teilscan hermetisch. Fuehre keinen Live-Backfill oder Jobstart aus und stoppe nach
M12.1.
```

## M12.2 – Strikt read-only nativer IMAP-Inventory-Connector

### Ziel

Einen produktionsfaehigen internen IMAP-Protokolladapter implementieren, der die
in M12.1 definierten Nachweise liefert und keinerlei Providerwrite ausfuehren
kann.

### Scope

- TLS-verifizierten Verbindungsaufbau mit festen Connect-, Read- und
  Gesamtlaufzeitlimits implementieren.
- Zugangsdaten ausschliesslich ueber die bestehenden rollenbezogenen Secret-
  Mounts beziehungsweise Umgebungsvariablen beziehen; keine neuen Secretdateien
  suchen oder protokollieren.
- Ordner ueber vollständiges LIST inventarisieren, Namen kanonisch und
  round-trip-sicher behandeln sowie Quarantaeneattribute erhalten.
- Mailboxen read-only oeffnen und UIDVALIDITY, UIDNEXT und optionale Capability-
  Werte erfassen.
- UIDs in begrenzten Seiten/Intervallen inventarisieren. Raw-Mail nur ueber
  `UID FETCH ... BODY.PEEK[]` fuer genau ausgewaehlte UIDs abrufen.
- `CONDSTORE`, `QRESYNC`, `VANISHED`, `HIGHESTMODSEQ`, `IDLE` oder Mailbox-ID nur
  nutzen, wenn Capability und konkrete Antwort belegt sind. Ein kompletter
  UID-Snapshot bleibt der korrekte Fallback.
- Fehler in stabile Kategorien uebersetzen: TLS, Authentifizierung, Timeout,
  Rate-Limit, Protokoll, Ordner verschwunden, UIDVALIDITY-Race, Teilscan und
  Server-BYE.
- Eine IMAP-Kommandowhitelist testen; jeder Schreibbefehl muss schon vor
  Netzwerkversand abgewiesen werden.

### Abnahme

- Protokolltests belegen, dass kein `\Seen`-Flag gesetzt und kein Write-Befehl
  gesendet wird.
- Unicode-, Sonderzeichen- und tiefe Ordnernamen werden korrekt inventarisiert.
- Paging verliert und dupliziert keine UID.
- Reconnect und Resume wiederholen nur idempotente read-only Operationen.
- TLS- und Zertifikatsfehler bleiben fail-closed; es gibt keinen unsicheren
  Schalter.
- Mailinhalt und Credentials fehlen vollständig in Fehlern und Telemetrie.

### Entwicklungsprompt

```text
Setze nur M12.2 um. Implementiere den typisierten nativen IMAP-Inventory-Connector
aus M12.1 mit strikter TLS-Pruefung, bestehenden Secret-Mounts, festen Timeouts,
read-only Mailboxoeffnung, vollstaendigem LIST, UIDVALIDITY/UIDNEXT,
seitengesteuerter UID-Inventarisierung und gezieltem UID FETCH BODY.PEEK fuer Raw-
Mails. Nutze optionale CONDSTORE/QRESYNC/VANISHED/HIGHESTMODSEQ/IDLE/OBJECTID-
Optimierungen nur nach belegter Capability. Baue eine technische
Kommandowhitelist, die alle IMAP-Schreibbefehle vor dem Netzwerk blockiert. Teste
gegen einen hermetischen Protokollserver und mindestens eine gepinnte
standardkonforme IMAP-Testimplementierung: Unicodeordner, leere/grosse Ordner,
Paging, Timeout, BYE, Reconnect, TLS-, Auth- und UIDVALIDITY-Races. Belege, dass
keine Flags, Mails oder Ordner veraendert und keine Inhalte oder Secrets geloggt
werden. Integriere noch keinen produktiven Backfill und stoppe nach M12.2.
```

## M12.3 – Adapter fuer Backfill und Live-Locator

### Ziel

Den neuen Connector hinter die vorhandenen M11-Ports setzen, ohne den bewährten
Reconciler oder den kontrollierten Himalaya-Aktionspfad zu umgehen.

### Scope

- Native Backfill- und Reconcile-Adapter fuer die vorhandenen M11-Schnittstellen
  implementieren.
- Capabilitystatus exakt aus dem nativen Connector weitergeben.
- Ordner- und UID-Snapshots mit Start-/End-Nachweis binden; eine waehrend des
  Laufs geaenderte Ordnerliste oder UIDVALIDITY macht den Lauf unvollstaendig.
- Den Live-Locator fuer positive lokale Treffer anhand Ressource, Ordner,
  UIDVALIDITY, UID und Erwartungsfeldern revalidieren.
- Bei veraltetem Locator einen begrenzten read-only Neuauflösungspfad vorsehen,
  ohne auf eine beliebige gleichnamige Mail auszuweichen.
- Connectorwahl typisiert konfigurieren: `himalaya-bounded` bleibt sichere
  Diagnose-/Fallbackoption, `native-imap-readonly` ist der einzige Kandidat fuer
  autoritative Coverage.
- Tools, Capabilityanzeige, Doctor, Plan und Tests auf denselben Connectorvertrag
  ausrichten.

### Abnahme

- Backfill und Reconcile erhalten reale UID-/UIDVALIDITY-Werte.
- Eine widerspruechliche oder wechselnde Quelle publiziert keine Generation.
- Live-Locator-Konflikte fuehren zu einer neuen Suche, nicht zu einer Aktion auf
  eine alte UID.
- Himalaya-Versand-/Move-Verhalten bleibt unveraendert.
- Agent, CLI, Toolkatalog, Skill und Verhaltenstest stimmen ueberein.

### Entwicklungsprompt

```text
Setze nur M12.3 um. Adaptiere den nativen read-only IMAP-Connector an die
bestehenden M11-Backfill-, Reconcile- und Live-Locator-Ports. Binde jeden
Ordnersnapshot an LIST, Ordneridentitaet, UIDVALIDITY und komplette UID-Menge und
verwirf einen waehrend des Scans wechselnden Zustand ohne Publikation. Revalidiere
positive lokale Treffer ueber Ressource, Ordner, UIDVALIDITY, UID und
Erwartungsfelder; ein Konflikt darf keine alte oder fuzzy ausgewaehlte Mail
freigeben. Halte Himalaya fuer bestehende kontrollierte Lesen-/Entwurf-/Versand-
und Move-Aktionen unveraendert und exponiere keinen freien IMAP-Zugriff. Richte
Status, Doctor, Plan, Toolkatalog, Skill und Regressionen auf denselben
Connectorvertrag aus. Nutze nur synthetische/testeigene Konten und starte keinen
produktiven Backfill oder Job. Stoppe nach M12.3.
```

## M12.4 – Effizientes Move-, Copy-, Delete- und Rename-Tracking

### Ziel

Die bestehende M11-Reconciliation mit dem realen Connector vollständig nutzbar
machen und ihren Ressourcenvertrag auf echte IMAP-Zustaende prüfen.

### Scope

- Neue UIDs, verschwundene UIDs und geaenderte Ordnerzustände inkrementell
  vergleichen.
- Einen eindeutigen Move aus verschwundenem alten Locator und genau einem
  identischen neuen Contentnachweis ableiten.
- Copy, Move, Copy/Delete-Ueberlappung, Delete, Wiederauftauchen,
  Quarantaenewechsel, Ordner-Rename und UIDVALIDITY-Reset konservativ trennen.
- Bei einem eindeutig providerbelegten Digest keinen Raw-Fetch ausfuehren. Bei
  mehrdeutigen neuen Locator genau die erforderliche Raw-Mail einmal abrufen und
  per SHA-256 zuordnen.
- Unveränderten Content, Parsertext, Threadgraph, Tags, Chunks, FTS und Embeddings
  wiederverwenden.
- Nur nach komplettem autoritativem Gesamtvergleich Tombstones publizieren.
- Periodischen Vollabgleich als Sicherheitsnetz definieren, auch wenn schnelle
  Deltas verfügbar sind.
- Aufruf-, Byte-, Parser-, ClamAV-, FTS-, OCR- und Modellmetriken inhaltsfrei
  messen.

### Abnahme

- Ein eindeutiger Move erzeugt null Body-, Parser-, OCR-, ClamAV-, FTS- und
  Modellarbeit.
- Ein mehrdeutiger Move erzeugt höchstens einen begrenzten Raw-SHA-Nachweis pro
  betroffenem Kandidaten und danach Content-Reuse.
- Ein Move durch Thunderbird, Smartphone/Webmail und Providerregel ist im
  standardkonformen IMAP-Teststack nach dem nächsten Reconcile auffindbar.
- Teilscan, Netzverlust und Ordnerlisten-Race erzeugen keine Deletes oder Moves.
- Copy und Move werden nicht verwechselt.
- UIDVALIDITY-Reset verliert keine belegten Inhalte und erfindet keine Locator.

### Entwicklungsprompt

```text
Setze nur M12.4 um. Verbinde den M11-Reconciler mit den autoritativen nativen
IMAP-Snapshots und vervollstaendige effizientes Tracking fuer neue Mail, Move,
Copy, Copy/Delete, Delete, Wiederkehr, Quarantaenewechsel, Ordnerrename und
UIDVALIDITY-Reset. Ein eindeutiger reiner Locatorwechsel muss Content,
Parsertext, Threads, Tags, FTS und Embeddings ohne Raw-, ClamAV-, OCR- oder
Modellarbeit wiederverwenden. Nur bei mehrdeutiger Identitaet ist ein begrenzter
Raw-SHA-Nachweis erlaubt. Publiziere Tombstones ausschliesslich nach einem
vollstaendigen konsistenten Ordnerabgleich. Teste echte externe Moves im
hermetischen IMAP-Stack, Netzverlust, Races, Crash und Resume und belege alle
Ressourcenzaehler. Aendere keine produktive Mail, starte keinen Live-Job und
stoppe nach M12.4.
```

## M12.5 – Kontrollierter Erstindex und Shadowvergleich

### Ziel

Den produktiven Indexaufbau technisch vorbereiten und zunächst in einem
hermetischen sowie anschließend separat freizugebenden Canary gegen das reale
Postfach nachweisen.

### Scope

- Staged Migration, Backup-, Integritaets- und Rollbackvertrag fuer die
  produktiven Mail- und Wissensdatenowner vervollständigen.
- `mail index plan` um reale Kapazitaets-, Laufzeit- und Connectorwerte
  erweitern.
- Einen zeitlich, nach Ordnern und Ressourcen begrenzten Canary-Backfillvertrag
  anbieten; Standard ist read-only Plan, Anwendung bleibt lokaler Write mit
  expliziter Freigabe.
- Vollbackfill wiederaufnehmbar, idempotent und innerhalb geprüfter Byte-, Mail-,
  Zeit- und Rate-Limits halten.
- Nach jeder kompletten Generation Projektionsdigests, SQLite-Integritaet,
  Content-/Occurrence-/Locatorzahlen, blockierte Inhalte und Coverage prüfen.
- Lokale Ergebnisse im Shadowmodus gegen aktuelle Serverergebnisse vergleichen,
  ohne unvollständige Servernulltreffer als Wahrheit zu verwenden.
- Semantik ausgeschaltet lassen; lexikalische Absender-, Adress-, Betreff-, Body-
  und strukturierte Suche zuerst abnehmen.

### Abnahme

- Canary kann ohne Veränderung einer Provider-Mail abgebrochen und fortgesetzt
  werden.
- Ein fehlgeschlagener Lauf erhält die letzte komplette Generation.
- Ein verifiziertes lokales Backup und Restore-Test existieren vor jedem
  produktiven Write auf Projektion oder Index.
- 100 Prozent der freigegebenen, nicht blockierten aktuellen Occurrences besitzen
  einen Locator; blockierte Inhalte sind getrennt erklärt.
- Der Vollbackfill wird in der Entwicklungsabnahme nicht automatisch produktiv
  ausgeführt.

### Entwicklungsprompt

```text
Setze nur M12.5 um. Erstelle den backup-, staged-migration-, canary- und
rollbackgesicherten Erstindexvertrag fuer den nativen IMAP-Connector. Erweitere
mail index plan um reale technische Aggregate und feste Ressourcenbudgets.
Implementiere begrenzten Ordner-/Zeitraum-Canary und wiederaufnehmbaren
Vollbackfill als getrennte lokal schreibende Tools mit unveraenderten expliziten
Freigaben. Pruefe nach jeder kompletten Generation Digests, SQLite, Counts,
Locatorcoverage, Scannerstatus und blockierte Inhalte. Vergleiche lokale Lexik
im Shadowmodus mit dem Server, ohne unvollstaendige Servernulltreffer als
Ground Truth zu behandeln. Aktiviere keine Semantik und fuehre im
Entwicklungsauftrag keinen produktiven Canary oder Vollbackfill ohne separate
ausdrueckliche Freigabe aus. Stoppe nach M12.5.
```

## M12.6 – Persistenter inkrementeller Job und Betriebsdiagnostik

### Ziel

Den Reconcile-Pfad nach erfolgreichem Erstindex als normalen, begrenzten und
restartfesten Betrieb vorbereiten.

### Scope

- Den vorbereiteten `mail-index`-Job in den typisierten Jobkatalog und den
  bestehenden Mail-Owner-Worker integrieren; keinen konkurrierenden zweiten
  Projektionswriter schaffen.
- Mailverarbeitung und Index-Reconciliation über denselben erneuerbaren
  Prozess-/Ownerlock serialisieren.
- Intervall und periodischen Vollabgleich aus M12.0-Messwerten ableiten und
  dokumentieren; keine willkürliche Frequenz festlegen.
- Persistenten Desired State, Lease, Heartbeat, Timeout, Backoff und
  fehlgeschlagene Erneuerung fail-closed integrieren.
- Nach erfolgreichem Rollout muss der gewünschte Jobzustand Containerneustarts
  überleben; ein Imagewechsel allein schaltet ihn nicht heimlich ein oder aus.
- Status/Doctor um letzte komplette Generation, Alter, Coverage, Scanmodus,
  Moves, Copies, Deletes, Raw-Fetch-Reuse, Fallbackrate und nächsten Lauf
  erweitern.
- Inhalt und Identitäten aus Telemetrie und Alerts fernhalten.

### Abnahme

- Genau ein Mail-Owner schreibt Projektion und Checkpoint.
- Ein paralleler Mailrun blockiert/reicht den Indexlauf kontrolliert weiter und
  beschädigt keinen Zustand.
- Containerrestart, Crash, Leaseverlust, Netzverlust und Timeout sind
  wiederaufnehmbar.
- Reine Moves verursachen auch im Jobpfad keine Inhaltsneuverarbeitung.
- `ON`, `OFF` und `DEGRADED/FAILED` bleiben unterscheidbar.
- Der Job wird in der Entwicklungsabnahme nicht produktiv aktiviert.

### Entwicklungsprompt

```text
Setze nur M12.6 um. Integriere den begrenzten mail-index-Reconcile als typisierten
Job in den bestehenden Mail-Owner-Worker und bewahre exakt einen Writer fuer
Projektion und Checkpoint. Nutze persistenten Desired State, Lease, Heartbeat,
Timeout, Backoff und denselben Prozesslock wie die Mailverarbeitung. Leite
Intervall und Vollabgleich aus den M12.0-Messwerten ab. Erweitere Status, Doctor
und Monitoring um technische Coverage-, Alters-, Delta-, Reuse- und
Fallbackmetriken ohne Mailinhalte oder IDs. Teste Konkurrenz, Restart, SIGKILL,
Leaseverlust, Netzverlust, Timeout und No-op. Ein produktiver Start oder eine
Rechteaenderung ist nicht Teil dieses Pakets; stoppe nach M12.6.
```

## M12.7 – Maschinenfestes Suchurteil und Agentenvertrag

### Ziel

Verhindern, dass ein Modell einen unvollständigen Nulltreffer erneut als „keine
Mail vorhanden“ formuliert.

### Scope

- Jede Mail-Suche liefert auf oberster Ebene `decision`, `absence_proven`,
  `negative_claim_allowed`, `complete`, `freshness`, `coverage`,
  `folder_errors`, `filter_limitations` und `results_may_be_truncated`.
- Ein unvollständiger Nulltreffer wird nicht als erfolgreiche leere Trefferliste,
  sondern als typisiertes `inconclusive`-Ergebnis mit sicherer nächster Aktion
  dargestellt.
- Der Agentenrouter und die Antwortgrenze dürfen eine definitive Negativaussage
  nur aus `decision=no-match` und `negative_claim_allowed=true` erzeugen.
- Positive historische Treffer ohne Live-Locator werden als historische Evidenz
  ausgewiesen, nicht als aktuelle Absender- oder Aktionsberechtigung.
- Absender-, Adresse-, Domain-, Betreff-, Body-, Zeitraum-, Ordner- und
  strukturierte Filter erhalten getrennte Coverage.
- Der Live-Locator bleibt vor Lesen oder externer Einzelaktion zwingend.
- Mehrsprachige End-to-End-Evals prüfen deutsche, englische und spanische
  Negativantworten sowie den konkreten Fehlerfall einer verschobenen Mail.

### Abnahme

- `complete=false`, Filterlimit oder Trunkierung kann nicht unbemerkt als
  „nicht vorhanden“ ausgegeben werden.
- Ein vollständiger aktueller Nulltreffer darf eindeutig `no-match` liefern.
- Ein historischer Treffer ohne aktuellen Locator wird wahrheitsgemäß erklärt.
- Agent und Tool dürfen keine Bodiesuche behaupten, wenn nur Metadaten geprüft
  wurden.
- Toolresultat, Skill, Systemvertrag und Verhaltenstest verwenden denselben
  Entscheidungswortschatz.

### Entwicklungsprompt

```text
Setze nur M12.7 um. Mache das Mail-Suchurteil maschinenfest: Jede Suche muss
decision, absence_proven, negative_claim_allowed, complete, freshness, coverage,
folder_errors, filter_limitations und results_may_be_truncated liefern. Ein
unvollstaendiger Nulltreffer ist zwingend inconclusive und darf an der
Agentenantwortgrenze nicht als definitive Abwesenheit formuliert werden. Erlaube
no-match nur bei frischer vollstaendiger autoritativer Coverage fuer alle
verwendeten Filter. Trenne historische Evidenz ohne Live-Locator von aktuellen
Treffern und behalte die Live-Revalidierung vor jeder Einzelaktion. Ergaenze
echte End-to-End-Verhaltenspruefungen in Deutsch, Englisch und Spanisch fuer
Nulltreffer, verschobene Mail, Metadaten-only, Bodyfilter, Stale Index und
Locator-Konflikt. Aendere keine Mail und stoppe nach M12.7.
```

## M12.8 – Gesamt-Abnahme, produktiver Canary und kontrollierter Rollout

### Ziel

Die Entwicklung vollständig abnehmen und den produktiven Betrieb nur in klar
getrennten, freizugebenden Stufen aktivieren.

### Entwicklungsabnahme

- Alle M12.0-M12.7-Tests und M11-Regressionspfade erneut ausführen.
- Ein standardskonformer hermetischer IMAP-Stack bildet mehrere Ordner, Unicode,
  große Mailboxen, Moves, Copies, Deletes, Rename, Quarantäne,
  UIDVALIDITY-Reset, Netzverlust, Timeout und Serverrestart ab.
- Wheel und alle Rollenimages bauen; Compose, SBOM, Provenance, Secret-/CVE-Scan,
  Signatur und Digestprüfung bleiben grün.
- Wheel, Image und CI-Artefakte enthalten keine EML, Datenbank, Locator,
  Zugangsdaten, Indexgeneration, Query, Adresse, Betreff, Body oder Vektordatei.
- Performancebericht misst No-op, neue Mail, eindeutigen/mehrdeutigen Move,
  periodischen Vollabgleich, Suchlatenz, CPU, RAM, Netzbytes und Plattenwachstum.
- Qualitaetsbericht prüft exakte Adresse, Absender, Domain, Betreff, Body,
  Zeitraum, Ordner und verschobene historische Mail sowie echte Nulltreffer.

### Getrennte produktive Rolloutstufen

1. Signierte Digests, Release und Quellrevision prüfen.
2. `mail status`, `mail doctor`, `mail index status`, `mail index doctor`,
   `mail index plan` und `jobs check --target all --deep` read-only erfassen.
3. Connector-Capabilities und Kapazitätsplan gegen die M12.0-Baseline prüfen.
4. Lokales verifiziertes Backup und Restore-Test aller betroffenen Data Owner
   durchführen; externe Mailzustände werden vom lokalen Rollback nicht ersetzt.
5. Schema gestaged migrieren und Integrität prüfen, bevor laufende Writer
   verändert werden.
6. Einen kleinen explizit freigegebenen Canary-Backfill ausführen; Semantik bleibt
   aus.
7. Canary-Coverage, Locator, ClamAV, Suchqualität, Latenz und Ressourcen prüfen.
8. Nach neuer Freigabe den begrenzten resumierbaren Vollkonto-Backfill ausführen.
9. Lokale Suche im Shadowmodus gegen aktuelle Live-Locator prüfen.
10. Erst bei vollständiger grüner Coverage den lokalen Auto-Suchpfad aktivieren.
11. Den inkrementellen Job separat einschalten und den gewünschten Zustand
    persistent halten.
12. Mindestens sieben Tage externe Moves, Copy/Delete, UIDVALIDITY, Coverage,
    Fallbackrate, Raw-Fetch-Reuse, CPU, RAM, Netzlast und Fehler beobachten.
13. Erst nach bestandener Beobachtung M12 als produktiv abgenommen markieren.
14. Bei Verschlechterung Indexjob stoppen und auf den fail-closed Serverpfad
    zurückschalten; keine Remote-Mail wird durch Indexrollback verändert.

### Produktive Definition of Done

- Alle freigegebenen Ordner besitzen vollständige aktuelle Coverage oder die
  Suche meldet sichtbar `inconclusive`.
- Eine über externen Mailclient verschobene Nachricht bleibt nach dem vereinbarten
  Reconcile-SLA auffindbar und besitzt einen aktuellen Live-Locator.
- Eindeutige Moves erzeugen keine erneute Inhaltsverarbeitung.
- Exakte Suche nach einer bekannten älteren Absenderadresse findet den belegten
  Treffer unabhängig vom aktuellen Ordner oder erklärt konkret eine noch offene
  Coverage-/Locatorgrenze.
- Eine sinkende Collection, beschädigte Projektion, fehlende Locator, Teilscan
  oder veraltete Generation kann nicht unbemerkt grün bleiben.
- Der automatische Job überlebt einen normalen Containerrestart mit seinem
  persistenten gewünschten Zustand.
- Keine Produktivmail, kein Secret und kein privater Index liegt in Git, Image,
  Wheel, Logs oder öffentlichen Artefakten.
- Das Urteil lautet ausdrücklich entweder `M12 PRODUKTIV ABGENOMMEN` oder
  `M12 NICHT PRODUKTIV ABGENOMMEN`.

### Entwicklungsprompt

```text
Setze nur M12.8 um. Aendere fachlichen Code ausschliesslich fuer einen durch
Regressionstest belegten M12-Fehler. Fuehre die Abnahmen M11 sowie M12.0 bis
M12.7 erneut aus, baue Wheel und Rollenimages, pruefe Compose, Artefakte, SBOM,
Provenance, Secrets, CVEs, Signaturen und Digests und fuehre die hermetische
standardkonforme IMAP-End-to-End-Integration aus. Dokumentiere Suchqualitaet,
No-op-/Move-/Copy-/Delete-Ressourcen, Latenz, CPU, RAM, Netzbytes und
Plattenwachstum. Erstelle den backup-, canary-, shadow-, rollback- und
siebentaegigen Beobachtungsablauf. Fuehre keine produktive Stufe ohne Jans
jeweilige ausdrueckliche Freigabe aus und erfinde bei fehlendem Docker-, IMAP-
oder Zielsystemnachweis kein positives Ergebnis. Berichte Entwicklung und
Produktivstatus getrennt und stoppe nach M12.
```

## Globale Pflichttests

- Capability-Probe sendet ausschließlich erlaubte read-only Kommandos.
- `EXAMINE`/äquivalente Read-only-Semantik und `BODY.PEEK` setzen kein Flag.
- Vollständiges LIST und UID-Paging für leere, große, Unicode- und verschachtelte
  Ordner.
- UIDVALIDITY, UIDNEXT, optionale MODSEQ-/QRESYNC- und vollständige
  UID-Snapshotpfade.
- No-op, neue Mail, Copy, Move, Copy/Delete, Delete, Wiederkehr, Quarantäne,
  Ordnerrename und UIDVALIDITY-Reset.
- Externer Move verursacht null unnötige Parser-, OCR-, ClamAV-, FTS- und
  Modellaufrufe.
- Mehrdeutiger Move bleibt begrenzt und erzeugt keine falsche Zuordnung.
- LIST-Race, Teilordner, Timeout, BYE, Netztrennung, TLS-/Authfehler, Rate-Limit,
  Crash und Resume.
- Atomare Projektion, SQLite-Transaktion, Checkpoint erst nach Commit und sichere
  Retention.
- Live-Locator-Konflikt verhindert Lesen oder Aktion über veraltete UID.
- Vollständiger Nulltreffer liefert `no-match`; jeder unvollständige Nulltreffer
  liefert `inconclusive`.
- Mehrsprachige Agentenantworten behaupten keine Vollständigkeit oder Bodysuche
  ohne Nachweis.
- Single-Writer-, Mount-, Secret-, Netzwerk-, Artefakt- und Datenschutzgrenzen.
- `version --verify`, `git diff --check`, `check-repo.sh`, Compose, Wheel,
  Rollenimages und hermetische Containerintegration.

## Reihenfolge der vorgesehenen Commits

1. `docs(imap): capture authoritative connector baseline`
2. `feat(imap): define readonly inventory and coverage contract`
3. `feat(imap): add native readonly inventory connector`
4. `feat(mail-search): adapt backfill and live locator to native imap`
5. `feat(mail-search): track external moves incrementally`
6. `feat(mail-search): stage controlled authoritative backfill`
7. `feat(jobs): schedule single-owner mail reconciliation`
8. `fix(agent): enforce evidence-bound negative mail answers`
9. `docs(mail-search): complete m12 acceptance and rollout contract`

Jedes Paket bleibt einzeln prüfbar, aktualisiert passende Tests, Dokumentation
und `SOURCE_MANIFEST.sha256` und beendet die Arbeit vor dem Folgepaket.

## Naechster erlaubter Schritt

Nach Annahme dieser Roadmap ist der nächste Entwicklungsschritt ausschließlich
M12.0. Die Roadmap selbst autorisiert weder den Zugriff auf produktive
Mailinhalte noch einen Backfill, eine Reconciliation, einen Jobstart, eine
Konfigurationsänderung oder einen produktiven Rollout.
