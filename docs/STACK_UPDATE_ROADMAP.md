# Roadmap für Updates des gesamten OpenClaw Stacks

Stand: 21. November 2026. Status: geplante Erweiterung des bestehenden
Releaseverfahrens; keine Aktivierung von Updatejobs oder produktiven Änderungen.

Die Updatefunktion pflegt den Personal Assistant, seine externen Komponenten
und die Kompatibilität mit angeschlossenen Diensten. Jan sieht und bedient sie
im bestehenden OpenClaw-Plugin. Neue Versionen werden geprüft und als
zusammenpassendes Release ausgeliefert. Eine verfügbare Upstream-Version ist
noch kein für unsere Installation freigegebenes Update.

## Bestehende Grundlage

Der [Buildvertrag](architecture/IMAGE_SUPPLY_CHAIN.md) bindet externe Images
und ausgewählte Artefakte an Prüfsummen; Paket- und Werkzeugversionen sowie
lokale Patches sind versioniert festgelegt. CI erzeugt
Softwarestücklisten (SBOM), Herkunftsnachweise und signierte Rollenimages.
[Deployment](DOCKER_DEPLOYMENT.md) und [Recovery](architecture/RECOVERY_AND_RELEASE.md)
verwenden verifizierte Backups und einen Rollback bei fehlgeschlagenem Smoke.
Diese Verfahren werden weiterverwendet. Main-Promotion, Veröffentlichung und
produktive Aktivierung behalten ihre getrennten Freigaben.

Ergänzt werden die gemeinsame Updateübersicht, regelmäßige Prüfung auf neue
Versionen und Sicherheitsmeldungen, die Pflege übernommener Codeabschnitte und
die administrative Bedienung im Plugin. Die vorhandene
[Komponenteninventur](../scripts/generate-component-inventory.py) erfasst
Repositorydateien; sie wird mit Herkunft und Wartungsverantwortung externer
Komponenten verbunden. Ein weiteres unabhängig gepflegtes Versionsregister
darf den bestehenden Locks nicht widersprechen.

## Umfang und Zuständigkeit

| Bereich | Zu erfassen | Updateweg |
| --- | --- | --- |
| Personal Assistant und OpenClaw | Produktrelease, eingebetteter Core, eigene/externe Plugins, lokale Core-/UI-Patches | Gemeinsam geprüfte Rollenimages und passende Deploymentdateien |
| Laufzeit und Lieferkette | Direkte und indirekte Pakete, Basisimages, OS-Pakete im Image, Himalaya, Scanner, GitHub Actions und Build-/Testwerkzeuge | Versionierte Locks, CI-Prüfung und neuer Build; Entwicklungswerkzeuge gesondert kennzeichnen |
| Übernommener Quellcode | Ursprung, Commit, lokale Funktionen/Dateien, Anpassungen, Lizenzhinweise und Tests | Relevante Änderungen vergleichen und gezielt portieren |
| Antivirus | Engine, Scannerimage und Signaturidentität | Engine über Release; Signaturen über den bestehenden getrennten Aktualisierungsweg |
| Externe Dienste | Nextcloud-Server, Talk, Notes, Deck und weitere verwendete Apps; Ollama-Server und ausgewählte Modelle | Fähigkeiten und Kompatibilität prüfen; Aktualisierung durch den jeweiligen Betreiber |
| Host und Clients | Betriebssystem, Docker/Compose, gegebenenfalls GPU-Treiber und verwendete Talk-Clients | Unterstützte Voraussetzungen anzeigen; gesonderte Host-/Clientwartung |

Der Ollama-Proxy gehört zu unserem Release; der Ollama-Upstream und seine
Modelle können außerhalb des Stacks laufen. „Gesamter Stack“ bedeutet deshalb
vollständige Sicht auf Abhängigkeiten und Zuständigkeit. Externe Dienste
erhalten dadurch keine Updatefreigabe. Versionsangaben stammen nur aus
unterstützten Diagnosen; fehlende Angaben bleiben unbekannt. Modelle erhalten
Identität, soweit verfügbar Digest und fachliche Abnahme; ein Modellwechsel
ist kein gewöhnlicher Paketpatch.

