# R0, U0 und NC0–NC2: Entwicklungsabnahme

Stand: 22. November 2026. Ausgangscommit: `0bad341`.
Dieser Bericht beschreibt Änderungen im Entwicklungsworktree. Er ist kein
Nachweis für eine Installation oder eine Verbindung zu einem echten Konto.
R0, U0 und NC0–NC2 sind umgesetzt. Die abschließende lokale Repository- und
Browserabnahme ist erfolgreich. Alle im Review konkret belegten Befunde wurden
korrigiert; der Umfang der unabhängigen Nachprüfungen steht unten.

## Ergebnis und Umfang

| Etappe | Ergebnis im Quellcode | Wesentliche Prüfung |
| --- | --- | --- |
| R0 | Escapte Kommas in Aufgaben-Kategorien bleiben Teil einer Kategorie. | Backslash-Parität, Faltung, Unicode, VALARM und Erstellen/Lesen. |
| U0 | 86 Einträge im abgeleiteten Komponentenregister mit Herkunft, Pins, Pflegeweg und offenen Angaben. | Drift in Docker-, Paket-, Workflow- und Patchquellen; exakte Signaturidentität. |
| NC0 | Getrennte Nachweise für Identität, DAV-Komponenten, konkrete Ordnerrechte und Talk-Laufzeit. | Fehlende, mehrdeutige, teilweise, veraltete und fehlgeschlagene Messungen. |
| NC1 | Dauerhafte kontogebundene Auswahl pro Komponente und Ordner, auch vor Ausgabe und Hintergrundzugriff. | CLI/Connector, echter SQLite-Index, Cache, ActionPlan, Native-Freigabe, Widerruf und A→B→A. |
| NC2 | Zugriff im bestehenden OpenClaw-Plugin über signierte Vorschau und separate Bestätigung. | Signierte lokale RPCs, zwei Akteure, Konflikte, einmaliger Verbrauch, Browserreload und unklare Antwort. |

Die Stufen sind Aus, Lesen, Lesen/Erstellen und unterstütztes
Lesen/Erstellen/Ändern. Die Oberfläche zeigt **Nextcloud erlaubt**,
**Für den Agenten freigegeben** und **Aktuell wirksam**. Dateien unterscheiden
Dateien-Erstellen und Unterordner-Erstellen. Neue Konten beginnen ohne Freigaben;
eine bestehende Installation erhält eine begrenzte Migrationsvorschau.

Fachliche Einzelfreigaben, ETags, Antivirusscan und Jobschalter bleiben eigene
Voraussetzungen. Ein allgemeiner Dateizugriff aktiviert keine Rechnungsablage;
lokale Rechnungs-, Auftrags- und Portfoliohistorie behält ihren eigenen Zugriff.
Mehrbenutzertrennung, Import/Export, weitere Fachkonfigurationen sowie U1–U4
liegen außerhalb dieses Entwicklungsschritts. Das Register startet keinen
Updatejob und importiert kein externes Plugin.

## Nachweisgrenzen

- Die Python-Prüfungen benutzen temporäre Stores, SQLite-Datenbanken, synthetische
  DAV-/MIME-Daten und begrenzte lokale Prozesse. Es wurden keine produktiven
  Ressourcen geändert, Freigaben erweitert oder Jobs aktiviert.
- Firefox bedient die ausgelieferten Setup-Assets mit nativen Klicks gegen den
  signierten Test-Gateway. Auswahl, Vorschau, Bestätigung, Neuladen, Konflikt und
  Verbindungsabbruch nach erfolgreichem Speichern sind abgedeckt. Der Schreibaufruf
  wird bei unklarer Antwort nicht automatisch wiederholt.
- Die unveränderte OpenClaw-Einstiegskarte und deren Cacheintegration benötigen
  den extrahierten gepinnten Renderer. Dieser lag lokal nicht vor; dieser
  zusätzliche Test ist nicht als bestanden gewertet.
- Die beiden optionalen Image-Smokes für den Setup-Owner und den Talk-Proxy
  benötigen ausdrücklich ausgewählte lokale Testimages. Ohne diese Images sind
  sie übersprungen; Quellcode- und Fixturetests ersetzen diese Buildabnahme nicht.
