# M14-Roadmap: Performanter ClamAV-Daemon und produktiver Mailindex

Stand: 2026-09-08
Vorgesehener Arbeitsbranch: `development/clamd-mail-index-rollout-m14`
Status: M14.0 bis M14.7 im Entwicklungsbranch umgesetzt und lokal/hermetisch
abgenommen. Im getrennt freigegebenen M14.8-Rollout waren Deploy und Canary
erfolgreich. Der erste Vollbackfill blieb nach 7.876 Nachrichten fail-closed an
sechs infizierten Nachrichten und dem Laufzeitbudget stehen. Die in ADR 0038
definierte Einzelfallquarantaene und der danach notwendige Neuaufbau werden als
signiertes Folgeimage vorbereitet. Der Indexjob bleibt aus.

## Ausgangslage

M11 hat den lokalen Hybridindex entwickelt. M12 hat den nativen, strikt
read-only arbeitenden IMAP-Inventar- und Reconciliation-Pfad ergänzt. M13 hat
die Mailwerkzeuge und ihre Vollständigkeitsnachweise als strukturierte
Agentenwerkzeuge verfügbar gemacht. Die produktive Suchlücke ist deshalb kein
fehlender zweiter Mailclient und kein fehlendes Suchschema mehr: Der
autoritative Erstindex wurde noch nicht vollständig veröffentlicht.

Die am 7. September 2026 read-only gemessene produktive Ausgangslage lautet:

- 22 lesbare IMAP-Ordner mit ungefähr 8.549 Nachrichten,
- `mail index status`: keine veröffentlichte Generation,
  `search_eligible=false`, Coverage 0,
- `jobs status --target mail-index --deep`: gewünschter Zustand `off`, null
  produktive Läufe,
- Suche nach einer bekannten Absenderadresse: `decision=inconclusive`, null
  Treffer, `complete=false`, `absence_proven=false`,
- Live-Server-Fallback: alle 22 Ordner angesprochen, aber nur bis zu 100
  Envelope-Datensätze je Ordner und keine belegte Body-Suche,
- ClamAV-Updater gesund; Signaturdatenbank aktuell und Scanner-Test sauber,
- `clamdscan` installiert, aber kein erreichbarer `clamd`-Prozess,
- deshalb startet der Scanpfad für nicht gecachte Inhalte jeweils das
  eigenständige `clamscan`; ein 42-Byte-Livetest dauerte zuletzt rund 10,3
  Sekunden, frühere Backfill-Beobachtungen lagen teilweise bei 14 bis 38
  Sekunden je Nachricht.

Diese Werte sind ein Diagnose-Snapshot, noch keine eingefrorene M14-Baseline.
Sie müssen in M14.0 reproduzierbar und ohne Mailinhalte erneut erhoben werden.
Bei rund 8.100 noch nicht im aktuellen Cache belegten Nachrichten würde allein
der zuletzt gemessene Einzelstartaufwand größenordnungsmäßig mehr als 23 Stunden
beanspruchen. Der vorhandene begrenzte Backfill kann so nicht innerhalb seines
Betriebsfensters fertig werden.

Der Virenschutz ist damit sicherheitsseitig nicht abgeschaltet: Er arbeitet
fail-closed und erkennt Testdaten. Sein Containerbetrieb verwendet aber nicht
den vorgesehenen residenten Daemonpfad. Das verhindert den praktikablen
Erstindex und lässt die Agentensuche auf einen unvollständigen Serverfallback
zurückfallen. Eine leere Fallback-Suche darf weiterhin niemals als Beweis
ausgegeben werden, dass eine Mail nicht existiert.

## Architektonisches Ziel

M14 ergänzt einen dedizierten, gehärteten und ausschließlich intern
erreichbaren `clamd`-Dienst. Der bestehende Signatur-Updater bleibt alleiniger
Schreiber des ClamAV-Datenbank-Volumes. Scanberechtigte Rollen übertragen zu
prüfende Bytes über einen eng freigegebenen Unix-Socket; Mailinhalte werden
weder über ein allgemeines Containernetz veröffentlicht noch in ein gemeinsam
lesbares temporäres Verzeichnis gelegt.

Der Mailindex erhält einen maschinenlesbaren Antivirus-Readiness-Gate. Ein
produktiver Indexlauf beginnt nur, wenn der Daemon erreichbar, seine
Signaturidentität belegt und seine gemessene Kapazität für den genehmigten Lauf
ausreichend ist. Fällt der Daemon während eines Laufs aus, bleiben Checkpoint
und letzte vollständige Generation erhalten; es entstehen weder falsche
Coverage noch Tombstones oder Negativaussagen.

Nach erfolgreicher Entwicklung wird ein signiertes Image getrennt produktiv
ausgerollt. Erst ein sauberer Canary, ein vollständig veröffentlichter und
autoritativer Vollindex, belegte positive und negative Suchfälle und eine
erneute Freigabe erlauben den persistenten `mail-index`-Job.

```text
                         egress-Netz
                              |
                     ClamAV-Signaturquelle
                              |
                              v
                  clamav-update (einziger Writer)
                              |
                    atomar aktualisiertes DB-Volume
                              |
                           read-only
                              v
                   clamd (resident, non-root)
                   - kein externes Netzwerk
                   - read-only root filesystem
                   - begrenzte Threads/RAM/PIDs
                   - privater Unix-Socket
                              |
                   nur ausgewählte Scanrollen
                              v
              Mail-Owner / Gateway / kontrollierte CLI
                   - Streamscan, kein Shared-Temp
                   - fail-closed und typisierte Fehler
                   - Cache nach Engine-/Signaturidentität
                              |
                              v
           M12 Backfill/Reconcile -> atomare Generation
                              |
                              v
                    vollständige Hybridsuche
```

