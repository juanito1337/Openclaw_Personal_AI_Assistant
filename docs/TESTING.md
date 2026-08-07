# Tests, Qualitaetsbaseline und Container-Runtime

Stand: 2026-08-06, fortgeschrieben bis M8. Sie startet keine produktiven Dienste und
verwendet weder `/srv/openclaw` noch produktive Zugangsdaten.

## Einheitlicher lokaler und CI-Prueflauf

Unter Linux x86_64 oder arm64 wird die exakt gepinnte Umgebung einmalig eingerichtet:

```bash
./scripts/bootstrap-dev.sh
```

Das Skript installiert die Python-Abhaengigkeiten aus `requirements-dev.lock` in
`.venv` und laedt ShellCheck 0.11.0 sowie Hadolint 2.15.1 nach `.tools/bin`. Beide
Binaerdateien werden vor der Installation gegen die im Skript fest eingetragenen
offiziellen SHA-256-Werte geprueft. Danach ist der verbindliche Gesamtcheck:

```bash
./scripts/check-repo.sh
```

GitHub Actions ruft genau diese beiden Befehle auf Python 3.11, 3.12 und 3.13 auf.
`check-repo.sh` prueft in dieser Reihenfolge:

1. exakte Quellmenge und SHA-256-Werte in `SOURCE_MANIFEST.sha256`,
2. exaktes Komponenten-Inventar, SHA-verifiziertes Legacy-systemd-Paket und den
   M7-Supply-Chain-Lock,
3. Repository-Hygiene, Bash-Syntax und vorhandene Legacy-systemd-Units,
4. ShellCheck und Hadolint,
5. beide Compose-Varianten mit `docker/deployment.env.example`,
6. `git diff --check`, den Architekturdokumentationsvertrag, die generierte
   M5-Befehlsreferenz und den generierten M8-Skill-Toolvertrag,
7. Ruff und mypy,
8. Python-Kompilierung,
9. die vollstaendige pytest-Suite mit Branch-Coverage,
10. die maschinenlesbare Baseline unter `build/m0-baseline.json`,
11. den M8-Backup-/Restore-Drill in temporaeren Fixture-Roots unter
    `build/m8-recovery.json`.

CI bewahrt JUnit, Coverage-JSON und diese Baseline fuer jede Python-Version als
Build-Artefakt auf. Damit bleiben spaetere Aenderungen sichtbar, ohne in M0 bereits
willkuerliche Coverage- oder Laufzeitgrenzen festzulegen.

Der alleinige Testbefehl ist `./scripts/run-tests.sh`. pytest sammelt damit sowohl
die unittest-Klassen als auch freie pytest-Funktionen. `tests/test-baseline.json`
fordert mindestens 458 Tests, darunter mindestens 420 unittest-kompatible Tests
(die bisherigen 349 sowie M0-M8-Regressionstests),
und genau die zuvor ausgelassenen mindestens 13 freien Tests aus
`tests/test_invoice_ocr_register.py`. Eine kleinere Teilcollection bricht bereits
nach dem Sammeln mit einem Fehler ab. Neue Tests duerfen die Zahl erhoehen; die
Baseline wird erst nach einem vollstaendigen gruenen Lauf bewusst angehoben.

## M0-Ausgangswerte und aktueller M8-Teststand

Gemessen auf Linux x86_64 mit Python 3.12.3. Die Werte sind Beobachtungen und noch
keine willkuerlichen Mindestquoten. `scripts/quality-baseline.py` erzeugt sie nach
jedem Lauf reproduzierbar aus `build/pytest.xml`, `build/coverage.json` und dem AST
der aktuellen Python-Dateien.

