# Release 3.4.0-r29: belegte Agentenwerkzeuge, Mailindex und Betriebsstabilitaet

Stand: 2026-09-21

`3.4.0-r29` ist der kumulative Releasekandidat fuer M11 bis M16. Er fuehrt die
seit `3.4.0-r28` entwickelten Mailindex-, Werkzeug-, Antivirus-,
Aktionsabschluss- und Betriebsverbesserungen in einer gemeinsamen, verifizierten
Produktidentitaet zusammen. Die Versionsidentitaet im Quellstand ist noch keine
Main-Promotion, keine Imageveroeffentlichung und kein produktives Deployment.
Diese drei Schritte behalten getrennte Freigaben.

## Wichtigste Aenderungen

### Vollstaendige und nachvollziehbare Mailsuche

- Ein lokaler Vollkontoindex verbindet FTS, Thread-/Tagkontext und stabile
  Live-Locators mit einer sichtbaren Vollstaendigkeits- und Frischebewertung.
- Ein nativer read-only IMAP-Connector inventarisiert Ordner und Nachrichten
  seitenweise und verfolgt externe Moves, Copies und Deletes transaktional.
- Negative Suchaussagen sind nur bei autoritativer, vollstaendiger und frischer
  Coverage erlaubt. Ein begrenzter Serverfallback darf keine Abwesenheit
  vortaeuschen.
- Indexdaten autorisieren niemals eine Mailaktion; Lesen, Antworten und
  Verschieben werden am Live-Server mit stabilem Locator erneut validiert.

### Native typisierte Agentenwerkzeuge

- Alle unterstuetzten Fachdomaenen werden ueber generierte, strukturierte
  `personal_assistant_*`-Werkzeuge exponiert.
- Der Werkzeugvertrag bindet Operation, Argumentschema, Modus, Approval,
  Evidenz, Fehlerkategorien und erlaubte Aussagen.
- Dotted Tool-IDs sind Selektoren und keine Shellbefehle. Rohe Himalaya-,
  SQLite-, Dateisystem- oder Webfallbacks ersetzen kein registriertes Tool.
- Ein Antwortguard verhindert unbelegte Erfolgsmeldungen, definitive
  Negativaussagen aus Teilresultaten und endlose Wiederholungen desselben
  fehlgeschlagenen Toolaufrufs.

### Residenter, fail-closed Virenscanner

- `clamd` laeuft als eigene, netzlose Rolle mit privatem Unix-Socket; nur der
  getrennte Updater besitzt Egress und Signaturschreibrechte.
- Raw-Mail, physische Anlagen und kontrollierte Uploads werden vor Parsing oder
  externem Write vollstaendig gescannt.
- Scanner- und Signaturidentitaet sind Bestandteil des Cachevertrags.
  Veraltete Signaturen, Timeouts und Scannerfehler blockieren fail-closed.
- Infizierte Inhalte werden quarantainiert und nie automatisch geloescht.

### Belegter Aktionsabschluss

- Explizite Schreibauftraege erzeugen eine aktuelle, turngebundene
  Aktionsverpflichtung statt eines unverbindlichen Zukunftsversprechens.
- Mailentwurf und Versand bleiben zwei getrennte Aktionen. Der vollstaendige
  unveraenderte Entwurf braucht vor dem Senden eine eigene Einmalfreigabe.
- Kalender-, Aufgaben-, Kontakt- und Nextcloud-Aenderungen verwenden exakte
  IDs, ETags, Erwartungswerte und einen Remote-Read-back.
- Timeout, Netzfehler, stale Approval, Replay und Teilerfolg bleiben sichtbare
  Terminalzustaende; unsichere Writes werden nicht automatisch wiederholt.

### Betriebsstabilitaet und Leistung

- Scheduler, Worker, Monitoring und Werkzeugaufrufe verwenden einen gemeinsamen
  datenschutzarmen Run-/Attempt-/Parent- und Resultatvertrag.
- Inkrementeller Nextcloud-Sync stoppt bei echten No-ops vor Download, Parsing
  und Projektionswrite und verarbeitet ein Delta als genau einen begrenzten
  Download/Write.
- Persistierte Ressourcen werden ueber stabile ID, kanonische URL,
  Komponententyp, Rechte und Discovery-Fingerprint validiert. Nulltreffer und
  Mehrdeutigkeit bleiben fail-closed.
- Rollenlokale Cgroup-, OOM-, Exit- und Latenzevidenz trennt Containerlast,
  Childprozesse und Fremdlast. Bestehende Runtimegrenzen wurden nicht heimlich
  erweitert.
- Rechnungs- und Portfolio-Doctor unterscheiden Konfiguration, Providergrenze,
  Mapping, Frische und bewussten Job-Off-Zustand, ohne Ersatzdaten zu erfinden.

## Architekturentscheidungen

