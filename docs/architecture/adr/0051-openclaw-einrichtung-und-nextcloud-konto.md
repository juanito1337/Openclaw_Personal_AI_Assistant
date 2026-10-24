# ADR-0051: OpenClaw-Einrichtung und Nextcloud-Kontoanbindung

- Status: Accepted (Entwicklungsentscheidung; keine produktive Aktivierung)
- Datum: 2026-10-22
- Ergaenzung: 2026-10-24, eng begrenzte Buildanpassung fuer den Browser-Einstieg
- Entscheider: Architecture Maintainers, Security Maintainers, Operations Maintainers
- Betroffene Milestones: M17, M18

## Kontext

Die vorhandene native Werkzeugbruecke besitzt noch keine administrative
Einrichtungsoberflaeche. Nextcloud-Zugang und einzelne Ressourcen werden bisher
getrennt konfiguriert. Eine URL allein authentisiert kein Konto. Der vorhandene
Talk-Bot benoetigt zudem einen auf dem Nextcloud-Server installierten Bot samt
Webhooksecret; diese Rechte folgen nicht aus einem Benutzer-App-Passwort.

## Entscheidung

Die vorhandene OpenClaw-Erweiterung erhaelt eine gleichurspruengliche
Einrichtungsoberflaeche und administrative Endpunkte. Die administrativen Aufrufe
verwenden APIs des gepinnten Cores. Operatorauthentisierung und administrative Autorisierung
werden vor jeder zustandsaendernden Anforderung geprueft; die Endpunkte sind keine
Agentenwerkzeuge. Die bestehende read-only-Konfigurationsgrenze des Gateways bleibt
erhalten. Ein eigener begrenzter Konfigurationsowner verwaltet die neue
Kontoanbindung; er besitzt weder Docker-Socket noch generische Shellschnittstelle.

Jeder GUI-Aufruf besitzt zusaetzlich eine Ed25519-Signatur des bereits gepaarten
Browsergeraets ueber Operation, exakte Parameter, Geraeteidentitaet, Zeitpunkt und
Einmalnonce. Der Owner prueft diese unabhaengig gegen die eng read-only gemountete
Gateway-Geraeteregistry, einschliesslich aktueller operator.admin-Freigabe und
Tokenwiderruf. Ein vom lokalen Prozess behaupteter Actor oder Socketzugriff reicht
nicht. Die private Signaturidentitaet bleibt im Browser; Gatewaytokens werden
weder in URLs noch an den Einrichtungsowner weitergegeben. Nonces bleiben fuer
das ganze Signaturgueltigkeitsfenster gegen Wiederholung gesperrt.

GUI-Deployments sperren generische Runtime- und Dateisystemwerkzeuge des Agenten
ueber OpenClaws Tool-Denyprofil und zusaetzlich den Pluginhook. Die nativen
Fachwerkzeuge bleiben verfuegbar. Dies ist eine notwendige Grenze, weil ein
beliebiger Shellprozess mit Gateway-UID sonst dessen Geraeteregistry manipulieren
koennte. Das Gateway und seine unveraenderlichen Plugins bleiben vertrauenswuerdige
Runtimekomponenten. Eine blosse Pfadregex ist keine ausreichende Isolation.

Der gepinnte Core unterstuetzt eine Plugin-Sidebarbeschreibung. Sein Renderer
bettet die Seite jedoch in ein iframe ein. Die vorhandenen Sandboxmodi erlauben
weder Popups noch Top-Level-Navigation; eine Plugin-Bridge zum Oeffnen eines Tabs
existiert dort nicht. Der urspruengliche Link mit `target="_blank"` innerhalb
dieses iframes ist deshalb kein funktionsfaehiger Einstieg. Eine pauschale
Erweiterung der Sandboxrechte scheidet aus.

