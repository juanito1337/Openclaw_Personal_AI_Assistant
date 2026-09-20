# ADR-0045: Risikobasierte Tests und reine Jobloop-Grenzen

Status: Accepted (2026-09-20)

## Kontext

M16.0 zeigte geringe direkte Abdeckung in Actions, Mailbridge, Container-Jobloop,
Mail-CLI und Nextcloud-Transport. Besonders der Jobloop mischte feste
Rollenprofile, reine Ergebnisentscheidung und Prozess-I/O in einer Funktion.

## Entscheidung

Wir sichern zuerst das bestehende Verhalten mit hermetischen Tests. Danach werden
nur die feste Profil-Allowlist und die reine Run-/Lease-Ergebnisentscheidung aus
dem Jobloop extrahiert. Prozess-, Scheduler-, Heartbeat- und Shutdown-I/O bleiben
im Adapter `docker/job_loop.py`.

Ein maschinenlesbarer Coverage-Vertrag bindet die Risikomodule an den vollständigen
M16.5-Vorherlauf. Kombinierte und Branch-Coverage müssen steigen;
Statement-Coverage darf nicht sinken. Der Collection-Floor steigt auf die reale
vollständige Collection. Lint- und Typbaselines bleiben nicht wachsend.

## Konsequenzen

- Leaseverlust und Exit-/Batchzustände sind ohne Prozesssimulation mutierbar
  gegenprüfbar.
- Ein fehlgeschlagener Schedulerabschluss kann nicht als gesunde Fortsetzung im
  finalen Heartbeat erscheinen.
- Jobprofile bleiben eine feste Allowlist und führen keine zweite Toolregistry ein.
- Live-Integrationen bleiben gesonderte Betriebsabnahmen; Fixtures beweisen keine
  Servererreichbarkeit.
- Weitere Modulteilungen benötigen erneut Charakterisierung, Schichtentest und
  messbare Coverage ohne Baselinewachstum.
