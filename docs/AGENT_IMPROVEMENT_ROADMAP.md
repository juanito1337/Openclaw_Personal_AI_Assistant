# M18 Verbesserungen an Effizienz und Zuverlaessigkeit des Agenten

Stand: 2026-10-13. Arbeitsstand: `feature/m17-nextcloud-talk`.
Status: M18 lokal umgesetzt und entwicklungsseitig abgenommen. Keine produktive Aktivierung.

Aktuelle Messwerte, Testmatrix und Grenzen stehen in der
[M18-Entwicklungsabnahme](AGENT_IMPROVEMENT_ACCEPTANCE_M18.md).

Diese datierte M18-Abnahme bezieht sich auf die unten aufgefuehrten Pakete.
Die anschliessend entwickelte [Nextcloud-Einrichtung in OpenClaw](NEXTCLOUD_SETUP_UI.md),
kontoweite Ressourcenerkennung und der Talk-Kontokanal nach
[ADR-0051](architecture/adr/0051-openclaw-einrichtung-und-nextcloud-konto.md)
besitzen eigene Tests und Abnahmegrenzen. Sie sind dadurch weder automatisch
gesamtproduktseitig abgenommen noch produktiv aktiviert; die bestehenden
M18-Nachweise bleiben unveraendert datierte Entwicklungsbelege.

Jan soll Auftraege verlaesslich ueber mehrere Werkzeuge erledigen lassen koennen,
aktuelle und vollstaendige Quellen erhalten und weder unnoetige Modellarbeit noch
wachsenden Speicherverbrauch bezahlen. Diese Roadmap fuehrt den Gesamtauftrag
nach der M16-Stabilisierung weiter. Der parallel vorbereitete M17-Talk-Kanal
bleibt erhalten und besitzt seine eigene Betriebsfreigabe.

## Ausgangslage und Prioritaeten

Die vorhandenen Approval-, ETag-, Antivirus-, Single-Writer- und Evidenzvertraege
bleiben verbindlich. Verbesserungen muessen durch reproduzierbare Gegenbeispiele
und Tests belegt sein. Ein groesseres Modell, hoehere Runtime-Limits oder weitere
Agenten sind kein Ersatz fuer korrekte Ablaeufe.

Die Ausgangssuite vor M18 bestand mit 1.326 Tests; der isolierte
Talk-HTTPS-Test wurde zusaetzlich separat ausgefuehrt. Diese Ausgangsbelege
ersetzen die Abnahme der folgenden Aenderungen nicht. Die historischen
[M16-Ergebnisse](M16_ACCEPTANCE.md) bleiben datierte Entwicklungsnachweise;
ihre damaligen offenen Releasepunkte sind kein aktueller Produktivstatus.

Die erste Gesamtpruefung findet konkrete Luecken trotz gruener Tests:

- Eine begrenzte WebDAV-Auflistung kann faelschlich vollstaendig erscheinen und
  damit weiterhin vorhandene Dateien aus der lokalen Wissensprojektion entfernen.
- Gleiche ETags verschiedener Dateien koennen faelschlich als Verschiebung
  gelten. Ein ETag ist keine dateiuebergreifende Identitaet.
- Der Modellproxy kann nach einem unerwarteten Koordinatorfehler ohne
  Reservierung weiterleiten und seine eigene Parallelitaetsgrenze umgehen.
- Die Aktionszustandsmaschine kann einen erforderlichen zweiten Leseschritt
  als unzulaessigen Workflowwechsel blockieren.
- Ein allgemeiner Mehrfachauftrag kann schon nach einem einzigen verifizierten
  Write als vollstaendig gelten. Ohne gebundene Zielmenge darf kein
  Gesamtabschluss behauptet werden.
- Laufbezogene Plugin-Maps behalten abgeschlossene Prompts und Evidenz im Speicher.
- Einzelne Freigabe- und Coveragepruefungen koennen unvollstaendige oder
  ungueltige Eingaben als erfolgreich behandeln.
- Bereits der Import des CLI laedt saemtliche Fachdienste. Fuenf lokale
  frische Pythonprozesse benoetigten dafuer 601 bis 631 ms und etwa 33 MiB
  Spitzenspeicher; dies ist keine Messung produktiver Antwortlatenz.
