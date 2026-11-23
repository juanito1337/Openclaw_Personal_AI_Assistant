# Aktueller Entwicklungsstand

Stand: **23. November 2026**. Diese Übersicht ist der Einstieg für die weitere
Entwicklung. Datierte Abnahmeberichte bleiben Nachweise ihres jeweiligen Laufs;
sie ersetzen keine aktuelle Prüfung der Installation.

## Aktuelle Fehlerkorrektur

Auf dem konsolidierten Commit `55e5003` entsteht im Nachfolgezweig
`codex/fix-calendar-day-evidence-20261123` eine begrenzte Korrektur für
„Welche Termine habe ich heute?“. Die Liveprüfung vom 23. November zeigte
funktionierenden Kalenderzugriff, aber ein allgemeines Listing mit 100 Zeilen
und `complete=false`. Daraus folgt keine Aussage über einen leeren heutigen Tag.
Die zusätzliche Tagesabfrage und ihre Nachweisgrenzen sind in
[ADR 0052](architecture/adr/0052-belegte-kalender-tagesabfrage.md) dokumentiert.
Die Korrektur ist lokal implementiert und unabhängig geprüft. Repository- und
Wheel-Abnahme bestehen mit jeweils 2.440 erfolgreichen Tests und zwei optionalen
Image-Skips; der Repositorylauf misst 74,37 % kombinierte Abdeckung. Die drei
isolierten Recovery-Szenarien bestehen ebenfalls. Die nachfolgenden älteren
Konsolidierungsnachweise belegen den Ausgangsstand. Container-Abnahme und
produktive Aktivierung bleiben getrennte Schritte; diese Änderung aktiviert
keinen produktiven Dienst.

## Maßgeblicher Quellstand

Die Konsolidierung baut auf `807c374437ff470f1ce81e171664c466f9e247f0`
vom 22. November auf. Der Arbeitszweig heißt
`codex/consolidate-development-20261123`. Sein Ausgangsstand enthält die
bisherigen Oktober- und Novemberentwicklungen einschließlich Rechnungslesepfaden,
Nextcloud-Zugriffsstufen und Komponentenregister. Die Konsolidierung ergänzt die
Dokumentation und abgeleitete Inventar-/Manifestdaten; sie ändert kein
Laufzeitverhalten.

Der Remote-Abgleich am 23. November ergab:

| Referenz | Commit | Einordnung |
| --- | --- | --- |
| `origin/test/codex-nextcloud-talk-diagnostics-20261115` | `807c374` | Veröffentlichte Entwicklungsbasis dieser Konsolidierung |
| `origin/main` | `a8bc173` | Enthalten; drei nachfolgende Entwicklungscommits noch nicht auf Main |
| lokales `main` | `a8bc173` | Ohne Force von `8e63d9f` auf Remote-Main vorgespult |
| lokales `test/codex-nextcloud-talk-diagnostics-20261115` | `807c374` | Ohne Force von `01e144f` auf den veröffentlichten Teststand vorgespult |
| `feature/m17-nextcloud-talk` | `43a3952` | Alter Arbeitszweig; vollständig in der Entwicklungsbasis enthalten |
| `test/nextcloud-setup-20261022` | `bb5d45a` | Enthaltener Zwischenstand |
| `test/nextcloud-launch-fix-20261024` | `83f6caa` | Enthaltener Zwischenstand |
| `test/nextcloud-setup-diagnostics-20261025` | `7285a21` | Enthaltener Zwischenstand |
| `codex/sigstore-local-transport-20261119` | `a8bc173` | Enthaltener Zwischenstand |
| `codex/invoice-read-evidence-20261121` | `807c374` | Vollständiger bisheriger Entwicklungsstand |

Die drei Commits gegenüber Remote-Main sind `0bad341` (Rechnungslesepfade und
Fehlerbelege), `d38045b` (Nextcloud-Zugriff und Komponentenregister) und
`807c374` (Image-Smokes und isolierte Transportfixtures). Ein erneutes
Cherry-pick dieser Arbeiten ist nicht erforderlich. Der ältere Portfolio-Testzweig
`origin/test/portfolio-csv-nextcloud` liegt außerhalb der direkten Abstammung;
seine fachliche Änderung ist bereits mit `7c74f6f` übernommen worden. Der
Patchvergleich zeigt nur abweichenden Changelog-Kontext.

## Abgleich der vorhandenen Arbeitsordner

Der alte Hauptarbeitsordner auf `feature/m17-nextcloud-talk` enthielt
108 geänderte versionierte Dateien und 43 neue, nicht ignorierte Dateien.
Verglichen wurden Dateiinhalt und Git-Dateimodus mit der Entwicklungsbasis
und deren Vorfahren:

| Gruppe | Identisch zur Basis | Identisch zu älterem enthaltenen Commit | Eigenständige Änderung |
| --- | ---: | ---: | ---: |
| Geänderte versionierte Dateien | 64 | 44 | 0 |
| Nicht versionierte Dateien | 24 | 19 | 0 |
| Gesamt | 88 | 63 | 0 |

Die drei älteren Build-Worktrees und der November-Worktree waren sauber.
Es musste kein Quelltext aus den alten Arbeitsordnern übernommen werden.
Insbesondere dürfen deren älteren Dateiversionen den neuen Stand nicht
überschreiben. Die alten Ordner und Zweige bleiben erhalten; sie sind keine
weiteren aktiven Entwicklungsgrundlagen. Ignorierte Dateien, lokale Laufzeitdaten
und Zugangsdaten waren nicht Teil des Vergleichs.

Für neue Arbeiten den Konsolidierungszweig beziehungsweise dessen Nachfolger
verwenden. Der zu diesem Chat gehörende Worktree heißt
`development-consolidation/Openclaw_Personal_AI_Assistant`. Es wurde weder ein
alter Arbeitsordner zurückgesetzt noch ein historischer Zweig gelöscht.

## Umgesetzt und dokumentiert

| Bereich | Vorhandener Entwicklungsstand | Nachweis und Grenzen |
| --- | --- | --- |
| Container und Fachfunktionen | Getrennte Prozesse/Rollen, Mail, Groupware, Rechnungen, Portfolio und kontrollierte Werkzeuge | [Architekturvertrag](architecture/README.md); keine neue Betriebsabnahme durch diese Übersicht |
| M18 Zuverlässigkeit | Sichere Synchronisation, Modellkoordination, mehrstufige Einzelaktionen, begrenzter Laufzustand und schlankere CLI | [Abnahme vom 13. Oktober](AGENT_IMPROVEMENT_ACCEPTANCE_M18.md); 1.573 damalige Tests |
| Nextcloud-Einrichtung und Talk | Kontoanmeldung, Ressourcenerkennung, App-Passwort und getrennte Zugriffs-/Talk-Diagnosen | [Einrichtung](NEXTCLOUD_SETUP_UI.md), [Diagnosevertrag](INTEGRATION_DIAGNOSTICS.md) |
| Rechnungslesepfade | Status, Liste, Review und Audit ohne implizite Anlage/Migration einer Datenbank; fehlende Archivzuordnung separat sichtbar | Commit `0bad341`, [Rechnungsvertrag](INVOICE_OCR_REGISTER.md) |
| R0 und U0 | Kategorienparser korrigiert; abgeleitetes Komponentenregister mit 86 Einträgen | [Foundation-Abnahme](NEXTCLOUD_FOUNDATION_VALIDATION.md), [Updateinventar](architecture/EXTERNAL_COMPONENTS.md) |
| NC0 bis NC2 | Getrennte Diagnosen, dauerhafte Ressourcenrichtlinie und Zugriffsstufen mit Vorschau/Bestätigung im OpenClaw-Plugin | [Nextcloud-Roadmap](NEXTCLOUD_ACCESS_ROADMAP.md), [Foundation-Abnahme](NEXTCLOUD_FOUNDATION_VALIDATION.md) |
| Auslieferung | Verifizierbarer lokaler Sigstore-TUF-Transport und aktualisierte Image-/Transportfixtures | Commits `a8bc173` und `807c374`; keine neue Veröffentlichung durch diese Konsolidierung |

## Letzter berichteter Betrieb

Der Entwicklungsverlauf im Chat „OpenClaw-Containerstart prüfen“ meldet für
**22. November 2026** die Veröffentlichung und Docker-Auslieferung von `807c374`,
ein verifiziertes lokales Backup mit Wiederherstellungsprobe und zehn gesunde
Container. Die anschließende Nachprüfung meldete:

- Nextcloud und Talk verbunden; Raum und erlaubter Benutzer geprüft.
- Kalender, fünf Aufgabenlisten, vier Adressbücher und den ausgewählten Ordner
  erfolgreich geprüft.
- Zugriffsstufen im Browser samt Vorschau geprüft, ohne neue Freigaben zu speichern.
- Mailjob nach gesonderter Zustimmung wieder eingeschaltet; frischer Workerstatus.

Dies ist ein **datierter Betriebsbericht aus dem genannten Chat**, keine
Liveprüfung vom 23. November. Eine Talk-Testnachricht wurde dabei nicht gesendet.
Die Ursache, warum der Mailjob nach dem Update zunächst OFF war, blieb offen.
Die früheren Aussagen „keine produktive Aktivierung“ in Entwicklungsberichten
beschreiben deren damaligen Auftragsumfang und widerlegen diesen späteren
Betriebsbericht nicht.