| Messwert | Dokumentierter Wert |
| --- | ---: |
| Tests bei Abschluss M0 gesammelt/ausgefuehrt | 379 / 379 |
| Tests nach M1 gesammelt/ausgefuehrt | 385 / 385 |
| Tests nach M2 gesammelt/ausgefuehrt | 395 / 395 |
| Tests nach M3 gesammelt/ausgefuehrt | 402 / 402 (zusaetzlich 2 pytest-Subtests) |
| Tests nach M4 gesammelt/ausgefuehrt | 413 / 413 (zusaetzlich 2 pytest-Subtests) |
| Tests nach M5 gesammelt/ausgefuehrt | 420 / 420 (zusaetzlich 2 pytest-Subtests) |
| Tests nach M6 gesammelt/ausgefuehrt | 428 / 428 (zusaetzlich 13 Subtest-Faelle im Wheel-Lauf) |
| Tests nach M7 gesammelt/ausgefuehrt | 448 / 448 (461 JUnit-Faelle inklusive 13 Subtests) |
| Tests nach M8 gesammelt/ausgefuehrt | 455 / 455 (468 JUnit-Faelle inklusive 13 Subtests) |
| Tests nach M8-CI-Diagnosehaertung gesammelt/ausgefuehrt | 458 / 458 (JUnit-Zaehler inklusive Subtest-Ereignissen siehe `build/m0-baseline.json`) |
| davon bestehende unittest-Tests | 349 |
| davon zuvor ausgelassene Rechnungs-pytest-Tests | 13 |
| neue M0-Regressionstests | 17 |
| neue M1-Dokumentationstests | 6 |
| neue M2-Runtime- und Migrationstests | 10 |
| neue M3-State-/Nebenlaeufigkeitstests | 7 |
| neue M4-Haertungsregressionstests | 11 |
| neue M5-Tool-/Schichtvertragsregressionstests | 7 |
| neue M6-Legacy-/Upgrade-Regressionsitems | 14 |
| neue M7-Lieferketten-Regressionsitems | 20 |
| neue M8-Recovery-/Skill-Regressionsitems | 7 |
| Gesamt-Coverage inklusive Branches (M7) | 59,18 % |
| reine Branch-Coverage (M7) | 43,83 % |
| Gesamt-Coverage inklusive Branches (M8) | 59,18 % |
| reine Branch-Coverage (M8) | 43,83 % |
| Laufzeit des finalen lokalen M6-Testlaufs | 62,94 s |
| Laufzeit des finalen lokalen M7-Gesamtchecks | 63,04 s |
| Laufzeit des finalen lokalen M8-Testlaufs | 56,65 s |
| Laufzeit in der frischen M7-Wheel-Testumgebung | 55,56 s |
| Wheelgroesse | 395.102 Bytes |
| M7-Wheel-Buildzeit | 4,582 s |
| Container-Imagegroesse des M6-Testimages | 425.555.866 Bytes |
| sauberer Container-Erstbuild | ca. 411,92 s |
| M6-Cache-Rebuild | 11 s |
| Container-CLI-Kaltstart | 1.081 ms |
| bekannte mypy-Altbefunde | 114 exakt baselinierte Befunde in 24 Dateien; 10 behoben |
| bekannte Ruff-Altbefunde | 557 exakt baselinierte Befunde; 224 behoben |

Kritische Sicherheitsmodule im aktuellen M8-Lauf:

| Modul | Coverage |
| --- | ---: |
| `personal_assistant/policy.py` | 87,16 % |
| `personal_assistant/job_control.py` | 83,65 % |
| `personal_assistant/source_manifest.py` | 86,08 % |
| `personal_assistant/release.py` | 77,59 % |
| `personal_assistant/antivirus.py` | 63,54 % |
| `personal_assistant/clamav_health.py` | 75,00 % |
| `personal_assistant/container_health.py` | 63,77 % |
| `personal_assistant/container_entrypoint.py` | 39,77 % |
| `mail_agent/assistant_bridge.py` | 35,00 % |
| `personal_assistant/actions.py` | 30,63 % |