## Verbindliche Sicherheits- und Betriebsgrenzen

- ClamAV bleibt für vollständige Raw-Mail, jeden physischen Anhang und jeden
  bereits geschützten Uploadpfad verpflichtend und fail-closed.
- Es gibt keinen Schalter, der Virenschutz, TLS, Audit, Backup, Größenlimits
  oder Quarantäne zum Erreichen eines Performanceziels abschwächt.
- Nur `clamav-update` darf die Signaturdatenbank schreiben. `clamd` und alle
  Clients mounten sie read-only.
- `clamd` erhält kein Egress-Netz, keine Secrets, keinen Docker-Socket, keine
  produktiven Mail-/Nextcloud-Mounts und keine Linux-Capabilities.
- Der Unix-Socket wird nur in tatsächlich scanberechtigte Rollen gemountet.
  UID/GID und Socketmodus werden explizit festgelegt und getestet; `0777` oder
  ein allgemein beschreibbares Hostverzeichnis sind ausgeschlossen.
- Containerclients streamen Inhalte. Ein Daemon darf nicht auf private
  temporäre Dateien eines anderen Containers angewiesen sein.
- Scanergebnisse dürfen weder Body, Betreff, Adresse noch Dateiinhalte in Logs,
  Metriken, Git, Image, Wheel oder CI-Artefakte übernehmen.
- Cache-Wiederverwendung ist nur für denselben Inhalts-SHA-256 und dieselbe
  belegte Engine-/Signaturidentität zulässig. Alte Einträge werden nicht
  umetikettiert oder blind migriert.
- Der langsame Standalone-Fallback darf keine vermeintlich gesunde
  Indexbereitschaft erzeugen. Sein Verhalten für andere bestehende,
  sicherheitskritische Einzeloperationen wird in M14.1 ausdrücklich festgelegt
  und bleibt immer fail-closed.
- Backfill und Reconcile bleiben strikt read-only gegenüber IMAP. M14 führt
  keine zweite Mail-Schreibimplementierung ein und ersetzt nicht den
  kontrollierten Himalaya-Pfad für erlaubte Einzelaktionen.
- Entwicklung und hermetische Tests verändern weder `/srv/openclaw` noch
  produktive Jobs, Mailflags, Ordner, Nachrichten oder andere externe Daten.
- Produktiver Deploy, Canary, Vollbackfill und Jobaktivierung sind voneinander
  getrennte, explizit genehmigte Aktionen mit Backup- und Rollbacknachweis.

## Mess- und Abnahmeprinzip

M14.0 friert zunächst reale Werte und eine repräsentative synthetische
Lastmatrix ein. Erst daraus werden konkrete Budgets für Scanlatenz, Durchsatz,
Speicher, Backfilldauer und Parallelität beschlossen. Es werden keine
willkürlichen Grenzwerte rückwirkend als Baseline ausgegeben.

Mindestens zu messen sind:

- ClamAV-Engine-, Signatur-, Client- und Daemonversion,
- Signaturalter und atomarer Update-/Reloadzeitpunkt,
- Cold- und Warm-Latenz für kleine Raw-Mail, typische Raw-Mail,
  PDF-Anhang und den erlaubten Maximalfall,
- Durchsatz bei der freigegebenen Parallelität sowie Queue-/Timeoutverhalten,
- `clamd`-RSS, Container-Peak-RAM, CPU, PIDs und Start-/Ready-Zeit,
- Cache-Hit-/Miss-Zahlen getrennt nach aktueller Scanneridentität,
- erwartete und tatsächliche Vollbackfilldauer, Nachrichten und Bytes,
- Zahl gescannter, gecachter, blockierter und fehlerhafter Inhalte,
- Resume-Aufwand nach kontrolliertem Abbruch oder Daemon-Neustart,
- Indexgeneration, Coverage, Locatorabdeckung und Projektionsgröße,
- p50/p95/p99 der lokalen Absender-, Betreff-, Body- und strukturierten Suche,
- inkrementelle Kosten für neue Mail, externen Move, Copy/Delete und No-op,
- Fallback- und `inconclusive`-Häufigkeit nach der Aktivierung.

## Paketübersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M14.0 | Reproduzierbare Antivirus-/Index-Baseline | M12/M13 Entwicklungsstand |
| M14.1 | Sicherheits-, Transport-, Cache- und Fallbackvertrag | M14.0 |
| M14.2 | Gepinntes, minimales und gehärtetes `clamd`-Image | M14.1 |
| M14.3 | Socketbasierter Scanadapter und ehrliche Diagnostik | M14.2 |
| M14.4 | Rollenbegrenzte Compose- und Deploymentintegration | M14.3 |
| M14.5 | Fehler-, Last-, Update- und Resilienzabnahme | M14.4 |
| M14.6 | Sicherer Resume-/Vollindex-Vertrag und Suchabnahme | M14.5 |
| M14.7 | Gesamtprüfung, Dokumentation und signiertes Kandidatenimage | M14.6 |
| M14.8 | Produktiver Canary, Vollbackfill, Jobfreigabe und Beobachtung | M14.7 |

## M14.0 – Reproduzierbare Antivirus- und Indexbaseline

### Ziel

Den aktuellen Engpass reproduzierbar messen und Scannerfunktion,
Signaturversorgung, Transportmodus, Cachewirkung und Indexzustand getrennt
ausweisen. Die Baseline ist inhaltsfrei und verändert keinen produktiven
Jobzustand.

### Scope

