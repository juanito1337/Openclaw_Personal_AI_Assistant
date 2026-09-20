# M16.0 – Reproduzierbare Stabilisierungsbaseline

M16.0 misst den Ausgangszustand fuer die M16-Konsolidierung, ohne
Produktverhalten oder Produktivdaten zu veraendern. Der maschinenlesbare
Bericht liegt in
[`docs/architecture/m16-baseline.json`](architecture/m16-baseline.json), das
priorisierte Risikoregister in
[`docs/architecture/m16-risk-register.json`](architecture/m16-risk-register.json).
Das Schema ist
[`docs/architecture/m16-baseline.schema.json`](architecture/m16-baseline.schema.json).

## Reproduktion

Die lokale Qualitaetsabnahme erzeugt zuerst die Test- und Coverageberichte:

```bash
./scripts/check-repo.sh
.venv/bin/python -m pytest --collect-only -q
```

Danach wird der datenschutzarme Bericht mit mindestens drei Wiederholungen
erzeugt:

```bash
.venv/bin/python scripts/benchmark_m16.py \
  --samples 5 \
  --collection-items 1050 \
  --quality-gate-status passed \
  --run-static-analysis \
  --output build/m16-baseline.json \
  --risk-output build/m16-risk-register.json
```

`collection-items` ist die letzte Zeile des unmittelbar davor ausgefuehrten
Collection-Laufs; ausgefuehrte Faelle inklusive Subtests kommen aus
`build/pytest.xml`. Der Bericht speichert nur freigegebene Aggregatfelder. Die
vollstaendigen Ausgaben der aufgerufenen Befehle, Mailtexte, Adressen,
Suchbegriffe, Kalender-/Rechnungsinhalte, Depotwerte, Secrets und lokale
Benutzerpfade werden verworfen.

## Messgrenzen

M16.0 fuehrt keinen produktiven Read-only-Canary aus. Image, Container,
Scheduler, produktiver Sync, Live-Mailindex, Nextcloud, Rechnungen und
Portfolio sind deshalb im Repositorybericht mit `not-measured` markiert. Das
ist weder ein Erfolg noch ein Fehlerbeleg. Eine spaetere produktive Messung
braucht die in der Roadmap geforderte separate Freigabe.

Der synthetische Mailbenchmark nutzt ausschliesslich reservierte
`.invalid`-Fixtures und weist Cold- sowie Warm-Cache getrennt aus. Full-,
Delta- und No-op-Sync sind einzeln vorhanden, bleiben ohne autorisierten
instrumentierten Lauf jedoch ebenfalls `not-measured`.

## Interpretation

M16.0 setzt keine neuen willkuerlichen Grenzwerte. Es friert Messmethode,
Werkzeugversionen, Quellcommit, Stichprobengroesse, p50/p95 und bekannte
Risiken ein. Die Zielpakete M16.1 bis M16.9 muessen ihre Veraenderungen gegen
diese Evidenz ausweisen; fehlende Messbarkeit darf nicht in einen gruenen
Status umgedeutet werden.
