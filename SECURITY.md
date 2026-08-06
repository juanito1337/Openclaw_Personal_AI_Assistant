# Sicherheitsrichtlinie

OpenClaw verarbeitet private Mail-, Kalender-, Kontakt-, Aufgaben-, Finanz- und
Nextcloud-Daten. Sicherheitsprobleme werden deshalb ohne produktive Datenkopien und
ohne oeffentliche Offenlegung untersucht.

## Schwachstellen melden

Melde einen Verdacht privat an den Repository-Owner. Eine Meldung enthaelt Version,
betroffene Komponente, reproduzierbare Schritte mit synthetischen Daten, erwartete
und beobachtete Sicherheitsgrenze sowie moegliche Auswirkungen. Keine echten
Credentials, Tokens, Mailinhalte, Datenbanken oder `/srv/openclaw`-Archive anhaengen.

Bei vermutetem Secret-Leak: Nutzung stoppen, Secret ueber den jeweiligen Provider
rotieren, betroffene Logs/Artefakte lokal sichern und erst danach die Ursache
analysieren. Secrets werden nicht in Issues, Chat, Commits oder Testfixtures kopiert.

## Unterstuetzter Stand

Sicherheitskorrekturen gelten fuer den aktuellen `main`-Stand und die in
`RELEASE.json` ausgewiesene installierte Releasefamilie. Historische Dateien unter
`docs/archive/` sind keine Sicherheitszusage. Die autoritative installierte Version
wird immer mit `./scripts/assistant.sh version --verify` bestimmt.

## Unverhandelbare Grenzen

- genau ein produktiver Writer je externer Schreibdomaene,
- keine parallelen systemd- und Container-Mailwriter,
- kein Secret in Git, Image, Log, Prompt, Memory oder Nextcloud,
- nur digest-gepinnte, SBOM-/SLSA-attestierte und fuer den erwarteten Git-Commit
  signierte Rollenimages duerfen die Deploymentgrenze passieren,
- ClamAV fail-closed fuer Mailattachments und kontrollierte Uploads,
- Remote-Writes nur ueber registrierte Tools, Ressourcerechte, Policy, Approval,
  Idempotenz und Audit,
- kein autonomes Delete, Overwrite, Share oder Permission-Expansion,
- verifiziertes lokales Backup vor write-faehigen Deployments,
- keine Behauptung, ein lokaler Image-/State-Rollback stelle Remoteaenderungen wieder
  her.

Technische Details und bekannte offene Isolationsluecken stehen unter
[`docs/architecture/TRUST_BOUNDARIES.md`](docs/architecture/TRUST_BOUNDARIES.md) und
in der [Rollenmatrix](docs/architecture/CONTAINER_ROLES.md). Die Softwarelieferkette
und die harte Critical-CVE-Sperre beschreibt
[`docs/architecture/IMAGE_SUPPLY_CHAIN.md`](docs/architecture/IMAGE_SUPPLY_CHAIN.md).

## Security-Review ausloesen

Zusaetzliches Review ist Pflicht bei Aenderungen an Secrets, Mounts, Netzwerken,
Containerusern/capabilities, Policy, ActionPlan, Tool-Approval, ETag/Idempotenz,
Antivirus, Parsern fuer unvertraute Daten, Mailversand/-verschiebung,
Nextcloud/CardDAV/CalDAV/Deck-Writes, Backup, Migration oder Rollback.

Der Review muss Missbrauchsfaelle, negative Tests, Fail-closed-Verhalten,
Datenminimierung und Wiederherstellung dokumentieren. Sicherheitskontrollen duerfen
nicht pauschal unterdrueckt werden; eng begrenzte Altbefunde benoetigen eine
quellgebundene, nicht wachsende Baseline.