## Prüfung dieser Konsolidierung

Am 23. November wurden folgende Prüfungen frisch ausgeführt:

| Prüfung | Ergebnis |
| --- | --- |
| Inhalts-/Modusvergleich alter lokaler Quellen | 151 Pfade erklärt, keine einzigartige Änderung und kein ausgeschlossener geschützter Pfad |
| `./scripts/assistant.sh version --verify` | Paketidentität und Quellmanifest erfolgreich geprüft; keine Aussage zur installierten Runtime |
| `./scripts/check-repo.sh` | Exit 0; 2.348 Tests bestanden, zwei optionale Image-Tests übersprungen; Testlauf 622,17 Sekunden |
| Coverage und statische Analyse | 74,17 % kombinierte Abdeckung; alle Risikogates erfüllt; keine neuen Ruff-/mypy-Befunde gegenüber den bestehenden Baselines |
| `./scripts/check-wheel.sh` | Exit 0; frisch installiertes Wheel mit 2.348 Tests und 301 Untertests erfolgreich, zwei optionale Image-Tests übersprungen; Testlauf 526,67 Sekunden |
| M8-Wiederherstellung | Drei isolierte synthetische Bestände exakt wiederhergestellt; kein Verlust im jeweiligen Snapshot enthaltener lokaler Operationen |
| Dokumentation, Komponentenregister, Inventar und Compose | Links, abgeleitete Verträge und beide Compose-Konfigurationen erfolgreich geprüft |
| Unabhängiger Konsolidierungsreview | Keine wichtigen Befunde; Hinweis auf veralteten lokalen Testzweig durch sicheres Vorspulen behoben |

Die beiden Image-Skips betreffen Setup-Owner und Talk-Proxy ohne eigens
vorgegebene Testimages. Es wurde kein neuer Imagebuild und kein aktueller
Livekontotest ausgeführt. Das ist keine vollständige neue Releaseabnahme.
Nach den Gesamtläufen wurden nur diese Ergebnisdokumentation und die
Zweignavigation ergänzt; Links, Inventar, Manifest, Versionsprüfung, Compose
und Diff wurden danach erneut geprüft. Der Laufzeitcode blieb unverändert.

Die vollständigen lokalen Protokolle liegen im ignorierten Verzeichnis
`build/consolidation/`: `check-repo.log`, `check-wheel.log`, `source-audit.json`,
`source-audit.py`, `git-refs.txt` und `portfolio-equivalence.txt`. Das Audit lässt
sich dort mit `python3 build/consolidation/source-audit.py --root <alter-worktree>
--target 807c374437ff470f1ce81e171664c466f9e247f0` wiederholen. Historische
Testzahlen und überlappende Läufe werden nicht addiert. Die vorhandenen
historischen Messwerte im Komponenteninventar wurden erhalten.

Der [Konsolidierungsplan](superpowers/plans/2026-11-23-development-consolidation.md)
hält Vorgehen, Umfang und Review-Schwerpunkte fest.

## Verbleibende Arbeit

- Ursache des nach dem letzten Update ausgeschalteten Mailjobs reproduzieren
  und den Erhalt des gewünschten Jobzustands prüfen.
- Den vollständigen Talk-Dialog einschließlich Zustellung und Freigaben in einem
  ausdrücklich freigegebenen Testszenario abnehmen; Verbindung allein genügt nicht.
- NC3 bis NC8 nach der [Nextcloud-Roadmap](NEXTCLOUD_ACCESS_ROADMAP.md): weitere
  Fachkonfiguration, Serverfreigaben, Benutzertrennung und zugehörige Bedienung.
- U1 bis U4 der [Update-Roadmap](STACK_UPDATE_ROADMAP.md) und Übernahmepakete
  R1 bis R6 aus dem [Funktionsvergleich](NEXTCLOUD_REUSE_COMPARISON.md).
- Das vorgeschlagene Agenten-Testlabor mit gemeinsamen Nutzerszenarien,
  simulierten Diensten und ergänzenden echten Modellläufen umsetzen.
- Allgemeine Mehrfachaufträge benötigen weiterhin einen strukturierten Planer;
  die vorhandene Absicherung mehrstufiger Einzelaktionen ersetzt ihn nicht.
- Main-Promotion und künftige Auslieferungen nach dem bestehenden Releasevertrag
  separat durchführen. Diese Konsolidierung aktiviert keine Jobs oder Rechte.