- Die Wiederherstellungsprobe verwendet drei isolierte historische Layouts und
  tatsächlich verifizierte synthetische Quellmanifeste. Sie prüft Archiv,
  Prüfsummen, SQLite-Integrität und lokale Wiederherstellung. Sie belegt weder
  eine Produktionssicherung noch einen Remote-Rollback oder einen signierten
  Image-Build.
- Echte Nextcloud-/Talk-Abnahme und produktive Aktivierung sind ein gesonderter
  Auftrag. Vorher gelten weiterhin verifiziertes Backup, signiertes Release und
  die bestehenden Deployment- und Rückfallprüfungen.

## Prüfprotokoll

Der abschließende Lauf von `./scripts/check-repo.sh` bestand mit **2.348 Tests,
zwei erwarteten Image-Test-Skips und 74,17 % kombinierter Abdeckung**. Laufzeit der
Testsuite: 464,98 Sekunden. Die bestehenden Risiko-Abdeckungsvorgaben,
Dokumentation, Kataloge, Build-/Lockverträge, 732 Manifesteinträge, statische
Analyse und drei isolierte M8-Wiederherstellungsszenarien waren erfolgreich.
Die letzte Wiederherstellungsprobe vom 22. November 2026 um 04:36 UTC stellte
alle drei synthetischen Bestände exakt wieder her; es ging keine im jeweiligen
Snapshot enthaltene lokale Operation verloren. Das ist keine Produktionssicherung.

Die erneute direkte Firefox-Abnahme mit Version 158.0.1 bestand mit neun
signierten Diagnoseaufrufen und zwölf signierten Zugriffsaufrufen. Sie prüfte
native Bedienung, zugängliche Namen, lesende Vorschau, Bestätigung und Reload,
ungültige alte Bestätigungen, Konflikt und unklare Antwort ohne Schreibwiederholung
sowie das Verwerfen eines alten Ordnernachweises nach unklarem Refresh.
Der Browser-Dauerspeicher blieb unverändert.

Die Taskprüfungen für Kategorien, Komponentenregister, Diagnose, Policy/Store,
direkte Fachzugriffe, Hintergrund-/Freigabepfade und Plugin-Einrichtung sind
unabhängig abgeschlossen; keine Befunde aus diesen Teilprüfungen sind offen.
Der letzte eng begrenzte Nachtest der sieben Hintergrundkorrekturen bestand mit
29 Tests. Einzelne Testläufe überlappen und werden nicht zu einer Gesamtsumme
addiert. Ruff und mypy akzeptieren ausschließlich ihre bestehenden Baselines;
die Baseline-Dateien wurden nicht erweitert.

Der erste vollständige Lauf beendete 2.308 Tests erfolgreich und meldete sieben
Fehler einschließlich zweier Untertests sowie zwei erwartete Image-Test-Skips.
Ein neuer Importzyklus, die Kontakt-Digest-Referenz und ältere Testfixtures für
Zugriffsmodus, Root-Auswahl und Imagevertrag wurden gezielt korrigiert. Die
betroffenen vier Suiten bestanden danach mit 65 Tests und 21 Untertests; weitere
170 Regressionstests prüften Policy, Verbindung, native Freigaben und Setup-Rolle.
Die Nachprüfungen der Einrichtung bestätigten die dauerhaften negativen
Kontobelege, aktuelle Ordneridentitäten, widerrufbare verdeckte Auswahl,
zeitlich korrekte Anzeige und zutreffende Migrationsvorschau. Die
Abdeckungsvorgaben waren bereits erfüllt.

Die Gesamtprüfung bestätigte die 122 eingefrorenen Dateidigests und meldete zwei
wichtige und einen kleineren Befund: unnötig breite CalDAV-Einzelabfragen,
dauerhaft ungeprüfte Sammlungen hinter dem Prüflimit sowie eine historische
Ordneranzeige nach unklarer Refresh-Antwort. Die unabhängige begrenzte Nachprüfung
bestätigte die drei Korrekturen und fand zwei unmittelbare Restfehler:
unzureichenden Nachweis eines fehlenden Zielobjekts und vermischte Diagnosealter.