Die groessten Module waren zu Beginn `personal_assistant/service.py` (2358 Zeilen),
`personal_assistant/portfolio.py` (2160), `personal_assistant/cli.py` (1692),
`mail_agent/app.py` (1406) und `mail_agent/classifier.py` (1351). Die groessten
Funktionen sind `build_tool_registry` (959 Zeilen), `personal_assistant.cli.main`
(764), `personal_assistant.cli.parser` (593), `extended_help` (429) und
`load_tool_settings` (294). Diese Werte begruenden spaetere Milestones, loesen in
M0 aber noch keine Refaktorierung aus.

M5 ersetzt `build_tool_registry` durch eine kleine Projektion, zerlegt Parser und
Handler des CLI nach Domaenen und trennt Workspace-, Mail-, Portfolio-, Bestell-
und Sicherheitsdienste als Anwendungs-Mixins. Die Portfolio-Importparser liegen in
einem eigenen Modul. Die unveraenderten 124 Toolprojektionen stehen in
`tests/golden/m5-tool-contract.json`; die stabile Top-Level-Hilfe in
`tests/golden/m5-cli-help.txt`. Aktuelle Modul- und Funktionsgroessen werden
weiterhin ausschliesslich durch `scripts/quality-baseline.py` gemessen.

M6 entfernt sieben nicht produktiv aufgerufene Produktdateien und isoliert 14
Rollbackdateien in einem eigenen Manifest. Das Inventar unter
`docs/architecture/component-inventory.json` umfasst 311 aktive, historische,
Kompatibilitaets- oder Migrationskomponenten. Es speichert keine produktive
Telemetrie und keinen Nutzerinhalt.

Die Containerwerte wurden lokal mit einem separaten Testimage ohne produktive
Mounts oder Netzwerke gemessen. Der Erstbuild umfasst das Herunterladen der
Basisimages und die vollstaendige Himalaya-Kompilierung; ein Cache-Rebuild ist
daher nicht mit diesem Ausgangswert vergleichbar. Der `container`-Job in
`.github/workflows/ci.yml` erfasst dieselben Werte auf dem CI-Docker-Runner und
schreibt sie in die Job Summary. Dabei wird kein produktiver Stack gestartet;
gemessen wird nur `personal-assistant --help` mit ueberschriebenem Entry Point.

## Ruff-/mypy-Ausgangsbaseline und enge Ausnahmen

Ruff prueft `E`, `F`, `I`, `B`, `UP` und `SIM` ohne global ignorierten Fehlercode.
Die 781 historischen `E501`-Zeilen sind in `tests/ruff-baseline.json` einzeln an
Datei, Code, Meldung und SHA-256 der konkreten Quellzeile gebunden. Damit kann keine
neue oder veraenderte ueberlange Zeile unter einer pauschalen Ausnahme verschwinden.
Nur `personal_assistant/tool_catalog/*.py` ignoriert eng `E501`: diese Dateien
enthalten exakte, generierte Beschreibungs- und Kommandozeichenketten des Golden
Contracts. Alle anderen Ruff-Regeln gelten dort weiter; normale Pythonmodule
erhalten diese Ausnahme nicht.
Die Baseline wird nur nach einem bewussten, vollstaendig gruenen Lauf neu erzeugt:

```bash
.venv/bin/python scripts/check-ruff.py \
  --baseline tests/ruff-baseline.json \
  --write-baseline mail_agent personal_assistant docker tests scripts
```

mypy prueft alle Module unter `mail_agent`, `personal_assistant` und `docker`.
Die historisch gewachsene Codebasis ist noch nicht vollstaendig typrein. Statt
`ignore_errors`, globaler Fehlercode-Unterdrueckung oder hunderten Inline-Ignores
enthaelt `tests/mypy-baseline.json` jeden akzeptierten Altbefund einzeln als
Kombination aus Datei, Fehlercode und Meldung. `scripts/check-mypy.py` laesst nur
diese exakten 124 Fingerprints zu; jeder neue oder zusaetzliche Befund ist rot.
Behobene Altbefunde sind erlaubt und muessen anschliessend aus der Baseline entfernt
werden. Eine Aktualisierung erfolgt ausschliesslich bewusst mit:

```bash
.venv/bin/python scripts/check-mypy.py \
  --baseline tests/mypy-baseline.json \
  --write-baseline mail_agent personal_assistant docker
```

Hadolint ignoriert keinen Befund. Direkte Alpine-Pakete und nicht-root-Benutzer sind
im M7-Dockerfile explizit und reproduzierbar festgelegt.
ShellCheck-Ausnahmen stehen direkt an den dynamischen `source`- beziehungsweise
absichtlich im Container ausgewerteten Shellzeilen.

## Quellmanifest

Das Manifest darf nur durch den Generator aktualisiert werden:

```bash
./scripts/source-manifest.py generate
./scripts/source-manifest.py verify
./scripts/assistant.sh version --verify
```

Die Quelle ist `git ls-files --cached --others --exclude-standard`; dadurch werden
neue, noch nicht committete Dateien im Entwicklungs-Worktree ebenfalls erfasst.
`SOURCE_MANIFEST.sha256` selbst ist immer ausgeschlossen. Fehlende Dateien,
fehlende oder zusaetzliche Eintraege, geaenderte Inhalte, doppelte Eintraege und
ungueltige Pfade sind Fehler. In einem exportierten Baum ohne `.git` wird die
tatsaechlich vorhandene Dateimenge gegen das Manifest verglichen; dadurch sind dort
auch unaufgefuehrte zusaetzliche Dateien ein Fehler. Der separate Artefaktcheck
kontrolliert zusaetzlich verbotene Laufzeit- und Secret-Dateien.

## Architektur- und Dokumentationstests

`scripts/check-docs.py` ist Bestandteil von `check-repo.sh` und damit von lokaler
Pruefung und CI. Der Check folgt realen relativen Markdown-Links, gleicht jedes
aktive Dokument unter `docs/architecture/` exakt mit
`docs/architecture/owners.json` ab, prueft die aktuelle Releaseidentitaet sowie die
Rollen-/SQLite-Matrix und stellt sicher, dass Zielbild, Betriebsmodell,
Testanleitung und Erweiterungsregeln vom README in hoechstens zwei Links erreichbar
sind. Historische Dokumente unter `docs/archive/` sind bewusst nicht normativ und
werden nicht als aktueller Architekturvertrag ausgewertet.

## M5-Tool-, CLI- und Schichtvertrag

Die statische, konfigurationsfreie Sicht wird so geprueft:

```bash
./scripts/assistant.sh tools list --catalog
./scripts/assistant.sh capabilities --schema
.venv/bin/python scripts/generate-command-reference.py --check
.venv/bin/python -m pytest -q tests/test_m5_tool_contract.py
```

Der statische Katalog listet alle bekannten Werkzeuge und erteilt keine Rechte. Die
Live-Befehle `tools list` und `capabilities` laden dagegen Konfiguration und
Ressourcen; die Capability-Antwort traegt `view=live-capabilities` und
`configured=true`. Der M5-Test prueft alle Tool-IDs, Modi, externe Wirkung,
Approvalklassen, Schemata, Fehlercodes, importierbare Domaenenhandler sowie reale
Doku- und Testanker. Er vergleicht die voll freigeschaltete Live-Projektion exakt
mit dem Golden Contract, fuehrt Policy-Negativtests aus und verwirft Importzyklen
oder Core-Imports konkreter Mailinfrastruktur.

## M6-Inventar, Legacy-Paket und Vorher-/Nachhervergleich

Die M6-Vertraege werden lokal und in CI durch dieselben Befehle geprueft:

```bash
.venv/bin/python scripts/generate-component-inventory.py verify
.venv/bin/python scripts/verify-legacy-package.py verify
.venv/bin/python -m pytest -q tests/test_m6_legacy_cleanup.py
.venv/bin/python scripts/benchmark-m6.py --samples 12 \
  --image openclaw-agent:m6-local --output build/m6-after.json
```

