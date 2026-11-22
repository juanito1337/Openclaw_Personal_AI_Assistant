# Roadmap für die Nextcloud Einrichtung in OpenClaw

Planungsstand: 21. November 2026. Umsetzungsstand: 22. November 2026.
Ausgangspunkt ist der Quellstand `0bad341`. Diese Roadmap aktiviert keine Rechte,
Jobs oder produktiven Änderungen.

Umsetzungsstand vom 22. November 2026: Der Kategorienparser aus R0 ist im
Entwicklungsstand korrigiert und mit gezielten Regressionen geprüft. Das
Komponentenregister aus U0 ist ebenfalls implementiert und unabhängig geprüft.
NC0 bis NC2 sind im Entwicklungsstand umgesetzt und je Etappe unabhängig geprüft.
Der [NC0–NC2-Umsetzungsplan](superpowers/plans/2026-11-22-nextcloud-access.md)
dokumentiert die Umsetzung; der [Abnahmebericht](NEXTCLOUD_FOUNDATION_VALIDATION.md)
hält Gesamttests und Prüfgrenzen nach. Produktive Installation und reale Abnahme
bleiben getrennt.

Jan soll im bestehenden OpenClaw-Plugin sehen und festlegen können, welche
Nextcloud-Daten der Assistant verwenden darf, welche Aktionen erlaubt sind und
ob die Verbindung tatsächlich arbeitet. Die Einrichtung soll schrittweise von
einem persönlichen Agenten für `janwrede` zu einem Angebot für weitere
Nextcloud-Benutzer wachsen.

Die Pflege externer Repositories und des gesamten Stacks ist ein eigener
Bestandteil: NC8 ergänzt eine Updateübersicht und kontrollierte Aktualisierung
im selben Plugin. Die gemeinsame [Update-Roadmap](STACK_UPDATE_ROADMAP.md)
deckt auch Container, Laufzeitpakete und externe Dienste ab.

## Zielbild und empfohlener Aufbau

Die bestehende Nextcloud-Einstellungsseite unseres OpenClaw-Plugins wird um
eine Rechteverwaltung erweitert. Jan hat den Ort ausdrücklich festgelegt:
Einrichtung und Bedienung bleiben in OpenClaw. Eine zusätzliche Einstellungs-App
in Nextcloud gehört nicht zum Ziel. Eine versionierte Konfiguration speichert
die Einstellungen intern; ihre manuelle Bearbeitung ist kein notwendiger
Bedienungsschritt. Export und geprüfter Import sind optionale Ergänzungen.

| Ansatz | Nutzen | Nachteil und Einordnung |
| --- | --- | --- |
| Unser OpenClaw-Plugin erweitern | Nutzt Einrichtung, Fachwerkzeuge und Freigaben weiter | Empfohlener Weg nach dem Funktionsvergleich; ausgewählte Upstream-Verfahren und Tests ergänzen. |
| Bestehendes Projekt gezielt anbinden | Kann fehlende Connectorfunktionen liefern | Benötigt einen Adapter zu unserem Werkzeug- und Freigabevertrag sowie eine Kompatibilitätsprüfung. |
| Ein bestehendes Projekt forken | Eigene Änderungen am vollständigen Projekt möglich | Dauerhafte Verantwortung für Upstream-Abgleich und Wartung; erst bei belegtem Vorteil gegenüber Anbindung oder gezielter Übernahme. |

Der Nutzerweg lautet: **Konto verbinden → Verbindung prüfen → Ressourcen
auswählen → Agentenrechte festlegen → Änderungen prüfen und übernehmen →
wirksamen Zustand kontrollieren**. Fachliche Abläufe wie Rechnungsablage werden
anschließend einzeln eingerichtet.

## Bestehende Projekte und Wiederverwendung

Quellenprüfung vom 21. November 2026. Die folgende Einordnung beruht auf
Dokumentation, Paketmetadaten und gezielten Codeprüfungen. Sie belegt keine
Laufzeitkompatibilität oder vollständige Sicherheitsabnahme.

