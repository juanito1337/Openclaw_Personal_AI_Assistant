# M12-Baseline: autoritative IMAP-Reconciliation

Stand: 2026-08-23

Diese Baseline trennt reproduzierbare Entwicklungswerte von einer noch nicht
freigegebenen produktiven Messung. Sie enthält keine Mailadressen, Betreffe,
Bodies, Querytexte, Zugangsdaten oder Locator.

## Ausgangszustand M11

Die read-only Bestandsaufnahme des laufenden r28-Stacks ergab vor M12:

- verifizierte Releaseidentität `3.4.0-r28`;
- 22 über Himalaya sichtbare Ordner;
- lokale FTS-Projektion mit 21.371 Zeilen;
- keine vollständige aktuelle Locatorgeneration (`current locators = 0`);
- `search_eligible = false` mit den Gründen `partial-generation`,
  `non-authoritative` und `stale`;
- Himalaya meldete Paging und Raw-Fetch, aber keine belegten UID-,
  UIDVALIDITY-, UIDNEXT-, MODSEQ-, QRESYNC-, IDLE- oder stabilen
  Ordneridentitätswerte.

Damit konnte der alte Pfad positive historische Treffer liefern, aber weder
externe Moves zuverlässig verfolgen noch einen kontoweiten Nulltreffer belegen.

## Reproduzierbare Befehle

Im Quellcheckout:

```bash
./scripts/assistant.sh version --verify
git status --short
./scripts/check-repo.sh
docker compose --env-file docker/deployment.env.example -f compose.yaml config --quiet
```

Im installierten Stack ausschließlich read-only und erst nach Installation eines
M12-Images:

```bash
/opt/openclaw-agent/scripts/assistant.sh mail index status
/opt/openclaw-agent/scripts/assistant.sh mail index doctor
/opt/openclaw-agent/scripts/assistant.sh mail index plan
/opt/openclaw-agent/scripts/assistant.sh mail index capabilities --no-raw-probe
/opt/openclaw-agent/scripts/assistant.sh jobs check --target all --deep
```

Der Capability-Probe gibt nur Capabilityflags, Ordner-/Nachrichtenzahlen,
UID-Minimum/-Maximum, Latenz und gesendete freigegebene Kommandoklassen aus. Der
optionale Raw-Nachweis verwendet genau einen `BODY.PEEK[]`-Abruf, speichert den
Payload nicht und gibt ihn nicht aus. `--no-raw-probe` unterlässt auch diesen
Abruf.

## Entwicklungsbaseline

Die hermetischen Tests belegen:

- TLS bleibt verifizierend und besitzt keinen Unsicher-Schalter;
- der Connector kann ausschließlich `CAPABILITY`, `LIST`, `STATUS`, `EXAMINE`,
  `UID SEARCH`, `UID FETCH` und `LOGOUT` ausdrücken;
- alle bekannten Schreibkommandos werden vor dem Transport abgewiesen;
- UID-Paging, modifiziertes UTF-7 und UIDVALIDITY-Races sind abgedeckt;
- Capabilityberichte enthalten keine gelesenen Header oder Bodies.

Der zentrale M0-Testpfad sammelt nach M12 insgesamt 942 Tests, davon 711
unittest-kompatible Items und weiterhin alle 13 freien Rechnungs-pytest-Tests.
Zusammen mit 87 unittest-Subtests entstehen 1.029 JUnit-Fälle. Die gemessene
Gesamt-Coverage einschließlich Branches beträgt 67,52 Prozent; die reine
Branch-Coverage beträgt 54,33 Prozent. Diese Werte sind Beobachtungen, keine neu
eingeführten willkürlichen Qualitätsgrenzen; nur eine kleinere Collection wird
fail-closed abgewiesen.

Das installierbare M12-Wheel wurde in einer frischen Umgebung mit dem gleichen
Testpfad geprüft: 576.302 Bytes, 2,487 Sekunden Buildzeit sowie 942 Tests und 87
Subtests in 96,65 Sekunden. Wheel und entpackter Produktbaum bestanden den
Artefaktscan ohne produktive Konfiguration, Secrets oder Laufzeitdaten.

Die drei lokalen Rollenimages aus dem Entwicklungscommit wurden mit dem
gepinnten Buildpfad gebaut und geprüft. Gemessene komprimierte Docker-Größen:
Runtime 377.021.636 Bytes, Proxy 23.420.998 Bytes und Maintenance 45.631.874
Bytes. SBOM, Provenance, Rootfs-Artefaktscan und Trivy meldeten in allen drei
Rollen null Secrets und null kritische CVEs. Der lokale Cache-Build dauerte rund
88 Sekunden; das ist kein sauberer Erstbuild und daher kein Build-SLA.

## Noch offene reale Messwerte

M12 führt im Entwicklungsauftrag weder produktiven Backfill noch Reconcile oder
Jobstart aus. Folgende Werte bleiben daher bis zum getrennt freizugebenden Canary
offen und dürfen nicht als bestanden dargestellt werden:

- reale Serverfähigkeiten (CONDSTORE, QRESYNC, IDLE, OBJECTID/Mailbox-ID);
- No-op-, Move-, Copy-, Delete- und Full-Snapshot-Latenzen am Zielkonto;
- reale Netzbytes, Peak-RAM, Projektionsgröße und Vollbackfillzeit;
- produktiver Locator-Coveragegrad und Reconcile-SLA.

Die Anfangsintervalle werden deshalb nicht als Qualitätsgrenze festgeschrieben.
Der Rollout misst sie zunächst im Canary und dokumentiert eine spätere Anpassung
als eigene Betriebsentscheidung.

## Erster produktiver Canary-Befund

Der am 26. August 2026 ausdrücklich freigegebene Ordner-Canary für
`Agent/Relevant` brach vor einer Abnahme mit
`IMAP-Gesamtlaufzeitlimit erreicht` ab. Ursache war kein Providerwrite und kein
600-Sekunden-Budgetverbrauch: Der bereits validierte CLI-Wert wurde nicht an den
nativen Connector weitergereicht, dessen unabhängiger Standard deshalb nach 120
Sekunden auslöste. Gleichzeitig blockierte der Canary den planmäßigen Maillauf
korrekt über den gemeinsamen Single-Writer-Lock; dessen erwarteter Exitcode wurde
jedoch fälschlich als Dienstausfall gewertet.

Beide Befunde sind im Quellstand durch Verhaltensregressionen korrigiert. Eine
erneute produktive Canary-Abnahme bleibt bis zur Installation und Verifikation
des daraus gebauten signierten Images ausdrücklich offen.