Das Inventar deckt alle im Quellmanifest enthaltenen Pythonmodule,
Shellentrypoints, Skills, systemd-Units, Migrationspfade und Markdown-Dokumente ab.
Jeder Datensatz enthaelt Owner, Klassifikation, produktive Referenzen,
Test-/Dokumentationsevidenz, Coverage-Snapshot, letztes bekanntes Git-Datum und
Rollbackrelevanz. `last_git_change=null` bedeutet, dass eine neue Datei im
ungecommitten M6-Worktree noch keinen Git-Zeitstempel besitzt. Produktive
Runtime-Telemetrie und Nutzerinhalte werden aus Datenschutzgruenden nicht in dieses
Repository-Inventar uebernommen.

Der Vergleich wurde auf demselben Linux-x86_64-Host und mit Python 3.12.3 gemessen.
Er ist eine Beobachtung, keine neue Qualitaetsschwelle:

| Messwert | vor M6 (M5) | nach M6 | Aenderung |
| --- | ---: | ---: | ---: |
| pytest-Items | 420 | 428 | +8 |
| Python-Dateien im Quellmanifest | 199 | 197 | -2 |
| Python-Bytes im Quellmanifest | 2.183.318 | 2.193.694 | +10.376 |
| Shell-Dateien im Quellmanifest | 30 | 29 | -1 |
| Shell-Bytes im Quellmanifest | 79.736 | 80.607 | +871 |
| Wheel-Bytes | 402.835 | 395.102 | -7.733 (-1,92 %) |
| Image-Bytes | 425.536.788 | 425.555.866 | +19.078 (+0,004 %) |
| Kaltimport `personal_assistant.cli`, Median 12 Prozesse | 231,273 ms | 291,912 ms | +60,639 ms |
| Kaltimport `mail_agent.cli`, Median 12 Prozesse | 249,090 ms | 303,095 ms | +54,005 ms |
| Cache-Imagebuild | 10,83 s | 11 s | +0,17 s |
| isolierter Container-CLI-Kaltstart | 975 ms | 1.081 ms | +106 ms |

Die produktiven Altdateien wurden kleiner und das Wheel schrumpfte; Inventar,
Verifier, ADR und 15 neue M6-/Manifest-Regressionsitems erhoehen dagegen den
Repositoryumfang. Das gemeinsame Runtime-Image blieb praktisch gleich gross, weil
M7 die rollenbezogene Image-Minimierung bewusst noch nicht vorzieht. Prozess- und
Container-Kaltzeiten schwanken auf dem gleichzeitig betriebenen Host deutlich;
deshalb dokumentiert M6 die gemessene Verschlechterung, ohne daraus eine
willkuerliche Sperre abzuleiten.

Das Image `openclaw-agent:m6-local` wurde mit dem M3-Runtimecheck und dem
M4-Hardeningcheck in temporaeren Containern abgenommen. Der exportierte Rootfs-
Artefaktscan war gruen, `/opt/openclaw-agent/legacy` ist nicht vorhanden, und alle
acht bereits laufenden Produktivcontainer behielten vor und nach der Abnahme ihre
Container-IDs und den Status `healthy`.

## M7-Rollenimages und Vorher-/Nachhervergleich

Die maschinenlesbare Evidenz steht in
[`architecture/image-baseline-m7.json`](architecture/image-baseline-m7.json).
Beide No-Cache-Builds wurden aus demselben sauberen Export mit normalisierten
Zeitstempeln als OCI-Archive erzeugt. Die drei Archiv-SHA-256-Werte waren zwischen
den Laeufen bytegenau identisch; der erste Gesamtlauf dauerte 184 s, der zweite
180 s. Syft 1.50.0, Trivy 0.73.0 und Cosign 3.1.3 sind als unveraenderliche
Scannerimages gepinnt.

