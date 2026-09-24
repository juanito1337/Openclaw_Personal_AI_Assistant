# Release 3.4.0-r29.0.2

Dieses Patch-Release enthaelt den speichersicheren Agent-CLI-Status aus
r29.0.1 und korrigiert die bei dessen produktiver Abnahme gefundene
Worker-Startbewertung. Die rollenbezogenen Runtime-Limits bleiben
unveraendert.

## Ursache und Korrektur

Container-Worker veroeffentlichen sofort einen frischen Heartbeat, warten aber
vor ihrem ersten Fachlauf absichtlich auf ihre konfigurierte Startverzoegerung.
In diesem Fenster stehen `state=starting|waiting|queued`, `result=unknown`, kein
Exitcode und kein Abschlusszeitpunkt. Der r29-Statuspfad kanonisierte `unknown`
faelschlich zu `failed`, sodass die Deployment-Abnahme vor dem ersten Lauf
automatisch zurueckrollte.

Dieser belegte Erststartzustand wird nun als `in-progress` ausgewiesen. Ein
Heartbeat mit Abschlusszeitpunkt, Exitcode oder echtem Fehlerresultat bleibt
unveraendert fail-closed.

## Verifikation und Installation

- Ein Regressionstest bildet einen frischen Sync-Worker in der initialen
  Wartezeit ab und erwartet einen erfolgreichen Status mit `in-progress`.
- Die bestehenden Tests sichern weiterhin ab, dass laufende neue Versuche alte
  Fehler nicht verriegeln und abgeschlossene Fehler sichtbar bleiben.
- Der 16-KiB-Logtail-Fix aus r29.0.1 bleibt enthalten; das Agent-CLI-Limit wird
  nicht erhoeht.
- Veroeffentlichung und Installation verwenden den signierten Tag `r29.0.2`
  sowie drei unveraenderliche, attestierte Rollenimage-Digests.

Die produktive Abnahme ist erst erfolgreich, wenn Version, Status, Tools,
Capabilities, Deep-Jobcheck, Antivirus und Agent-Tools aus dem installierten
Stack erfolgreich zurueckgelesen wurden.