- Release, Worktree, Compose-Rendering und aktuelle Werkzeugversionen erfassen.
- `security antivirus doctor` und `self-test` getrennt auswerten. Ein sauberer
  oder korrekt als infiziert erkannter Test ist nicht automatisch der Nachweis
  eines aktiven Daemons.
- Updatergesundheit, Signaturalter und tatsächlich verwendeten Scantransport
  maschinenlesbar erfassen.
- `mail index status`, `doctor`, `capabilities --no-raw-probe` und
  `jobs status --target mail-index --deep` dokumentieren.
- Nur synthetische, datenschutzsichere Payloadgrößen benchmarken; keine
  produktiven Mailinhalte exportieren oder in Berichte aufnehmen.
- Aus Nachrichtenzahl, aktuellem Cache, Warm-/Cold-Latenz und Laufzeitlimit eine
  nachvollziehbare Backfillprognose bilden.
- Bestehende Ressourcenlimits und OOM-/Restartwerte read-only erfassen.

### Abnahme

- Report trennt `scanner_works`, `daemon_ready`, `signatures_fresh`,
  `fallback_used` und `index_ready`.
- Jede Zahl besitzt Befehl, Zeitpunkt, Version und Maßeinheit.
- Keine private Mailmetadaten oder Inhalte erscheinen in Fixture oder Git.
- Es wird kein Backfill, Reconcile, Jobstart, Containerneustart oder Deploy
  durchgeführt.

### Entwicklungsprompt

```text
Setze ausschließlich M14.0 aus
docs/MAIL_ANTIVIRUS_INDEX_ROLLOUT_ROADMAP.md um. Lies AGENTS.md sowie die Mail-,
Runtime-, Tool- und Antivirusdokumentation vollständig. Verändere keine Dateien
unter /srv/openclaw, keine produktiven Container, Jobs, Maildaten oder
Konfigurationen. Erfasse Release und Worktree sowie die registrierten
Antivirus-, Mailindex- und Jobstatuspfade read-only. Trenne Scannerfunktion,
Daemonbereitschaft, Signaturfrische, aktiven Transport, Standalone-Fallback,
Cache und Indexbereitschaft. Benchmarke ausschließlich synthetische Payloads in
reproduzierbaren Größen und dokumentiere Cold/Warm-Latenz, Durchsatz, CPU, RAM,
PIDs, Startzeit und eine nachvollziehbare Backfillprognose. Speichere keine
Adresse, keinen Betreff, Body, Locator oder Secret. Setze noch keine
willkürlichen Zielwerte, starte keinen Backfill und beginne nicht mit M14.1.
```

## M14.1 – Sicherheits-, Transport-, Cache- und Fallbackvertrag

### Ziel

Vor der Containerimplementierung die Vertrauensgrenzen und das Verhalten für
jeden Ausfallzustand verbindlich festlegen.

### Scope

- ADR für `clamav-update`, Signaturvolume, `clamd`, privaten Unix-Socket und
  scanberechtigte Rollen erstellen.
- Bedrohungsmodell für manipulierte Streams, Socketzugriff, Symlink-/Pfadfehler,
  übergroße Eingaben, Zip-Bombs, Timeouts, Daemonabbruch, veraltete Signaturen
  und Update-Races festhalten.
- Streamtransport als Containerstandard definieren; keine gemeinsam lesbaren
  Mail-Tempdateien und kein ungeschützter TCP-Port.
- Kanonische Scanneridentität aus belegter Engine- und Signaturversion
  definieren. Transport (`daemon-stream`, `standalone`) bleibt ein separates
  Diagnosefeld und wird nicht mit Cacheidentität verwechselt.
- Eine explizite Fallbackmatrix definieren. Der Indexer startet ohne gesunden
  Daemon nicht; bestehende Einzeloperationen dürfen einen dokumentierten
  Standalone-Fallback nur sichtbar, begrenzt und weiterhin fail-closed nutzen.
- Timeout-, Größen-, Parallelitäts- und Queuevertrag aus M14.0 ableiten.
- Host-/Legacybetrieb rückwärtskompatibel halten, ohne Containerbereitschaft aus
  `systemctl` abzuleiten.

### Abnahme

- Für jeden Ausfall ist festgelegt: fortsetzen, Standalone-Fallback,
  `degraded`, pausieren oder fail-closed abbrechen.
- Kein Pfad kann Virenschutz umgehen oder `complete=true` nach einem
  Scannerfehler erzeugen.
- Nur benannte Rollen erhalten den Socket; Signaturwriter und Scanner bleiben
  getrennt.
- Cache-Golden-Tests verhindern Wiederverwendung nach Signaturwechsel und
  erlauben sie für exakt identische belegte Identität.

### Entwicklungsprompt

```text
Setze nur M14.1 um. Erstelle auf Basis der gemessenen M14.0-Werte eine ADR für
einen dedizierten non-root clamd-Dienst mit read-only Signaturvolume und
rollenbegrenzt gemountetem Unix-Socket. Definiere Streamtransport, UID/GID,
Socketmodus, Größen-, Timeout-, Thread-, Queue- und Ressourcenvertrag. Lege eine
kanonische Engine-/Signaturidentität und getrennte Transportdiagnostik fest.
Definiere eine explizite Fallbackmatrix: Der Mailindex darf ohne gesunden Daemon
nicht starten; vorhandene Einzeloperationen bleiben nur über einen sichtbaren,
begrenzten und fail-closed Standalone-Fallback kompatibel. Modellieren und teste
Daemonabbruch, stale signatures, Update-Race, Oversize, Timeout und Cachewechsel
als Vertrag. Implementiere noch keinen Container, führe keine produktive Aktion
aus und stoppe nach M14.1.
```

