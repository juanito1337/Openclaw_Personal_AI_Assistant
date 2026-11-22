# Funktionsvergleich für unser OpenClaw Nextcloud Plugin

Stand: 21. November 2026. Verglichen wurden unser Quellstand
`0bad34127629e221c27895c85efdc79ec4b20d9e` und
[`keithvassallomt/openclaw-nextcloud` bei `c6659f3`](https://github.com/keithvassallomt/openclaw-nextcloud/tree/c6659f3e20761b6b681f3e9135103a205eb23720),
Paketversion 0.9.0. Der Upstream-Commit war zum Abruf der aktuelle Main-Stand.

**Empfehlung: einzelne Protokollfunktionen und Testszenarien übernehmen,
unser OpenClaw-Plugin weiterführen.** Der größte Nutzen liegt in genauerer
Kalenderabfrage, kontrolliertem Dateilesezugriff, Live-Dateinamensuche, Notes
und zusätzlichen Kontaktfeldern. Ein kompletter Fork würde unsere vorhandene
Einrichtung und Zugriffssteuerung nicht ersetzen und viele Fachfunktionen
doppeln.

Die Prüfung umfasst Quellcode, registrierte Werkzeugdefinitionen und
Testquellen. Ein kleiner lokaler Parserversuch verwendete ausschließlich
synthetische Daten. Upstream-Code wurde nicht ausgeführt, seine Tests wurden
nicht als bestanden bewertet. Es gab keine Serverabfragen an Jans Nextcloud,
Installation oder produktive Änderung.

## Unterschied im Aufbau

Upstream ist eine OpenClaw-Skill mit einer gebündelten Node-CLI. Die
App-Passwort-Eingabe verwendet die allgemeine OpenClaw-Skillverwaltung;
Serveradresse und Benutzer kommen aus Konfiguration beziehungsweise Umgebung.
Es gibt keine vergleichbare geführte Einrichtung mit Ressourcenvorschau und
eigener Rechteoberfläche. Siehe
[README](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/README.md)
und [Paketdefinition](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/package.json).

Unsere [Plugin-Integration](../docker/openclaw-personal-assistant-plugin/index.js)
registriert dagegen native Werkzeuge, die administrative Setup-Oberfläche und
den Talk-Kontokanal. Die fachlichen Connectoren laufen in Python hinter
Ressourcenprüfung, Aktionsfreigabe und Ergebnisnachweisen. Ein interner
Connectoraufruf ist dabei noch keine unterstützte Agentenfunktion.

Die dauerhafte lokale Einschränkung pro Ressource fehlt noch in beiden
verglichenen Lösungen in der für uns vorgesehenen Form. Sie bleibt eigene
Arbeit in NC1/NC2 der [Roadmap](NEXTCLOUD_ACCESS_ROADMAP.md), unabhängig von der
Übernahme einzelner API-Funktionen.

## Vergleich und Entscheidung je Funktion

| Funktion | Unsere Integration | Upstream und Übernahmeentscheidung |
| --- | --- | --- |
| Anmeldung, Setup, Talk | Geführte Anmeldung, Kontoprüfung, Ressourcen- und Talk-Auswahl, getrennte Diagnose und Workerbelege | Upstream bietet keine entsprechende Oberfläche oder Talk-Implementierung. Unseren Weg behalten. |
| Kalender lesen, suchen, anlegen, ändern | Bereits registriert; gezielte Updates, ETag und Serienfreigabe vorhanden | CRUD nicht erneut übernehmen. |
| Kalenderzeitraum und Terminserien | Konfigurierter Zeithorizont; CLI `list/search` ohne frei wählbares Zeitfenster; Parser verarbeitet einen VEVENT je Antwortobjekt | `from/to` und serverseitige CalDAV-Expansion sind echte Ergänzungen. Query-Verfahren und Serienfixtures portieren, Identität und Fehlerbehandlung anpassen. |
| Aufgabenbasis | Start, Fälligkeit, Datum ohne Uhrzeit, Priorität, Beschreibung, Kategorien, Status, Fortschritt und Abschluss/Wiederöffnung vorhanden | Keine Neuimplementierung dieser Felder. Upstream nennt Kategorien `tags`. |
| Aufgabenmetadaten | `LOCATION`, `URL` und `CLASS` fehlen im registrierten Aufgabenweg | Feldvalidierung und gezielte Ergänzung übernehmen; `CLASS` ist ein Datenfeld und keine Zugriffsberechtigung. |
| Aufgaben mit Komma in Kategorie | Lokaler Leser trennt auch escapte Kommata | Escape-bewusste Trennung als kleine priorisierte Korrektur. Lokal reproduziert, siehe unten. |
| Kontaktbasis | Lesen, Suche, Create/Update sowie ETag, Erwartungswerte und Erhaltung nicht geänderter Eigenschaften vorhanden | Vorhandenen Kontaktworkflow behalten. |
| Kontaktanschrift, Geburtstag, Titel | ADR/BDAY/TITLE werden nicht als Fachfelder angeboten; vorhandene unbekannte Eigenschaften bleiben bei anderen Änderungen erhalten | Strukturierte Anschriften, Geburtstag und Titel gezielt ergänzen. Eindeutige Adressauswahl ergänzen; die erste passende Adresse reicht nicht. |
| Kontakte ohne E-Mail und Detailabruf | Create verlangt eine E-Mail; interner Detailabruf vorhanden, aber kein allgemeiner registrierter Kontakt-GET | Optionale Erweiterung für Telefon-/Adresskontakte und Detailansicht. Kollisions- und Auswahlregeln vorher anpassen. Kontaktkategorien sind auch upstream kein belegter Übernahmekandidat. |
| Dateien auflisten und ablegen | Live-Listing, Index-Sync, Ordner, neue Textdateien, Upload und Verschieben ohne Überschreiben vorhanden | Upload-/Schreibweg behalten; Upstream-Upload kann vorhandene Dateien überschreiben. |
| Dateien gezielt lesen | ETag-gebundener Download intern vorhanden, etwa für Portfolio; kein allgemeines registriertes Datei-Lesewerkzeug | Bestehenden Download als begrenzte Fachoperation erschließen. UTF-8-/Binär-Verhalten und Tests als Vorlage nutzen; keinen zweiten Transport einführen. |
| Dateien live suchen und verlinken | Allgemeine Suche verwendet lokalen Index; Live-Listing liefert keinen `fileId` | WebDAV-Dateinamensuche sowie `oc:fileid` und interne Links ergänzen. Upstream sucht Namen, nicht Dateiinhalte. |
| Notes | Neue Textdateien möglich, aber keine registrierte Notes-App-API | Notes-Status, Liste und Lesen sind echte Ergänzungen; danach eigenes Create. Edit erst mit geprüftem Konfliktvertrag. |
| Deck | Board-Discovery und verwaltete Bestellkarten registriert; Board-/Stack-/Card-Methoden intern vorhanden | Allgemeine Leseoperationen ergänzen. Labels und Kommentare sind zusätzliche API-Funktionen. Bestellautomatik bleibt begrenzt. |
| Freigaben und Rechte | Keine allgemeine Agentfunktion für öffentliche Freigaben oder Rechteänderung | Upstream listet Shares und erzeugt/löscht öffentliche Links. Das ist kein fertiger Editor für Serverrechte. Schreibfunktionen nicht übernehmen. |

## Priorisierte Übernahmen

| Paket | Priorität und Umfang | Umsetzung in unserem Projekt | Abnahmekriterium |
| --- | --- | --- | --- |
| R0 Parser und Regressionen | Zuerst: Kategorienfehler; zusätzliche passende Fixtures | Vorhandene Parser und Tests ergänzen; keine neue Fähigkeit oder Berechtigung | Escapte Trennzeichen, Faltung und Unterkomponenten bleiben korrekt; bestehende Felder unverändert |
| R1 Kalenderagenda | Hoch: begrenztes Zeitfenster und Serienvorkommen | Bestehenden Calendar-Connector, Service, CLI und Katalog erweitern | EXDATE, verschobene Vorkommen, Zeitzonen, Tagesgrenzen und Teilergebnisse korrekt; Serienidentität erhalten |
| R2 Dateien lesen und finden | Hoch: kontrollierter Live-Read, Live-Dateinamensuche, interne Links | Bestehenden Files-Connector nutzen; neue registrierte Leseoperationen | Exakter erlaubter Pfad, ETag, Größen-/Zeitlimits, Scan und Inhaltsgrenzen; keine Suche außerhalb der ausgewählten Ressourcen |
| R3 Kontaktfelder | Danach: ADR, BDAY, TITLE; optional Kontakte ohne E-Mail | Vorhandene Modelle, Parser, Feldänderung und Katalog erweitern | Teiländerung erhält andere Adressen/Parameter; Mehrdeutigkeit, ungültiger Geburtstag und ETag-Konflikt blockieren |
| R4 Notes | Danach: App-Erkennung, Liste und Lesen; Create separat | Kleiner Notes-Adapter über vorhandenen NextcloudClient | Fehlende App, fehlendes Recht, leere und unvollständige Liste unterscheidbar; jede neue Fähigkeit ausdrücklich ausgewählt |
| R5 Aufgabenfelder | Danach: Ort, URL und CLASS | Vorhandenen VTODO-Workflow erweitern | Weggelassene Felder bleiben erhalten; URI wird korrekt behandelt; CLASS erweitert keine Rechte |
| R6 Deck allgemein lesen | Später: Boards, Spalten, Karten, Labels und Kommentare | Bestehenden Deck-Connector und eigene begrenzte Leseoperationen ergänzen | Konto/Board/Karte exakt gebunden, Teilfehler sichtbar, keine Ausweitung der Managed-Order-Schreibgrenze |

R0 kann unabhängig vorbereitet werden. R1 bis R6 werden jeweils erst nach
Einbindung in die Rechteauswertung ausgeliefert. Die Reihenfolge ist eine
Priorisierung, keine pauschale Freigabe für die Umsetzung aller Pakete.
Allgemeine Notes-/Deck-Updates und Kommentare schreiben benötigen später
eigene Verträge und sind nicht Teil dieser ersten Übernahmen.

## Bestätigter kleiner Parserbefund

Der lokale Methodenaufruf `NextcloudTasks._parse_vtodo` wurde mit einer
synthetischen VTODO und folgendem Feld ausgeführt:

```text
CATEGORIES:Sales\, West,Other
Erwartet: ["Sales, West", "Other"]
Erhalten: ["Sales\\", " West", "Other"]
```

Die Ursache ist die ungefilterte Trennung mit `split(",")` im
[Aufgabenparser](../personal_assistant/connectors/nextcloud/tasks.py).
Die escape-bewusste Trennung im Upstream war eine geeignete Verhaltensreferenz.
Am 22. November 2026 wurde der Fehler im lokalen Entwicklungsstand mit einem
eigenen Textlistenparser korrigiert. 25 gezielte Tests und zehn Unterfälle
bestanden; eine unabhängige Prüfung fand keine notwendigen Korrekturen.
Der allgemeine Einzelwertparser bleibt unverändert. Die Korrektur ist noch
nicht produktiv installiert; weitere Übernahmen bleiben eigene Entwicklungspakete.

## Anpassungen statt direkter Übernahme

- **Ressourcenwahl:** Unsere stabilen Konto-/Ressourcen-IDs bleiben maßgeblich.
  Keine automatische erste Sammlung, keine mehrdeutige Namensauswahl. Für
  Anschriften muss die Auswahl auch innerhalb eines Kontakts eindeutig sein.
- **Serien:** Eine expandierte Termininstanz braucht Ressourcen-ID, UID und
  Wiederholungsidentität. Ihre Darstellung ist keine Freigabe zur Änderung
  einer einzelnen Instanz oder der ganzen Serie. Begrenzte Agenda-Abfrage und
  bestehender Index-Sync bleiben getrennte Verträge.
- **Transport:** Vorhandenen NextcloudClient und den geschützten Kontotransport
  verwenden. Die Node-CLI, Umgebungszugänge und gebündelten Abhängigkeiten werden
  nicht als weiterer Agenten-Ausführungsweg eingebaut. Kein allgemeiner
  Shellzugriff und keine beliebigen lokalen Downloadziele.
- **Dateisuche:** Upstream setzt Suchtext und Kontoname direkt in XML ein und
  sucht kontoweit. Stattdessen XML sicher erzeugen, Wildcards eindeutig
  behandeln, erlaubte Wurzeln begrenzen und serverseitige SEARCH-Unterstützung
  prüfen. Fehlende Unterstützung ist ein sichtbarer Zustand; lokale
  Indexergebnisse werden nicht als Live-Suche ausgegeben.
- **Schreibschutz:** Upstream besitzt ETags für CalDAV/CardDAV. Trotzdem bleiben
  unsere ETag-Pflicht, Erwartungswerte, einzelne Freigabe und unmittelbare
  Ergebnisprüfung erforderlich. Ein `--confirm`-Text ist keine gleichwertige
  Freigabe. Notes-/Deck-Schreibwege ohne belastbare atomare Konfliktkontrolle
  werden nicht als sichere allgemeine Updates angeboten.
- **Diagnose:** Fehler einer einzelnen Sammlung dürfen nicht verschwinden.
  Jede neue Funktion liefert Zeitpunkt, Umfang, Limits, Teilfehler und
  Vollständigkeit. Ohne Hintergrundworker ist dessen Zustand „nicht anwendbar“.
- **Nicht übernehmen:** öffentliche Links, Löschoperationen, stilles
  Überschreiben, pauschale Berechtigungserweiterung sowie automatisch ergänzte
  Organisator-/Teilnehmerzusagen bei Terminen.

Die Ausführung bleibt: **OpenClaw-Plugin → typisierter Werkzeugvertrag →
Ressourcenprüfung und Freigabe → bestehender Python-Service/Connector →
Nextcloud → geprüfte Ergebnisevidenz**. Anmeldung, Auswahl und Diagnose werden
in unserer bestehenden OpenClaw-Einrichtung erweitert.

## Testfälle und offene Protokollfragen

| Bereich | Aus Upstream nutzbare Szenarien | Zusätzlich bei uns erforderlich |
| --- | --- | --- |
| Kalender | Serienexpansion, EXDATE, verschobenes Vorkommen; VTIMEZONE/VALARM verdecken keine Eventfelder | Fehlende Expansion, falsche Zeitfenster, DST, Ganztag, Mehrfachressourcen, Trunkierung und Reihenfolge; kein alter Serienmaster als neues Vorkommen |
| Kontakte | Gruppierte/faltbare ADR, sieben Komponenten, Teiländerungen; Geburtstag mit und ohne Jahr sowie Schalttage | Mehrere gleiche Adresstypen blockieren ohne eindeutige Auswahl; unveränderte Parameter, Photos und unbekannte Eigenschaften erhalten |
| Aufgaben | Zusätzliche Metadaten, URL mit Komma, Unterkomponenten erhalten | Escapte Kategorien, Feldlöschung nur ausdrücklich, ETag-Konflikt, CLASS ohne Zugriffswirkung |
| Dateien | Exakte Binärbytes, striktes UTF-8, kein lokales Überschreiben | Scanfehler, fremde Ressource, ETag 412, tatsächliche Größenüberschreitung und kontrolliertes Ziel |
| Suche | Dateinamensuche und fileId | XML-Sonderzeichen, Unicode, Wildcards, fehlendes SEARCH, einzelne propstat-Fehler, begrenzte Resultate |
| Notes und Deck | API-Feld- und Endpunktzuordnung | App fehlt versus 401/403, OCS-Fehler trotz HTTP 200, unvollständige Auswahl, Konto-/Boardbindung und Redaktion |

Zusätzlich weicht das Deck-Reorder-Routing ab: Upstream setzt die Zielspalte
in den URL-Pfad, unser Connector die Quellspalte und das Ziel in den Body.
Die [offizielle API-Dokumentation](https://github.com/nextcloud/deck/blob/main/docs/API.md)
und der inzwischen geschlossene
[Deck-Bericht 7933](https://github.com/nextcloud/deck/issues/7933) zeigen, warum
dies gegen die konkrete unterstützte Deck-Version getestet werden muss.
Das ist ein offener Kompatibilitätspunkt, kein nachgewiesener Fehler der
produktiven Installation. Eine spätere Prüfung muss die tatsächliche
Zielspalte erneut lesen, nicht nur HTTP 200 bewerten.

Upstream enthält zwei Mock-HTTP-Testsuiten. Der geprüfte sichtbare
[Bundle-Workflow](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/.github/workflows/bundle.yml)
prüft den reproduzierten CLI-Build und führt diese Tests nicht aus. Wir
übernehmen geeignete Szenarien in unsere Tests und führen sie selbst aus,
statt einen grünen Build als Funktionsnachweis zu verwenden.

## Quellen für die Umsetzung

- Kalender: [Upstream-Abfrage und Expansion](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L1116),
  lokal [Kalender-Connector](../personal_assistant/connectors/nextcloud/calendar.py)
  und [CLI](../personal_assistant/cli_parsers/nextcloud.py).
- Aufgaben: [Upstream-Kategorien](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L1279),
  [Zusatzfelder](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L1472),
  lokal [Aufgaben-Connector](../personal_assistant/connectors/nextcloud/tasks.py).
- Kontakte: [Upstream-Parser](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L1962),
  [Adressänderung](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L2030),
  lokal [Kontaktmodell](../personal_assistant/connectors/nextcloud/contacts.py)
  und [Kontaktänderungen](../personal_assistant/contact_tools.py).
- Dateien: [Upstream-Lesen](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L936),
  [Live-Suche](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L1006),
  lokal [Datei-Connector](../personal_assistant/connectors/nextcloud/files.py),
  [Workspace-Service](../personal_assistant/services/workspace.py) und
  [registrierte Werkzeuge](../personal_assistant/tool_catalog/nextcloud.py).
- Notes und Deck: [Notes](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L748),
  [Deck](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/index.js#L2380),
  lokal [Deck-Connector](../personal_assistant/connectors/nextcloud/deck.py) und
  [Bestellwerkzeuge](../personal_assistant/tool_catalog/orders.py).
- Testvorlagen: [Regressionsszenarien](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/test/regressions.mjs)
  und [Sicherheitsfälle](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/test/safety-features.mjs).

## Übernahme und Pflege

Bevor Code oder Fixtures übernommen werden, werden Herkunft, exakter Commit,
übernommener Umfang und lokale Änderungen dokumentiert. Der
[MIT-Lizenzhinweis](https://github.com/keithvassallomt/openclaw-nextcloud/blob/c6659f3e20761b6b681f3e9135103a205eb23720/LICENSE)
mit Copyright wird bei entsprechenden Übernahmen mitgeführt. Für eine
Python-Portierung zählt das übernommene Verhalten samt Tests; das JavaScript-
Bundle und seine Abhängigkeiten sind dafür nicht erforderlich.

Jede tatsächliche Übernahme erhält vor der Auslieferung einen Pflegeeintrag
mit Quellfunktion/Testfall, Ausgangscommit, lokalen Anpassungen und letzter
Upstream-Prüfung. Paketupdates ersetzen diesen Abgleich nicht. Die
[Stack-Update-Roadmap](STACK_UPDATE_ROADMAP.md) verbindet ihn mit
Komponentenübersicht, Sicherheitsmeldungen, Kompatibilitätsprüfung und dem
bestehenden Releaseweg; die Bedienung bleibt im OpenClaw-Plugin.

Der Vergleich ist abgeschlossen. Offen bleiben Detaildesign, Implementierung,
hermetische Tests und spätere ausdrücklich freigegebene reale Abnahmen je
Übernahmepaket. Die [Roadmap](NEXTCLOUD_ACCESS_ROADMAP.md) legt dafür Reihenfolge
und unveränderten Einrichtungsort in OpenClaw fest.
