# M9-Rolloutvertrag: Mail-Qualitaet und Review-Triage

Stand: 2026-08-14. Dieses Dokument beschreibt eine spaetere, ausdruecklich
freizugebende Produktivaktivierung. Die Entwicklungsabnahme selbst veraendert
weder `/srv/openclaw` noch Jobs, Konfiguration oder Postfach.

## Sicherheitsgrenzen

- Zuerst nur neue Mails beobachten. Der vorhandene Bestand in `Agent/Pruefen`
  wird weder automatisch analysiert noch verschoben.
- Ordneranlage, Deployment und Jobstart sind getrennte Freigaben. Ein erfolgreiches
  Image oder `mail folders plan` erteilt keine davon.
- Einzelkorrekturen brauchen unveraendert Ordner, aktuelle Mailbox-ID, erwarteten
  Betreff, eines der Urteile `relevant`, `routine` oder `spam` und `--yes`.
- Kein Rolloutschritt loescht Mail, fuehrt `EXPUNGE` aus, sendet eine Nachricht,
  erweitert Rechte oder waehlt automatisch einen Kalender aus.
- Ein lokales Releasebackup stellt bereits erfolgte IMAP-/CalDAV-Aenderungen nicht
  zurueck. Fuer deren vollstaendige Ruecknahme muessen vor dem Rollout verifizierte
  externe Backup-/Restore-Hooks konfiguriert und getestet sein.

## 1. Technische Freigabe vor einem Produktivauftrag

Der zu testende Commit muss gepusht sein. Ein `test/**`-Branch laesst den
Containerworkflow alle Rollen bauen, scannen, attestieren und signieren. Erst ein
vollstaendig gruener Workflow darf ueber den normalen Deploymentpfad aktiviert
werden. Lokal beziehungsweise in CI muessen fuer exakt diesen Commit gruen sein:

```bash
./scripts/assistant.sh version --verify
./scripts/check-repo.sh
./scripts/check-wheel.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
sg docker -c './scripts/check-m8-integration.sh'
```

Die Imagefreigabe umfasst zusaetzlich Rollen-Smokes, Rootfs-Artefaktscan, SBOM,
Provenance, kritischen CVE-/Secret-Scan, M3-State-Isolation und M4-Hardening. Lokale
Testimages ersetzen keine signierten Registry-Digests.

## 2. Read-only Vorhermessung

Auf dem Produktivhost werden die registrierten Befehle in der kurzlebigen
`agent-cli`-Rolle ausgefuehrt. Die folgende Hilfsfunktion ist nur ein Launcher;
die Befehle dahinter bleiben der autoritative Toolvertrag:

```bash
cd /srv/openclaw/deployment
run_openclaw() {
  docker compose --env-file .env --profile tools run --rm --no-deps agent-cli \
    /opt/openclaw-agent/scripts/assistant.sh "$@"
}
run_openclaw version --verify
run_openclaw jobs status --target all --deep
run_openclaw mail status
run_openclaw mail review status --days 7
run_openclaw mail learning evaluate --limit 5000
run_openclaw performance mail --limit 20
run_openclaw mail folders plan
```

Erfasst werden nur Aggregate und technische Evidenz. Rohe Betreffe, Adressen,
Bodies, Anhaenge oder produktive Statusausgaben werden nicht als CI-Artefakt oder
Git-Datei gespeichert. Der dokumentierte M9-Ausgangsstand ist 41 von 269
Review-/Unsicherheitsfaellen (15 Prozent), 97,67 Prozent Lernpraezision, 12,34
Prozent Abdeckung, zwei verpasste relevante Mails und null
Spam-Weiterleitungsrisiken in der damaligen Walk-forward-Stichprobe. Eine spaetere
Messung ist nur mit demselben Zeitfenster und derselben Auswertungssemantik
vergleichbar.

## 3. Deployment und getrennte Ordnerfreigabe

Das signierte Image wird ausschliesslich mit dem normalen Deploymentskript und
drei unveraenderlichen Digests aktiviert. Der Ablauf prueft Attestierungen,
Single-Writer, SQLite-Integritaet und ein extrahierbares SHA-256-verifiziertes
lokales Backup, bevor ein Writer startet; bei einem fehlgeschlagenen Smoke wird
automatisch der vorherige lokale Stand restauriert.

Nach erfolgreichem Deployment wird `mail folders plan` erneut gelesen. Nur wenn
der Plan exakt die erwarteten konfigurierten Ordner nennt und Jan diese eine
Aktion ausdruecklich freigibt, darf ausgefuehrt werden:

```bash
run_openclaw mail folders apply --yes
```

Dieser Schritt darf fehlende konfigurierte Ordner anlegen, verschiebt aber keine
bestehende Mail. Ein fehlender Kalender wird separat mit `calendar discover`
read-only untersucht; Ressourcenauswahl oder Rechteaenderung gehoeren nicht zum
M9-Canary.

## 4. Canary fuer neue Mail

Der bestehende Job-Sollzustand wird nicht still geaendert. Ist der Mailjob bereits
aktiv, wird nur sein normaler naechster Lauf beobachtet. Ist er aus, braucht
`jobs on mail --no-run-now` eine eigene ausdrueckliche Startfreigabe. Danach werden
zunaechst nur neue Ergebnisse mit diesen read-only Befehlen beurteilt:

```bash
run_openclaw jobs status --target mail --deep
run_openclaw mail status
run_openclaw mail review status --days 1
run_openclaw mail learning evaluate --limit 5000
```

Abbruchkriterien sind ein zweiter Writer, ein Mailjob-Fehler, unvollstaendige
Suche, ein verschlechtertes `relevant_missed` oder `spam_forward_risk`, untypisierte
neue Revieweintraege, falsches relevantes Routing, eine stale/korrupte
Suchprojektion oder eine unerklaerte Zunahme von Fehlerordnerfaellen. Keine
Schwelle wird nachtraeglich passend gewaehlt; Befund, Stichprobe und Vergleich
werden gemeinsam berichtet.

Nach mindestens sieben vollstaendigen Tagen werden `mail review status --days 7`,
`mail learning evaluate --limit 5000` und `performance mail --limit 20` gegen die
Vorhermessung gestellt. Eine Verbesserung darf nur behauptet werden, wenn
Reviewanteil beziehungsweise Lernabdeckung zusammen mit Praezision,
`relevant_missed` und `spam_forward_risk` vorliegen.

## 5. Stopp und Rollback

Bei einem Abbruch wird der Mailjob nach ausdruecklicher Freigabe mit
`jobs off mail` gestoppt und der exakte Fehler gesichert. Danach wird nur ein vom
Deployment erzeugtes und verifiziertes Releasebackup ueber den dokumentierten
Rollbackpfad restauriert. Alle Writer muessen dabei gestoppt sein. Wenn Remote-
Aenderungen zurueckgenommen werden muessen, ist der zum Backup gehoerende externe
Restore-Hook erforderlich; ohne ihn bleibt der Remotezustand ausdruecklich
unsicher.

Neu angelegte IMAP-Ordner und bereits verschobene neue Nachrichten werden niemals
automatisch geloescht oder massenhaft zurueckverschoben. Ihre Triage ist ein
eigener, explizit freizugebender Auftrag. Der historische Reviewbestand bleibt
auch nach erfolgreichem Canary unangetastet.