| Rolle | M6-Bytes | M7-Bytes | Groesse | kritische CVEs | SPDX-Pakete |
| --- | ---: | ---: | ---: | ---: | ---: |
| Runtime | 425.555.866 | 376.499.617 | -11,53 % | 0 | 926 |
| Proxy | 425.555.866 | 23.417.257 | -94,50 % | 0 | 84 |
| Maintenance | 425.555.866 | 45.627.796 | -89,28 % | 0 | 95 |

Der direkt vergleichbare 12-Prozess-Benchmark auf demselben Host zeigt zugleich
eine Verschlechterung des Kaltstarts: Runtime +33,32 %, Proxy +103,68 % und
Maintenance +52,26 %. Der Median des Peak-RSS aenderte sich um -1,34 %, +19,24 %
beziehungsweise +19,63 %. Diese Regression wird fuer M7 bewusst akzeptiert: Der
Alpine-Wechsel beseitigt 21 nicht behobene kritische Befunde des zuvor getesteten
Debian-Runtimepfads und reduziert Proxy-/Maintenance-Image und Paketzahl massiv.
M7 setzt dafuer keinen nachtraeglich gewaehlten Performance-Grenzwert; der gleiche
CI-Benchmark macht spaetere Aenderungen sichtbar. Die Entscheidung ist in
[ADR-0011](architecture/adr/0011-reproduzierbare-rollenimages.md) festgehalten.

## M8-Integration, Fehler und Recovery

Der lokale/CI-identische hermetische Dockerlauf ist:

```bash
sg docker -c './scripts/check-m8-integration.sh'
```

Er verwendet nur das digest-gepinnte Python-Fixture-Image, ein internes temporaeres
Netz und ein temporaeres Writer-Volume. Es gibt keine Hostports, produktiven Mounts,
Konten oder Secrets. Geprueft werden IMAP, SMTP, WebDAV/CardDAV/CalDAV,
ETag/HTTP-412, Ollama, Marktdaten, ClamAV clean/EICAR, Netztrennung/-recovery,
SIGKILL/Containerrecovery und der Nachweis, dass ein zweiter Mailwriter abgewiesen
wird. Laufzeiten stehen in `build/m8-integration.json`.
Der lokale M8-Abnahmelauf mass 2,583 s bis zum gesunden Fixture-Stack, 2,219 s
fuer das komplette Protokollszenario und 2,558 s vom SIGKILL bis zur erneut
gesunden Fake-Service-Instanz. Diese Werte sind Fixture-Beobachtungen, keine
Produktions-SLAs.

Der lokale Restore-Drill laeuft auch innerhalb von `check-repo.sh`:

```bash
.venv/bin/python scripts/m8-recovery-drill.py --output build/m8-recovery.json
```

Er erzeugt kleine temporaere r26.1-/r27.2.5-States mit realen SQLite-Dateien, nutzt
die produktiven Backup-, Verify-, Restore-Test- und lokalen Restore-Skripte und
vergleicht `state`, `config` und `secrets` bytegenau. Das dritte Szenario injiziert
einen unlesbaren Layoutmarker, verlangt einen unveraenderten Preflight-Abbruch und
stellt danach den Snapshot wieder her. Weder `/srv/openclaw` noch Docker-
Produktivcontainer werden verwendet.

`tests/test_m8_recovery.py` fuehrt den echten Deployment-Shellablauf mit ersetzten
Protokollgrenzen in einem Tempverzeichnis aus: Smoke-Exit 23 muss den automatischen
Rollback ausloesen; ein Rollbackfehler wird als Exit 70 sichtbar. Weitere
Verhaltenstests pruefen Offline-Pflicht, Hook-Abbruch vor Containerstop und den
lokalen Wiederanlauf trotz fehlschlagendem externen Hook. DB-Lock, Full-/Read-only-
Volume, SIGKILL, ungueltige Migration und verlorene Scheduler-Lease werden durch
die bestehenden M3-/Schedulertests real injiziert.

