# Migrationen

Der produktive Container-Migrations- und Rollbackpfad ist im
[Docker-Betrieb](DOCKER_DEPLOYMENT.md) beschrieben. Regeln fuer neue Daten- und
Schemamigrationen stehen in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

Die alte native Migration liegt als historischer Stand unter
[`archive/MIGRATION_PRE_CONTAINER.md`](archive/MIGRATION_PRE_CONTAINER.md).

## Unterstuetzte direkte Upgradegrenze

Der aktuelle Stack unterstuetzt direkte Upgrades ab `3.4.0-r26.1`. Die
maschinenlesbare Policy und das neutrale Parser-Fixture liegen unter
[`architecture/compatibility-policy.json`](architecture/compatibility-policy.json)
und `tests/fixtures/upgrade/r26.1/`. Aeltere Installationen muessen zunaechst mit
ihrem historischen Release bis r26.1 migriert werden; sie duerfen die entfernten
Einmalskripte nicht aus einem aktuellen Checkout nachladen.

Diese Untergrenze entfernt keine Datenmigration: SQLite-Schemamigrationen,
Container-State-Migration und verifizierte Legacy-Archive bleiben verpflichtend.
