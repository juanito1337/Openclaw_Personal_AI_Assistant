# Hotfix 3.4.0-r9: Remote-verifizierte Idempotenz

## Problem

Workspace-Aktionen vertrauten bei `status=completed` ausschliesslich SQLite. Ein extern fehlendes Nextcloud-Ziel wurde nicht erkannt.

## Loesung

`ActionService.execute_workspace()` verifiziert die Nachbedingung in Nextcloud, bevor eine Aktion als Dublette gilt.

- Fehlende create-only Ordner und Dateien werden erneut angelegt.
- Dateien mit identischem SHA-256 gelten als bestaetigte Dublette.
- Abweichende Zieldateien werden nicht ueberschrieben.
- Moves werden ueber Quelle und Ziel verifiziert.
- Reconciliation wird im Audit protokolliert.

Die Schutzgrenzen von r8 bleiben unveraendert: kein Loeschen, kein Ueberschreiben und kein Zugriff ausserhalb `Assistent/`.
