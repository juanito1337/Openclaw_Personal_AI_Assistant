# M8 Recovery-, Release- und Canary-Vertrag

Status: fuer Test, Releasevorbereitung und lokale Recovery-Abnahme verbindlich.
Ein produktives Deployment ist nicht Bestandteil von M8 und benoetigt einen
separaten ausdruecklichen Auftrag.

## Recovery-Modell

```text
signierte Kandidaten-Digests + kompatibles Layout
                    |
                    v
       alle aktuellen Writer stoppen
                    |
                    v
 externer Snapshot (falls konfiguriert) + verifiziertes lokales Backup
                    |
                    v
   Kandidat: Infrastruktur -> Smoke -> genau ein Writer -> restliche Worker
                    |
             Fehler | Erfolg
                    |    `----> beobachteter Canary -> Freigabe
                    v
   Kandidat stoppen -> externer Restore (falls vorhanden)
                    -> lokales Backup bytegenau wiederherstellen
                    -> vorherige drei Images / vorheriges Runtime-Modell starten
```

Das Releasebackup enthaelt `state`, `config` und `secrets`, die drei vorherigen
Image-Referenzen, Runtime-Typ und optional eine externe Snapshotreferenz. Pruefsumme,
Tar-Struktur, SQLite-Integritaet und ein Restore in ein temporaeres Verzeichnis
werden vor der Markierung `verified=true` geprueft.

Vor dem Writerstop muss der persistierte Runtime-Typ mit den beobachteten
systemd- beziehungsweise Docker-Writern uebereinstimmen. Ein Marker
`legacy-systemd` bei laufenden Docker-Writern oder aktive Legacy-Writer bei
`docker` ist ein Integritaetsfehler und bricht vor dem Backup ab. Fehler innerhalb
der Compose-Hilfsfunktion erben den Rollback-Trap; nach einem verifizierten Backup
darf deshalb auch ein fehlgeschlagenes `layout-init` keinen halben Kandidaten ohne
automatischen Rollback zuruecklassen.

`restore-local-state.sh` ist die einzige gemeinsame lokale Restore-Implementierung.
Sie verlangt `OPENCLAW_RESTORE_OFFLINE=YES`, vorhandene geschuetzte Zielwurzeln,
ein verifiziertes Backup und erfolgreiche SQLite-Pruefung. Sie ersetzt nur Inhalte
innerhalb der drei exakten Wurzeln und entfernt die Wurzeln selbst nicht.

Fehlt ein zu einer Snapshotreferenz gehoerender externer Restore-Hook, bricht
`rollback.sh` vor `compose down` ab. Scheitert ein vorhandener Hook erst bei der
Ausfuehrung, wird der verifizierte alte lokale Zustand trotzdem wiederhergestellt
und gestartet; der Befehl endet jedoch ungleich null und kennzeichnet den
Remote-Zustand als unklar. Ein Deployment mit fehlgeschlagenem automatischem
Rollback endet mit Exitcode 70 und ist nicht freigegeben.

## Gemessener lokaler Restore-Drill

Quelle ist [`recovery-baseline-m8.json`](recovery-baseline-m8.json), erzeugt mit:

```bash
.venv/bin/python scripts/m8-recovery-drill.py \
  --output docs/architecture/recovery-baseline-m8.json
