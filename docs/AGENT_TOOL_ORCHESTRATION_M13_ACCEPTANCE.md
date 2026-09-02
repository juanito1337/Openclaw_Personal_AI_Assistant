# M13-Abnahme: verlässliche Werkzeugsteuerung

Stand: 2026-09-01. M13.0 bis M13.7 sind als Entwicklungsstand implementiert.
M13.8 trennt lokale/hermetische Abnahme, CI-Attestierung und produktiven
read-only Canary ausdrücklich voneinander.

## Implementierter Vertrag

- 159 Katalogoperationen, davon 158 nativ unterstützt und eine begründet
  ausgeschlossene interne Mail-Kalenderoperation.
- 19 native, domänen-/effektbezogene OpenClaw-Werkzeuge mit einem
  providerkompatiblen flachen JSON-Schema; ein Operations-Discriminator und
  sichtbare Pflichtfeldsignaturen werden zur Laufzeit weiterhin gegen das
  exakte operationsspezifische Schema validiert.
- gezielte Aufnahme genau dieses Plugins in das begrenzte `coding`-Profil über
  `tools.alsoAllow`; keine pauschale Freigabe aller Plugins.
- feste argv-Ausführung ohne Shell sowie Blockierung roher Fach-Execs und
  generischer Secretpfade.
- deterministisches read-only Routing aus dem aktuellen Nutzerprompt.
- Einzelfreigaben für Local-write und Write, gebunden an Turn, ToolCall,
  Operation, Argumentdigest und Ablaufzeit; Gateway-Schweregrade sind
  `warning` beziehungsweise `critical`, kein `allow-always`.
- Evidenzschema v1 und fail-closed Guard für Zustands-, Negativ-, Versions- und
  Schreiberfolgsbehauptungen.
- inhaltsfreie Laufzeitmetriken; keine Queries, Adressen, Resultate oder Secrets.

Der maschinenlesbare Diagnosepfad ist:

```bash
./scripts/assistant.sh agent-tools status
```

## Reproduzierbare Entwicklungsabnahme

```bash
./scripts/generate-agent-tools.py verify
./scripts/benchmark-m13.py --phase implemented
python -m pytest -q tests/test_agent_tool_orchestration_m13.py
./scripts/check-repo.sh

./docker/scripts/build-local.sh \
  openclaw-agent:m13-candidate \
  openclaw-agent:m13-candidate-proxy \
  openclaw-agent:m13-candidate-maintenance

./scripts/check-role-images.sh \
  openclaw-agent:m13-candidate \
  openclaw-agent:m13-candidate-proxy \
  openclaw-agent:m13-candidate-maintenance \
  "$(git rev-parse HEAD)"

OPENCLAW_M13_RUNTIME_IMAGE=openclaw-agent:m13-candidate \
  ./scripts/check-m13-integration.sh
```

Der M13-Integrationslauf verwendet `--network none`, ein read-only Image,
Capability-Drops und temporäre Dateisysteme. Er registriert die echten nativen
Tools im gepinnten OpenClaw und verlangt, dass dessen Runtime-Inspect exakt alle
19 statischen Toolnamen veröffentlicht. Eine geladene Pluginfabrik ohne im
Agentenlauf auswählbare Namen gilt ausdrücklich als Fehler. Der Test führt
außerdem ein isoliertes Gateway mit dem produktionsgleichen `coding`-Profil aus
und vergleicht dessen RPC-Inventar `tools.effective` exakt mit dem generierten
19-Werkzeug-Vertrag. Damit wird auch eine fehlende `tools.alsoAllow`-Freigabe
erkannt. Der Test führt
strukturierte Leseaufrufe für Runtime, Mail,
Nextcloud, Aufgaben und Portfolio einschließlich eines exakten read-only
ISIN-Mappingvorschlags aus, blockiert einen rohen Fach-Exec und ersetzt
eine unbelegte Mail-Negativaussage. Der Schreib-Canary aktualisiert ausschließlich
eine synthetische Aufgabe über den gemounteten Fake-Launcher. Er belegt den
Nachzustand, verwirft die verbrauchte Freigabe bei einem Replay und berührt weder
einen externen Dienst noch produktive Daten. Externe und produktive
Schreibaktionen: jeweils null. Der Approval-Canary verwirft außerdem alle nicht
vom Gateway unterstützten Schweregrade, bevor ein Release grün werden kann.

## Lokal gemessener Abnahmestand

Die folgenden Werte wurden am 1. September 2026 mit den oben dokumentierten
Befehlen und der gepinnten Werkzeugumgebung erhoben:

| Prüfung | Ergebnis |
| --- | --- |
| pytest-Collection | 966 von 966 Items erfolgreich; 1.071 JUnit-Fälle einschließlich 105 Subtests |
| Coverage | 67,91 % gesamt; 54,90 % Branch-Coverage |
| Wheel | frische Installation erfolgreich; 584.363 Bytes; Build 5.471 ms; vollständiger Testlauf grün |
| Runtimeimage | 377.094.735 Bytes; Cold-Start/Import Median 2.606,447 ms; 38.272 KiB Peak RSS |
| Proxyimage | 23.421.993 Bytes; Cold-Start/Import Median 2.148,793 ms; 24.764 KiB Peak RSS |
| Maintenanceimage | 45.631.733 Bytes; Cold-Start/Import Median 1.275,995 ms; 14.572 KiB Peak RSS |
| cacheloser reproduzierbarer Doppelbuild | erster Lauf 260 s; zweiter Lauf 244 s; alle drei OCI-SHA-256 paarweise identisch |
| Supply Chain | drei SBOMs und Provenance-Nachweise; Rootfs-/Secretprüfung grün; Trivy Critical 0 und Secrets 0 |
| Containerverträge | Rollen-, Runtime-, Hardening-, M8-, M11-, M12- und M13-Integration grün |
| Signatur | lokaler positiver und negativer Cosign-Blobtest grün; Registry-/keyless-Signatur erst in CI nach Push belegbar |

Die Cold-Start-Werte stammen aus jeweils fünf frischen `docker run`-Aufrufen von
`scripts/benchmark-m7.py`. Sie messen Containerstart plus Python-Modulimport auf
diesem Entwicklungsrechner und sind keine allgemeine SLA. Die OCI-Digests des
reproduzierbaren Doppelbuilds lauten:

- Runtime: `sha256:5bf58295db421075bb7043064e1d369c1e79640d1c0f34c5c24050228ea54a64`
- Proxy: `sha256:f6402d81223f7a707207f65c60f5642db793b63a2dc9cbdc7f7dcdf66abac0a5`
- Maintenance: `sha256:af435df244515de2589cb947847e6b2a8c88224ae6e400d18777b1f512d6febd`

Die lokalen Image-IDs sind keine veröffentlichten Registry-Digests. Der Build
verwendete die bewusst nicht publizierbare Revisionsmarke `m13-working-tree`;
erst CI darf die Images des nachfolgenden exakten Git-Commits signieren und
veröffentlichen.

## CI- und Lieferketten-Gate

`.github/workflows/container.yml` führt M13 nach Rollenbuild und M11/M12 aus.
Danach bleiben die bestehenden Gates unverändert: Rootfs-/Secretcheck, Syft-SBOM,
Trivy-CVE-/Secretscan, SLSA-Provenance, reproduzierbarer Doppelbuild, keyless
Cosign-Signatur und Registry-Verifikation aller drei Rollendigests.

Ein lokal erfolgreiches Image ist noch kein signiertes Release. Der
CI-Signatur-/Attestierungsnachweis kann erst nach Push des exakten Commits grün
sein und bleibt bis dahin offen. Lokale Imagegröße, Buildzeit und Startzeit sind
oben reproduzierbar belegt; die veröffentlichten Commitimages werden in CI erneut
im vorhandenen M7-Artefaktformat erfasst und gegen diese Werte sichtbar gemacht.

## Getrennter produktiver read-only Canary

Erst nach grünem signierten Testimage darf Jan getrennt die folgenden
Gesprächsfälle prüfen: verifizierte Produktversion, Runtime-/Jobstatus,
Mail-Positivtreffer, belegter Mail-Nulltreffer mit vollständiger Coverage,
Nextcloud-Liste, Kontakt-/Kalender-/Aufgabenstatus, Holdings und ein absichtlich
unvollständiges Suchergebnis. Der Agent muss jeweils das native Tool in der
Aktivitätsanzeige verwenden und Einschränkungen korrekt ausgeben.

Dieser Canary aktiviert keinen Job und führt keine produktive Schreibaktion aus.
Write-Tests bleiben auf den hermetischen Integrationsvertrag begrenzt. M13
aktiviert insbesondere nicht automatisch den M12-Mailindex.

## Rollbackgrenze

Bei Plugin-Ladefehler, Guard-Fehler, Tooldrift oder unerwarteter Schreibwirkung
wird der Kandidat nicht aktiviert beziehungsweise auf das vorherige signierte
Image zurückgerollt. Ein Image-Rollback ersetzt weiterhin keinen Restore bereits
erfolgter Remoteänderungen. Da der M13-Canary read-only ist, darf er selbst keinen
solchen Restorebedarf erzeugen.