## M14.2 – Gepinntes und gehärtetes `clamd`-Image

### Ziel

Ein minimales reproduzierbares Rollenimage und einen residenten Scannerprozess
bereitstellen, ohne den Compose-Produktivstack bereits umzuschalten.

### Scope

- Eigenes Buildtarget oder klar getrenntes Rollenimage mit exakt gepinnten
  ClamAV-Paketen und Basisimage erstellen.
- Nur für `clamd`, Healthcheck und Prozessstart notwendige Binärdateien und
  Laufzeitbibliotheken aufnehmen.
- Root-Dateisystem read-only, non-root User, `cap_drop: ALL`,
  `no-new-privileges`, begrenzte PIDs/RAM/CPU und kontrollierte tmpfs-/Socket-
  Verzeichnisse vorbereiten.
- Signaturdatenbank nur read-only akzeptieren; fehlende, unvollständige,
  veraltete oder während Start wechselnde Datenbank verhindert Readiness.
- `clamd.conf` deterministisch erzeugen oder imageeigen versionieren. Keine
  Secrets, Shellsubstitution aus untrusted Input oder freier TCP-Listener.
- Start-, Stop-, Reload- und Healthchecksemantik implementieren. Readiness muss
  einen echten Daemonkontakt und die aktive Signaturidentität belegen.
- Image in Supply-Chain-Lock, SBOM-, Secret-, CVE-, Rollen- und
  Architekturprüfungen aufnehmen.

### Abnahme

- Image baut reproduzierbar und startet ohne Root/Caps/Egress.
- Clean-Test ist sauber, EICAR wird erkannt, Scannerfehler bleibt Fehler.
- Healthcheck wird erst nach geladener Datenbank grün.
- Socket und temporäre Dateien besitzen exakt die beschlossenen Rechte.
- Image enthält keine Secrets, Maildaten, produktive Konfiguration, Datenbanken
  oder Logs.

### Entwicklungsprompt

```text
Setze ausschließlich M14.2 um. Implementiere das in ADR M14.1 festgelegte
gepinntes minimale clamd-Rollenimage, aber ändere noch nicht den produktiven
Composepfad. Starte clamd non-root mit read-only Root-Dateisystem, ohne
Capabilities, ohne Egress und mit begrenzten Ressourcen. Binde ausschließlich
eine read-only Signaturdatenbank sowie kontrollierte tmpfs-/Socketpfade ein.
Implementiere eine echte Readinessprüfung mit Daemonkontakt und aktiver
Engine-/Signaturidentität. Teste fehlende, stale, unvollständige und wechselnde
Signaturen, Clean, EICAR, Fehler, Stop und Reload hermetisch. Aktualisiere Pins,
Supply-Chain-Prüfungen, SBOM-/Secret-/CVE-Scan und Rollensmokes. Verändere keine
Produktivcontainer und beginne nicht mit M14.3.
```

## M14.3 – Socketbasierter Scanadapter und ehrliche Diagnostik

### Ziel

Den bestehenden `HostAntivirus`-Vertrag um einen containergeeigneten,
streambasierten Daemontransport erweitern und seinen tatsächlich verwendeten
Pfad maschinenlesbar machen.

### Scope

- Unix-Socket und Streamscan ohne Shellinterpretation ansprechen. Der Daemon
  benötigt keinen Dateipfad aus dem Clientcontainer.
- Clean, Infected, Oversize, Timeout, Broken Pipe, Socket denied/unavailable,
  Protokollfehler und Daemon-BYE stabil typisieren.
- `doctor`, `self-test`, Status und Telemetrie um `daemon_ready`,
  `transport`, `fallback_used`, `fallback_reason`, `engine_version`,
  `signature_version`, `signature_age`, `latency_ms` und Readinessgrund
  ergänzen.
- In Containern Daemongesundheit am Socket statt an einem nicht vorhandenen
  systemd-Unitstatus bestimmen. Hostbetrieb behält seinen dokumentierten Pfad.
- Cache nur über den kanonischen M14.1-Identitätsvertrag verwenden; alte
  unbekannte Identitäten bleiben getrennt.
- Für Indexläufe eine Preflightfunktion bereitstellen, die Standalone-Fallback
  ausdrücklich nicht als Daemonbereitschaft akzeptiert.
- Fehlermeldungen inhaltsarm halten; keine temporären Pfade oder gescannten
  Nutzdaten protokollieren.

### Abnahme

- Verhaltensprüfungen belegen den aktiven Transport, nicht nur vorhandene
  Binärdateien.
- Ein sauberer Standalone-Scan kann `scanner_works=true`, aber niemals
  `daemon_ready=true` oder `index_ready=true` erzeugen.
- EICAR wird über den Stream erkannt; Scannerfehler werden nicht als sauber
  gecacht.
- Signaturwechsel invalidiert Cache, ein unveränderter Digest mit identischer
  Identität wird wiederverwendet.

### Entwicklungsprompt

```text
Setze nur M14.3 um. Erweitere den bestehenden Antivirusadapter gemäß ADR M14.1
um Unix-Socket-INSTREAM, ohne freie Shell und ohne gemeinsam gemountete
Mail-Tempdateien. Typisiere Clean, Infected, Oversize, Timeout, Socket denied,
Socket unavailable, Protokollfehler und Daemonabbruch. Mache in Doctor,
Self-Test, Status und technischer Telemetrie den wirklich verwendeten Transport,
Fallbackgrund, Daemonreadiness sowie Engine-/Signaturidentität sichtbar.
Container dürfen systemctl nicht als Daemonnachweis verwenden; Hostbetrieb muss
rückwärtskompatibel bleiben. Implementiere das Index-Preflight so, dass ein
Standalone-Fallback nie als performante Indexbereitschaft gilt. Ergänze echte
Verhaltens-, Cache- und Negativtests und stoppe nach M14.3.
```

