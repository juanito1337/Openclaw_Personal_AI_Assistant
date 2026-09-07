# ADR-0037: Residenter ClamAV-Daemon für den Mailindex

Status: Accepted

## Kontext

Der Containerstack besitzt bereits einen isolierten `clamav-update`-Dienst und
ein gemeinsam verwendetes Signaturvolume. Normale Rollen enthalten
`clamdscan` und `clamscan`, aber keinen erreichbaren residenten `clamd`-Prozess.
Der Antivirusadapter fällt deshalb für jeden ungecacheten Inhalt auf einen
neuen `clamscan`-Prozess zurück. Dieser lädt die Signaturdatenbank je Scan neu.

Der Scanner bleibt dabei fail-closed und erkennt Malware. Der gemessene
Einzelstartaufwand von mehr als zehn Sekunden verhindert jedoch den
autoritativen Erstindex für ungefähr 8.549 Nachrichten. Ohne veröffentlichte
Indexgeneration bleibt die kontoweite Suche begrenzt und darf einen leeren
Trefferbestand nicht als Abwesenheitsbeweis verwenden.

## Entscheidung

Der Stack ergänzt einen eigenständigen langlebigen `clamd`-Container. Updater
und Daemon verwenden dasselbe attestierte Maintenance-Rollenimage, bleiben aber
prozess-, rechte- und netzseitig getrennte Rollen:

- `clamav-update` besitzt als einziger Prozess Egress und Schreibrecht auf dem
  Signaturvolume.
- `clamd` besitzt kein Netzwerk, keine Secrets und keine Fachstates. Er liest
  Signaturen read-only und schreibt nur seinen privaten Unix-Socket.
- Ein einmaliger eng begrenzter Socket-Initializer besitzt ausschließlich die
  Root-/Capability-Ausnahme `CHOWN` und `DAC_OVERRIDE` für dieses Volume.
- Gateway, Mailworker und Operator-CLI erhalten den Socket read-only und die
  numerische Zusatzgruppe 101. Andere Rollen erhalten keinen Socket.
- Clients verwenden ausschließlich `PING`, `VERSION` und `INSTREAM`. Der Port
  kann keine Daemonadministration, keinen Dateipfadscan und keinen
  ClamAV-Shutdown ausdrücken.
- Scanbytes werden gestreamt. Temporäre Maildateien werden nicht zwischen
  Container-Dateisystemen geteilt und es wird kein TCP-Port exponiert.

Die Cacheidentität ist die vom aktiven Daemon belegte Kombination aus Engine-
und Signaturversion. Der verwendete Transport wird separat gespeichert und
diagnostiziert. Ein Standalone-Fallback bleibt für bisherige begrenzte
Einzeloperationen sichtbar und fail-closed verfügbar, ist aber keine
Indexbereitschaft. Backfill und Reconcile brechen vor dem ersten IMAP-Raw-Fetch
ab, wenn der Daemon oder die Signaturfrische nicht belegt ist.

## Folgen

- Die Signaturdatenbank wird einmal resident geladen; wiederholte Streamscans
  vermeiden den gemessenen Prozess-/Datenbankstart je Mail.
- Der neue Daemon vergrößert den residenten Speicherbedarf. Sein 2-GiB-Limit
  folgt dem bereits beobachteten Signaturladebedarf und wird in M14 weiter
  gemessen.
- Der Gatewaystart hängt von Daemon-Readiness ab. Ein nicht scanabhängiger
  Serverfallback bleibt nach einem späteren Daemonausfall sichtbar, aber kein
  sicherheitskritischer Pfad darf ungeprüfte Inhalte verarbeiten.
- Alte Cachezeilen mit einer anderen oder unbekannten Scanneridentität werden
  nicht umetikettiert. Sie können erst nach einem identischen belegten
  Identitätsnachweis wiederverwendet werden.
- Ein lokaler Rollback entfernt keine Mail und stellt keine externe Mail wieder
  her. Partielle Indexstagingdaten bleiben erhalten, beweisen aber keine
  Vollständigkeit.

## Verworfene Alternativen

### `clamscan` für den gesamten Erstindex weiterverwenden

Sicherheitsfunktion ist vorhanden, die reale Laufzeit liegt aber außerhalb des
begrenzten Betriebsfensters und verhindert die eigentliche Suchfunktion.

### Daemon und Signaturupdate in einem Prozesscontainer kombinieren

Damit erhielte der Parserdienst unnötig Egress und der Signaturwriter würde mit
dem Scanprozess gekoppelt. Getrennte Rollen besitzen kleinere Rechte und klarere
Health-/Fehlerzustände.

### ClamAV-TCP-Port im Backendnetz veröffentlichen

Das würde mehr Container als nötig erreichbar machen und benötigt keine
Authentifizierung. Der rollenbegrenzt gemountete Unix-Socket bildet die engere
Grenze.

### Antivirus für den Erstindex umgehen oder nur Anhänge scannen

Das verletzt den bestehenden fail-closed Vertrag für vollständige Raw-Mail und
physische Anhänge und ist ausgeschlossen.
