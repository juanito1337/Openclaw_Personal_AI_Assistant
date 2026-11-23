# Konsolidierung des Entwicklungsstands

Datum: 23. November 2026. Auftrag: vorhandene Entwicklung zu einer eindeutigen,
verlustfreien Arbeitsgrundlage konsolidieren. Ausgangspunkt: `807c374`.

## Entscheidung und Umfang

Die bestehenden Oktober-/Novemberzweige bilden bereits eine aufeinander
aufbauende Historie. Der jüngste Stand wird als Basis eines eigenen
Konsolidierungszweigs verwendet. Ein erneutes Cherry-pick würde vorhandene
Änderungen duplizieren; das Kopieren des alten Arbeitsordners würde teilweise
neuere Implementierungen zurücksetzen. Deshalb nur tatsächlich einzigartige
lokale Änderungen übernehmen, falls der Inhalts-/Modusvergleich solche findet.

Der Nutzer hat die Konsolidierung beauftragt. Routineentscheidungen erfolgen
im bestehenden Git-/Releasevertrag. Vorhandene Arbeitsordner bleiben erhalten.
Neue Funktionen, Deployment, Jobsteuerung und Änderungen von Rechten gehören
nicht zu diesem Auftrag.

## Ausführung

1. Alle Entwicklungszweige, Worktrees und nicht ignorierten lokalen Änderungen
   inventarisieren; Remote-Referenzen abrufen. Basis und Vorfahren vergleichen.
   Erwartung: jede lokale Änderung ist erklärt; einzigartige Änderungen bleiben
   erhalten und werden ausdrücklich integriert.
2. Isolierten Worktree auf dem jüngsten vollständigen Stand anlegen und
   `codex/consolidate-development-20261123` erstellen. Keine Quellkopie aus einem
   älteren Worktree über die neue Basis legen.
3. [Aktuelle Übersicht](../../DEVELOPMENT_STATUS.md) mit Git-Herkunft,
   Novemberfortschritt, datierten Entwicklungs-/Betriebsbelegen und offenen Punkten
   erstellen. README und Roadmap dorthin verlinken; Rechnungslesevertrag präzisieren.
4. Komponenten-/Dateiinventar und Quellmanifest nach Dokumentationsänderungen
   aktualisieren. Versions-, Dokumentations-, Repository-, Wheel- und
   Composeprüfungen sowie `git diff --check` ausführen. Ergebnisse und Grenzen
   in der Übersicht festhalten.
5. Unabhängigen Review der Konsolidierung durchführen, Befunde berichtigen,
   abschließend Manifest/Links/Diff prüfen und den konsolidierten Stand committen.

## Review-Schwerpunkte

- Kein einzigartiger lokaler Quellstand verloren; keine alte Version zurückkopiert.
- Tatsächliche Git-Abstammung statt Gleichsetzung anhand von Branchnamen.
- Datierte Entwicklungsabnahme, berichtete Auslieferung und aktuelle Prüfung
  bleiben unterscheidbar; keine erfundene heutige Betriebsfreigabe.
- Zugriffs-, Datenbank-, Freigabe- und Laufzeitverträge bleiben unverändert.
- Neue Dokumente sind erreichbar; Inventar und Manifest stimmen mit dem Quellbaum.

## Ergebnisprotokoll

- Bestandsaufnahme: 151 alte lokale Pfade sind in Basis oder Vorfahren enthalten;
  keine eigenständige Änderung. Die übrigen bestehenden Worktrees sind sauber.
- Remote-Abgleich: `origin/main` bei `a8bc173`, Entwicklungs-Testbranch bei
  `807c374`. Die drei Novembercommits nach Main bilden eine lineare Fortsetzung.
- Entscheidung: Der alte Hauptordner bleibt als historischer Arbeitsstand erhalten.
  Seine Bereinigung wäre nicht erforderlich, um den vollständigen neuen Stand
  bereitzustellen, und würde zusätzliche Benutzerdateien berühren.
- Lokale Zeiger von `main` und `test/codex-nextcloud-talk-diagnostics-20261115`
  nach bestätigter Abstammung ohne Force auf die veröffentlichten Remote-Stände
  vorgespult; keine Serverreferenz verändert.
- `check-repo.sh`: Exit 0, 2.348 Tests, zwei optionale Image-Skips, 74,17 %
  Abdeckung, Risikogates und drei Wiederherstellungsproben erfolgreich.
- `check-wheel.sh`: Exit 0, 2.348 Tests und 301 Untertests, zwei optionale
  Image-Skips; frische Paketinstallation geprüft.
- Unabhängiger Review: keine wichtigen Befunde. Der Hinweis auf den veralteten
  lokalen Testzweig wurde durch Vorspulen und Dokumentation seines Stands behoben.
  Die allgemeine Prüfung des unveränderten Laufzeitcodes und der Livebetrieb
  waren nicht Teil dieses Dokumentations-/Git-Reviews; der Diff bestätigt den
  fehlenden Laufzeiteingriff, die Tests prüfen den vorhandenen Stand.
- Nach dem Gesamtlauf nur Ergebnisdokumentation und abgeleitete Daten ergänzt;
  abschließende Links-/Inventar-/Manifest-/Versions-/Compose-/Diffprüfung.
  Die alten Arbeitsordner bleiben erhalten; Main-Promotion und erneutes
  Deployment sind nicht Teil dieses Auftrags.
