# ADR-0041: Einheitlicher Lauf- und Telemetrievertrag

Status: Accepted

Datum: 2026-09-20

## Kontext

Scheduler, Containerheartbeat, Mailtelemetrie und Monitoring beschrieben
denselben Lauf mit unterschiedlichen Identitaeten und Resultatwerten. Ein
Retry konnte den vorherigen Crash verdecken, verschachtelte Mail-/Indexmessungen
konnten einen Fehler doppelt zaehlen und ein erfolgreicher Nextcloud-No-op
wurde wegen eines alten fachlichen Aenderungszeitpunkts als veraltet bewertet.
Historische Alertursachen blieben ohne explizite Ablaufzeit sichtbar.

## Entscheidung

Alle Laufzeitpfade verwenden die in
`personal_assistant.run_contract` definierte geschlossene Resultatmenge und die
vier Identitaeten `job_id`, `run_id`, `attempt_id` und `parent_run_id`.
Scheduler-Retries bleiben Attempts desselben logischen Runs. Inflight-Daten sind
gegenwaertiger Zustand; nur ein belegter toter Owner erzeugt ein terminales
`interrupted`-Ergebnis. Cross-Run-Berichte aggregieren an der Wurzel.

Syncbeobachtungen trennen Pruefzeit, erfolgreiche Pruefung und belegten
Datenwechsel. No-op ist ein erfolgreicher Zustand, aber kein Datenwechsel.
Alerts besitzen eine stabile Ursache und eine erneuerbare Ablaufzeit.
Telemetrieidentitaeten bleiben technisch begrenzt und inhaltsfrei.

Historische Werte werden ausschliesslich beim Lesen kompatibel normalisiert.
Neue Schreiber duerfen keine offenen oder konkurrierenden Resultatvokabulare
verwenden.

## Konsequenzen

- Status, Doctor, Scheduler und Performance koennen einen Attempt eindeutig
  korrelieren.
- Crash und Retry bleiben beide sichtbar, ohne den logischen Lauf doppelt zu
  zaehlen.
- Ein erfolgreicher Nextcloud-No-op bleibt frisch, ohne einen Datenwechsel zu
  erfinden; ein echter Teilfehler bleibt sichtbar.
- Alte Alerts laufen kontrolliert aus und koennen keinen gesunden aktuellen
  Zustand dauerhaft ueberschreiben.
- Spaetere Scheduler- und Syncoptimierungen in M16.3 bauen auf diesem Vertrag
  auf und duerfen ihn nicht mit einem zweiten Ergebnisraum umgehen.
