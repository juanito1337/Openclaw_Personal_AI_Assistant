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

## Externe OpenClaw-Plugins

Ausfuehrbare Plugins duerfen im Containerbetrieb nicht aus dem beschreibbaren
Gateway-State geladen werden. Der aktuelle Runtime-Imagevertrag enthaelt Brave
und Signal in den durch `docker/openclaw-plugins/package-lock.json` gesperrten
Versionen. Die Native-zu-Container-Migration fuegt deren read-only Imagepfade in
`plugins.load.paths` ein, prueft die bisherigen Datensaetze in
`installed_plugin_index`, synchronisiert diesen generierten Registrycache
transaktional auf die exakten Versionen, Integritaetswerte und Imagepfade und
entfernt die alten npm-Projektkopien ausschliesslich aus dem privaten Staging.
Originales Legacy-Home und verifiziertes Migrationsarchiv bleiben unveraendert.

Ein weiterer verwalteter oder fremder Plugin-Pfad wird nicht stillschweigend
uebernommen. Die Migration stoppt vor der Publikation, bis das Plugin mit exakter
Version und Integritaet in den Image- und Supply-Chain-Vertrag aufgenommen wurde.
Nach der Migration verhindert `OPENCLAW_NIX_MODE=1` Plugininstallation und
-update im laufenden Container.
