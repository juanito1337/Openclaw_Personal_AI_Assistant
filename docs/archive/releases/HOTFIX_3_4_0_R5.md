# Hotfix 3.4.0-r5

## Behoben

- Doppelte `resource.id` blockieren den Assistant nicht mehr.
- `resources.toml` wird vor dem Import des Assistant-Cores standalone und atomar bereinigt.
- Nextcloud-Discovery ersetzt generierte Ressourcen idempotent.
- Der Installer benoetigt kein `pytest` und installiert keine Pakete.

## Sicherheitsverhalten

- Vollbackup des Workspace vor jeder Aenderung.
- Separates Backup der Registry vor der Deduplizierung.
- Letzte Definition einer doppelten ID bleibt erhalten.
- Automatischer Rollback bei jedem fehlgeschlagenen Test.
- Sync-Timer bleibt bis zur manuellen Validierung deaktiviert.
