# Repository-Audit R27.0.1

Ausgangsbasis ist R27.0. R27.0.1 uebernimmt die waehrend der produktiven
Container-Migration beobachteten Korrekturen in den Git-Quellstand, ohne
produktive Zugangsdaten, Zertifikate oder Laufzeitdaten einzuchecken.

## Uebernommene Korrekturen

- aktive absolute Workspace-Pfade werden bei der Migration in `openclaw.json`
  und den bekannten produktiven TOML-Dateien auf den Container-Workspace
  umgeschrieben
- Himalaya-`secret-tool`-Befehle werden kontrolliert in lokale Secret-Dateien
  migriert und in der Containerkonfiguration auf `/run/openclaw-secrets`
  umgestellt
- eine fehlende `[nextcloud]`-Sektion wird nur bei vollstaendigen
  Nextcloud-Zugangsdaten hinzugefuegt; eine bestehende ausdrueckliche
  Konfiguration wird nicht ueberschrieben
- lokale oeffentliche CA-Zertifikate aus `/srv/openclaw/config/ca/*.crt` werden
  beim Containerstart mit dem System-Truststore kombiniert
- der ClamAV-Updater prueft seine Signaturdatenbanken statt des Gateway-Ports
- `calendar create` wird ohne den ungueltigen Schalter `--yes` exponiert
- `refresh-deployment.sh` aktualisiert Host-Compose und Deploymentskripte aus
  Git, erhaelt aber `.env` und aktive lokale Hooks
- die GHCR-Standardreferenz stimmt mit dem produktiven Repository ueberein

## Sicherheitsgrenzen

Nicht im Repository enthalten sind produktive Passwoerter, App-Tokens, private
Schluessel, CA-Private-Keys, persoenliche Konfigurationen, Datenbanken, Mails,
Rechnungen, Logs, Sitzungen oder Backups. Die Migrationshilfe fuehrt vorhandene
lokale Secret-Befehle nur auf dem Zielhost aus und schreibt die Ergebnisse mit
Dateimodus `0600` ausserhalb des Repositorys.

Das lokale Release-Backup bleibt vor einem schreibenden Produkttest verpflichtend.
Externe Backup-Hooks sind in dieser Installation optional, weil der Agent einen
eigenen eingeschraenkten Nextcloud-Benutzer verwendet und kritische Daten separat
gesichert werden. Ohne externe Hooks kann der automatische Rollback bereits
erfolgreiche Remote-Aenderungen nicht rueckgaengig machen.

## Verifikation

- neue Migrationstests pruefen Pfadumschreibung, Secret-Uebernahme,
  Nextcloud-Aktivierung und Idempotenz
- neue Regressionstests pruefen den ClamAV-Healthcheck, die CA-Bundle-Logik und
  den `calendar create`-Befehl ohne `--yes`
- die gezielten R27.0.1-Tests sowie alle am Ende des vollstaendigen Laufs noch
  offenen Registry- und Storage-Migrationstests bestanden
- Shell-Syntax und Python-Kompilierung der geaenderten Dateien wurden geprueft
- `SOURCE_MANIFEST.sha256` wurde aus den verfolgten Quelldateien neu erzeugt

Der vollstaendige Repository-Testlauf erreichte in der Erstellungsumgebung das
Zeitlimit erst in den letzten Testmodulen; bis dahin trat kein Fehler auf. Die
verbleibenden vier Tests wurden anschliessend separat erfolgreich ausgefuehrt.
GitHub Actions ist die abschliessende verbindliche CI-Pruefung vor dem Tag.
