# Standard-Betriebsprofil

Das Profil `standard-operations` aktiviert nach einer einzigen ausdruecklichen
Betreiberfreigabe alle normalen, nicht-destruktiven Funktionen der bereits
eindeutig konfigurierten Ressourcen. Danach muss nicht jedes Kalender-, Aufgaben-
oder Kontaktwerkzeug noch einmal separat technisch freigeschaltet werden.

```bash
./scripts/assistant.sh setup standard-operations --yes
```

Im Docker-Stack wird der Befehl ausschliesslich ueber die kurzlebige
`agent-cli`-Rolle ausgefuehrt. Der Gateway bleibt unveraendert und mountet seine
Konfiguration weiterhin read-only.

## Aktivierter Umfang

Soweit die jeweilige Ressource bereits eindeutig ausgewaehlt und aktiviert ist,
aktiviert das Profil:

- einzelnes Lesen, Erstellen und kontrolliertes Verschieben von Mail;
- create-only Schreiben, Hochladen, Ordneranlegen und no-overwrite Verschieben im
  begrenzten Nextcloud-Workspace;
- Lesen, Anlegen und ETag-geschuetztes Aktualisieren bestehender Kalendertermine;
- Lesen, Anlegen und ETag-geschuetztes Aktualisieren beziehungsweise Abschliessen
  bestehender Aufgaben;
- Lesen, create-only Anlegen und ETag-geschuetztes Aktualisieren von Kontakten;
- den bereits konfigurierten, agentenverwalteten Bestellkarten-Workflow.

Nicht konfigurierte oder bewusst deaktivierte Ressourcen werden nicht automatisch
ausgewaehlt oder eingeschaltet. Bereits registrierte Rechte werden unveraendert
verwendet. Fehlt ein normales Nextcloud-Recht, prueft das Profil die exakt
ausgewaehlte Datei-, CalDAV- oder CardDAV-Ressource zuerst aktuell und read-only
ueber DAV. Nur ein vom Server bestaetigtes Recht wird anschliessend lokal in der
Resource Registry registriert. Die einmalige `--yes`-Freigabe autorisiert genau
diese begrenzte lokale Rechtserweiterung.

Die Ressourcenwahl, Nextcloud-ACLs und externe Daten werden dabei nicht
veraendert. Liefert die aktuelle Discovery keine eindeutige Ressource oder
bestaetigt sie ein benoetigtes Recht nicht, bricht das Profil vor Registry- und
Werkzeugaktivierung ab. Registry und Werkzeugkonfiguration werden gesichert; ein
Fehler beim zweiten Schritt stellt die vorherige Registry wieder her.

## Weiterhin geschuetzte Aktionen

Das Betriebsprofil ist keine pauschale Handlungsvollmacht. Unveraendert gesperrt
oder gesondert freigabepflichtig bleiben:

- Loeschen, Ueberschreiben, Teilen, Massenbearbeitung und ressourcenuebergreifendes
  Verschieben;
- Aenderungen an Credentials, Ressourcenwahl, erlaubten Wurzeln oder Policies;
- serverseitige ACL- oder Freigabeaenderungen;
- Start, Neustart oder Abschalten von Jobs;
- Mailversand ohne unveraendert praesentierten Entwurf und ausdrueckliche
  Sendefreigabe;
- eine konkrete Aenderung eines bestehenden Kontakts, Termins oder einer Aufgabe
  ohne eindeutigen Nutzerauftrag, UID-/ID-Auswahl, ETag und Erwartungspruefung.

Der einmalige Profilwechsel beseitigt somit technische Doppelfreigaben. Die
fachliche Freigabe fuer eine konkrete externe Aenderung bleibt bestehen und kann
durch den eindeutigen direkten Nutzerauftrag erteilt werden.

## Docker-Ausfuehrung

```bash
sg docker -c 'cd /srv/openclaw/deployment && \
docker compose --env-file .env --profile tools run --rm --no-deps agent-cli \
/opt/openclaw-agent/scripts/assistant.sh setup standard-operations --yes'
```

Anschliessend zeigen `tools list`, `capabilities`, `calendar status`, `tasks status`
und `contacts status` die tatsaechlich verfuegbaren Funktionen. Der Profilbefehl
ist idempotent: Ein erneuter Lauf aendert eine bereits passende Konfiguration
nicht.

Ein direkt mit `docker exec` gestarteter `assistant.sh`-Diagnosebefehl erbt die
vom PID-1-Entrypoint geladenen Variablen technisch nicht. Der Launcher laedt
deshalb in diesem Sonderfall dieselben bereits gemounteten, rollenbezogenen
Env-Dateien erneut mit dem strikten Datenparser. Er durchsucht keine Verzeichnisse,
wertet keinen Shell-Code aus und erweitert weder Secret-Mounts noch Rechte. Die
Gateway-Konfiguration bleibt dabei read-only; Profilaktivierung erfolgt weiterhin
nur ueber `agent-cli`.