Auch diese beiden Restfehler sind korrigiert. Der Koordinator hat den vollständigen
Restdiff gelesen und anschließend neun ActionPlan-/SQLite-/Owner-Regressionen,
sechs Prüfungen mit gepinntem CalDAV-Transport und zwei signierte Browsertests
selbst ausgeführt. Sie bestanden; der oben genannte Gesamtlauf prüfte den
gleichen korrigierten Quellcode. Ein belegtes oder ungewisses Ziel führt weder
zum zweiten PUT noch zur Verschlechterung eines abgeschlossenen Plans. Nur ein
eindeutiger 404-Beleg des exakten Kindes innerhalb einer gültigen Multistatus-
Antwort erlaubt nach erneuter Freigabe die Wiederanlage. Diagnosebereiche
behalten ihre eigenen serverseitigen Messalter. Eine zusätzliche allgemeine
Reviewrunde fand nicht statt; Entscheidung 19 dokumentiert diese Abgrenzung.

Die Testläufe überlappen und werden nicht addiert. Nach dem Gesamtlauf wurden
nur dieser Bericht und abgeleitete Inventar-/Manifestdaten aktualisiert und
erneut geprüft. Der Entwicklungsworktree und seine Detailprotokolle bleiben
erhalten. Commit, Veröffentlichung, Image-Build und produktive Aktivierung
wurden in diesem Auftrag nicht ausgeführt.

## Getroffene Entscheidungen und Kosten bei Fehlannahmen

Die folgenden Entscheidungen übernehmen alle als `Ruling` protokollierten
Abwägungen aus beiden Entwicklungsplänen in ihrer Reihenfolge. Der bestehende
Worktree enthält weiterhin die Detailprotokolle, bis ein versionierter
Entwicklungsstand vorliegt.