- Portfolio-CSV kann nicht-endliche Zahlen enthalten; ein Pfadwechsel zwischen
  Virenscan und Import kann die Bindung an die geprueften Bytes verlieren.
  Rechnungstexte werden bislang ohne Formelneutralisierung als CSV exportiert.

## Umsetzungsfolge und Abnahme

| Paket | Verbesserung | Abnahmekriterium | Status |
| --- | --- | --- | --- |
| M18.0 | Gesamtinventur und nachvollziehbare Ausgangsbelege | Laufzeit, Werkzeuge, Suche/Lernen, Groupware, Rechnungen, Portfolio, Sicherheit und Release getrennt bewertet; aktuelle und historische Belege getrennt | abgeschlossen |
| M18.1 | Verlustfreie inkrementelle Nextcloud-Projektion | Begrenzte/fehlerhafte Listings loeschen nichts; Kopien mit gleichem ETag bleiben getrennt; belegte Inhaltswiederverwendung, No-op und Delta funktionieren | abgeschlossen |
| M18.2 | Verbindliche Modellkoordination | Jeder Koordinatorfehler blockiert vor Upstream; Reservierungen werden bei allen Fehlerpfaden genau einmal freigegeben; Limits unveraendert | abgeschlossen |
| M18.3 | Verlaessliche mehrstufige Auftraege | Status, Auswahl und Quellenlesen duerfen derselben Einzelaktion vorausgehen; Mehrfachauftraege erhalten keine verfruehte Erfolgsmeldung; Fremddomaenen, ungebundene Writes und Schleifen bleiben gesperrt | abgeschlossen |
| M18.4 | Begrenzter Speicher fuer Agentenlaeufe | Gesamtzahl/Alter sind begrenzt; Freigabewiederaufnahme bleibt korrekt, fremde oder abgelaufene Evidenz unbrauchbar | abgeschlossen |
| M18.5 | Belastbare Qualitaets- und Releasepruefungen | Leere/fehlerhafte/NaN-Coverage faellt durch; Vollsuite mit normalen Ausgabeoptionen behaelt Risikogate; Aktionsfreigabe ersetzt keine technische Kandidatenpruefung | abgeschlossen |
| M18.6 | Schnellere schlanke CLI-Einstiege | Statische Introspektion braucht keine Fachdienste; Vorher/Nachher aus identischem Harness, Verhalten und Fehlervertraege unveraendert | abgeschlossen |
| M18.7 | Fachliche und intelligente Antwortqualitaet | Positive, negative, unvollstaendige und mehrstufige Szenarien; keine erdachten Fakten/Erfolge; finite Portfoliowerte, scanidentischer Import und sicherer CSV-Text | abgeschlossen |
| M18.8 | Gesamtabnahme und aktuelle Fortschrittssicht | Unabhaengiger Review, Gesamtpruefung, Wheel/Images fuer betroffene Pfade, Vorher/Nachher und offene Betriebsnachweise sichtbar | abgeschlossen, nur Entwicklung |

Unabhaengige Pakete duerfen parallel bearbeitet werden. Jeder abgeschlossene
Schritt erhaelt unten einen konkreten Code-, Test- und Messnachweis. Eine gruene
Gesamtsuite ohne den jeweiligen Gegenbeispieltest schliesst keinen Befund.

## Pruefbereiche der Gesamtinventur

| Bereich | Zu pruefen | Grenze |
| --- | --- | --- |
| Laufzeit und Scheduler | Prioritaeten, Leases, Speicher, Fehlerpfade und faire Wartezeiten | Keine Jobstarts, keine erhoehten Limits |
| Modell und native Werkzeuge | Koordination, Routing, mehrstufige Aktionsabschluesse, Kontext- und Ergebnisbudgets | Keine direkte Umgehung des Proxys oder Approvalvertrags |
| Mail und Lernen | Vollstaendigkeit, Frische, Live-Locator, Originalanhaenge, Abstention, unveraenderliche Lernbelege | Keine produktiven Maildaten als Fixtures oder automatische Regelaktivierung |
| Groupware und Wissen | Delta/No-op, Kopien/Moves, ETags, Ressourcenauswahl, Quellenbelege | Keine Remote-Loeschung oder Rechteaenderung |
| Rechnungen und Portfolio | Fehlende Felder, Provenienz, Waehrung/Frische, Providergrenzen und Teilfehler | Keine erfundenen Werte, keine produktiven Einzelkorrekturen ohne Freigabe |
| Sicherheit und Auslieferung | Secret-Isolation, TLS/ClamAV, Backup/Rollback, verifizierte Qualitaets- und Releasegates | Kein Deployment oder Main-Promotion durch Entwicklungsfreigabe |

