# M15-Entwicklungsabnahme und Rolloutgrenze

Stand: 2026-09-13

## Umfang

M15 ergaenzt die nativen M13-Werkzeuge um einen maschinenlesbaren
Aktionsabschlussvertrag. Der erste End-to-End-Pfad lautet:

```text
exakte Mail -> ClamAV -> deterministische Vorschau -> Einzelfreigabe
-> create-only ActionPlan -> UID/ETag-Read-back -> belegter Abschluss
```

Die lokale Abnahme verwendet ausschliesslich synthetische Nachrichten und
Fake-Connectoren. Sie veraendert keine produktive Mail, keinen Kalender, keine
Jobs, keine Rechte und keine Dateien unter `/srv/openclaw`.

## Reproduzierbare Abnahme

```bash
./scripts/assistant.sh version --verify
.venv/bin/python scripts/generate-agent-tools.py verify
.venv/bin/python scripts/generate-skill-tool-contract.py --check
.venv/bin/python scripts/benchmark-m13.py --phase implemented
.venv/bin/python scripts/benchmark-m15.py --phase implemented
.venv/bin/python -m pytest -q tests/test_action_completion_m15.py
./scripts/check-repo.sh
./scripts/check-wheel.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
./docker/scripts/build-local.sh \
  openclaw-agent:m15-candidate \
  openclaw-agent:m15-candidate-proxy \
  openclaw-agent:m15-candidate-maintenance
./scripts/check-role-images.sh \
  openclaw-agent:m15-candidate \
  openclaw-agent:m15-candidate-proxy \
  openclaw-agent:m15-candidate-maintenance \
  "<exakte Revision oder ehrlich markierter Working-Tree>"
OPENCLAW_M13_RUNTIME_IMAGE=openclaw-agent:m15-candidate \
  ./scripts/check-m13-integration.sh
OPENCLAW_M15_RUNTIME_IMAGE=openclaw-agent:m15-candidate \
  ./scripts/check-m15-integration.sh
./scripts/check-source-secrets.sh
./scripts/check-local-signature.sh
./scripts/check-image-supply-chain.sh \
  openclaw-agent:m15-candidate runtime "<Revision>" build/m15/runtime
./scripts/check-image-supply-chain.sh \
  openclaw-agent:m15-candidate-proxy proxy "<Revision>" build/m15/proxy
./scripts/check-image-supply-chain.sh \
  openclaw-agent:m15-candidate-maintenance maintenance "<Revision>" build/m15/maintenance
./scripts/check-reproducible-images.sh
```

Der Image-Integrationstest startet nur kurzlebige Container mit
`--network none`, read-only Rootfs, Capability-Drops, Scripted Replies und
synthetischen Mail-/CalDAV-Adaptern. Er prueft Erfolg, unvollstaendige Daten,
Netzfehler, Timeout, Approval-Replay, Teilfehler und die inhaltsarme Telemetrie.
Ein lokal gebautes Rollenimage belegt noch keine CI-Provenance,
Registry-Signatur oder produktive Aktivierung.

## Lokaler Abnahmestand