Die synthetische Suchevaluation zeigt fuer den lokalen Embedding-/Hybridpfad
keinen eindeutig besseren Qualitaetsvertrag: hoeherer Recall ging mit sinkender
Precision und zusaetzlicher Rechenlast einher. Semantische Suche bleibt deshalb
deaktiviert. Lexikalische Suche, Thread-/Tagkontext und Live-Revalidierung bleiben
der produktive Pfad.

Ein authentisierter AF_UNIX-Executor-Prototyp belegt Schema-, Tool-, Approval-,
Replay-, Deadline-, Backpressure- und Antwortgroessengrenzen. Er wird in r29
nicht produktiv aktiviert; Gateway, Rollenmounts und Secrets bleiben unveraendert.

## Qualitaets- und Leistungsbaseline

Der lokale M16.10-Auditpfad sammelte 1.221 pytest-Items und fuehrte zusammen mit
111 Subtests 1.332 JUnit-Faelle ohne Fehler oder Skips aus. Die kombinierte
Coverage lag bei 70,272 %, die reine Branch-Coverage bei 57,657 %. Ruff, mypy,
ShellCheck, Hadolint, Compose-Render, Python-Kompilierung, Dokumentpruefung,
Komponenteninventar, Quellmanifest und `git diff --check` waren gruen.

Der 100-Objekt-Sync ergab lokal 567,656 ms Full-p50, 5,435 ms No-op-p50,
9,276 ms Einzel-Delta-p50 und 20,482 ms fuer zeitkritische Mailarbeit zwischen
zwei Background-Batches. Der synthetische Mailbenchmark hielt Recall@10 bei
0,65 und MRR bei 0,6667; Cold-, Warm-p50- und Warm-p95-Latenz verbesserten sich.
Diese Werte sind reproduzierbare lokale Baselines, keine Zusage fuer fremde IMAP-
oder Modellserver.

## Artefakte und Lieferkette

Das Wheel wird aus einem sauberen Quellsnapshot gebaut, in einer neuen virtuellen
Umgebung installiert und dort mit CLI-, Release- und Gesamttests geprueft. Drei
Rollenimages (`runtime`, `proxy`, `maintenance`) werden aus demselben Commit und
derselben Releaseidentitaet gebaut. Rollen-Smokes, Artefakthygiene, Secret-Scan,
CVE-Policy, SPDX-SBOM, Provenance und byteidentische No-cache-OCI-Exporte sind
Bestandteil des Kandidatenpfads.

Die Policy blockiert jeden Critical-Fund ohne pauschale Ausnahme. High-Funde
bleiben sichtbar und gehoeren in die laufende Basisimagepflege. Lokale Image-IDs
oder lokale Provenance ersetzen keine Registry-Digests, Cosign-Signaturen oder
Registry-Attestierungen.

## Upgrade- und Sicherheitsgrenzen

- Persistenter Zustand, Instanzkonfiguration und Secrets bleiben ausserhalb des
  Images unter `/srv/openclaw`.
- Ein Deployment stoppt alte Writer, erstellt und verifiziert ein lokales Backup
  und prueft die signierten Rollenimages vor jeder Laufzeitaenderung.
- Ein lokaler Rollback ersetzt keinen Restore bereits erfolgreicher externer
  IMAP-, WebDAV-, CardDAV- oder CalDAV-Aenderungen.
- Produktive Jobaktivierung, Rechteausweitung und Schreib-Canaries bleiben
  ausdrueckliche Betriebsentscheidungen.

## Promotionsvertrag

Vor einer Main-Promotion muessen derselbe unveraenderliche Kandidatencommit,
`RELEASE.json`, ein kryptografisch verifizierter Git-Tag, drei signierte
Registry-Digests, SBOM/Provenance und ein signaturverifizierter r28-Rollbacksatz
zusammenpassen. Der getrackte Vertrag bleibt eine Draft-Vorlage; die
commitgebundene Ready-Evidenz wird nach dem Commit als separates Release-Artefakt
erzeugt, damit keine unmoegliche Selbstreferenz auf den eigenen Git-SHA entsteht.

Die drei r28-Registryrollen wurden read-only mit Cosign, SLSA und SPDX
verifiziert. Der r28-Git-Tag ist annotiert, aber nicht kryptografisch signiert;
produktive Backup-/Restoreevidenz wurde in dieser Entwicklungsarbeit nicht
geoeffnet. Diese verbleibende Rollbackgrenze ist in
[`m16-rollback-r28.json`](architecture/m16-rollback-r28.json) maschinenlesbar.

Erst danach folgen separat:

1. Freigabe und Ausfuehrung der Imageveroeffentlichung,
2. Freigabe und fast-forward-only Main-Promotion,
3. Freigabe fuer Backup, produktives Deployment und Canaries.

Die jeweils aktuelle technische Abnahme und verbleibende Blocker stehen in
[`M16_ACCEPTANCE.md`](M16_ACCEPTANCE.md).
