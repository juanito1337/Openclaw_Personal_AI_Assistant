# Standard-Betriebsprofil

Das Releaseprofil `standard` ist der normale Betriebsmodus und wird bei jedem
Prozessstart angewendet. Ein Agent muss deshalb nach Installation, Update oder
Containerneustart nicht erneut fuer Kalender-, Aufgaben-, Kontakt-, Mail- oder
Workspace-Funktionen technisch freigeschaltet werden.

Die aktive Instanzdatei waehlt weiterhin Konten und Ressourcen aus. Das
Standardprofil macht nur fuer bereits aktivierte und eindeutig ausgewaehlte
Ressourcen die vollstaendige nicht-destruktive Werkzeugoberflaeche wirksam. Es
ueberstimmt dabei alte, vor dieser Entscheidung gespeicherte einzelne
`allow_* = false`-Schalter bei jedem Laden der Konfiguration. Die Instanzdatei
wird dazu nicht heimlich umgeschrieben.

## Direkt verfuegbarer Umfang

Soweit die jeweilige Ressource bereits aktiviert und eindeutig ausgewaehlt ist,
stehen nach jedem Start direkt bereit:

- einzelnes Lesen und kontrolliertes Verschieben von Mail;
- create-only Schreiben, Hochladen, Ordneranlegen und no-overwrite Verschieben im
  begrenzten Nextcloud-Workspace;
- Lesen, Anlegen und ETag-geschuetztes Aktualisieren bestehender Kalendertermine;
- Lesen, Anlegen und ETag-geschuetztes Aktualisieren beziehungsweise Abschliessen
  bestehender Aufgaben;
- Lesen, create-only Anlegen und ETag-geschuetztes Aktualisieren von Kontakten;
- der bereits konfigurierte, agentenverwaltete Bestellkarten-Workflow.

Nicht konfigurierte oder bewusst deaktivierte Ressourcen werden nicht
automatisch ausgewaehlt oder eingeschaltet. Das Profil erzeugt keine
Zugangsdaten, aendert keine Nextcloud-ACL und registriert keine vom Server nicht
bestaetigten Rechte. Fehlt eine ausgewaehlte Ressource oder ein benoetigtes
Registry-/Serverrecht, meldet der jeweilige Status das als Fehler; der Agent darf
den Funktionsumfang dann nicht vortaeuschen.

`./scripts/assistant.sh capabilities` nennt das wirksame Profil unter
`operations_profile`. Kalender-, Aufgaben- und Kontaktstatus geben denselben Wert
aus. Bei normalem Betrieb gilt:

```json
{
  "operations_profile": {
    "name": "standard",
    "automatic_at_process_start": true
  }
}
```

## Weiterhin geschuetzte Aktionen

Das Standardprofil ist keine pauschale Handlungsvollmacht. Unveraendert gesperrt
oder gesondert freigabepflichtig bleiben:

- Loeschen, Ueberschreiben, Teilen, Massenbearbeitung und
  ressourcenuebergreifendes Verschieben;
- Aenderungen an Credentials, Ressourcenwahl, erlaubten Wurzeln oder Policies;
- serverseitige ACL- oder Freigabeaenderungen;
- Start, Neustart oder Abschalten von Jobs;
- Mailversand ohne unveraendert praesentierten Entwurf und ausdrueckliche
  Sendefreigabe;
- eine konkrete Aenderung eines bestehenden Kontakts, Termins oder einer Aufgabe
  ohne eindeutigen Nutzerauftrag, UID-/ID-Auswahl, ETag und Erwartungspruefung.

Technische Verfuegbarkeit und fachliche Autorisierung bleiben damit getrennt:
Der Agent kennt und erreicht seine normalen Werkzeuge sofort, darf aber eine
konkrete externe Aenderung erst innerhalb ihres typisierten Approval-Vertrags
ausfuehren.

## Kompatibilitaets- und Reparaturbefehl

Der bestehende Befehl bleibt fuer eine absichtlich auf `restricted` gesetzte oder
unvollstaendig migrierte Altinstallation erhalten:

```bash
./scripts/assistant.sh setup standard-operations --yes
```

Er prueft die exakt ausgewaehlten Ressourcen read-only ueber WebDAV, CalDAV oder
CardDAV. Nur ein vom Server bestaetigtes normales Recht darf lokal in die Resource
Registry aufgenommen werden. Ressourcenwahl, Nextcloud-ACLs und externe Daten
bleiben unveraendert. Eine fehlende, mehrdeutige oder unzureichend berechtigte
Ressource bricht den Vorgang atomar ab.

Im Docker-Stack laeuft diese administrative Kompatibilitaetsoperation nur ueber
die kurzlebige `agent-cli`-Rolle. Sie ist fuer eine gesunde Standardinstallation
nicht Teil des normalen Starts:

```bash
sg docker -c 'cd /srv/openclaw/deployment && \
docker compose --env-file .env --profile tools run --rm --no-deps agent-cli \
/opt/openclaw-agent/scripts/assistant.sh setup standard-operations --yes'
```

Ein Operator kann eine Instanz fuer Diagnose oder Notbetrieb explizit auf
`operations.profile = "restricted"` setzen. Dann gelten die einzelnen
`allow_*`-Schalter wieder unveraendert. Unbekannte Profilwerte brechen das Laden
fail-closed ab.

## Containergrenzen

Der Gateway mountet seine Administrationskonfiguration weiterhin read-only.
Der automatische Standardmodus benoetigt dort keinen Schreibzugriff, weil er die
effektive Werkzeugansicht beim Laden erzeugt. Rollenbezogene Secret-Mounts,
Netzwerke, Datenowner und Single-Writer-Grenzen werden dadurch nicht erweitert.

Ein direkt mit `docker exec` gestarteter `assistant.sh`-Diagnosebefehl laedt nur
die fuer seine Rolle bereits gemounteten Env-Dateien mit dem strikten Datenparser.
Ohne passenden Rollenmount entstehen weder Zugangsdaten noch neue Rechte.