Die Ergaenzung vom 2026-10-24 erlaubt stattdessen eine lokale, deterministische
Aenderung des gepinnten oeffentlichen Control-UI-Assets beim Imagebuild. Nur wenn
alle drei Descriptorwerte exakt uebereinstimmen, rendert der Host eine eigene
inhaltsfreie Linkkarte:

- `pluginId = personal-assistant-tools`
- `id = personal-assistant-nextcloud`
- `path = /plugins/personal-assistant-tools/nextcloud`

Das Linkziel ist die feste Konstante
`/plugins/personal-assistant-tools/nextcloud/setup`, mit `target="_blank"` und
`rel="noopener noreferrer"`. Weder Descriptorfelder noch URL-Parameter liefern
ein frei waehlbares Ziel. Alle anderen Pluginseiten behalten unveraendert ihren
iframe und dessen bisherige Sandbox. Der Benutzerklick geschieht im Host und
oeffnet die Einrichtung gleichurspruenglich in einem eigenen Tab ohne Opener.
Die bestehende OpenClaw-Geraeteanmeldung bleibt Voraussetzung; der Link
uebertraegt keine Tokens und erteilt keine RPC- oder Kontoberechtigung.

Dies ist eine ausdruecklich dokumentierte Aenderung am abgeleiteten Core-Asset,
keine zusaetzliche Upstream-Plugin-API. Ein Coreupgrade oder ein Patch am laufenden
Produktionscontainer ist damit nicht autorisiert. Eingangs-SHA-256 und erwartete
Renderstelle werden vor der Ersetzung geprueft; ein abweichendes Asset bricht den
Build ab. Auch die benoetigte Service-Worker-Anpassung ist auf einen festen Pfad
begrenzt: Nur der geaenderte Pluginrenderer wird zuerst aus dem Netz geladen,
statt eine vorhandene Cachekopie vorzuziehen. Der bisherige Offlinefallback und
die Cachebehandlung aller anderen Assets bleiben erhalten. Die Ausnahme wird
relativ zum Service-Worker-Scope bestimmt und funktioniert auch bei einem
Control-UI-Unterpfad. Der Service Worker erhaelt die eigene Asset-Cache-ID
`2026.7.1-pa-setup-launch-v1`. Seine vorhandene Aktivierungsnachricht loest nach
Uebernahme der Browserclients den vorhandenen Reloadpfad des Cores aus. Eine
erneute Registrierung identischer Workerbytes aktiviert ihn nicht erneut;
dadurch entsteht kein Reload bei jedem Seitenaufruf. Diese Cache-ID aendert
weder Produktrelease noch Upstream-Coreversion.

Eingangs- und Ergebnis-Hashes beider Assets gehoeren zum versionierten Image-
und Supply-Chain-Vertrag. Bei einem kuenftigen Corewechsel wird der Patch erneut
geprueft und nach Moeglichkeit durch eine unterstuetzte Upstream-Schnittstelle
ersetzt.

Nextcloud Login Flow v2 verbindet die vom Operator eingegebene HTTPS-Basisadresse
mit einer ausdruecklichen Anmeldung und Freigabe im Nextcloud-Browserfenster.
Polltoken und App-Passwort bleiben serverseitig. Loginvorgaenge sind zeitlich und
an eine authentisierte Operatorsitzung gebunden. Das App-Passwort wird atomar in
einem dedizierten, rollenbegrenzt eingebundenen persistenten Verbindungsstore
gespeichert. Runtimeprozesse lesen ihn ausschliesslich. Der Store gehoert zum
lokalen Backup-/Restoreumfang; er ist kein Git-, Log- oder Chatinhalt.

Transportpruefungen binden Requests an die ausgewaehlte HTTPS-Origin und
aufgeloeste Adressen, erhalten Installationsunterpfade, pruefen Zertifikate und
begrenzen Zeit und Antwortgroesse. Private LAN-Adressen sind fuer die ausgewaehlte
Origin moeglich; Loopback, Link-local, Metadatenziele, fremde Redirects und
DNS-Rebinding sind ausgeschlossen. Zugangsdaten werden niemals an nachgelieferte
fremde URLs gesendet.

