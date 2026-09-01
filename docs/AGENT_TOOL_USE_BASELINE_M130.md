# M13.0-Toolnutzungsbaseline

Stand: 2026-09-01. Diese Baseline enthält ausschließlich synthetische
Formulierungen. Sie liest weder produktive Chats noch Postfach-, Nextcloud-,
Portfolio- oder Laufzeitdaten und führt keine externe Schreibaktion aus.

## Messvertrag

Das Korpus unter `tests/fixtures/m13/tool-use-corpus.json` bildet 15 konkrete
Verhaltensfälle in Deutsch und Spanisch ab. Es trennt erwartete Domäne, zulässige
erste Katalogoperationen, verbotene Fallbacks, erforderliche Evidenzfelder und
Antwortclaim. Enthalten sind alle bisher beobachteten Fehlerklassen:

- Core- statt Produktversion,
- gepunktete Tool-ID oder rohe Himalaya-/Shellausführung,
- unbelegte Verneinung eines vorhandenen Connectors,
- unnötige ISIN-Rückfrage statt Provider-Discovery,
- Konfigurationsedit nach einem Fehler,
- Negativaussage aus unvollständiger Suche,
- Schreiberfolg ohne verifizierten Nachzustand,
- Tool-/Freigabewechsel durch nicht vertrauenswürdigen Inhalt.

Reproduzierbare Befehle:

```bash
./scripts/benchmark-m13.py --phase legacy
./scripts/benchmark-m13.py --phase implemented
python -m pytest -q tests/test_agent_tool_orchestration_m13.py
```

`legacy` ist keine nachträglich erfundene Modellquote. Jeder Fall ist dort als
bekannte reproduzierte Fehlerklasse markiert und gilt einzeln als offen. Der
implementierte Lauf prüft reale Router- und Guardfunktionen. Ein lokaler
Gemma-Canary ist optional und wurde für diese deterministische Baseline nicht
ausgeführt; CI benötigt weder Ollama noch produktive Connectoren.

## Ausgangs- und Vergleichswerte

| Messwert | Legacy-Fehlerkorpus | M13 deterministisch |
| --- | ---: | ---: |
| Fälle | 15 | 15 |
| als korrekt abgeschlossen | 0 | 15 |
| bekannte/offene Fehlverhalten | 15 | 0 |
| externe Schreibaktionen | 0 | 0 |
| Routerlaufzeit lokal, Minimum | nicht gemessen | 0,132 ms |
| Routerlaufzeit lokal, Mittel | nicht gemessen | 0,648 ms |
| Routerlaufzeit lokal, Maximum | nicht gemessen | 3,642 ms |
| Promptlänge, Mittel | 52,4 Zeichen | 52,4 Zeichen |

Gemessen mit Python 3.12.3 und Node.js 24.19.0 auf dem Entwicklungsrechner. Die
Laufzeit charakterisiert nur Regexrouting und Claimprüfung auf dieser Hardware;
sie ist keine Produktions-SLA und enthält weder Modell- noch Netzwerkzeit.

Es gibt zunächst keine willkürliche allgemeine Latenz- oder Modellquote. Alle 15
kritischen Sicherheits-/Fehlerfälle sind dagegen absolute Regressionsgates. Neue
reale Fehlertypen werden als synthetischer Verhaltensfall ergänzt, bevor eine
Grenze gelockert wird.

