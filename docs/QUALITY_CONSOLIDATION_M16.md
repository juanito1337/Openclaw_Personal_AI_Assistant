# M16.6 – Risikobasierte Tests und Codekonsolidierung

Stand: 2026-09-20

M16.6 schließt die in M16.0 priorisierte direkte Testlücke der Actions-,
Mailbridge-, Jobloop-, Mail-CLI- und Nextcloud-Transportpfade. Das Paket ändert
keine Tool-ID, keine externen Rechte und keine Produktivkonfiguration.

## Verantwortungsmatrix

| Pfad | Unit-/Charakterisierungstest | Contracttest | Integration/E2E |
| --- | --- | --- | --- |
| ActionPlan, Idempotenz, Nachbedingung | `test_risk_paths_m166.py`, bestehende direkte Domain-Tests | Policy-, ETag- und Toolvertragstests | hermetische Nextcloud-Fakes; keine Live-Schreibvorgänge |
| Mail-Assistant-Bridge | `test_risk_paths_m166.py` mit kontrolliertem Payload-Staging | ActionPlan bleibt einzige Schreibgrenze | Mail-/Kalender-Architekturtests |
| Container-Jobloop | reine Profil- und Ergebniszustandstests sowie atomare Heartbeats | Run-/Schedulervertrag | bestehende Container- und Scheduler-Fixtures |
| Mail-CLI | `test_mail_cli_m166.py` für Hilfe, Config-, Force- und Performancepfad | stabiler Exit-/JSON-Vertrag | vollständiger CLI-/Wheel-Testpfad |
| Nextcloud-Transport | TLS-, Credentials-, HTTP- und Fehlerabbildungstests | DAV-Status-/ETag-Verträge | bestehende Files-, Kalender-, Tasks- und Contacts-Fixtures |

Integrationstests ersetzen keine deterministischen Entscheidungen. Umgekehrt
behaupten die hermetischen Tests keine Live-Erreichbarkeit eines Servers.

## Begrenzte Extraktion

`docker/job_loop.py` behält Prozessstart, Lease-Erneuerung, Heartbeat und
Shutdown-I/O. Zwei bereits vorhandene, reine Grenzen wurden extrahiert:

- `personal_assistant/container_job_profiles.py` enthält ausschließlich die
  feste Allowlist aus Job, Imagekommando, Intervall und Rollen-Environment;
- `personal_assistant/job_runtime.py` entscheidet aus Exitcode, Stop- und
  Lease-Evidenz über Runresultat, Businessstatus, Fehlerzähler und begrenzte
  Batchfortsetzung.

Ein fehlgeschlagenes `scheduler.finish` setzt weiterhin Exit 125, wird nun aber
vor dem finalen Heartbeat erneut ausgewertet. Dadurch kann eine verlorene Lease
nicht mehr gleichzeitig als gesunde Batchfortsetzung erscheinen. Es gibt keine
zweite Toolliste und keine Rückwärts-Kompatibilitätsschicht.

## Nicht wachsende Qualitätsverträge

[`m16.6-risk-coverage.json`](architecture/m16.6-risk-coverage.json) bindet die
Vorherwerte an M16.5-Commit `2a45ec5`. Der vollständige Testlauf muss für jedes
gelistete Risikomodul kombinierte und Branch-Coverage echt verbessern;
Statement-Coverage darf nicht sinken. `scripts/check-risk-coverage.py` läuft
nach dem normalen Coverage-Lauf und schlägt bei fehlenden Modulen, Gleichstand
oder Regression fehl.

Der Collection-Floor in `tests/test-baseline.json` wird nur auf den tatsächlich
gesammelten vollständigen Stand angehoben. Ruff- und mypy-Baselines dürfen nicht
wachsen. Der bestehende AST-Schichtentest prüft den gesamten internen
Importgraphen und verbietet Core-Rückimporte aus `mail_agent`.

## Gemessener Nachherstand

Der vollständige Lauf sammelte 1.152 pytest-Items und 111 Subtests. Alle 1.263
JUnit-Fälle waren erfolgreich. Die kombinierte Repository-Coverage stieg von
69,21 auf 69,76 Prozent, die Statement-Coverage von 73,43 auf 74,01 Prozent und
die Branch-Coverage von 56,56 auf 57,02 Prozent. Die gebundenen Risikomodule
verbesserten sich wie folgt:

| Modul | kombiniert vorher → nachher | Branch vorher → nachher |
| --- | --- | --- |
| `personal_assistant/actions.py` | 37,24 → 44,05 % | 25,54 → 32,07 % |
| `mail_agent/assistant_bridge.py` | 35,00 → 60,00 % | 22,00 → 46,00 % |
| `docker/job_loop.py` | 11,83 → 33,33 % | 4,69 → 10,42 % |
| `mail_agent/cli.py` | 40,81 → 41,72 % | 25,77 → 26,07 % |
| `personal_assistant/connectors/nextcloud/client.py` | 36,56 → 66,67 % | 0,00 → 42,86 % |

Der erste vollständige erfolgreiche Lauf dauerte 143,92 Sekunden, die finale
Kontrollwiederholung 155,91 Sekunden. Ruff und mypy meldeten keine neuen
Befunde; ihre bekannten Baselines wurden nicht vergrößert.

## Reproduktion

```bash
.venv/bin/python -m pytest -q \
  tests/test_risk_paths_m166.py \
  tests/test_mail_cli_m166.py \
  tests/test_quality_risk_m166.py
./scripts/check-repo.sh
```

Alle neuen Fakes, Dateien, Datenbanken und DAV-Antworten sind lokal und
synthetisch. `/srv/openclaw`, produktive Jobs, Mailkonten und Nextcloud werden
nicht berührt.
