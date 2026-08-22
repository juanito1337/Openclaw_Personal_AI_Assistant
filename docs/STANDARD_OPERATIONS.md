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

Soweit die jeweilige Ressource bereits eindeutig ausgewaehlt, aktiviert und mit
den erforderlichen Rechten im Resource Registry belegt ist, aktiviert das Profil:

- einzelnes Lesen, Erstellen und kontrolliertes Verschieben von Mail;
- create-only Schreiben, Hochladen, Ordneranlegen und no-overwrite Verschieben im
  begrenzten Nextcloud-Workspace;
- Lesen, Anlegen und ETag-geschuetztes Aktualisieren bestehender Kalendertermine;
- Lesen, Anlegen und ETag-geschuetztes Aktualisieren beziehungsweise Abschliessen
  bestehender Aufgaben;
- Lesen, create-only Anlegen und ETag-geschuetztes Aktualisieren von Kontakten;
- den bereits konfigurierten, agentenverwalteten Bestellkarten-Workflow.

Nicht konfigurierte oder bewusst deaktivierte Ressourcen werden nicht automatisch
ausgewaehlt oder eingeschaltet. Fehlt einer bereits konfigurierten Ressource ein
registriertes Recht, bricht der Profilwechsel vor jeder Aenderung ab. Es werden
weder neue Rechte in die Resource Registry geschrieben noch externe Daten
veraendert. Die Konfigurationsdatei wird atomar ersetzt und vorher gesichert.

## Weiterhin geschuetzte Aktionen

Das Betriebsprofil ist keine pauschale Handlungsvollmacht. Unveraendert gesperrt
oder gesondert freigabepflichtig bleiben:

- Loeschen, Ueberschreiben, Teilen, Massenbearbeitung und ressourcenuebergreifendes
  Verschieben;
- Aenderungen an Credentials, Ressourcenwahl, erlaubten Wurzeln oder Policies;
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
