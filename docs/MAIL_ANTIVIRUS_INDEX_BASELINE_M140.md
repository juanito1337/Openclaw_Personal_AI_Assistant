# M14.0-Baseline: Antiviruspfad und produktiver Mailindex

Stand: 2026-09-07

Diese Baseline enthält ausschließlich technische Aggregate. Adressen,
Betreffe, Bodies, Anhänge, Locator und Zugangsdaten werden weder hier noch in
den zugehörigen Tests gespeichert.

## Produktiver Ausgangszustand vor M14

| Messwert | Beobachtung |
| --- | --- |
| Personal-Assistant-Release | `3.4.0-r28` |
| produktive Quellrevision | `f97866e95e366b44d0d7fb701789b4142af4dd3d` |
| lesbare IMAP-Ordner | 22 |
| inventarisierte Nachrichten | ungefähr 8.549 |
| Mailindexgeneration | fehlt |
| lokale Suchfreigabe | `search_eligible=false` |
| produktive Indexjobläufe | 0 |
| gewünschter Indexjobzustand | `off` |
| ClamAV-Updater | gesund |
| ClamAV | `1.4.3/28115` |
| Signaturzeit | 2026-09-06 06:26 UTC |
| `clamd` im Scanpfad | nicht erreichbar |
| Standalone-Fallback | `clamscan` |
| 42-Byte-Livescan | 10.342,38 ms |
| frühere beobachtete ungecachete Scans | ungefähr 14–38 Sekunden/Nachricht |
| saubere Cacheeinträge im Sieben-Tage-Status | 428 |

Eine kontoweite Suche nach einer bekannten Absenderadresse ergab vor M14 null
Treffer, aber ausdrücklich:

- `decision=inconclusive`,
- `complete=false`,
- `absence_proven=false`,
- `negative_claim_allowed=false`,
- alle 22 Ordner angesprochen,
- maximal 100 Envelope-Datensätze je Ordner,
- Body-Suche nicht belegt,
- `results_may_be_truncated=true`.

Damit ist weder die Existenz noch die Abwesenheit der gesuchten Mail aus dem
Nulltreffer ableitbar. Die frühere natürlichsprachliche Negativaussage war
nicht durch den Werkzeugvertrag gedeckt.

Bei rund 8.100 nicht durch den aktuellen Cache belegten Nachrichten ergibt
bereits der letzte kleine Livescan eine Größenordnung von mehr als 23 Stunden.
Das ist eine Prognose, kein SLA. Größe, Anhänge, Cacheidentität und tatsächlicher
Durchsatz beeinflussen die reale Dauer zusätzlich.

## Reproduzierbare read-only Befehle

Im Quellcheckout:

```bash
./scripts/assistant.sh version --verify
git status --short
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
```

Im installierten Stack:

```bash
/opt/openclaw-agent/scripts/assistant.sh security antivirus doctor
/opt/openclaw-agent/scripts/assistant.sh mail index status
/opt/openclaw-agent/scripts/assistant.sh mail index doctor
/opt/openclaw-agent/scripts/assistant.sh mail index capabilities --no-raw-probe
/opt/openclaw-agent/scripts/assistant.sh jobs status --target mail-index --deep
```

Private Suchbegriffe werden nur interaktiv über das registrierte
`mail search`-Werkzeug verwendet und nicht in Git oder öffentliche Logs kopiert.
Jedes Ergebnis muss anhand von `decision`, `complete`, `absence_proven`,
`negative_claim_allowed`, `coverage`, `folder_errors`,
`filter_limitations` und `results_may_be_truncated` bewertet werden.

## M14-Entwicklungsnachweis

Der hermetische Test verwendet eine synthetische lokale Signatur und keine
öffentliche Netzwerkverbindung. Er belegt:

- realen Start des gepinnten `clamd`-Binärprogramms,
- non-root User `100:101`, read-only Rootfs, `cap_drop=ALL` und
  `network_mode=none`,
- privaten Unix-Socket mit separatem Initializer,
- erfolgreichen Clean-Streamscan,
- erfolgreichen synthetischen Malwaretreffer,
- typisierte Socket-, Oversize-, Protokoll- und Fallbackfehler,
- getrennte Felder für Scannerfunktion, Daemonreadiness, Signaturfrische,
  Transport, Fallback und Indexbereitschaft,
- Verweigerung der Indexbereitschaft bei lediglich funktionierendem
  Standalone-Fallback.

Reproduzierbarer Befehl nach dem Maintenance-Imagebuild:

```bash
OPENCLAW_M14_MAINTENANCE_IMAGE=openclaw-agent:m14-maintenance \
  ./scripts/check-m14-integration.sh
```

Der hermetische Lauf vom 7. September 2026 auf der lokalen x86_64-Docker-
Engine ergab für das 65.520-Byte-Testobjekt 32 erfolgreiche Warm-Scans in
0,040089 Sekunden (798,233 Scans/s beziehungsweise 52.300.224 Byte/s). Der
Daemon war nach 1.689 ms ansprechbar, nutzte zwei Prozesse und zeigte im
Momentanwert 7,867 MiB belegten Containerspeicher. Das Maintenance-Image war
45.638.260 Byte groß; Runtime und Proxy belegten 377.152.122 beziehungsweise
23.422.435 Byte. Diese Werte sind reproduzierbare Entwicklungs-
Beobachtungen, keine hardwareunabhängigen Grenzwerte und kein Nachweis für den
produktiven Vollbackfill. Der Test belegt außerdem einen echten synthetischen
Signaturreload sowie einen fail-closed Stop mit anschließendem Recovery.

## Noch offene produktive Messwerte

M14.0 bis M14.7 verändern den laufenden Stack nicht. Folgende Werte werden erst
im getrennt genehmigten M14.8-Canary gemessen und dürfen vorher nicht als
bestanden ausgegeben werden:

- produktive `clamd`-Start-/Ready-Zeit und Peak-RAM,
- Warm-Latenz und Durchsatz für reale Größenklassen,
- tatsächliche Resume- und Vollbackfilldauer,
- vollständige autoritative Generation und Locatorabdeckung,
- produktive Suchlatenzen und Bodytreffer,
- erfolgreicher bekannter Absendertreffer,
- belegter Negativfall bei vollständiger Coverage,
- inkrementelle No-op-, New-Mail- und Move-Kosten,
- siebentägige Stabilität von Signaturreload, Ressourcen und Indexjob.

Konkrete produktive Budgets werden aus dem Canary abgeleitet und als
Betriebsentscheidung dokumentiert. Bis dahin bleibt M14.8 offen.
