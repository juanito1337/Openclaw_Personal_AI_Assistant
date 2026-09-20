# M16.9 – Semantic-Search- und Tool-Executor-Entscheidung

Stand: 2026-09-21. Beide Implementierungen sind hermetische
Architekturprototypen. Sie sind weder im Toolkatalog noch in Compose, Gateway,
Scheduler oder einem produktiven Job registriert.

## Semantic Search

Das synthetische Evalset bildet anonymisierte Suchmuster als exakte,
kontextuelle, synonyme und negative Fälle ab. Es vergleicht Lexik,
Thread-/Tagkontext, einen rein lokalen digestgebundenen Modellprototyp und eine
Hybridwertung. Für jede Variante werden Precision, Recall, korrekte Abstention,
Fehlklassifikationen, p50/p95, deterministische Rechenoperationen und
Vektorspeicher berichtet.

Der aktuelle synthetische Vertrag weist für Hybrid-Retrieval keinen
qualitätsneutralen Mehrwert gegenüber Lexik nach: Recall steigt von 0,50 auf
0,666667, gleichzeitig sinkt Precision von 0,75 auf 0,666667. Negative Fälle
werden in beiden Verfahren vollständig abgelehnt. Die Aktivierungsentscheidung
lautet deshalb `disabled`. Außerdem ist eine synthetische Messung niemals
Zielhardwareevidenz.

Der Modellprototyp ist an Name, Dimension und vollständigen SHA-256-Digest
gebunden. Er sendet keinen Inhalt nach außen. Der abgeleitete Index liegt in
einer eigenen Wurzel, lässt sich deterministisch entfernen und neu aufbauen und
verändert weder Quellmail noch lexikalischen Index. Embeddings bleiben
unvertraute Ableitungen, autorisieren keine Aktion und ersetzen keine aktuelle
serverseitige Locatorprüfung.

Eine spätere Aktivierung braucht ein eigenes Vorhaben mit:

- mindestens zwei lokal installierten, vollständig digestgebundenen Modellen;
- Zielhardwaremessung auf einem datenschutzgerecht gelabelten Evalset;
- vorab definierten Qualitäts-, Latenz-, Speicher- und Abstentiongrenzen;
- getrennt freigegebenem Canary, vollständigem Rebuild und geprüftem Rollback;
- unverändertem lexikalischem Fallback und serverseitiger Revalidierung.

## Privilegiengetrennter Tool-Executor

Das Zielbild trennt das LLM-nahe Gateway von Fach-Secrets und schreibbaren
Domainmounts:

```text
unvertrauter Dialog / Modelloutput
              |
              v
Gateway: Tool-ID + geschlossenes Schema + gebundenes Approval
keine Fach-Secrets, keine RW-Domainmounts
              |
       HMAC-authentisierter, längenbegrenzter AF_UNIX-Frame
              |
              v
rollenbegrenzter Executor: Registry + Policy/Approval + Idempotenz
              |
              v
genau ein Domainconnector mit minimalen Secrets/Mounts/Rechten
```

Der Prototypvertrag enthält Tool-ID, Schemaversion, exakte Argumente, Approval-
Bindung, inhaltsarme Evidenz, Deadline, Idempotenzschlüssel, Nonce,
Request-ID und Signatur. Die Antwort ist größenbegrenzt und weist Blocker sowie
verifizierte Nachbedingung getrennt aus. Unbekannte Tools, fremde Schemata,
Argumentänderung, Approvalabweichung, Replay, abgelaufene Deadline,
Idempotenzkonflikt, Überlast und Executor-Crash enden fail-closed. Ein
Shellfallback existiert nicht.

### Threat Model

| Bedrohung | Prototypnachweis | Verbleibende Produktionsarbeit |
| --- | --- | --- |
| Modell erfindet Tool oder Argument | geschlossene Registry und exaktes Typschema | generierten Katalog anbinden, keine zweite Registry |
| Argument wird nach Approval geändert | HMAC und Approval-Digest binden Tool, Schema, Argumente und Idempotenz | turngebundene Approval-Nonce integrieren |
| Replay oder doppelte Wirkung | Nonce- und Idempotenzsperre | persistenter, transaktionaler Replaystore pro Writer |
| gestohlener Fachschlüssel im Gateway | Gatewayprofil lehnt Fach-Secrets und RW-Mounts ab | Compose-Rollen und Secretrotation separat migrieren |
| Socket-Missbrauch | AF_UNIX, Modus `0600`, Längenlimit und HMAC | Peer-Credentials, Socketowner und Keyrotation ergänzen |
| Executor-Crash | typisierter `executor-unavailable`-Blocker ohne Fallback | Supervisor-/Retryvertrag und ungewisse Writes integrieren |
| Überlast | begrenzte Slots, fail-closed Backpressure | faire rollenbezogene Queue und Deadlinebudget messen |
| Inhaltsabfluss im Audit | nur Digests, Tool-ID, Argumentnamen und Status | vorhandenes Auditformat ohne Payloadmigration anbinden |
| übergroße Antwort | festes Antwortlimit | bestehende Ergebnisprojektion und Digestbeleg integrieren |

Die HMAC im Prototyp ist nur ein hermetischer RPC-Nachweis und kein
Produktionsschlüsselkonzept. Ein produktiver Executor müsste die bestehende
typisierte Toolquelle, Policy, ActionPlans, Approvalbindungen, Audit- und
Nachbedingungsverträge direkt verwenden. Er darf keine zweite Autorität und
keinen allgemeinen Kommandoexecutor einführen.

## Reproduktion

```bash
.venv/bin/python scripts/benchmark_architecture_m169.py
.venv/bin/python -m pytest -q tests/test_architecture_prototypes_m169.py
```

Die maschinenlesbare Evidenz steht in
[`m16.9-architecture-decision-baseline.json`](architecture/m16.9-architecture-decision-baseline.json).
Sie liest keine produktiven Mails, Secrets, Mounts oder Zustände und führt kein
Deployment aus.

