# ADR 0043: stabile Ressourcenidentitaet statt paralleler Selektoren

- Status: Accepted
- Datum: 2026-09-20

## Kontext

Der direkte Kalender und der historische Mail-Kalenderpfad konnten verschiedene
Selektoren tragen. Ein exakter read-only Fallback verhinderte zwar einen Write auf
einen ungeprueften Kalender, liess aber einen veralteten Pfad dauerhaft bestehen.
Weitere Domaenen verwendeten dieselbe Registry, beschrieben ID, fachlichen Namen,
Komponente, Remote-Identifier und Rechte jedoch nicht in einem gemeinsamen
Vertrag. Insbesondere durfte eine doppelte Registry-ID nicht laenger nach dem
Prinzip „letzte Definition gewinnt“ in einen operativen Write gelangen.

## Entscheidung

Alle konfigurierten Domaenenreferenzen werden durch einen zentralen typisierten
Resolver inventarisiert. Operative Aufloesung erfolgt ausschliesslich ueber eine
eindeutige stabile `resource_id`. Fachlicher Name, Unterpfad und Remote-Identifier
sind Metadaten und keine fuzzy Auswahlschluessel. Doppelte, fehlende oder stale
IDs sowie fehlende Komponenten, Credentials und Rechte haben getrennte Codes und
bleiben fail-closed.

Mail- und Direktkalender muessen dieselbe ID referenzieren. Der untypisierte
Legacy-Selektor wird nicht mehr aufgeloest. Die einmalige lokale Angleichung ist
eine preview- und digestgebundene Konfigurationsmigration mit Backup,
Nachvalidierung, atomarer Publikation und explizitem Rollback. Sie veraendert
weder Registryrechte noch externe Ressourcen.

## Konsequenzen

- Status, Doctor und der eigene Ressourcenstatus verwenden dieselbe Inventarsicht.
- Discovery darf nie automatisch den ersten oder einen fuzzy Treffer auswaehlen.
- Ein genauer Diagnose-Fallback kann Drift sichtbar machen, aber keinen gesunden
  Betriebszustand vortaeuschen.
- Registryduplikate bleiben fuer ein separates Reparaturwerkzeug lesbar, werden
  aber fuer normale `get`- und Schreibpfade blockiert.
- Ressourcen- oder Rechteauswahl bleibt ein eigener expliziter Setupvorgang.

