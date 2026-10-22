# ADR-0051: OpenClaw-Einrichtung und Nextcloud-Kontoanbindung

- Status: Accepted (Entwicklungsentscheidung; keine produktive Aktivierung)
- Datum: 2026-10-22
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
Einrichtungsoberflaeche und administrative Endpunkte. Sie verwendet ausschliesslich
APIs des gepinnten Cores. Operatorauthentisierung und administrative Autorisierung
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

Der gepinnte Core unterstuetzt eine Plugin-Sidebarbeschreibung, uebergibt jedoch
keine Authentisierung an deren iframe. Die Sidebar liefert deshalb nur einen
inhaltsfreien Einstieg; die eigentliche Seite oeffnet gleichurspruenglich in einem
eigenen Tab und verwendet die bestehende OpenClaw-Geraeteanmeldung. Weder ein
Corefork noch ein stilles Coreupgrade ist Bestandteil dieser Entscheidung.

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