```

Der Lauf vom 6. August 2026 verwendete ausschliesslich temporaere Fixture-Roots:

| Szenario | Backup | Restore | Archiv | Ergebnis |
| --- | ---: | ---: | ---: | --- |
| minimale direkte Upgrade-Version `3.4.0-r26.1` | 0,381 s | 0,299 s | 927 B | exakter State/Config/Secrets-Baum |
| aktueller Stand `3.4.0-r27.2.5` | 0,448 s | 0,315 s | 608 B | exakter State/Config/Secrets-Baum |
| fehlgeschlagenes Upgrade mit unlesbarem Layoutmarker | 0,350 s | 0,270 s | 609 B | Preflight unveraendert rot, danach exakter Restore |

Der gemessene lokale Fixture-RTO ist damit maximal **0,315 Sekunden**. Das ist kein
Produktions-SLA: Image-Pull, externe Snapshotwiederherstellung, grosse produktive
Datenmengen und Health-Konvergenz sind nicht enthalten. Vor einer produktiven
Freigabe muss ein separater Drill auf dem Zielhost diese Bestandteile messen.

Der beobachtete Fixture-RPO ist **0 akzeptierte Operationen**, weil nach dem
Snapshot nur absichtlich fehlerhafte Upgrade-Aenderungen erfolgten. Der echte lokale
RPO endet am letzten verifizierten Snapshot: jede danach bestaetigte lokale
Aenderung kann verloren gehen. Remote-RPO ist ohne externen Snapshot **nicht
begrenzt und nicht durch OpenClaw wiederherstellbar**.

## Hermetischer End-to-End- und Fehlerstack

`tests/integration/m8/compose.yaml` publiziert keine Hostports, nutzt ein internes
Netz, read-only Rootfs, keine Capabilities und ausschliesslich `.invalid`-Fixtures.
Er stellt IMAP, SMTP, WebDAV/CardDAV/CalDAV, Ollama, EODHD-aehnliche Marktdaten und
ein kontrolliertes ClamAV-Streamprotokoll bereit. CI und lokal verwenden:

```bash
sg docker -c './scripts/check-m8-integration.sh'
```

Der Lauf prueft echte Protokollinteraktionen, einen stale ETag/HTTP-412-Konflikt,
saubere und EICAR-Antivirus-Fixtures, Netztrennung und Wiederverbindung, SIGKILL und
Neustart des Fake-Service-Containers sowie einen exklusiven Mailwriter-Lock. Ein
zweiter Writer endet mit Exitcode 73; erst nach dem Kill des ersten Writers darf
der Nachfolger den Lock erhalten. Messwerte werden unter
`build/m8-integration.json` abgelegt.

Der lokale Abnahmelauf benoetigte **2,583 s** bis zum gesunden Stack, **2,219 s**
fuer das Protokollszenario und **2,558 s** vom SIGKILL bis zur erneut gesunden
Fake-Service-Instanz. Auch diese Werte beschreiben nur die kleine hermetische
Fixture und sind kein Produktions-SLA.

Weitere reale, temporaere Failure-Injections der Gesamtsuite:

| Fehler | Verhaltensnachweis |
| --- | --- |
| SQLite-Lock und konkurrierende Writer | `tests/test_state_layout_m3.py` |
| volles oder read-only Volume | Migrations-Preflight bleibt unveraendert |
| ungueltige Migration | Kompatibilitaetsgate rot, State-Hash unveraendert |
| fehlgeschlagener Produktsmoke | echter `deploy.sh`-Ablauf ruft automatischen Rollback; Rollbackfehler wird Exit 70 |
| verlorene Scheduler-Lease | abgelaufene Lease wird kontrolliert uebernommen; wiederholte Renewal-Fehler bleiben fail-closed |
| fehlender Restore-Hook | Abbruch vor dem ersten Containerstop |
| fehlschlagender Restore-Hook | alter lokaler Stand startet, Remotezustand bleibt sichtbar unklar |

## Releasecheckliste

Die Checkliste ist in dieser Reihenfolge abzuarbeiten und als Releaseevidenz zu
speichern:

1. Sauberer Review-Commit; keine unbeabsichtigten Worktree-Dateien.
2. `version --verify`, `git diff --check`, `check-repo.sh`, Wheel-Pruefung und beide
   Compose-Renderings gruen.
3. M8-Integrationsstack und Recovery-Drill gruen; keine produktiven Mounts,
   Netzwerke, Konten oder Secrets in deren Logs/Artefakten.
4. Drei Rollenimages aus exakt einem Commit bauen; Rootfs-Artefaktscan, SBOM,
   Provenance, kritischer CVE-/Secretscan und Rollen-Smokes gruen.
5. Alle drei unveraenderlichen Registry-Digests samt Release, Rolle, Commit,
   Signatur und Attestierungen verifizieren.
6. Zielhost: freien Platz, `/srv/openclaw`-Rechte, SQLite-Integritaet, externe
   Erreichbarkeit und Dockerzugriff read-only pruefen.
7. Genaues vorheriges Rollenset, Runtime-Typ und startbare Legacyquelle erfassen.
8. Alle Legacy-Writer inaktiv und Writer-Timer disabled nachweisen.
9. Fuer einen Write-Canary: externe Backup-/Restore-Hooks ausfuehrbar und ihr
   letzter Restore-Drill belegt; danach Writer stoppen und lokales Backup erzeugen.
10. Canary nach dem Verfahren unten. Bei jeder Abweichung keine Freigabe, sondern
    Recovery mit exakter Fehler- und Remotegrenzenmeldung.
11. Nach Erfolg Releaseidentitaet, Jobs, Health, Audit und Single-Writer-Zustand
    erneut pruefen. Produktive Aktivierung bleibt ein separater Auftrag.

## Single-Writer-Pilot und Canary

Es gibt keinen Parallel-Canary fuer Mailwrites. Der Pilot trennt Beobachtung und
Writeruebergabe zeitlich:

1. Kandidatenimages und Layoutkompatibilitaet pruefen, waehrend der alte Stand
   unveraendert laeuft. Noch keinen Kandidatencontainer starten, der schreiben kann.
2. Alte Business-Writer stoppen und ihre Inaktivitaet sowie deaktivierte
   Legacy-Timer belegen. Erst danach externen und lokalen Restorepunkt erzeugen.
3. Nur Kandidaten-Proxy und -Gateway starten. Read-only CLI-/Release-/Capability-
   und Mail-Dry-run-Smoke ausfuehren; keine produktive Sendung oder Remote-Aenderung.
4. Falls ein Write-Smoke ausdruecklich freigegeben und extern rueckrollbar ist,
   exakt einen Kandidaten-Mailwriter fuer den begrenzten Test starten. Nie den alten
   Writer parallel starten und nie den Service skalieren.
5. Nach erfolgreichem Smoke genau einen normalen Mailwriter sowie die uebrigen
   Worker starten; Legacy-Writer nochmals als inaktiv nachweisen.
6. Fuer das definierte Beobachtungsfenster Health, Job-Sollzustand, Scheduler-Lease,
   Mailwriter-Lock, Audit und Fehlerzaehler beobachten. Keine zweite Writergruppe
   als Vergleich aktivieren.
7. Bei Fehler Kandidaten stoppen, optionalen externen Snapshot und den verifizierten
   lokalen Stand wiederherstellen, vorheriges Rollenset starten und dessen
   Release/Health/Single-Writer-Zustand verifizieren. Ohne externen Snapshot Remote-
   Aenderungen einzeln benennen und nicht als zurueckgerollt ausgeben.

Der M8-Nachweis ist eine technische Freigabevorbereitung, keine Behauptung, dass
dieser Canary bereits auf `/srv/openclaw` oder produktiven Konten ausgefuehrt wurde.