Nextcloud benötigt einen zum Installationstyp passenden Upgradeweg.
Hauptversionen dürfen nicht übersprungen werden; ein Downgrade ersetzt keine
Wiederherstellung aus Backup. Ein Serverupdate bleibt eine eigene koordinierte
Betriebsaktion. Siehe [Nextcloud Upgradeverfahren](https://docs.nextcloud.com/server/stable/admin_manual/maintenance/upgrade.html).

## U0 Komponenten und Herkunft erfassen

**Entwicklungsstand vom 22. November 2026:** Das offline erzeugte
[Komponentenregister](architecture/EXTERNAL_COMPONENTS.md) ist implementiert
und unabhängig geprüft. Es leitet 86 Einträge aus den vorhandenen Quellen ab;
`check-repo.sh` erkennt fehlende Herkunft, geänderte Pins und veraltete
Projektionen. Externe Laufzeitversionen und noch offene Lizenzprüfungen bleiben
als unbekannt beziehungsweise offen gekennzeichnet. Das ist die Grundlage für
U1 bis U4; eine laufende Updateprüfung oder produktive Aktualisierung wurde
nicht eingerichtet.

Ein versionierter Wartungsnachweis ergänzt die vorhandenen Locks und
Release-Metadaten: Herkunft, Einbindungsart, Version/Commit/Digest,
verantwortlicher Wartungsbereich, Update-/Advisoryquelle, Lizenzhinweise,
lokale Patches, Kompatibilitätsanforderungen und zugehörige Tests. Build und
Übersicht verwenden konsistent daraus abgeleitete Angaben. Private
Instanzdaten bleiben außerhalb der öffentlichen Buildmetadaten.

Bei Übernahmen aus `keithvassallomt/openclaw-nextcloud` werden Quellfunktion
beziehungsweise Testfall, Ausgangscommit, lokale Fundstelle, Anpassungen und
letzter geprüfter Upstream-Commit festgehalten. Ein neuer Commit löst einen
Vergleich der betroffenen Bereiche aus. Die Entscheidung „übernehmen“, „nicht
relevant“ oder „lokal bereits gelöst“ bleibt begründet nachvollziehbar. Ein
Paketupdate ersetzt keine übersetzte Python-Funktion. Ein Fork benötigt
zusätzlich seinen Upstream-Ausgangspunkt und die Reihe eigener Änderungen.

**Abnahme:** Jede ausgelieferte externe Komponente und Codeübernahme hat
eine Herkunft und einen Pflegeweg. Lizenzhinweise werden auch für kopierte
Dateien und Fixtures geprüft; eine generierte SBOM allein genügt dafür nicht.

## U1 Updates und Sicherheitsmeldungen erkennen

Ein ausdrücklich aktivierter Prüfjob kontrolliert freigegebene Quellen und
bereitet begrenzte Änderungsvorschläge vor. Vorschlag für den Start: täglich
prüfen, normale Updates wöchentlich bündeln und Sicherheitsmeldungen priorisiert
anzeigen. Ein Wartungsverantwortlicher bewertet Dringlichkeit, Supportende und
Relevanz für unsere Verwendung. Archivierte Projekte und überfällige Prüfungen
werden sichtbar. Diese Roadmap aktiviert den Job nicht.

[Dependabot](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-version-updates)
erstellt Update-PRs aus unterstützten Paketmanifesten.
[Renovate Custom Managers](https://docs.renovatebot.com/modules/manager/regex/)
können zusätzlich eigene Versionsfelder erkennen. Empfehlung für das
Detaildesign: Renovate am vorhandenen Supply-Chain-Lock erproben; Dependabot
bleibt eine Alternative für Standardmanifeste. Ein Werkzeug wird gewählt,
damit keine konkurrierenden Update-PRs entstehen. Quellcodeübernahmen benötigen
zusätzlich den begrenzten Upstream-Vergleich aus U0.

Pins, Paketlocks, Integritätswerte, Patchnachweise und erzeugte Builddateien
müssen gemeinsam validiert werden. Ein Bot übernimmt keine fachliche
Patchanpassung oder Freigabe. Ausgewählte Artefakte und das vorige geprüfte
Release müssen für Build und Rollback verfügbar bleiben; entfernte Pins oder
Registry-Aufräumregeln dürfen keinen unbemerkten Verlust des Rückwegs verursachen.
Aufbewahrung und Bezugswege müssen zum jeweiligen Lizenzvertrag passen.

**Abnahme:** Paket-/Imageupdate, Sicherheitsmeldung und relevante Codeänderung
ergeben nachvollziehbare Vorschläge. Timeout, HTTP 403, Rate-Limit, verschwundene
Artefakte und archivierte Repositories bleiben sichtbare Befunde mit Zeitpunkt
und letzter erfolgreicher Prüfung. Sie bedeuten nicht „aktuell“. Automatische
Installation und automatisches Zusammenführen bleiben aus.

## U2 Ein kompatibles Release prüfen

Der Kandidat verbindet Produktrelease, getesteten Quellcommit, Rollen- und
Scanner-Digests, Deploymentbundle, Plugin-/Toolvertrag, Konfigurations- und
Datenbankschema sowie SBOM, Herkunft und Signaturen. Das Deploymentbundle
umfasst Compose und Hostskripte mit eigener überprüfter Revision und gesichertem
Rückweg. Benötigte Zwischenupgrades werden vorab angegeben; beliebige neueste
Einzelkomponenten ergeben kein freigegebenes Release.

Die Abnahme prüft OpenClaw-Plugin-API, gebundene UI-Patches, Ressourcenrechte,
Freigaben, ETags, Migrationen und betroffene Nextcloud-/Talk-APIs. Hinzu kommen
Ollama-Proxy und Upstream, Antivirus-Bereitschaft und bestehende Integrations-
und Recoverytests. Eine Kompatibilitätsmatrix nennt tatsächlich getestete
Server-/App-Kombinationen. Isolierte Testdaten ersetzen keine produktive Abnahme.

Bestehende Sicherheits-, Secret-, SBOM-, Herkunfts- und Signaturprüfungen bleiben
verbindlich. Nicht erreichbare Signaturprüfdaten blockieren die Freigabe mit
einem klaren Diagnosebefund. Eine Netzstörung erlaubt keinen Prüfungsbypass.

**Abnahme:** Exakte Quellen, überprüfte Buildinputs und alle nötigen Ergebnisse
sind dem Kandidaten zugeordnet. Lokale Ressourcenbeschränkungen und ausgeschaltete
Jobs überstehen Migration und Neustart; neue Fähigkeiten erhalten keine
automatische Freigabe. Fehlende oder gemischte Releasebelege blockieren.

## U3 Updates im OpenClaw Plugin bedienen

Die administrative Einrichtung erhält **System und Updates**. Pro Komponente
werden belegter Iststand, verfügbare und für uns freigegebene Kandidaten,
Prüfzeitpunkt, Quelle, Kompatibilität, Supportstatus und Zuständigkeit angezeigt.
Produktrelease und eingebettete Komponenten bleiben getrennt.

Jan kann „Jetzt prüfen“ aufrufen, Details ansehen und einen geprüften Updateplan
auswählen. Dieser zeigt Änderungen, Zwischenstände, Ausfallzeitabschätzung,
Backupumfang, Migrationen und Rollbackgrenzen. „Verfügbar“, „in Prüfung“,
„freigegeben“, „blockiert“, „veraltet“ und „unbekannt“ unterscheiden sich von
„installiert und geprüft“. Die Vorschau bindet Zielrelease, Digests,
Ausgangszustand und Planrevision; deren Änderung macht die Freigabe ungültig.
Aktivierung bleibt eine ausdrücklich freigegebene administrative Aktion.

**Abnahme:** Teilfehler und extern zu wartende Dienste sind sichtbar. Fehlt der
Installationsweg, bleiben Prüfen und Anzeigen nutzbar, aber eine Update-Schaltfläche
darf keine Ausführung vortäuschen. Eine Talk-Nachricht oder Agentenantwort ersetzt
den administrativen Kontrollpfad nicht.

## U4 Installieren und den Endzustand belegen

Das Plugin übergibt einen begrenzten Plan an einen administrativen Executor auf
dem Host. Der Gateway erhält weder Docker-Socket noch allgemeine Shell- oder
Hostschreibrechte. Der Executor verwendet bestehende Deployment-, Backup- und
Rollbackverfahren und akzeptiert nur validierte Releasepläne, keine beliebigen
Befehle, Repositories oder Image-URLs. Transport, Authentisierung und Installation
dieses Executors gehören ins Detaildesign.

Vor Aktivierung werden Signaturen, Ausgangszustand, Platz, Migrationsfähigkeit
und startbarer Rückweg geprüft. Unter exklusiver Updatesperre folgen kontrollierter
Writerstop, verifiziertes lokales Backup und gegebenenfalls externer Snapshot.
Danach werden die zusammengehörigen Deploymentdateien und Images installiert,
Migrationen geprüft und die Rollen in der bestehenden Reihenfolge gestartet.
Zugangsdaten und Ressourcenrichtlinien bleiben geschützt; zuvor ausgeschaltete
Jobs werden nicht aktiviert. Pro Schreibdomäne bleibt genau ein Writer zuständig.

Nach dem Start werden Produktidentität, tatsächliche Digests, Rollen, Jobs,
Ollama-Proxy/Upstream, Antivirus sowie Nextcloud-Anmeldung, ausgewählte Ressourcen,
Rechte und Talk-Worker erneut geprüft. Eine gespeicherte Talk-Auswahl beweist
keine Zustellung. Ein echter Testaustausch wird separat autorisiert und belegt;
Standardprüfungen schreiben keine Testnachrichten oder Dateien auf den Server.

Der vorhandene [Deploypfad](../docker/scripts/deploy.sh) aktiviert den produktiven
Mail-Write-Smoke standardmäßig. Der neue Updateplan muss deshalb den Prüfmodus
explizit festlegen: Ohne separat freigegebene Schreibprobe wird der vorhandene
Lesemodus mit `OPENCLAW_WRITE_TEST_ENABLED=false` verwendet. Eine Schreibprobe
benötigt die vorgesehenen verifizierten externen Backup-/Restore-Nachweise.
Ein ausgelassener Schreibtest bleibt als „nicht geprüft“ sichtbar und belegt
keine erfolgreiche Schreibabnahme; fehlende Produktionsbereitschaft darf nicht
durch einen grünen Lesetest verdeckt werden.

Der Ablaufzustand auf dem Host überlebt den Pluginneustart. Doppelklick und
erneuter Verbindungsaufbau starten keinen zweiten Rollout. Ein verlorener
Browserkontakt ist kein Erfolgsnachweis. Ausstehende oder zustellungsunsichere
Talk-/Mail-Aktionen werden beim Neustart nicht wiederholt.

Bei fehlgeschlagenem Smoke stoppt der Kandidat; der bestehende Rollback stellt
den verifizierten lokalen Ausgangszustand wieder her. Fehler beim Restore
bleiben sichtbar. Externe Daten und separat aktualisierte Server erfordern ihren
eigenen geprüften Wiederherstellungsweg. Ein Imagewechsel belegt das nicht.

**Abnahme:** Wiederholter Klick, Gatewayneustart, ungültiges Image, fehlender Platz,
fehlerhafte Migration, Verbindungs- und Rollbackfehler sind getestet. Historie
und Diagnose zeigen Ergebnis, Belege und Grenzen ohne Inhalte oder Zugangsdaten.
Ein gemischter oder ungeprüfter Endzustand bleibt fehlgeschlagen beziehungsweise
unklar.

## Reihenfolge und Verbindung zur Nextcloud Roadmap

| Schritt | Lieferung | Zeitpunkt |
| --- | --- | --- |
| U0 | Komponenten- und Herkunftsnachweise | Vor der ersten externen Übernahme aus R0 bis R6 |
| U1 und U2 | Updatevorschläge und geprüfte Releasekandidaten | Parallel zur Rechteverwaltung; für externe Übernahmen verbindlich |
| U3 | Updateübersicht und Planvorschau im Plugin | Früher nutzbarer Schnitt nach Bestands- und Kandidatenprüfung |
| U4 | Expliziter Rollout mit dauerhafter Fortschrittsanzeige und Recovery | Nach Abnahme des administrativen Executors |

Die [Nextcloud-Roadmap](NEXTCLOUD_ACCESS_ROADMAP.md) führt dies als NC8. NC5
verantwortet konkrete Wiederverwendung, NC7 die Abnahme. Bis zur geprüften
U4-Bedienung bleiben die bestehenden Deploymentwerkzeuge der Installationsweg.
Zuerst kommen Inventur, Pflegebelege und verlässliche Updatehinweise; darauf
baut die bedienbare Updatefunktion auf.
