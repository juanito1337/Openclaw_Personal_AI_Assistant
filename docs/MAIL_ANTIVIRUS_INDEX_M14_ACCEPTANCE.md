# M14-Entwicklungsabnahme und produktive Stopppunkte

Stand: 2026-09-08

M14.0 bis M14.7 ersetzen den minutenlangen Standalone-Scan im Mailindex durch
einen residenten, gehärteten `clamd` und einen verpflichtenden fail-closed
Preflight. Diese Entwicklungsabnahme ist noch keine produktive Aktivierung.

## Abgenommener Entwicklungsumfang

- privater Unix-INSTREAM-Transport ohne Mailpfad- oder TCP-Freigabe,
- getrennte Rollen fuer Signaturupdate, Socketinitialisierung und Scanner,
- non-root `clamd`, read-only Rootfs/Signaturen, keine Capabilities und kein Netz,
- typisierte Socket-, Timeout-, Protokoll- und Groessenfehler,
- kanonische Engine-/Signaturidentitaet mit Cachetrennung,
- sichtbarer Standalone-Fallback fuer Einzeloperationen, aber niemals als
  Indexbereitschaft,
- verpflichtender Daemon-/Signatur-Preflight vor Backfill und Reconcile,
- unveraenderte atomare M12-Projektion: Scannerfehler veroeffentlichen keine
  partielle Generation,
- Compose-, Deployment-, Rollback-, Rollen-, Supply-Chain- und CI-Integration.

Der lokale Echt-Daemontest verwendet ausschließlich synthetische Daten:

```bash
docker build --target maintenance-runtime -t openclaw-agent:m14-maintenance .
OPENCLAW_M14_MAINTENANCE_IMAGE=openclaw-agent:m14-maintenance \
  ./scripts/check-m14-integration.sh
```

Die festen Sicherheits- und Kapazitaetsgrenzen stehen maschinenlesbar in
`docs/architecture/clamd-operating-budget-m14.json`. Produktive Durchsatzwerte
werden nicht erfunden; sie entstehen erst im Canary.

## Getrennte produktive Freigaben

M14.8 besitzt vier Stopppunkte, die jeweils eine ausdrueckliche Freigabe
benoetigen:

1. signiertes Kandidatenimage deployen und Scanner-Smoke ausfuehren,
2. begrenzten, benannten Mailordner als Canary lokal indizieren,
3. nach Auswertung und Dimensionierung den resumierbaren Vollbackfill starten,
4. erst bei vollständiger autoritativer Generation den inkrementellen
   `mail-index`-Job aktivieren.

Vor jedem produktiven Schritt gelten Release-/Digestprüfung, verifiziertes
Backup, freier Platz, genau ein Writer und die in
`docs/MAIL_SEARCH_M11_ACCEPTANCE_AND_ROLLOUT.md` dokumentierten Such- und
Rollbackregeln. Weder Image- noch Indexrollback verändern externe Mail.

## Noch offen

- abschliessender produktiver Capacity-Nachweis fuer den gesamten
  suchberechtigten Bestand; der erste Lauf erreichte 7.876 Nachrichten,
  985.296.943 Byte und 173 Seiten innerhalb des 3.600-Sekunden-Budgets,
- sechs einzeln freizugebende, frisch nachzupruefende Quarantaeneaktionen; der
  Fundstellenbericht bleibt inhaltsfrei und ist keine Sammelfreigabe,
- danach ein separat freizugebender `--restart`-Vollbackfill mit explizit
  ausgewiesenem Ausschluss von `Agent/Virusverdacht`,
- vollständige Indexgeneration und aktuelle Locatorabdeckung,
- positive Absender-/Body-/Zeitraumsuche und kontrollierter Negativfall,
- inkrementelle New-Mail-/Move-/No-op-Kosten,
- sieben Tage Beobachtung von Signaturreload, Ressourcen und Jobgesundheit.

Solange diese Punkte fehlen, ist M14.8 ausdrücklich **nicht abgenommen**.