## M14.4 – Rollenbegrenzte Compose- und Deploymentintegration

### Ziel

Den gehärteten Scanner in den Stack integrieren, ohne Datenowner-, Mount-,
Netzwerk- oder Single-Writer-Grenzen aufzuweichen.

### Scope

- `clamd` als eigene Compose-Rolle mit benanntem Socketvolume und read-only
  Signaturvolume ergänzen.
- Socket nur in Gateway, Mail-Owner/Mailworker und die kontrollierte Operator-
  CLI mounten, sofern deren bestehende Fachpfade tatsächlich scannen. Andere
  Rollen erhalten ihn nicht vorsorglich.
- `clamd` an kein Egress- oder öffentliches Netz hängen. Falls Compose für
  Serviceauflösung ein internes Netz technisch benötigt, darf darüber kein
  Scanport exponiert werden.
- Startabhängigkeiten und Degraded-Verhalten so festlegen, dass
  sicherheitskritische Scans fail-closed bleiben, reine nicht-scanabhängige
  Read-only-Funktionen aber nicht fälschlich als gesund oder kaputt erscheinen.
- Updater, atomare Signaturpublikation und Daemonreload unter konkurrierendem
  Zugriff testen.
- Ressourcenwerte aus M14.0/M14.5 ableiten; kein blindes Kopieren bestehender
  Limits.
- Deploymentbundle, Beispielumgebung, Rollback, Rollenimages,
  Composevalidierung und Artefaktprüfung erweitern.

### Abnahme

- Compose-Rendering und Containerlint sind grün.
- Nur `clamav-update` schreibt Signaturen; nur `clamd` schreibt den Socket.
- Keine Rolle erhält neue Secrets, fachliche Statewrites oder unnötige Netze.
- Live-Härtungstest bestätigt non-root, read-only, Caps, Mountmodi,
  Netzisolation, Ressourcen und Health.
- Rollback auf das vorherige Image benötigt keine Löschung von Index oder
  Antiviruscache und ändert keine externe Mail.

### Entwicklungsprompt

```text
Setze ausschließlich M14.4 um. Integriere das geprüfte clamd-Image als eigene
gehärtete Compose-Rolle. Verwende ein privates Socketvolume und das bestehende
Signaturvolume read-only; nur clamav-update darf Signaturen schreiben. Mounte
den Socket ausschließlich in nachweislich scanberechtigte Rollen. Gib clamd
keinen Egress, keinen veröffentlichten Port, keine Secrets, keine Mail-/Cloud-
States, keine Capabilities und kein beschreibbares Root-Dateisystem. Leite
Ressourcen aus Messwerten ab und teste Compose, Rollenmounts, Netzwerk,
Startreihenfolge, Update/Reload, Deploymentbundle und Rollback hermetisch.
Veraendere weder /srv/openclaw noch laufende Container und stoppe nach M14.4.
```

## M14.5 – Fehler-, Last-, Update- und Resilienzabnahme

### Ziel

Belegen, dass der neue Pfad nicht nur im Happy Path schnell ist, sondern bei
realistischen Fehlern sicher und wiederaufnehmbar bleibt.

### Scope

- Hermetischen End-to-End-Stack aus Signatur-Updater, `clamd`, Scanclients und
  synthetischem Mailindex ausführen.
- Kleine/große Raw-Mail, null/einen/mehrere Anhänge, erlaubten Grenzfall,
  Oversize, EICAR und Parserfehler prüfen.
- Daemon vor Scan, während Stream und nach erfolgreichem Scan abbrechen;
  Timeout, Queuevoll, OOM/Restart, Socketrechte und beschädigte Antwort testen.
- Signaturupdate und Reload während laufender Scans simulieren. Jeder
  Scan speichert genau die tatsächlich verwendete Identität.
- Parallelität stufenweise messen und Sättigung, RAM-Peak, CPU, Queuezeit und
  Fehlerrate dokumentieren.
- Aus M14.0 und den neuen Messungen konkrete Betriebsbudgets beschließen und in
  einer maschinenlesbaren Baseline einfrieren.
- Regressionen gegen diese Baseline sichtbar machen, ohne CI von variabler
  öffentlicher Netzwerk- oder Hostleistung abhängig zu machen.

### Abnahme

- EICAR und Scannerfehler gelangen nie in Parser, FTS, Embeddings oder
  fachliche Writes.
- Abbruch erzeugt keine saubere Cachezeile, vollständige Generation oder
  Tombstones.
- Ein Signaturreload korrumpiert weder Scans noch Identität.
- Die beschlossene Kapazität reicht nachweislich für das vereinbarte
  Backfillfenster; andernfalls bleibt M14 offen.
- Performanceprüfungen enthalten echte Aufrufe, keine Textsuche nach
  Konfigurationswerten.

### Entwicklungsprompt

```text
Setze nur M14.5 um. Baue einen hermetischen End-to-End-Teststack für Updater,
clamd, Socketclients und einen synthetischen M12-Index. Prüfe Clean, EICAR,
mehrere Anhänge, Oversize, Parserfehler, Socketfehler, Queuevoll, Timeout,
Daemonabbruch während eines Streams, OOM/Restart sowie Signaturupdate und
Reload unter Last. Belege, dass unvollständige Scans weder gecacht noch geparst
oder veröffentlicht werden. Messe Cold/Warm-Latenzen, Durchsatz, RAM, CPU,
PIDs, Queuezeiten und Parallelität. Leite daraus konkrete Betriebsbudgets für
den real gemessenen Kontoumfang ab und friere sie reproduzierbar ein. Nutze in
CI nur hermetische Daten und beginne nicht mit M14.6.
```