Die gemessenen Recoverywerte und ihre bewusst engen Grenzen stehen im
[M8-Recoveryvertrag](architecture/RECOVERY_AND_RELEASE.md). Ein Fixture-RTO ist
kein Produktions-SLA; Remoteaenderungen sind ohne externen Snapshot nicht durch
lokalen Rollback wiederherstellbar.

Skilldrift wird ohne Textduplikation geprueft:

```bash
.venv/bin/python scripts/generate-skill-tool-contract.py --check
.venv/bin/python -m pytest -q tests/test_m8_skill_contract.py
```

Der Test gleicht alle 124 Tool-IDs, Commands, Modi, externe Wirkungen, Approvals,
Release und Testanker gegen die typisierte Registry ab, prueft die kurze
Triggerbeschreibung und verlangt die domaenenspezifischen Referenzen sowie die
Abwesenheit des entfernten Zweit-Agent-Skills.

Die finale lokale M8-Artefaktabnahme baute das Wheel in 2,865 s; es blieb 395.102
Bytes gross und bestand in der frischen Installationsumgebung 455 Tests plus 13
Subtests in 43,95 s. Der cache-gestuetzte Build der drei M8-Rollen dauerte 27,7 s.
Rollen-Smokes, M3-Runtime, M4-Haertung, Rootfs-Artefaktscan, SPDX, Provenance und
Trivy waren gruen:

| M8-Rolle | Image-Bytes | SPDX-Pakete | kritische CVEs | Secret-Befunde |
| --- | ---: | ---: | ---: | ---: |
| Runtime | 376.488.274 | 926 | 0 | 0 |
| Proxy | 23.417.259 | 84 | 0 | 0 |
| Maintenance | 45.627.770 | 95 | 0 | 0 |

Diese lokalen Images sind nicht signierte produktive Releaseartefakte. Das
Deployment-Gate akzeptiert weiterhin nur die drei unveraenderlichen, attestierten
Registry-Digests eines freigegebenen Commits.

## Wheel- und Artefaktpruefung

```bash
./scripts/check-wheel.sh
```

Das Skript exportiert die exakte Git-Quellmenge in ein leeres temporaeres
Verzeichnis, verifiziert dort das Manifest, baut ein Wheel, installiert es in eine
frische venv und prueft CLI-Hilfe, Paketimport aus `site-packages`,
`version --verify` und denselben Testbefehl. Der installierte Testmodus laedt die
beiden Produktpakete vor pytest nachweislich aus `site-packages` und bricht ab, falls
waehrend Collection oder Ausfuehrung ein Produktmodul aus dem Snapshot importiert
wird. Die Coverage-Baseline entsteht im normalen Repository-Lauf; sie wird im
Wheel-Lauf nicht redundant mit abweichenden Installationspfaden neu gemessen. Wheel
und die produktbezogenen Pfade des exportierten Image-Dateisystems werden durch
`scripts/check_artifact.py` auf
produktive Konfigurationen, Datenbanken, Logs, lokale virtuelle Umgebungen,
Laufzeitdateien, Schluesseldateien und typische Secret-Muster geprueft. Dazu gehoeren
auch `/home/node/.openclaw`, `/etc/openclaw-env`, `/run/openclaw-env` und
`/run/openclaw-secrets`, nicht nur `/opt/openclaw-agent`.

## Dynamische M3-Containerabnahme

Nach dem isolierten Imagebuild wird dasselbe Skript lokal und in GitHub Actions
ausgefuehrt:

```bash
./scripts/check-container-runtime.sh openclaw-agent:m3-test
```

Es verwendet ausschliesslich ein mit `mktemp` angelegtes Fixture-State-Verzeichnis,
`--network none` und kurzlebige Container. Geprueft werden zwei parallele Starts,
die Layoutsperre, eine manipulierte Legacy-`assistant.sh`, read-only Imagecode,
unveraenderte Instanzkonfiguration beim Neustart, Release-/OCI-Revision,
tatsaechliche Modul- und Executable-Pfade, Layout 3, getrennte Rollenmounts sowie der
vollstaendige Image-Artefaktscan.
Ein vorhandener Compose-Stack und `/srv/openclaw` werden nicht verwendet.