## Fortschritt und Nachweise

- M18.0: Lokaler Quellbaum und bestehende Roadmaps erneut gelesen. Die
  Paketidentitaet wurde mit `./scripts/assistant.sh version --verify` verifiziert;
  sie ist keine Aussage ueber das installierte Produktivimage. Unabhaengige
  Audits untersuchten Laufzeit, Aktionslogik und Abnahmegates. Ein M16-Querschnitt
  aus 172 Tests besteht; die oben genannten Gegenbeispiele zeigen zusaetzliche
  Luecken ausserhalb dieser bisherigen Testabdeckung.
- M18.5: 45 Tests in `tests/test_quality_risk_m166.py` bestehen einschliesslich
  NaN/Infinity, fehlender Werte, fehlerhaftem JSON und tatsaechlichem Shellpfad
  mit `-q`/`-v`, laufexklusivem Bericht und fehlender frischer Messung.
  74 Promotionsregressionen bestehen. Der aktuelle Bedienvertrag
  steht in [M16_RELEASE_SCOPE.md](M16_RELEASE_SCOPE.md#aktionspruefung-mit-unveraenderter-ready-evidenz).
- M18.6: `personal_assistant/bootstrap.py` verschiebt ausschliesslich die
  konkreten Service-/Mailadapterimporte an die bestehende Konstruktorgrenze.
  Alle sechs neuen CLI-Regressionen und die Gesamtmatrix bestehen. Fuenf frische Prozesse im
  identischen Importharness benoetigen danach 382 bis 426 ms und etwa 28 MiB;
  Module 334 vorher, 255 nachher; kein Serviceimport bei statischer Abfrage.
  Die Messung ist lokal und kann durch parallele Entwicklungspruefungen
  schwanken. Es gibt keine neue Zeitgrenze und keine Produktionslatenzbehauptung.
- M18.7: Der lokale Fachquerschnitt aus Maillernen/-suche/-weiterleitung,
  CardDAV, CalDAV, Aufgaben, Rechnungsabnahme und Portfolio besteht mit
  159 Tests und 23 Subtests. Zusaetzlich bestehen 52 Order-/Antivirus-/Risikotests.
  Die Import-/Exportregressionen sind im Rechnungs-/Portfolioquerschnitt enthalten.
- M18.3/M18.4: 115 Aktions-/Plugin-/Kapazitaetsregressionen und 24 Subtests
  bestehen. Der unabhaengige Plugin-Gegenlauf besteht mit 95 Tests und 24 Subtests;
  reproduzierte Befunde zu Ablauf-Races, Vorschauabschluss und Einzelzielketten
  sind geschlossen. Allgemeine Mehrfachauftraege bleiben bewusst einzeln zu binden.
- M18.8: `./scripts/check-repo.sh` erfolgreich: 1.573 Tests bestanden,
  ein separat ausgefuehrter Docker-Test bewusst uebersprungen, Gesamtcoverage
  70,98 Prozent und alle Risikogates bestanden. `./scripts/check-wheel.sh`
  besteht nach frischer Installation ebenfalls mit 1.573 Tests und 247 Subtests.
  Alle drei lokalen Rollenimages sind gebaut und mit `check-role-images.sh`
  geprueft, einschliesslich des echten Talk-HTTPS-/Zertifikatstests.
  Unabhaengige Reviews haben keine offenen reproduzierten Befunde im
  bearbeiteten Umfang. Die lokalen Auditimages sind keine signierte Releasefreigabe.

## Abschluss und Betriebsgrenze

Die vollstaendige Entwicklungsmatrix besteht; alle Pakete M18.0 bis M18.8 sind
lokal abgeschlossen. Produktive Messwerte
werden nur aus eigens freigegebenen aktuellen Beobachtungen uebernommen;
synthetische Zeiten bleiben als solche gekennzeichnet.

Commit/Push, signierte Veroeffentlichung, Main-Promotion, Deployment und die
produktive Abnahme werden getrennt nach dem bestehenden Releasevertrag
freigegeben. Fehlende externe Nachweise bleiben offen. Ein Image-Rollback
stellt keine entfernten Mail-, Kalender-, Datei- oder Registeraenderungen wieder her.