## M14.6 – Sicherer Resume-/Vollindex-Vertrag und Suchabnahme

### Ziel

Backfill und Reconcile an die neue Daemonreadiness binden und beweisen, dass
Abbruch, Resume und Veröffentlichung den autoritativen M12-Vertrag erhalten.

### Scope

- Vor Backfill/Reconcile aktiven Daemontransport, Signaturfrische,
  Scanlimits, freien Platz, Checkpointintegrität und Kapazitätsbudget prüfen.
- Einen vorhandenen partiellen M12-Checkpoint validieren und fortsetzen; bei
  Inkompatibilität sichtbar neu planen, niemals produktive Datenbanken löschen.
- Scanneridentität pro Content speichern. Bereits exakt passend gecachte
  Inhalte wiederverwenden, unbekannte oder alte Identität erneut scannen.
- Daemonfehler pausiert den Indexlauf kontrolliert. Letzte vollständige
  Generation bleibt veröffentlicht; partielle Stagingdaten beweisen keine
  Abwesenheit.
- Hermetisch vollständige Ordner-/Locatorabdeckung, Bodyindex,
  Attachment-Metadaten und atomare Rootgeneration belegen.
- Suchgoldfälle abdecken: exakte Absenderadresse, normalisierter Name,
  Betreffphrase, Bodybegriff, Zeitraum, aktueller Ordner nach externem Move und
  ein belegter Negativfall.
- Negativfall nur bei `complete=true`, frischer autoritativer Coverage,
  unterstützten Filtern und `negative_claim_allowed=true` akzeptieren.

### Abnahme

- Abbruch und Resume duplizieren oder verlieren keine Mail und wiederholen nur
  die nach dem Identitätsvertrag nötigen Scans.
- Ein Move unveränderten Inhalts erzeugt null neue ClamAV-/Parser-/Embedding-
  Arbeit.
- Positive Goldfälle werden mit aktuellem Live-Locator gefunden.
- Ein unvollständiger Lauf bleibt `inconclusive`; nur der vollständige
  Negativfall erlaubt eine definitive Abwesenheitsaussage.
- Kein Test schreibt IMAP-Flags oder verändert einen Mailserver.

### Entwicklungsprompt

```text
Setze ausschließlich M14.6 um. Binde M12-Backfill und Reconcile an einen
maschinenlesbaren clamd-Preflight für Transport, Signatur, Limits, Speicher,
Checkpoint und Kapazität. Validiere und resume partielle Stagingzustände, ohne
produktive Datenbanken zu löschen oder alte Cacheidentitäten umzudeuten. Bei
Daemonfehler bleibt die letzte vollständige Generation aktiv und der Lauf
unvollständig. Teste hermetisch Crash/Resume, neue Mail, unveränderten Move,
Copy/Delete, Signaturwechsel und vollständige Veröffentlichung. Ergänze
Verhaltensgoldfälle für Absenderadresse, Name, Betreff, Body, Zeitraum,
verschobenen Locator und einen echten Negativfall. no-match ist nur bei
complete, fresh, authoritative und vollständig unterstützten Filtern zulässig.
Führe keine produktive Indizierung oder Jobaktivierung aus und stoppe nach
M14.6.
```

## M14.7 – Gesamtprüfung, Dokumentation und Kandidatenimage

### Ziel

Den Entwicklungsstand vollständig prüfen, dokumentieren und als
unveränderliches, signiertes Kandidatenimage bereitstellen. Das ist noch keine
produktive Aktivierung.

### Scope

- Lokalen vollständigen Repositorycheck, Manifest, Lints, Typprüfung, Shell-,
  Dockerfile- und Composeprüfung sowie alle M11–M14-Integrationen ausführen.
- Wheel und alle Rollenimages aus sauberem Checkout bauen und auf Secrets,
  private Konfiguration, Maildaten, Datenbanken, Logs und Laufzeitstate prüfen.
- SBOM, Provenance, CVE-/Secret-Scan, Cosign-Signatur und Digestbindung für den
  exakten Commit erzeugen und verifizieren.
- `docs/ANTIVIRUS.md`, Test-, Build-, Deployment-, Backup-, Rollback-, Mail-
  und Betriebsdokumentation aktualisieren.
- Agentenreferenzen und Toolbeschreibungen so ergänzen, dass
  `scanner_works`, `daemon_ready`, `index_ready` und `inconclusive` nicht
  verwechselt werden.
- Produktives Runbook mit getrennten Stopppunkten für Deploy, Canary,
  Vollbackfill und Jobaktivierung schreiben.

### Abnahme

- `./scripts/check-repo.sh`, `git diff --check` und Composevalidierung sind
  erfolgreich.
- Hermetischer ClamAV-/M12-End-to-End-Test ist grün.
- Gepinnte Artefakte sind sauber, signiert und auf exakten Commit gebunden.
- Dokumentation enthält Befehle, erwartete Ergebnisfelder, Fehlerpfade,
  Ressourcenbudget und Rollback.
- Kein produktiver Container, Job, Index oder Mailzustand wurde verändert.

### Entwicklungsprompt

