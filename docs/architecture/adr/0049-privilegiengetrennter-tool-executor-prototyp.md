# ADR-0049: Privilegiengetrennter Tool-Executor bleibt Prototyp

- Status: Accepted
- Datum: 2026-09-21
- Entscheider: Security Maintainers, Tool Contract Maintainers, Operations Maintainers
- Bezug: M16.9, ADR-0002, ADR-0009, ADR-0036, ADR-0039

## Kontext

Das LLM-nahe Gateway orchestriert typisierte Werkzeuge. Ein kompromittierter
Dialog-, Modell- oder Gatewaypfad darf daraus keinen Zugriff auf sämtliche
Fach-Secrets, schreibbare Domainzustände oder einen allgemeinen Shellpfad
ableiten. Eine Prozessgrenze hilft nur, wenn Authentisierung, Schema, Approval,
Idempotenz, Audit, Deadline und Überlastverhalten ebenfalls geschlossen sind.

## Entscheidung

M16.9 definiert ausschließlich einen hermetischen RPC-Prototyp. Das Gatewayziel
besitzt keine Fach-Secrets und keine schreibbaren Domainmounts. Es übergibt über
einen mit `0600` geschützten AF_UNIX-Socket einen längenbegrenzten, HMAC-
authentisierten Frame. Der kanonische Request bindet Tool-ID, Schemaversion,
Argumente, Approval, Evidenz, Deadline, Idempotenz, Nonce und Request-ID.

Der Executor akzeptiert nur registrierte Tool-IDs mit geschlossenem Typschema
und exakt gebundenem Approval. Replay, Idempotenzkonflikt, fremde Signatur,
fremdes Tool/Schema, geänderte Argumente, Deadline, Backpressure, Crash und
übergroße Antwort werden als typisierte Blocker ohne Shellfallback behandelt.
Das Audit speichert keine Argumentwerte oder Evidenzinhalte.

Der Prototyp wird nicht in Toolkatalog, Compose, Gateway, Scheduler oder
Produktionsdeployment registriert. Er ist keine zweite Toolquelle und keine
neue Autorität.

## Alternativen

- Gateway mit allen Fach-Secrets und RW-Mounts: verworfen wegen zu großem
  Blast Radius.
- Allgemeiner Shell-/HTTP-Executor: verworfen, weil Tool-, Policy- und
  Approvalverträge umgangen würden.
- Netzwerk-RPC ohne lokale Socketgrenze: verworfen, solange kein zusätzlicher
  Host-/Netzzugriff fachlich nötig ist.
- Sofortige Produktivmigration: verworfen; persistenter Replaystore,
  Peer-Credentials, Keyrotation, Writer-/Crashsemantik und Rollback fehlen.

## Rollout- und Rückbaugrenze

Ein Folgevorhaben muss die generierte Registry direkt verwenden, pro Rolle
minimale Secrets/Mounts nachweisen und ActionPlan, Approval, Audit,
Nachbedingung sowie ungewisse externe Writes unverändert erhalten. Erst ein
separater Container-Canary darf Compose ändern. Rückbau entfernt Socket,
Executorrolle und RPC-Credential; das Gateway kehrt auf den bereits getesteten
nativen Router zurück. M16 führt keinen dieser Schritte aus.

