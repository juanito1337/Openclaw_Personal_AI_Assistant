# M15.0-Baseline: Ankuendigung ohne Aktionsabschluss

Stand: 2026-09-13

## Gemessener Ausgangsfehler

M13 verlangte aktuelle Werkzeugevidenz fuer Zustands- und Erfolgsclaims. Ein
Agent konnte einen konkreten Schreibauftrag dennoch mit „ich trage die Termine
jetzt ein“ oder „ich muss das Tool nun aufrufen“ beenden, weil diese Antwort
noch keinen Erfolgsclaim enthielt. Weder ein Write-Aufruf noch ein
Nachzustandsbeleg waren fuer das Beenden des Turns zwingend.

Das synthetische Korpus
`tests/fixtures/m15/action-completion-corpus.json` trennt Execute, Preview,
Erklaerung und mehrdeutige Leseanfrage. Es enthaelt keine produktiven Adressen,
Buchungsnummern, Kalender-UIDs oder Inhalte. Der Vertikalschnitt beschreibt zwei
rein synthetische Fluege.

## Reproduzierbare Messung

```bash
.venv/bin/python scripts/benchmark-m15.py --phase legacy
.venv/bin/python scripts/benchmark-m15.py --phase implemented
.venv/bin/python -m pytest -q tests/test_action_completion_m15.py
```

Die Legacy-Phase stellt fuer den Execute-Fall keinen belegten Terminalzustand
her. Die implementierte Phase muss alle vier Korpusfaelle bestehen, das
Zukunftsversprechen blockieren, fuer den Zwei-Ziel-Fall zwei getrennte
Nachzustandsbelege zaehlen und `external_writes: 0` melden. Latenzen sind
Maschinenmesswerte und keine willkuerliche Releasegrenze; jeder kritische Fall
ist einzeln ein absolutes Gate.

## Phasen und Evidenz

| Phase | Erforderlicher Beleg |
| --- | --- |
| Intent | geschlossene Klasse aus aktuellem Nutzerprompt |
| Read | exakte aktuelle Mailquelle und konfigurierte Kalenderressource |
| Argumente | operation-spezifisches Schema, Preview- und Kandidatendigest |
| Approval | eigener Allow-once-Nonce fuer exakt einen Kandidaten |
| Write | registrierte Create-Operation und eigener ActionPlan |
| Read-back | eindeutige UID, ETag und identische Kernfelder |
| Abschluss | nur typisierter Terminalzustand; kein Zukunftsversprechen |

Produktive Kalenderwrites, Jobs, Rechte und `/srv/openclaw` sind nicht Teil
dieser Baseline.