Der bereits ausgefuehrte Canary war mit 68 Nachrichten, vier Seiten und
14.203.451 Byte vollständig. Der Vollbackfill veraenderte weder IMAP noch
Providerflags, blieb wegen sechs `infected`-Fundstellen und des Laufzeitlimits
aber korrekt unvollständig. Der Indexjob ist weiterhin aus. Produktive
Quarantaene, Neuaufbau und Jobstart sind nicht durch diese Dokumentation
genehmigt.

## Produktiver Zwischenstand 2026-09-12

Nach den sechs einzeln freigegebenen Quarantaeneaktionen und dem ausdrücklich
freigegebenen Neuaufbau/Fortsetzen wurde eine vollständige, autoritative
Generation veröffentlicht und vom normalen Sync importiert. Der anschließende
Reconcile der Generation
`4262cabf16f8639853d887aa54be4e790c3f3204d62778dae921bc56f5d18d37`
verarbeitete 8.860 aktuelle Nachrichten, davon 73 neu und 8.787 unverändert;
eine entfernte Nachricht wurde erkannt. Es gab keine Blockade, keinen
Parserfehler und keinen IMAP-Schreibzugriff. Die Coverage umfasst alle 21
suchberechtigten Partitionen; ausschließlich `Agent/Virusverdacht` bleibt als
`malware-quarantine-not-searchable` ausgeschlossen.

Die Abnahme deckte danach zwei Quellfehler auf: Eine historische Monolith-
Dublikatzeile ohne v2-Locator konnte einen echten Treffer vergiften, und der
registrierte Shadowbefehl wurde an die ältere externe Mail-CLI weitergeleitet.
Die Korrektur begrenzt Suchtreffer auf die neueste kanonische v2-Generation und
revalidiert Treffer mit dem nativen read-only UID-/UIDVALIDITY-Pfad. Vor einer
Aktivierung muss das daraus gebaute signierte Image separat installiert und die
bekannte Positivsuche einschließlich Locator sowie `mail index shadow` erneut
belegt werden. `jobs on mail-index` bleibt danach weiterhin ein eigener,
ausdrücklich freizugebender Schritt; dieser Zwischenstand aktiviert den Job
nicht.

Das signierte Image für Commit
`230b15d5df57043d19885bf72049715adfb9cd0f` wurde anschließend mit verifiziertem
Backup und erfolgreichem Produkt-Smoke installiert. Die freigegebene
Jobaktivierung setzte den persistenten Sollzustand auf `on`; der erste
Mail-Owner-Zyklus endete erfolgreich. Dabei wurde ein weiterer Abnahmefehler
sichtbar: Ein unveränderter vollständiger Snapshot aktualisierte nur Cursor und
Heartbeat, nicht aber `generated_at` der Projektion. Auto-Suche und Sync blieben
deshalb trotz gesundem Job bei `stale-generation`.

Der Folgestand erneuert bei einem belegten No-op ausschließlich das atomare
Root-Manifest. Die kryptografische Root-Generation und alle immutable
Partitionen bleiben gleich; Raw-Fetch, Parser, OCR, ClamAV, FTS und Modellarbeit
bleiben null. Außerdem quittiert `jobs on` die kurze asynchrone Phase bis zum
ersten Heartbeat als `starting`, ohne einen falschen Betriebsalarm zu erzeugen.
Dieser Folgestand benötigt vor der endgültigen produktiven Indexabnahme erneut
ein signiertes Deployment. Bis danach ein frischer Rootnachweis importiert und
die bekannte Positivsuche ohne Fallback wiederholt wurde, bleibt M14.8
ausdrücklich nicht produktiv abgenommen.

Die Nachkontrolle zeigte zusätzlich, dass eine lange Scheduler-Wartezeit des
gemeinsamen Mail-Owners den nur phasenweise geschriebenen Index-Heartbeat
überaltern ließ. Der Status verwendet deshalb bei `queued`/`waiting` den
frischen Heartbeat dieses Owners als reinen Liveness-Nachweis, behält aber das
letzte Reconcile-Ergebnis getrennt bei. Beide Heartbeats alt bleibt weiterhin
ein echter Fehler.