```text
Setze nur M14.7 um. Führe die vollständige lokale und hermetische Abnahme des
M14-Entwicklungsstands durch: Tests, Collection-Untergrenze, Coverage, Ruff,
mypy, ShellCheck, Dockerfilelint, Compose, git diff --check, Python-Kompilierung,
Manifest, Wheel und alle Rollenimages. Prüfe Artefakte auf Secrets,
Konfiguration, Maildaten, Datenbanken, Logs und Runtime-State. Erzeuge SBOM,
Provenance, CVE-/Secret-Scan, Cosign-Signatur und Digestnachweis für den exakten
Commit. Aktualisiere Antivirus-, Mail-, Test-, Build-, Deployment-, Backup- und
Rollbackdokumentation sowie Agentenvertrag. Erstelle ein Runbook mit getrennten
Freigaben für Deploy, Canary, Vollbackfill und Jobstart. Verändere keine
Produktivsysteme und beginne nicht mit M14.8.
```

## M14.8 – Produktiver Canary, Vollbackfill und Jobfreigabe

### Ziel

Das geprüfte Image kontrolliert installieren, den vollständigen Index
veröffentlichen und erst danach den inkrementellen Job aktivieren und
beobachten.

### Scope

- Vor jeder Änderung Version, exakten Quellcommit, Image-Digest, Signatur,
  Composekonfiguration, Legacywriter, freien Platz und Ressourcen prüfen.
- Verifiziertes lokales Release-/Statebackup erstellen und Restore testen.
  Externe Mail wird durch M14 nicht verändert; dies ausdrücklich dokumentieren.
- Signiertes Kandidatenimage installieren und ausschließlich Scanner/Runtime
  smoke-testen. Bei Fehler vor dem Indexlauf zurückrollen.
- `clamd`-Readiness, aktuelle Signaturen, Clean, EICAR, Latenz, RAM und
  Transport im produktiven Stack belegen.
- Einen begrenzten M12-Canary auf einem ausdrücklich ausgewählten Ordner
  durchführen. Keine IMAP-Writes und keine automatische Jobaktivierung.
- Canaryergebnis prüfen und eine neue Freigabe für den anhand realer Messwerte
  dimensionierten Vollbackfill einholen.
- Vollbackfill unter Mail-Owner-Lock ausführen beziehungsweise fortsetzen,
  Checkpoint, Scannerfehler, Kapazität und Generation überwachen.
- Bei Antivirus-Funden den Lauf nicht blind fortsetzen: Fundstellen mit `mail
  index blocked` inhaltsfrei erfassen, jede weiterhin infizierte Mail nur nach
  eigener expliziter Freigabe, unveraenderten Erwartungswerten und frischem
  Raw-/Attachment-Scan in den fest konfigurierten Malware-Ordner verschieben.
  Scanner-/Decode-/Groessenfehler sind keine Quarantaenefreigabe.
- Nach allen einzeln freigegebenen Moves einen separat freigegebenen Backfill
  mit `--restart` starten. Der Malware-Ordner bleibt vor jedem Raw-Fetch aus dem
  Suchumfang ausgeschlossen und wird in Plan, Ergebnis und Coverage sichtbar.
- Nur eine vollständige, frische, autoritative Generation mit kompletter
  Locatorabdeckung akzeptieren.
- Datenschutzsichere Suchabnahme durchführen: bekannte positive
  Absender-/Namenssuche, Betreff, Body, extern verschobene Nachricht,
  Drei-Tage-/Recent-Sicht und ein kontrollierter Negativfall. Reale private
  Suchbegriffe werden nicht in Git oder öffentliche Logs übernommen.
- Erst nach separater Freigabe `mail-index` aktivieren, inkrementellen No-op,
  neue Mail und externen Move prüfen und mindestens sieben Tage beobachten.
- Bei Daemon-, Coverage-, Locator-, Latenz-, Speicher- oder Suchregression den
  Indexjob stoppen, Serverfallback sichtbar lassen und den dokumentierten
  Rollback ausführen. Indexstate wird nicht gelöscht; externe Mail wird nicht
  „zurückgerollt“ oder verändert.

### Abnahme

- `security antivirus doctor` belegt Daemontransport und aktuelle Signaturen;
  Clean und EICAR liefern die erwarteten Ergebnisse.
- Vollbackfill veröffentlicht genau eine vollständige autoritative Generation;
  keine suchberechtigte Partition und kein suchberechtigter Ordner fehlt. Der
  konfigurierte Malware-Ordner ist als einziger Sicherheitsbereich explizit in
  `excluded_folders` belegt.
- Bekannte positive Nachrichten werden einschließlich aktueller Locator
  gefunden; Body-Suche ist belegt.
- Der kontrollierte Negativfall liefert nur bei vollständiger Coverage
  `negative_claim_allowed=true`.
- `jobs status --target mail-index --deep` belegt nach separater Freigabe den
  gewünschten und tatsächlichen Zustand sowie erfolgreiche Läufe.
- Sieben Tage lang bleiben Signaturupdates, inkrementelle Läufe,
  Ressourcenverbrauch, Fallbackrate und Suchvollständigkeit innerhalb der in
  M14.5 beschlossenen Budgets.
- Backup, Restoretest, Image-Digest, Befehle, Laufzeiten, Suchbelege,
  Einschränkungen und eventuelle Rollbackaktion sind dokumentiert.

### Betriebsprompt

