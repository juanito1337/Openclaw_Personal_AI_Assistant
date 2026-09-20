# Einheitlicher Lauf- und Telemetrievertrag (M16.2)

Stand: 2026-09-20

Dieser Vertrag beschreibt, wie Scheduler, Containerworker, Mailtelemetrie,
Jobstatus und Monitoring denselben technischen Lauf identifizieren und
klassifizieren. Er veraendert weder Job-Sollzustaende noch produktive
Konfigurationen.

## Identitaet

Jeder geplante Lauf besitzt vier inhaltsarme Identitaeten:

| Feld | Bedeutung |
| --- | --- |
| `job_id` | feste allowlistete Jobklasse, zum Beispiel `mail` oder `sync` |
| `run_id` | stabile Identitaet des logischen Schedulerlaufs |
| `attempt_id` | eindeutige Identitaet genau eines Ausfuehrungsversuchs |
| `parent_run_id` | optionale Wurzel eines verschachtelten Fachlaufs |

Ein Retry behaelt `run_id` und erhaelt eine neue `attempt_id`. Ein
verschachtelter Mail-/Indexlauf verweist mit `parent_run_id` auf den
uebergeordneten Lauf. Aggregierte Performanceberichte zaehlen die Wurzel genau
einmal und verwenden, falls vorhanden, deren belegtes Ergebnis. Die einzelnen
Attempts bleiben fuer die Diagnose sichtbar.

Identitaeten sind auf 160 Zeichen sowie Buchstaben, Ziffern und die technischen
Trennzeichen `._:-` begrenzt. Betreff, Absender, Dokumentpfade, Befehlsargumente,
Modellantworten, Zugangsdaten und andere Nutzdaten sind keine Laufidentitaeten
und duerfen nicht in die Telemetrie gelangen.

## Geschlossene Resultatklassen

Neue Schreiber verwenden ausschliesslich:

| Resultat | Geschlossenes Kriterium |
| --- | --- |
| `completed` | Lauf ist terminal, ohne fachlichen Fehler abgeschlossen |
| `degraded` | Lauf ist terminal, besitzt belegten Teilerfolg und mindestens einen sichtbaren Teilfehler |
| `interrupted` | gestarteter Attempt endete durch Prozessverlust, Signal oder verlorene Lease |
| `skipped-not-due` | Lauf wurde korrekt bewertet, war aber nach seinem Intervall noch nicht faellig |
| `in-progress` | aktueller Attempt besitzt eine lebende Lease beziehungsweise einen lebenden Owner |
| `blocked` | Policy, Sollzustand, Abhaengigkeit oder Freigabe verhinderte den Start kontrolliert |
| `failed` | ausgefuehrter terminaler Lauf scheiterte ohne belastbaren fachlichen Teilerfolg |

`ok`, `success`, `error`, `running`, `deferred` und `cancelled` werden nur beim
Lesen historischer Daten eindeutig auf den neuen Vertrag abgebildet. Neue
Schreiber duerfen diese Werte nicht erzeugen. Unbekannte Werte werden nicht als
Erfolg interpretiert.

## Checkpoints, Retry und Crash

- Ein Inflight-Checkpoint traegt `in-progress` und ist kein historischer Lauf.
- Solange PID, Boot-ID und Prozessstart uebereinstimmen, darf ein zweiter
  Recorder den Checkpoint weder ueberschreiben noch als Fehler werten.
- Ist der belegte Owner nicht mehr lebendig, wird genau ein terminaler
  `interrupted`-Attempt geschrieben.
- Eine abgelaufene Scheduler-Lease beendet den alten Attempt als
  `interrupted`. Die erneute Einreihung erzeugt eine neue `attempt_id` unter
  derselben `run_id`.
- Ein Parent- und dessen Child-Telemetrie duerfen denselben fachlichen Fehler im
  Gesamtbericht nicht doppelt zaehlen.

## Nextcloud-Frische

Der Syncstatus trennt drei Zeitpunkte:

- `checked_at`: letzter abgeschlossener Pruef- oder Syncversuch,
- `last_successful_check_at`: letzter fachlich erfolgreicher Versuch,
- `last_data_change_at`: letzter belegter lokaler Datenwechsel.

Ein erfolgreicher No-op aktualisiert die erfolgreiche Pruefung, setzt
`data_changed=false` und behaelt `last_data_change_at` unveraendert. Wenn ein
Connector noch keinen belastbaren Aenderungsnachweis liefern kann, bleibt
`data_changed` unbekannt; es wird kein Aenderungszeitpunkt erfunden. Ein
spaeterer Teilfehler bleibt `degraded`, auch wenn ein oberflaechlicher DAV-Check
erfolgreich ist.

## Alerts

Die stabile Ursache ist `job:code`. Wiederholungen behalten `first_seen`,
aktualisieren `last_seen` und erhalten eine Ablaufzeit von 24 Stunden. Eine
belegte gesunde Beobachtung loest den Alert mit `healthy-observation` auf. Wird
eine Ursache innerhalb ihrer Ablaufzeit nicht erneut beobachtet, endet sie mit
`expired-without-refresh`; sie bleibt nicht als aktueller Fehler stehen. Neue,
aktive und geloeste Alerts werden getrennt ausgegeben.

## Reproduzierbare Abnahme

Die synthetischen Regressionen enthalten keine produktiven Daten und laufen mit:

```bash
.venv/bin/python -m pytest -q tests/test_runtime_telemetry_m162.py
./scripts/check-repo.sh
```

Sie pruefen den geschlossenen Ergebnisraum, Retry, Leaseverlust,
Crash/Wiederaufnahme, Parent-/Child-Deduplizierung, gesunden Nextcloud-No-op,
sichtbaren Teilfehler und kontrollierten Alertablauf.
