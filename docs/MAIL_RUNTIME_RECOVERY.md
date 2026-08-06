# Kontrollierte Mail-Laufzeit und Wiederaufnahme (R24)

Der Container-Mailworker startet einen begrenzten Drain-Lauf und ueberwacht ihn in
der allowlist-basierten Worker-Schleife. R24 nutzt intern standardmaessig 2400
Sekunden Gesamtbudget und 180 Sekunden Reserve.
Neue Modellarbeit wird nur begonnen, wenn fuer Queue, Modell und sauberes Beenden genug
Restzeit vorhanden ist.

Beim Erreichen der Reserve wird der aktuelle sichere Verarbeitungsschritt abgeschlossen,
der Fortschritt gespeichert und der Lauf mit `runtime-reserve` ohne Fehler beendet. Nicht
verarbeitete Mails bleiben unveraendert und werden vom naechsten Timerlauf aufgenommen.

Standardaufruf des Workers:

```bash
python3 -m mail_agent drain --max-runtime 2400 --shutdown-reserve 180
```

Der Worker sendet bei Lease- oder Laufzeitfehlern `SIGTERM` und wartet begrenzt auf
den kontrollierten Abschluss. Die historische systemd-Unit mit
`TimeoutStartSec=50min`, `TimeoutStopSec=2min` und `KillSignal=SIGTERM` liegt nur noch
im SHA-verifizierten Rollbackpaket unter `legacy/systemd/`; sie ist kein aktiver
Deploymentpfad.