```text
Führe ausschließlich den produktiven M14.8-Rollout nach
docs/MAIL_ANTIVIRUS_INDEX_ROLLOUT_ROADMAP.md und dem abgenommenen Runbook aus.
Lies AGENTS.md und alle Mail-, Runtime-, Tool-, Deployment-, Backup- und
Rollbackreferenzen vollständig. Prüfe zuerst Release, exakten Commit,
signierten Image-Digest, Compose, Ressourcen, Legacywriter sowie aktuellen
Antivirus-, Index- und Jobstatus read-only. Erstelle und restore-teste das
verifizierte lokale Backup. Installiere das signierte Kandidatenimage und belege
clamd-Readiness, Signaturfrische, Clean, EICAR, Transport, Latenz und RAM. Führe
nur nach der dafür erteilten Freigabe den exakt begrenzten Canary aus. Stoppe,
berichte und rolle zurück, falls er nicht vollständig und sicher ist. Hole vor
dem anhand realer Messwerte dimensionierten Vollbackfill eine neue explizite
Freigabe ein. Akzeptiere nur eine vollständige, frische, autoritative Generation
mit kompletter Locatorabdeckung. Prüfe danach bekannte positive Absender-,
Namens-, Betreff-, Body-, Move- und Recent-Fälle sowie einen kontrollierten
Negativfall, ohne private Begriffe in Git oder öffentliche Logs zu schreiben.
Aktiviere mail-index erst nach einer weiteren ausdrücklichen Freigabe. Beobachte
inkrementelle Läufe und Ressourcen sieben Tage. Bei Daemon-, Coverage-,
Locator-, Speicher-, Latenz- oder Suchregression stoppe den Indexpfad, erhalte
den sichtbaren Serverfallback und führe den dokumentierten Rollback aus. Lösche
keine produktiven Datenbanken und behaupte niemals, dass ein lokaler Rollback
externe Mail verändert oder wiederherstellt.
```

### M14.8-Zwischenbefund und Folgeprompt

Der am 8. September 2026 freigegebene Canary indizierte 68 Nachrichten in vier
Seiten vollständig. Der danach separat freigegebene Vollbackfill erreichte in
173 Seiten 7.876 Nachrichten und 985.296.943 Byte. Er schrieb keine IMAP-Daten
oder Providerflags, veröffentlichte wegen sechs `infected`-Blockaden und des
Laufzeitlimits aber absichtlich keine vollständige Generation. Alle sechs
Blockaden lagen in einem normalen Agentenordner; Mailinhalte wurden nicht
ausgegeben. `clamd` blieb gesund. Diese Zahlen sind ein produktiver,
inhaltsfreier Betriebsbeleg, keine Testfixture.

```text
Setze ausschließlich den M14.8-Antivirus-Fundstellen-Nachlauf um. Erzeuge eine
inhaltsfreie read-only Liste checkpointgebundener Fundstellen. Implementiere
einen separaten Writevertrag, der genau eine unverändert per Kandidaten-ID,
Quellordner, Mailbox-ID und Raw-SHA-256 gebundene Fundstelle nach expliziter
Einzelfreigabe erneut exportiert, Raw-Mail und jeden physischen Anhang ohne Cache
scannt und nur bei erneut bestätigtem Fund in den fest konfigurierten
Malware-Ordner verschiebt. Nutze Mail-Owner-Lock, Policy, idempotenten ActionPlan
und inhaltsfreies Audit. Verweigere Bulk, frei waehlbares Ziel, Hashkonflikt,
sauberen oder unklaren Nachscan, Scannerfehler und automatischen Retry. Schliesse
den Malware-Ordner vor Raw-Fetch aus Backfill und Reconcile aus und weise ihn in
Plan, Ergebnis und autoritativer Coverage explizit aus. Ergaenze einen separat
freizugebenden lokalen `--restart`-Neuaufbau, Verhaltens- und Negativtests,
Toolvertrag, Skill, ADR, Runbook und Changelog. Veraendere bei Entwicklung keine
produktiven Mails oder Jobs. Baue, pruefe und signiere ein Folgeimage; produktive
Einzelmoves, Neuaufbau und Jobaktivierung bleiben jeweils eigene Freigaben.
```

## Gesamt-Abnahmekriterien für M14

M14 ist erst abgeschlossen, wenn alle folgenden Aussagen belegt sind:

- Der produktive Scanpfad verwendet einen gesunden residenten `clamd` über den
  privaten rollenbegrenzten Unix-Socket.
- Signaturupdate und Scanbetrieb besitzen getrennte Owner und funktionieren
  auch bei atomarem Reload ohne Cache- oder Scanverwechslung.
- Clean, EICAR, Oversize, Timeout, Daemonabbruch und stale signatures sind
  fail-closed und durch echte Verhaltensprüfungen abgedeckt.
- Der gemessene Scanpfad erfüllt die nach M14.0 beschlossenen Kapazitätsbudgets
  für das reale Postfach.
- Ein Crash/Resume verliert keine Nachricht und veröffentlicht keine partielle
  Generation.
- Der vollständige produktive Index ist frisch, autoritativ und besitzt
  komplette aktive Locatorabdeckung.
- Bekannte Absender-, Adress-, Betreff-, Body-, Zeitraum- und Move-Fälle werden
  gefunden; ein Negativurteil ist nur mit vollständigem Nachweis möglich.
- Der inkrementelle Job ist erst nach separater Freigabe aktiv und sein Zustand
  wird durch den registrierten Deep-Status belegt.
- Wheel, Images, SBOM, Provenance, Scans, Signatur und Digest sind geprüft; kein
  privates oder produktives Material ist enthalten.
- Runbook, Architektur, Tests, Baseline, Ressourcen, Backup, Restore,
  Monitoring, Fehlerbehandlung und Rollback sind vollständig dokumentiert.

Bis dahin muss der Agent eine leere unvollständige Serversuche als
`inconclusive` erklären. Er darf nicht behaupten, eine Nachricht existiere
nicht, nur weil der lokale Vollindex noch fehlt.