| Nr. | Entscheidung | Konsequenz bei falscher Annahme |
| --- | --- | --- |
| 1 | Foundation: Änderungen und Reviews bleiben im Worktree; nur die Koordination erzeugt Gesamtmanifest und Inventar, vorhandene Dokumentarbeit bleibt erhalten. | Ohne späteren Commit ist der Stand noch nicht dauerhaft veröffentlicht; verlorene lokale Änderungen wären erneut zu erstellen. |
| 2 | U0 umfasst auch die Workflow-Aufzählung im M7-Prüfer. Nicht unterstützte Deklarationen werden abgelehnt, ohne neue Parserabhängigkeit. | Neue gültige Syntax benötigt eine bewusste Parsererweiterung; sonst blockiert die Prüfung einen Build. |
| 3 | NC0–NC2 behalten denselben Worktree-/Manifestablauf ohne Teilcommits. | Die vollständige Änderung muss gemeinsam gesichert und versioniert werden. |
| 4 | Aktivierungsnachweis und stabiler Lock nutzen den bestehenden Verbindungsmount; Initialisierung erfolgt ausschließlich beim verifizierten Offline-Deployment mit eigenem Beleg. | Fehler im Installationspfad blockieren Nextcloud statt einen Legacy-Rückfall zu erlauben; Wiederherstellung muss den Nachweis mitnehmen. |
| 5 | Firefox prüft Setup-Assets direkt; die unveränderte Core-Einstiegskarte wird bei fehlendem Renderer separat als ungeprüft ausgewiesen. | Ein Fehler nur am Core-Einstieg oder Cache würde erst in der zusätzlichen Build-/Installationsabnahme auffallen. |
| 6 | NC0-Entwicklung läuft nach U0-Implementierung parallel zu deren disjunktem Review. | Eine neue gemeinsame Abhängigkeit verlangt erneute Integrationstests; keine Etappe darf deshalb ungeprüft aktiviert werden. |
| 7 | Policy/Store und Diagnose dürfen an vereinbarten Schnittstellen parallel entstehen. | Änderungen an Belegfeldern benötigen Nachtests aller Verbraucher. |
| 8 | Vordergrundzugriffe beginnen gegen stabile Typen vor Abschluss des Offline-Migrationsreviews; neue Helper bleiben getrennt. | Ein revidierter Storevertrag erzeugt Nacharbeit und erneute Verbraucherprüfung. |
| 9 | Rollen mit vorgeschriebenem Mount blockieren bei fehlendem Zustand. Absichtlich ungemountete Rollen bleiben für Nextcloud unkonfiguriert. | Ein falsch deklarierter Rollenbedarf führt zu sichtbarer Start-/Zugriffssperre und benötigt eine korrigierte Auslieferung. |
| 10 | Datei- und Fachkomponenten haben getrennte Implementierer, gemeinsamen Zugriffskontext und gemeinsame Abnahme. | Missverständnisse an dieser Schnittstelle benötigen eine kombinierte Korrektur. |
| 11 | Native Bindung darf nach Freeze des Vordergrunds parallel entstehen; sie ersetzt keine Prüfung im Python-Prozess. | Ein veränderter Übergabevertrag benötigt beide Laufzeittests erneut. |
| 12 | SQLite-/Sync-Arbeit beginnt nur in freigegebenen disjunkten Dateien während des Vordergrundreviews. | Spätere Änderungen an gemeinsamen Connectoren verlangen erneute integrierte Tests. |
| 13 | Die erlaubte erneute Create-only-Anlage eines nachweislich fehlenden Ziels bleibt mit aktueller Bindung und nötiger frischer Freigabe erhalten. Ungewisse oder veraltete Aktionen dürfen nicht wiederholt werden. | Eine falsche Wiederholungsabgrenzung könnte doppelte Änderungen verursachen; dafür prüfen ActionPlan-Regressionen den tatsächlichen Requestzähler. |
| 14 | Kontaktanlage aus Mail bekommt einen Vorschau-Digest: im aktiven Modus erforderlich, für Legacy optional. Die lesende Vorschau speichert keine Mailinhalte. | Aktive ältere Aufrufer ohne Digest müssen erst eine neue Vorschau erzeugen; CLI, Katalog und native Schemas müssen synchron bleiben. |
| 15 | NC2-Quellcode darf gegen stabile Verträge parallel zu Task 4 entstehen; Freigabe setzt deren geprüfte Integration voraus. | Änderungen an Hintergrundbindung oder Policy verlangen erneute Setup-Prüfung vor Auslieferung. |
| 16 | Fehlgeschlagene Kontoprüfungen und neue eigene Beobachtungen ausgewählter Ordner bleiben als private Metadaten im vorhandenen Store über lokale Änderungen und Neustarts erhalten. | Eine falsche Gültigkeitsdauer könnte Zugriff unberechtigt sperren oder alte Rechte wieder zulassen; Nachtests prüfen Neustart, lokale Änderungen und tatsächliche Zugriffe. |
| 17 | Bei Kollisionen mit der Geheimnis-Redaktion gibt ein begrenzter, revisionsgebundener und authentifizierter Selektor ausschließlich den Widerruf eines gespeicherten Zugriffs frei. | Eine mehrdeutige Zuordnung könnte den falschen Zugriff entziehen oder einen Widerruf verhindern; signierte Aufrufe sowie Kollisions- und Manipulationstests prüfen diese Grenze. |
| 18 | Die begrenzte Sammlungserkennung verwendet den bestehenden signierten Refresh. Ungeprüfte oder geänderte Ziele kommen zuerst, danach die ältesten eigenen Messungen. Nur frische Belege unveränderter Ziele desselben Kontos dürfen mit ihrem ursprünglichen Zeitpunkt übernommen werden. | Eine erneuerte Zeitangabe würde Rechte überbewerten; eine unfaire Reihenfolge könnte spätere Sammlungen weiterhin ausschließen. Owner-/Transporttests prüfen Budget, Fortschritt, Fehlerpräfix und Belegalter. |
| 19 | Zwei unmittelbar belegte Restfehler werden im bereits freigegebenen Entwicklungsumfang korrigiert, obwohl der verwendete Ablauf nur eine Schlusskorrektur vorsah. Ein bekannter wichtiger Fehler wird nicht als fertiger Stand übergeben. Die Korrektur bleibt auf eindeutigen Abwesenheitsnachweis und getrennte Diagnosealter begrenzt; statt einer weiteren allgemeinen Reviewrunde folgen konkrete Regressionen und der Gesamtlauf. | Zusätzlicher Aufwand und erneutes Regressionsrisiko; echte ActionPlan-/SQLite-Nachweise, signierte UI-Prüfung und vollständige Verifikation sind deshalb erforderlich. |

## Weiterführende Dokumente

- [Nextcloud einrichten](NEXTCLOUD_SETUP_UI.md)
- [Nextcloud-Roadmap](NEXTCLOUD_ACCESS_ROADMAP.md)
- [Update-Roadmap](STACK_UPDATE_ROADMAP.md)
- [Komponentenregister](architecture/EXTERNAL_COMPONENTS.md)
- [Foundation-Plan](superpowers/plans/2026-11-22-nextcloud-foundation.md)
- [NC0–NC2-Plan](superpowers/plans/2026-11-22-nextcloud-access.md)