| Pruefung | Ergebnis |
| --- | --- |
| Repositorycheck | 1.036/1.036 pytest-Items in 136,98 s; 111 Subtests; Manifest, Ruff, mypy, ShellCheck, Hadolint, Compose, Python-Kompilierung und `git diff --check` gruen |
| Coverage | 68,45 % branch-einbezogen; 72,73 % Statements; 55,69 % reine Branch-Coverage |
| Wheel | frische Installation und kompletter Testlauf gruen; 615.456 Bytes; Build 2.678 ms; 1.036 Tests plus 111 Subtests in 107,31 s |
| M13-Korpus | 17/17; externe Writes 0 |
| M15-Korpus | 4/4; externe Writes 0 |
| M15-Imageintegration | echter Plugin-Hookpfad gruen; Scripted Replies, Approval-Replay, Netzwerkfehler, Timeout, Informationsbedarf und Teilerfolg geprueft; externe Writes 0 |
| Approval-/Antwortkorrelations-Hotfix | 1.041/1.041 pytest-Items in 201,06 s; Coverage 68,49 % kombiniert; M15-Imageintegration mit Event-Run-ID und kontextloser Ja/Nein-Retryabwehr gruen |
| aktualisiertes Wheel | frische Installation und kompletter Testlauf gruen; 616.293 Bytes; Build 4.136 ms; 1.041 Tests plus 111 Subtests in 149,43 s |
| Approval-Kontext | Der Hook-Run bleibt ueber eingefrorene genehmigte Parameter bis zur fortgesetzten Tool-Factory gebunden; der Test entfernt die Run-ID absichtlich aus dem Factory-Kontext |
| Stale-/Replay-Nachweis | typisiertes `approval-required`, `executed=false`, `external_write_attempted=false`, kein Launcheraufruf und kein automatischer Retry; ein blosses `/approve` wird vom Antwortguard verworfen |
| Antwortauslieferung | Die Event-Run-ID bindet den letzten Antwortschutz auch ohne Run-ID im Message-Kontext; ungebundene Ja/Nein-Retryfragen werden durch belegten Blocker und vollstaendige neue Anweisung ersetzt |
| Rollen-Smoke | Runtime, Proxy und Maintenance gruen; keine produktiven Container verwendet |
| lokale Working-Tree-Images | Runtime 377.223.464 Bytes; Proxy 23.422.616 Bytes; Maintenance 45.638.559 Bytes |
| cacheloser Doppelbuild | 148 s / 150 s; OCI-Tar-SHA-256 je Rolle paarweise identisch |
| Supply Chain | drei SPDX-SBOMs und lokale Provenance; Rootfs-/Secretpruefung gruen; Trivy Critical 0 und Secrets 0 je Rolle |
| Signatur | lokaler positiver und negativer Cosign-Blobtest gruen |

Die lokalen Kandidaten tragen bewusst die Revision `m15-working-tree`. Damit
wird ein ungepushter Stand nicht als exaktes Commitimage ausgegeben. Erst ein
Push kann den vorhandenen CI-Pfad fuer BuildKit-Provenance, Registry-SBOM und
keyless Cosign gegen den exakten Commit ausloesen.

Die SHA-256 der reproduzierbaren OCI-Tar-Artefakte lauten Runtime
`10426669d10abeb3ed0cd051c51d3c67a8aed78332819922540e39e89e97dc9c`,
Proxy `68a614ad01e2c1ee6184c4efc57fe265a12dfae4df63700f3c57aedbf188db7c`
und Maintenance
`c7560563722cb156f0e8e8aac1a894528112edffca016b54fc5c44e82e87e561`.

## Absolute Gates

- Preview und Erklaerung erzeugen keine Aktionsverpflichtung und keinen Write.
- Execute erzeugt eine nur fuer den aktuellen Turn gueltige Verpflichtung.
- Zukunftsversprechen, Schweigen und Meta-Abbruch werden nach hoechstens einer
  Revision fail-closed blockiert.
- Stale Quelle, geaenderter Digest, fremder Turn und Approval-Replay schliessen
  den Workflow nicht.
- Unvollstaendige Flugdaten enden ohne erfundene Werte als
  `information-required`.
- Raw-Mail und jede physische Anlage passieren den fail-closed ClamAV-Pfad.
- Jeder Termin ist ein eigener Create mit eigener Freigabe, UID, ActionPlan und
  Nachzustandspruefung.
- Fehlender/abweichender UID-, ETag-, Zeit-, Titel-, Ort- oder
  Beschreibungsbeleg autorisiert keinen Erfolg.
- Teilerfolg ist sichtbar und kein Gesamtabschluss; automatisches Delete oder
  Remote-Rollback findet nicht statt.
- M13-Evidenz-, Approval-, argv-only-, Circuit-Breaker- und
  Single-Writer-Grenzen bleiben unveraendert.

## Produktive Rolloutgrenze

Ein spaeterer produktiver Canary beginnt read-only mit Kalenderstatus, exakter
Mailauswahl, ClamAV, Vorschau und Duplikatpruefung. Er ist keine Berechtigung,
einen Termin anzulegen. Jeder produktive Kandidat benoetigt danach eine neue
explizite Einzelfreigabe gegen das signierte Image und den aktuellen Digest.
Ein Image-/State-Rollback entfernt keinen bereits erzeugten CalDAV-Termin.

Bis signiertes CI-Image und ein getrennt freigegebener produktiver Read-only-
Canary vorliegen, lautet das Gesamturteil **M15 NICHT PRODUKTIV ABGENOMMEN**.
Die Entwicklungsimplementierung ist lokal und hermetisch abgenommen; diese
Aussage autorisiert weder Deployment noch Kalender-Write.
