# M16.8: Rechnungs- und Portfolio-Restbestaende

Stand: 2026-09-21. Dieses Paket ist eine hermetische Entwicklungsabnahme. Es
liest und veraendert keine produktiven Belege, Watchlists, Mappings, Kurse,
Anlageprofile oder Jobzustaende.

## Rechnungen

Der aggregierte Backlog-Audit weist ab Schema 2 zusaetzlich typisierte
Reviewgruende, belegte Datumsrollen, Scanneridentitaeten und die Abdeckung der
gespeicherten Original-SHA-256 aus. Er bleibt inhaltsfrei und oeffnet weder PDFs
noch Nextcloud. Die bestehende Einzelbeleg-Vorschau bindet nun sichtbar:

- Original-SHA-256 und Scanneridentitaet,
- Extraktor-/Regelversion und Feldprovenienz,
- ausgewaehlte und ausgeschlossene Datumsrollen,
- getrennte Quell-, Pfad-, Register-, Empfangs- und erkannte Rechnungsjahre,
- einen digestgebundenen Pfadmigrationsplan.

Der Migrationsplan ist absichtlich nicht ausfuehrbar. Eine spaetere
Einzelmigration muss lokalen Backup und extern wiederherstellbaren
Nextcloud-Snapshot, unveraenderten SHA-256 und ETag, genau null Zieltreffer und
ein nicht vorhandenes Ziel belegen. Ein anderes Jahr, anderer Inhalt,
existierendes Ziel oder mehrere Zielkandidaten blockieren fail-closed. Es gibt
weder Overwrite noch Bulk-Apply.

Legacy- und aktueller Extraktor werden nur auf demselben SHA-gebundenen,
synthetisch gelabelten Beleg verglichen. Die Baseline verbessert im Testkorpus
die Feldgenauigkeit von 0,60 auf 1,00; dies ist ein Regressionsergebnis und
keine Aussage ueber den produktiven Altbestand.

## Portfolio

`portfolio status` und `portfolio doctor` liefern einen geschlossenen
Diagnosezustand:

- `off`,
- `configured-limited`,
- `healthy`,
- `stale`,
- `mapping-required`,
- `provider-entitlement-denied`.

Konfiguration, Providerzugriff, Mapping, Kursfrische und gewuenschter Jobzustand
bleiben getrennte Felder. Ein bewusst auf `off` gesetzter Job ist gesundes
Betriebsziel und kein Stackfehler. HTTP 401, 402, 403 und 429 sowie eine leere
Providerantwort behalten eigene Kategorien. Research-Entitlementfehler werden
nicht automatisch wiederholt; Fremdprovider, Webersatzkurse und erfundene
Ticker bleiben verboten. Feedback aendert das versionierte Nutzerprofil nie
automatisch.

## Reproduktion

```bash
.venv/bin/python scripts/benchmark_domain_quality_m168.py
.venv/bin/python -m pytest -q \
  tests/test_domain_quality_m168.py \
  tests/test_invoice_reprocess_preview_m105.py \
  tests/test_invoice_reprocess_apply_m106.py \
  tests/test_invoice_backlog_audit_m107.py \
  tests/test_portfolio_tool.py \
  tests/test_portfolio_research.py \
  tests/test_job_control.py
```

Die maschinenlesbare Baseline steht in
[`m16.8-domain-quality-baseline.json`](architecture/m16.8-domain-quality-baseline.json).

## Grenzen

- Der Benchmark ist synthetisch und enthaelt keine produktiven Dokumentwerte.
- Der Pfadmigrationsvertrag ist nur Preview und Validator, kein Writer.
- Mapping bleibt eine providergebundene, explizit freizugebende Einzelaktion.
- M16.8 aktiviert keinen Job und kauft oder simuliert keinen Providerzugang.