## Dynamische M4-Containerabnahme

Nach der M3-Pruefung wird dasselbe gebaute Image weiter isoliert geprueft:

```bash
./scripts/check-container-hardening.sh openclaw-agent:m4-test
```

Der Prueflauf legt nur temporaere Docker-Netze und Container an. Er bestaetigt
Nicht-root-UID/GID, leere effektive Capability-Maske, read-only Rootfs, fehlende
fremde Secretdateien, positive Backend- und negative Fremdnetz-Erreichbarkeit,
SIGTERM-Weitergabe, Docker-Inspect-Werte fuer PID/CPU/RAM, eine tatsaechlich
erzwungene PID-Grenze und einen isolierten OOM-Kill. Er verbindet sich weder mit
Produktionsnetzen noch mit `/srv/openclaw` und startet keinen fachlichen Job.

ClamAV-Negativfaelle laufen ohne Internet als Python-Fixtures: fehlende oder alte
`main`/`daily`/`bytecode`-Signaturen und eine nicht belegbare Scanneridentitaet
brechen fail-closed ab. `runtime-hardening.json` wird separat gegen gerendertes
Compose geprueft; dadurch fallen neue Ports, Netze, Rootrollen, Secrets oder
gelockerte Ressourcenlimits bereits im normalen Repository-Check auf.

## M3-State-, Parallelitaets- und Zugriffstests

`tests/test_state_layout_m3.py` verwendet ausschliesslich temporaere Fixtures und
reale Prozesse. Es prueft acht konkurrierende ActionPlan-Writer, gleichzeitige
Schedulerzugriffe von Mail/Sync/Portfolio/Monitor, SQLite-Lock- und WAL-Verhalten,
SIGKILL vor Commit, abgelaufene Leases (zusammen mit `test_work_scheduler.py`),
Full-/Read-only-Preflight, konsistente Backups, Restore alter und neuer Layouts und
die Mountmatrix aus gerendertem Compose.

Persistente Zugriffe einer geaenderten Rolle werden instrumentiert geprueft:

```bash
./scripts/audit-state-access.py --role <rolle> \
  --root "<bereich>=<absoluter-fixture-pfad>" -- <fixture-kommando>
```

Die synthetische, reproduzierbare Vorher-/Nachher-Messung wird so wiederholt:

```bash
.venv/bin/python scripts/benchmark-state-layout.py
```

Lokaler Messwert vom 2026-08-05 (7 Laeufe, sechs logische Schemas plus 500
WAL-Inserts): Legacy-Verzeichnis Median `285.646 ms`, Layout-3-Teilbaeume
`324.948 ms`; geschriebene Bytes `8,491,008` gegen `8,802,304`, gelesene
Block-I/O-Bytes `0` gegen `4,096`. Das ist eine Fixture-Baseline, kein produktiver
SLA-Grenzwert; der Mehraufwand umfasst die getrennte Wissensdatenbank.

## Gepinnte Werkzeugversionen

| Werkzeug | Version |
| --- | --- |
| pip | 26.2.1 |
| setuptools | 83.0.0 |
| build | 1.5.0 |
| pytest | 9.1.1 |
| pytest-cov | 7.1.0 |
| Coverage.py | 7.15.3 |
| Ruff | 0.15.22 |
| mypy | 2.3.0 |
| ShellCheck | 0.11.0 |
| Hadolint | 2.15.1 |

Die transitiven Python-Abhaengigkeiten sind ebenfalls exakt in
`requirements-dev.lock` festgelegt. Docker, Compose, Git und die jeweilige
Python-Version stammen vom Host beziehungsweise CI-Runner und werden in
`build/m0-baseline.json` protokolliert.
