# Kontrollierte Mail-Laufzeit und Wiederaufnahme (R24)

Der Maildienst bleibt als `Type=oneshot` waehrend eines kompletten Laufs im Zustand
`activating/start`. Deshalb gilt `TimeoutStartSec=50min` als aeussere Notbremse.
R24 nutzt intern standardmaessig 2400 Sekunden Gesamtbudget und 180 Sekunden Reserve.
Neue Modellarbeit wird nur begonnen, wenn fuer Queue, Modell und sauberes Beenden genug
Restzeit vorhanden ist.

Beim Erreichen der Reserve wird der aktuelle sichere Verarbeitungsschritt abgeschlossen,
der Fortschritt gespeichert und der Lauf mit `runtime-reserve` ohne Fehler beendet. Nicht
verarbeitete Mails bleiben unveraendert und werden vom naechsten Timerlauf aufgenommen.

Standardaufruf der Unit:

```bash
python3 -m mail_agent drain --max-runtime 2400 --shutdown-reserve 180
```

Die systemd-Unit behaelt `TimeoutStartSec=50min`, erhaelt aber `TimeoutStopSec=2min` und
`KillSignal=SIGTERM`. Das aeussere Limit soll nur noch echte Defekte abfangen und nicht
den regulaeren Arbeitszyklus beenden.