Nach erfolgreicher Anmeldung werden die authentisierte Benutzer-ID,
DAV-Principal/Home-Adressen, Kalenderkomponenten, Aufgabenlisten, Dateiwurzeln und
Talkraeume mit ihren effektiven Serverrechten ermittelt. Teilfehler bleiben
sichtbar. Die Freigabe des Kontos umfasst dessen vorhandene lesbare Ressourcen;
die Runtime verwendet stabile Ressourcen-IDs und keine erste oder namensgleiche
Zufallsauswahl. Mehrere Kalender und Aufgabenlisten bleiben separat adressierbar.
Konkrete Schreiboperationen behalten ActionPlan, Einzelfreigabe, Antivirus,
ETag und Remote-Readback. Serverrechte oder Freigaben werden nicht veraendert.

Fuer die Einrichtung ohne Nextcloud-Administrator wird ein ausgehender
Talk-Benutzerkanal ergaenzt. Er verwendet das angemeldete dedizierte Agentenkonto,
den vorhandenen OpenClaw-Agenten und dessen unveraenderten Freigabepfad. Er ersetzt
keinen Fachconnector. Nachrichtenannahme, Verarbeitung und Antwortzustellung
werden persistent getrennt; Historie wird bei Erstaktivierung nicht als neue
Befehle ausgefuehrt. Raum und Sender sind explizit gebunden, eigene Antworten
werden anhand bestaetigter Nachrichtenidentitaeten ausgeschlossen. Ungewisse
Zustellung oder unterbrochene Verarbeitung startet keinen zweiten externen Write.
Medien werden ohne eigenen Scan-/Medienvertrag nicht als Auftraege verarbeitet.

Die bestehende Bot-/Webhookvariante aus [ADR-0050](0050-nextcloud-talk-als-privater-mobilkanal.md)
bleibt kompatibel. Beide Adapter duerfen nicht gleichzeitig denselben Raum
verarbeiten. Eine produktive Migration ist ein separater Betriebsauftrag.

## Verifikation und Konsequenzen

Hermetische Tests pruefen Login/2FA-Weiterleitung und Polling, Ablauf/Abbruch,
Auth-/CSRF-/Scopefehler, Secretredaktion, TLS/SSRF/Unterpfade, mehrfache und
read-only-Ressourcen, Kontoaenderung, Backup-/Restore sowie Talk-Deduplizierung,
Neustart und ungewisse Zustellung. Ein erfolgreicher Mocktest ist kein Nachweis
einer Verbindung zur produktiven Nextcloud. Die sichtbare GUI meldet nur
tatsaechlich beobachtete Verbindung und aktuelle Teilfehler.

Die Einfuehrung braucht aktualisierte Datenowner-, Container-, Tool- und
Benutzerdokumentation. Der unveraenderliche Image-/Releasepfad, lokale Backups und
gesonderte produktive Aktivierung bleiben verbindlich.

Die Einstiegskarte benoetigt zusaetzlich eine echte Browserabnahme mit dem
gepinnten Hostrenderer: Benutzerklick, eigener Tab, festes gleichurspruengliches
Ziel, `window.opener === null` und dort verfuegbarer gleichurspruenglicher
Browserspeicher. Negative Faelle fuer abweichende Plugin-ID, Descriptor-ID oder
Pfad muessen die urspruengliche Sandbox erhalten. Ein Browser mit bereits
gecachtem altem Renderer muss nach dem Update die Korrektur laden koennen;
nach dem einmaligen Uebergangsreload muss die Seite stabil bleiben.
HTTP-Marker und statische Quelltests allein sind dafuer keine ausreichende
Evidenz; aus dieser ADR folgt keine bereits erfolgreiche produktive Abnahme.