| Projekt | Vorhandener Nutzen | Einordnung für unser OpenClaw-Plugin |
| --- | --- | --- |
| [Offizielles Nextcloud Talk Plugin](https://docs.openclaw.ai/channels/nextcloud-talk) | Nativer Talk-Kanal mit Bot/Webhook, Raum- und Absenderregeln | Für Kommunikation; kein vollständiger Dateien-/CalDAV-/CardDAV-Connector. Eine exakt gepinnte Variante steht bereits in unserem Buildvertrag. Die neueste Upstream-Dokumentation belegt nicht die Fähigkeiten unserer eingebetteten Version. |
| [keithvassallomt/openclaw-nextcloud](https://github.com/keithvassallomt/openclaw-nextcloud) | MIT; Skill und Node-CLI für Dateien, Kalender, Aufgaben, Kontakte, Deck, Notes und Freigaben; geprüft bei `c6659f3`, Paket 0.9.0 | Bester breiter OpenClaw-spezifischer Kandidat zur gezielten Codeübernahme. Keine eigene geführte Setup-Seite oder gleichwertige Ressourcenrichtlinie. |
| [cbcoutinho/nextcloud-mcp-server](https://github.com/cbcoutinho/nextcloud-mcp-server) | AGPL-3.0; eigenständiger Python-MCP-Server mit breiter App-Abdeckung, Login Flow v2 und dokumentierten Lese-/Schreib-Scopes | Alternative für einen angebundenen Connector. Benötigt zusätzlichen Dienst und Adapter zu unseren Werkzeugen, Freigaben und Diagnosen; kein unmittelbar austauschbares OpenClaw-Plugin. |
| [khnz/openclaw-channel-nextcloud-talk-user](https://github.com/khnz/openclaw-channel-nextcloud-talk-user) | MIT; natives Talk-Plugin als Benutzerkonto, OpenClaw-Setup-Wizard und Login Flow v2 | Referenz für unseren Kontokanal. Sehr junges Repository vom 20.11.2026; verlangt Plugin-API mindestens 2026.11.5, daher kein ungeprüfter Ersatz für unsere gepinnte Schnittstelle. |
| [sintax-tech/mcp-for-nextcloud](https://github.com/sintax-tech/mcp-for-nextcloud) | Nextcloud-App mit Benutzer-/Gruppenrechten und Ressourcenbeschränkung; AGPL mit zusätzlichem Attributionsterm laut Projekt | Fachlich interessante Referenz für das Rechtemodell. Die Verwaltungsoberfläche liegt in Nextcloud und erfüllt damit nicht Jans festgelegten Ort in OpenClaw. |

Bei der breiten Skill von keithvassallomt sind ETag-Prüfungen für CalDAV und
CardDAV vorhanden. Anpassungsbedarf bleibt konkret: Datei-Uploads können
bestehende Dateien überschreiben, eine fehlende Ressourcenauswahl kann auf die
erste Sammlung zurückfallen, und `--confirm` ersetzt keine gebundene native
Aktionsfreigabe. Deshalb kommt eine unveränderte zusätzliche Schreibroute
nicht infrage. Belege stehen im
[geprüften Quellstand](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js)
und im [Skill-Vertrag](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/SKILL.md).

Weitere Suchtreffer ersetzen diese Auswahl nicht:
[NexLink](https://github.com/asistent-alex/openclaw-nexlink) ist eine Python-Skill;
die dort beworbenen Kalender- und Aufgabenfunktionen gehören zu Exchange.
Beim [far-ux-Projekt](https://github.com/far-ux/openclaw-nextcloud-integration)
fehlten im geprüften Repository die im Paket referenzierten Implementierungen.

Der [detaillierte Vergleich mit keithvassallomt/openclaw-nextcloud](NEXTCLOUD_REUSE_COMPARISON.md)
ist abgeschlossen. Er trennt registrierte Agentenfunktionen von internen
Connectormethoden und nennt konkrete Übernahmepakete R0 bis R6, Fundstellen
und Abnahmekriterien. Ein lokaler synthetischer Versuch bestätigt außerdem
einen kleinen Fehler beim Lesen von Aufgabenkategorien mit escaptem Komma.

**Entscheidungsempfehlung aus dem Vergleich:** Unser Plugin und die vorhandenen
Python-Connectoren behalten. Gezielt Kalenderexpansion, Dateileseverhalten,
Dateinamensuche, Notes und zusätzliche Kontakt-/Aufgabenfelder portieren.
Vorhandenes CRUD, Talk, Setup und Bestellabläufe werden nicht ersetzt. Die
Upstream-Skill wird nicht parallel als weiterer Ausführungsweg installiert.
Ein kompletter Fork oder zusätzlicher MCP-Dienst ist für diese Übernahmen
nicht vorgesehen; eine spätere Neubewertung braucht eine konkrete weitere Lücke.

Die Quelltextprüfung ersetzt noch keine Integrationsabnahme. Pro Paket folgt
nach dem Detaildesign ein isolierter Test mit exakt begrenzten Ressourcen.
Die Einrichtung bleibt in OpenClaw; die Recherche installiert oder aktiviert
keine Komponente.

## Ausgangslage und verbleibende Lücken

Die [bestehende Einrichtung](NEXTCLOUD_SETUP_UI.md) enthält Kontoanmeldung,
authentisierte Identitätsprüfung, Kalender-, Aufgaben-, Adressbuch- und
Ordnererkennung sowie Talk-Raum- und Benutzerauswahl. Diagnose und laufende
Talk-Verbindung werden bereits getrennt dargestellt. Die Fachwerkzeuge
besitzen Ressourcen-IDs, Freigaben und Konfliktschutz.

Der Vergleich vom 21. November zeigte folgende Lücken. Die ersten beiden
werden durch NC1 und NC2 im aktuellen Entwicklungsstand geschlossen:

- Beim bisherigen verwalteten Konto wurden lesbare erkannte Ressourcen
  übernommen; der Dateizugriff umfasste den verfügbaren Kontobaum. Die neue
  Richtlinie begrenzt diesen Zugriff pro Ressource, Komponente und Unterordner.
- Das Standardprofil normalisiert Werkzeugrechte bei jedem Start. Auch die
  Kontoübernahme und konkrete Ressourcenauswahl setzen Rechte aus den
  Serverangaben. Einzelne bestehende `allow_*`-Schalter reichen deshalb als
  dauerhafte Zugriffsstufen nicht aus. Die neue Restriktionsschicht wird nach
  diesen Projektionen und unmittelbar vor Zugriff und Ausgabe erneut geprüft.
- Die Setup-Seite zeigt Serverrechte, ändert aber keine Nextcloud-Freigaben.
- Automatische Rechnungs-, Mail-Kalender- und Deck-Ziele werden bei einer
  Kontoanbindung nicht automatisch auf das neue Konto übertragen. Dafür fehlt
  ein durchgängiger Einrichtungsweg mit gezielter Auswahl.
- Mehrere erlaubte Talk-Sender ergeben noch keine nachgewiesene Trennung von
  Daten, Speicher, Werkzeugrechten und Freigaben zwischen Benutzern.

Damit ist die technische Anbindung weit fortgeschritten. Für eine umfassend
steuerbare und für mehrere Benutzer geeignete Integration sind die folgenden
Etappen nötig.

## Das Berechtigungsmodell

Die Oberfläche zeigt für jede Ressource drei Angaben: **Nextcloud erlaubt**,
**für den Agenten freigegeben** und **aktuell wirksam**. Zusätzlich zeigt sie,
welche konkrete Aktion noch eine Bestätigung benötigt.

Wirksam ist nur die Schnittmenge aus bestätigten Serverrechten, ausdrücklich
freigegebenem Ressourcenbereich, lokaler Agentenrichtlinie und unterstützter
Fachoperation. Im Mehrbenutzerbetrieb kommt die Berechtigung des anfragenden
Benutzers hinzu. Eine Einmalfreigabe bestätigt eine konkrete Aktion innerhalb
dieser Grenzen; sie erweitert sie nicht.

| Zugriffsstufe | Bedeutung |
| --- | --- |
| Aus | Der Agent darf die Ressource weder lesen noch verändern; auch Suchindex und zwischengespeicherte Inhalte dürfen sie nicht zugänglich machen. |
| Lesen | Unterstützte Listen, Suchen und Leseoperationen im gewählten Bereich. |
| Lesen und Erstellen | Zusätzlich unterstützte neue Objekte anlegen; vorhandene Objekte bleiben geschützt. |
| Lesen, Erstellen und Ändern | Zusätzlich unterstützte gezielte Änderungen, etwa Aufgaben abschließen; bestehende Einzelfreigaben bleiben erforderlich. |

Diese Stufen sind verständliche Vorlagen. Intern werden konkrete Operationen
pro Ressource gespeichert. Die Oberfläche bietet nur tatsächlich unterstützte
Aktionen an. Bei Dateien bedeutet „Ändern“ beispielsweise nicht automatisch
das Überschreiben vorhandener Dateien. Talk erhält eigene Einstellungen für
Räume, zugelassene Absender und Antworten. Hintergrundjobs besitzen einen
separaten Ein-/Aus-Zustand.

Löschen, Teilen, Rechteverwaltung und autonome Hintergrundaktionen werden
nicht durch eine allgemeine Stufe „Vollzugriff“ freigeschaltet. Ihre vorhandenen
Grenzen bleiben erhalten; ein neuer Funktionsumfang braucht einen eigenen
vollständigen Werkzeug- und Freigabevertrag.

## Etappen und Abnahme

### Vorab R0 Parserfehler und bestehende Protokollfälle

Die Aufgabe `CATEGORIES:Sales\, West,Other` muss zwei Kategorien ergeben.
Der bisherige Parser lieferte drei. Die lokale Korrektur vom 22. November 2026
trennt und decodiert die Werte in einem Durchlauf; Regressionen prüfen auch
Backslash-Folgen, Faltung, Unicode, VALARM und den Erstellungs-Roundtrip.
Geeignete Upstream-Testfälle für Faltung, Unterkomponenten und Binärdaten
ergänzen bestehende Tests, soweit sie nicht bereits abgedeckt sind.

Das abweichende Deck-Reorder-Routing wird gegen die unterstützte API-Version
geprüft. Ein erfolgreicher HTTP-Status allein belegt keine Verschiebung in die
Zielspalte. Dieser Punkt bleibt bis zum Test ein Kompatibilitätsbefund.

**Abnahme:** Kategorien werden ohne Datenverlust gelesen; zusätzliche
Regressionen weisen das relevante Verhalten nach. Produktive Daten werden
dabei nicht verändert. Ein eventuell bestätigter Deck-Fix folgt als getrennte,
geprüfte Korrektur.

### NC0 Einrichtung und Diagnose vervollständigen

Die vorhandene Seite führt Anmeldung, Kontoidentität, Ressourcenauswahl,
Serverrechte und Laufzeitstatus in einem nachvollziehbaren Ablauf zusammen.
Dateien, Kalender, Aufgaben, Kontakte und Talk erhalten jeweils einen klaren
Status und einen konkreten nächsten Schritt. Nicht geprüfte, teilweise
geprüfte, deaktivierte und veraltete Bereiche bleiben unterscheidbar.

**Abnahme:** Jan kann pro Bereich erkennen, welches Konto verbunden ist, was
geprüft wurde und was noch fehlt. „Verbunden“ bedeutet weder rekursiven
Dateizugriff noch eine zugestellte Chatantwort. Checks erzeugen standardmäßig
keine Testdateien oder Nachrichten. Grundlage ist der bestehende
[Diagnosevertrag](INTEGRATION_DIAGNOSTICS.md).

### NC1 Dauerhafte Agentenrechte und Konfigurationsschema

Eine eigenständige Restriktionsschicht begrenzt die vorhandenen Fachwerkzeuge.
Sie gilt nach Standardprofil und Kontoübernahme sowie bei jeder konkreten
Ressourcen- und Operationsauswahl. CLI, native Agentenwerkzeuge, Suche,
Indexzugriff und Hintergrundabläufe verwenden dieselbe Entscheidung.

Die Konfiguration enthält Schema-Version, Kontobindung, Ressourcen-IDs,
erlaubte Unterpfade und Operationen, den verifizierten Kontoinhaber und
Änderungsrevision. Eine getrennte Richtlinie je Talk-Absender folgt erst in NC6.
Zugangsdaten bleiben im vorhandenen geschützten Verbindungsdienst. Gewünschte
Rechte und gemessene Serverrechte werden getrennt gespeichert.

Für bestehende Installationen wird der bisherige Umfang zunächst als
prüfbare Migrationsvorschau dargestellt. Der Wechsel auf die neue Richtlinie
erfolgt atomar nach Bestätigung; eine fehlende neue Datei bedeutet im
Legacy-Zustand nicht automatisch Rechteentzug. Nach Aktivierung führen eine
fehlende oder ungültige Richtlinie zu einer sichtbaren Sperre, niemals zur
Rückkehr zu umfassendem Standardzugriff. Neue Ressourcen und Konten erhalten
erst nach Auswahl Zugriff. Kontowechsel übernimmt keine alten Freigaben.

Eine Einschränkung wirkt vor dem nächsten Werkzeug- oder Hintergrundrequest.
Bereits akzeptierte externe Änderungen lassen sich dadurch nicht zurücknehmen.
Offene Pläne, Freigaben, Cachezugriffe und Workerbelege werden bei relevanten
Änderungen an die neue Revision gebunden beziehungsweise ungültig.

**Abnahme:** „Nur lesen“ bleibt nach Neustart, Update, Ressourcenerkennung und
expliziter Werkzeugauswahl wirksam. Direkte CLI-Aufrufe und lokale Indizes
umgehen die Einschränkung nicht. Widerruf und Kontowechsel sind getestet.

### NC2 Rechte im OpenClaw-Plugin bearbeiten

Pro Ressource erscheinen die bestätigten Serverrechte, die wählbare
Agentenstufe und das wirksame Ergebnis. Bei Ordnern lässt sich der erlaubte
Teilbaum auswählen. Fehlende Serverrechte werden mit Ursache angezeigt.
Vor dem Speichern sieht Jan die Änderungen und betroffenen Abläufe.

Oberfläche und interne Konfiguration verwenden dasselbe Schema und denselben
administrativen Prüf- und Speicherpfad. Speicherformat und Dateiname gehören
ins Detaildesign; Jan bedient die Einstellungen in OpenClaw. Ein optionaler
Export enthält keine Zugangsdaten. Ein späterer Import benötigt Anmeldung,
Validierung, eine Vorschau und die passende Änderungsfreigabe. Unbekannte
Felder, fremde Kontobindungen, ungültige Ressourcen und veraltete Revisionen
werden zurückgewiesen. Eine Datei in Nextcloud wird nicht automatisch als
Steuerdatei ausgeführt.

**Abnahme:** Jan kann einen Kalender auf Lesen, eine Aufgabenliste auf Ändern
und einen Ordner auf Aus setzen sowie den wirksamen Zustand erneut prüfen.
Parallele Änderungen werden erkannt. Ein optionaler Export und die Diagnose enthalten keine
Passwörter, Tokens oder Dokumentinhalte; Ressourcen- und Benutzerzuordnungen
bleiben private Konfigurationsdaten.

### NC3 Fachliche Abläufe gezielt einrichten

Die Seite bietet einzelne Einrichtungsschritte für Rechnungsarchiv,
Mail-zu-Kalender und Deck-Aufträge. Jeder Schritt zeigt Quelle, exaktes Ziel,
benötigte Operationen, erlaubte Daten und den separaten Jobzustand. Ein
gespeichertes Ziel startet noch keinen Hintergrundprozess.

Rechnungen erhalten insbesondere eine klare Trennung zwischen vorhandenen
lokalen Rechnungsdaten, Zugriff auf Archivdateien und neuer automatischer
Ablage. Historische Daten werden nicht allein durch eine neue Kontoverbindung
dem neuen Konto oder weiteren Benutzern zugeordnet.

**Abnahme:** Jeder Ablauf ist entweder vollständig ausgewählt und geprüft oder
meldet den konkreten fehlenden Schritt. Keine automatische Neuzuordnung alter
Ziele, kein Jobstart als Nebenwirkung der Einrichtung.

### NC3a Gezielte Funktionsübernahmen nach dem Vergleich

Die folgenden Pakete erweitern jeweils die bestehenden Connectoren, Services,
CLI und typisierten Werkzeuge. Sie benutzen dieselbe OpenClaw-Einrichtung,
Kontobindung und Rechteauswertung wie die vorhandenen Funktionen.

| Reihenfolge | Paket | Sichtbarer Nutzen und Abnahme |
| --- | --- | --- |
| 1 | R1 Kalenderagenda | Abfragen wie „Termine nächste Woche“ nutzen ein begrenztes Zeitfenster und einzelne Serienvorkommen. Ausnahmen, verschobene Termine, Zeitzonen und Teilfehler sind korrekt; Serienbearbeitung wird dadurch nicht erweitert. |
| 2 | R2 Dateizugriff | Ausgewählte Dateien kontrolliert lesen, Namen live suchen und interne Nextcloud-Links ausgeben. Bestehenden ETag-Download nutzen; Pfade, Größen, Scans und Vollständigkeit prüfen. Keine beliebigen lokalen Downloadziele. |
| 3 | R3 Kontaktfelder | Postanschrift, Geburtstag und beruflichen Titel lesen beziehungsweise gezielt bearbeiten. Bestehende Felder erhalten und bei mehreren passenden Anschriften eine genaue Auswahl verlangen. Kontakte ohne E-Mail bleiben eine optionale Erweiterung. |
| 4 | R4 Notes | Notes-App erkennen, Notizen auflisten und lesen. Erstellen später separat; Änderungen erst mit geprüftem Konfliktschutz. |
| 5 | R5 Aufgabenfelder | Ort, URL und CLASS ergänzen. Start, Fälligkeit, Kategorien, Fortschritt und Abschluss sind bereits vorhanden. CLASS gewährt keine zusätzlichen Rechte. |
| 6 | R6 Deck lesen | Allgemeine Boards, Spalten, Karten, Labels und Kommentare lesbar machen. Vorhandenen Connector nutzen und die Schreibgrenze für Bestellkarten erhalten. |

Neue Lese- und Schreiboperationen werden erst nach Einbindung in NC1/NC2
ausgeliefert. Jede neue Funktion ist zunächst ungewählt und führt ihre eigene
Diagnose. Die Freigabe des Gesamtpakets bedeutet keine automatische Auswahl
neuer Ressourcen. R1/R2 sind der erste fachliche Ausbau; R3 bis R6 folgen in
separaten Schritten. Umfang und Tests sind im
[Vergleich](NEXTCLOUD_REUSE_COMPARISON.md) konkretisiert.

**Abnahme:** Pro Paket stimmen unterstützte CLI, Katalog, Referenz und
Verhaltenstests überein. Teilergebnisse bleiben sichtbar; Freigaben,
Erwartungswerte, ETags und geprüfte Schreibresultate werden beibehalten.
Übernommener Code beziehungsweise Fixtures erhalten Herkunfts- und Lizenzhinweise.

### NC4 Tatsächliche Nextcloud Rechte kontrolliert verwalten

Hier wird der Wunsch umgesetzt, auch die in Nextcloud gewährten Rechte von
der Einrichtung aus ändern zu können. Dazu braucht jeder Ressourcentyp einen
eigenen unterstützten Verwaltungsweg und eine dazu berechtigte menschliche
Identität. Das normale Agentenkonto erhält dafür keine pauschalen Adminrechte.

Die Share-Funktionen der verglichenen Skill erzeugen oder entfernen öffentliche
Links. Sie liefern diesen Rechteeditor nicht und werden dafür nicht als
zusätzliche Agentenwerkzeuge übernommen.

Der erste Schnitt bearbeitet eine exakt ausgewählte bestehende Freigabe an
das Agentenkonto, etwa für einen Ordner. Kalender, Aufgaben, Adressbücher und
Talk folgen nach Prüfung ihrer jeweiligen APIs. Nicht unterstützte Änderungen
verweisen auf den passenden Einrichtungsweg in Nextcloud.

Nextcloud dokumentiert für Datei- und Ordnerfreigaben getrennte Rechte und
eine API zum Ändern einer bestehenden Freigabe. Daraus folgt kein einheitlicher
Rechteeditor für alle Apps; Talk hat eigene Teilnehmerrechte und
Voraussetzungen. Die Umsetzung muss diese Unterschiede berücksichtigen.
Siehe [OCS Share API](https://docs.nextcloud.com/server/stable/developer_manual/client_apis/OCS/ocs-share-api.html)
und [Talk Teilnehmerverwaltung](https://nextcloud-talk.readthedocs.io/en/stable/participant/).

Der Ablauf lautet: aktuellen Serverzustand lesen, Vorher/Nachher-Vorschau
zeigen, exakt freigeben, einmal ändern und Rechte erneut abfragen. Konflikte
werden über dokumentierte Versions-/Bedingungsmechanismen abgesichert.
Fehlt ein ausreichend sicherer Mechanismus, bleibt die Änderung zunächst in
der nativen Nextcloud-Verwaltung. Eine erfolgreiche lokale Speicherung ist
kein Beleg für geänderte Serverrechte.

**Abnahme:** Eine autorisierte einzelne Rechteänderung ist remote nachgewiesen.
Unberechtigte Identitäten, widerrufene Freigaben, konkurrierende Änderungen und
Teilfehler werden korrekt behandelt. Lokale Agentenbegrenzungen bleiben
bestehen, auch wenn jemand die Serverrechte später erweitert.

### NC5 Wiederverwendete Komponenten im OpenClaw-Plugin pflegen

Ein verwendetes Fremdprojekt wird auf eine geprüfte Version festgelegt und
über den bestehenden Image-Build ausgeliefert. Unser OpenClaw-Plugin bleibt
die Oberfläche für Einrichtung, Diagnose und Agentenrechte. Zusätzliche
Werkzeuge dürfen weder die Rechteauswertung noch die gebundenen Freigaben
umgehen. Talk erhält für jeden Raum weiterhin genau einen aktiven Kanalpfad.

Bei einer Anbindung werden nur benötigte Operationen registriert. Ein Fork
benennt den Grund, die übernommenen Änderungen, Lizenzhinweise, Tests und den
Weg für spätere Upstream-Updates. Falls kein Kandidat ausreichend Arbeit
spart, wird die vorhandene Implementierung direkt weiterentwickelt.

Auch einzelne übernommene Funktionen und Testfälle erhalten einen Pflegeeintrag:
Ursprungscommit, lokale Fundstellen und Anpassungen, Verantwortlichkeit sowie
letzter geprüfter Upstream-Stand. Ein Paketupdate aktualisiert keine übersetzte
Python-Funktion. Relevante Korrekturen werden gezielt portiert und erneut geprüft.
Das Komponentenregister aus NC8/U0 ist vor der ersten Übernahme erforderlich.

**Abnahme:** Die gewählte Komponente funktioniert mit unserer eingebetteten
OpenClaw-Schnittstelle. Alle benutzbaren Aktionen erscheinen konsistent in
Diagnose, Werkzeugvertrag und Rechteverwaltung. Ein zusätzlicher CLI-, Skill-
oder MCP-Pfad ermöglicht keinen ungeprüften Zugriff.

### NC6 Weitere Nextcloud Benutzer sicher aufnehmen

Vor der Erweiterung der bisherigen Auswahl `janwrede` werden Datenzugriff und
Ausführung einer verifizierten Nextcloud-Benutzeridentität zugeordnet. Das
Detaildesign entscheidet zwischen persönlichen Kontoverbindungen und explizit
zugeordneten Ressourcen des gemeinsamen Agentenkontos. Ein Talk-Absender darf
niemals allein wegen seiner Chatfreigabe alle Daten des Agentenkontos nutzen.

Getrennt werden Sitzungen, Gedächtnis, Suchindizes, Cacheergebnisse, lokale
Rechnungsdaten, Ausgaben, Freigaben und Hintergrundaufträge. Nicht eindeutig
zugeordnete Altbestände bleiben für neue Benutzer gesperrt. Gruppenräume
benötigen zusätzlich eine Prüfung der Antwortempfänger, damit private
Ergebnisse nicht an weitere Raumteilnehmer gelangen. Diagnose für Benutzer
offenbart keine Namen oder Pfade fremder Ressourcen.

**Abnahme:** Tests mit zwei Benutzern beweisen, dass A weder Daten noch
Freigaben von B verwenden kann. Entzug von Mitgliedschaft oder Zugriff stoppt
weitere zulässigkeitsabhängige Requests. Erst danach folgt ein Pilot mit einem
zweiten Benutzer; eine Freigabe für alle Serverbenutzer kommt anschließend.

### NC7 Wiederholbare Abnahme und kontrollierte Auslieferung

Diese Etappe begleitet jeden lieferbaren Zwischenstand. Tests prüfen die
Rechteauswertung, Schema-Migration, Import/Export, Neustart, Kontowechsel,
Widerruf, partielle Diagnose und Benutzertrennung. Browserprüfungen belegen
die verständliche Anzeige und die Vorschau vor Änderungen.

Neue unterstützte Fachoperationen erscheinen konsistent in CLI, typisiertem
Katalog, Dokumentation und Verhaltenstests. Administrative Setup-Operationen
bleiben im autorisierten Kontrollpfad und werden keine Agentenwerkzeuge.
Produktive Auslieferung verwendet geprüfte versionierte Images, verifiziertes
Backup, Smoke-Prüfung und den bestehenden Rollbackweg.

**Abnahme:** Ein Testbericht unterscheidet lokale Tests, echte Serverprüfungen
und produktive Aktivierung. Ein lokales Restore stellt keine geänderten
Nextcloud-Freigaben wieder her; dafür braucht es einen eigenen geprüften
Wiederherstellungsweg oder eine klar benannte Grenze.

### NC8 Updatefunktion und dauerhafte Pflege des Stacks

Die Einrichtung unseres OpenClaw-Plugins erhält **System und Updates**.
Jan sieht dort den belegten Iststand, verfügbare und geprüfte Updates,
Kompatibilität, Prüfzeitpunkt und Zuständigkeit. Ein administrativer Updateplan
führt über Vorschau, ausdrückliche Freigabe, Backup und Installation zur
erneuten Prüfung von Nextcloud, Talk und den übrigen betroffenen Diensten.

Die [Stack-Update-Roadmap](STACK_UPDATE_ROADMAP.md) konkretisiert U0 bis U4:
Komponenten und übernommene Codeabschnitte erfassen, Upstream-Änderungen erkennen,
zusammengehörige Releases prüfen, die Übersicht anbieten und schließlich den
kontrollierten Rollout aus dem Plugin ermöglichen. Die vorhandenen signierten
Builds, Deployment- und Rollbackverfahren bleiben die Grundlage. Der Gateway
erhält keinen Docker-Socket und keinen allgemeinen Hostzugriff.

Nextcloud-Server, Apps, Host-Ollama und Hostsystem werden in der Kompatibilität
berücksichtigt; ihre Wartung bleibt beim jeweiligen Betreiber. Neue Ressourcen,
Rechte und zuvor ausgeschaltete Jobs werden durch ein Update nicht aktiviert.
Nicht erreichbare Updatequellen und Signaturprüfdaten bleiben sichtbar blockiert
oder unbekannt und werden nicht als aktueller Stand ausgegeben.

**Abnahme:** Ein Update ist nur mit belegtem Endzustand abgeschlossen. Die
Oberfläche überlebt den Gatewayneustart durch einen persistenten Ablaufzustand
außerhalb des Gateways; doppelte Auslösung erzeugt keinen zweiten Rollout.
Fehler führen in den bestehenden Recoveryweg und benennen dessen lokale und
externe Grenzen. NC8/U0 beginnt vor der ersten Codeübernahme; NC8/U1 und U2
begleiten jede spätere Freigabe. Die vollständige Bedienoberfläche kann danach
folgen, während der bestehende Operatorweg nutzbar bleibt.

## Reihenfolge und erster lieferbarer Umfang

| Lieferung | Etappen | Sichtbarer Nutzen |
| --- | --- | --- |
| Vorab | R0, mit NC7 und NC8/U0 | Kategorienfehler korrigieren; Herkunft und Pflege übernommener Parser-/Testteile sichern. |
| A | NC0 bis NC2, jeweils mit NC7; parallel NC8/U1 und U2 | Saubere Einrichtung und dauerhafte Zugriffsstufen; Grundlage für gepflegte Abhängigkeiten. |
| B1 | NC3a, zunächst R1 und R2; mit NC5/NC7/NC8 | Genauere Kalenderagenda und kontrollierter Dateizugriff mit geprüftem Updateweg. |
| B2 | NC3; R3 bis R6 einzeln; mit NC5/NC7/NC8 | Fachabläufe sowie Kontakt-, Notes-, Aufgaben- und Deck-Funktionen gezielt ergänzen und pflegen. |
| Updates | NC8/U3, danach U4 | Updateübersicht und Vorschau im Plugin; danach kontrollierte Installation mit Backup und Recovery. |
| C | NC4, mit NC7; NC5 bei Wiederverwendung | Unterstützte Serverrechte werden aus OpenClaw kontrolliert bearbeitet; verwendete Komponenten bleiben nachvollziehbar aktualisierbar. |
| D | NC6, mit NC7 | Geprüfter Pilot für einen zweiten Benutzer, danach schrittweise Erweiterung. |

Der Projektvergleich liegt vor; NC5 und NC8 begleiten jede Wiederverwendung.
NC8 ist eine begleitende Arbeit und wird nicht bis nach dem Mehrbenutzerbetrieb
verschoben. Ihre Übersicht kann unabhängig von neuen Fachfunktionen entstehen.
NC3 kann nach Lieferung A parallel zu den fachlichen Übernahmen vorbereitet
werden. NC4 kann ebenfalls nach Lieferung A vorbereitet werden.
NC6 braucht die verbindliche Rechteauswertung aus NC1 und NC2. Die Bedienung
bleibt bei allen Lieferungen in OpenClaw. Zieltermine werden nach Detaildesign
und Prüfung der jeweiligen Server-APIs festgelegt.

**Empfohlener nächster Entwicklungsschritt:** R0 als kleine Korrektur und
Lieferung A samt NC8/U0 bis U2 detaillieren. Für A zunächst einen
Kalender, eine Aufgabenliste und einen Ordner durchgängig prüfen. Danach R1/R2
an genau diese Rechteverwaltung anschließen. Ein vollständiger Fork ist dafür
nicht erforderlich.

## Anknüpfungspunkte im Repository

- [Setup-Service](../personal_assistant/nextcloud_setup.py) und
  [Setup-Oberfläche](../docker/openclaw-personal-assistant-plugin/setup-ui.html):
  vorhandenen administrativen Ablauf erweitern.
- [Kontoprojektion](../personal_assistant/nextcloud_connection.py),
  [Werkzeugeinstellungen](../personal_assistant/tool_settings.py) und
  [Ressourcenauswahl](../personal_assistant/services/nextcloud_account.py):
  dauerhafte Restriktionen durchgängig berücksichtigen.
- [Groupware-Vertrag](../skills/personal-assistant/references/groupware.md),
  [Integrationsdiagnose](INTEGRATION_DIAGNOSTICS.md) und
  [Talk-Roadmap](MOBILE_NEXTCLOUD_TALK_ROADMAP.md): bestehende Ressourcen-,
  Freigabe-, Diagnose- und Kanalgrenzen weiterführen.

Die bestehenden Setup-, Ressourcen-, Standardprofil- und Talk-Tests werden
gezielt ergänzt. Ein zusätzliches allgemeines Integrationsframework ist für
diese Etappen nicht erforderlich.
