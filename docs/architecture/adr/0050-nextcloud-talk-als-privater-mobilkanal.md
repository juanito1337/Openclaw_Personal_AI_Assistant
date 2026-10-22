# ADR-0050: Nextcloud Talk als privater Mobilkanal ueber begrenzten Webhook

- Status: Accepted
- Datum: 2026-10-05
- Ergaenzung IP-Webhook: 2026-10-13 (Entwicklungsfreigabe, keine Betriebsaktivierung)
- Ergaenzung integrierter TLS-Proxy: 2026-10-13 (optionaler Stackdienst, keine Betriebsaktivierung)
- Entscheider: Architecture Maintainers, Security Maintainers, Operations Maintainers
- Betroffene Milestones: M17

## Kontext

Der Personal Assistant soll aus einer vorhandenen nativen Mobil-App erreichbar
sein. Jan betreibt bereits Nextcloud und verwendet normalerweise WireGuard. Eine
eigene App wuerde Authentisierung, Push, Offlinezustand und sichere Updates neu
implementieren. Eine direkte Freigabe des OpenClaw-Gateway-Protokolls wuerde
dagegen eine breite administrative und agentennahe Schnittstelle auf das Telefon
verlagern.

Der zum eingebetteten OpenClaw-Core `2026.7.1` passende offizielle Connector
`@openclaw/nextcloud-talk@2026.7.1` besitzt einen separaten HTTP-Webhooklistener.
Er lauscht ohne Konfiguration auf `0.0.0.0:8788`, prueft die Nextcloud-HMAC und
Backendorigin, begrenzt Body und Authfehler und dedupliziert anhand der
Talk-Nachrichtenidentitaet. Die in neuerer Upstream-Dokumentation beschriebene
Integration in Port `18789` ist in dieser Version noch nicht vorhanden.

## Entscheidung

Nextcloud Talk wird als einziger M17-Mobilkanal verwendet. Das Telefon verbindet
sich ausschliesslich ueber HTTPS und WireGuard mit Nextcloud. Es erhaelt weder
Gatewaytoken noch direkten Zugriff auf Gateway- oder Webhookports.

Der Connector wird exakt auf `2026.7.1` gepinnt und ausschliesslich im
unveraenderlichen Runtime-Image installiert. Der Listener bindet innerhalb des
Gatewaycontainers auf Port `8788`; Docker publiziert ihn standardmaessig nur als
`127.0.0.1:8788`. Ein separater TLS-Reverse-Proxy im Agentenstack leitet
ausschliesslich den exakten Pfad `/nextcloud-talk-webhook` ueber das interne
`talk-link`-Netz an `gateway:8788` weiter. Port `18789` und alle anderen
Gatewaypfade bleiben unveraendert auf Host-Loopback und werden nicht durch diese
Route erreichbar.

Nextcloud und OpenClaw laufen auf getrennten Rechnern. Der Reverse-Proxy wird
deshalb vom OpenClaw-Rechner betrieben und nimmt die Webhookverbindung nur an
einer privaten, vom Nextcloud-Server erreichbaren Adresse an. Beide Rechner
befinden sich im selben lokalen Netzwerk; ein Server-zu-Server-WireGuard-Tunnel
ist deshalb fuer M17 nicht erforderlich. Seine Firewall- und Proxy-Allowlist
wird auf die stabile Nextcloud-Quelladresse `192.168.2.3` begrenzt. Am
OpenClaw-Rechner wurde `192.168.2.38/24` am 2026-10-06 read-only beobachtet.
Nextcloud war bei derselben Pruefung ueber `1337-cloud.ddns.net` erreichbar;
der Name loeste lokal auf `192.168.2.3` auf und seine TLS-Kette sowie
DNS-Identitaet wurden auf dem OpenClaw-Rechner akzeptiert. Der urspruenglich
vorgesehene Webhookname ist
`home-agent.home.arpa`; er wurde am 2026-10-07 ueber den autoritativen
DNS-Server, den Host-Resolver und Dockers Standardresolver jeweils als
`192.168.2.38` verifiziert. Der fruehere Single-Label-Alias `home-agent` bleibt
unbenutzt. Die von Jan am 2026-10-13 gelieferte Nextcloud-Containerkonfiguration
verwendet hingegen Dockers `127.0.0.11` mit Google-Upstreams, die diesen lokalen
Namen nicht aufloesen. Die historische DNS-Pruefung auf dem Agenten belegt
deshalb nicht den Resolverpfad aus Nextcloud.

Jan hat deshalb am 2026-10-13 die Entwicklung des Callbacks
`https://192.168.2.38/nextcloud-talk-webhook` freigegeben. Der bisherige
DNS-Webhook bleibt eine Alternative. Die IP-Variante erlaubt ausschliesslich
RFC1918-IPv4 oder IPv6-ULA (`fc00::/7`), gebunden an exakt eine erwartete
Webhook-IP. Oeffentliche, Loopback-, Link-Local- und mehrdeutige Ziele bleiben
gesperrt; HTTPS, Port 443 und der exakte Pfad bleiben unveraendert. Die
Nextcloud-Basisadresse `https://1337-cloud.ddns.net:9001` behaelt ihren
DNS-/Zertifikatsvertrag und wird nicht durch eine IP ersetzt.

Vor Aktivierung werden die dauerhaft feste Webhook-IP, ein Zertifikat mit
IP-SAN `192.168.2.38` aus einer verwalteten privaten CA und der begrenzte
HTTPS-Proxy belegt. Nur das oeffentliche CA-Zertifikat wird nach Fingerprint-
Pruefung in Nextclouds nativen Zertifikatsspeicher sowie ueber den bestehenden
Runtime-CA-Vertrag fuer `talk doctor` importiert. Private Schluessel bleiben
im dedizierten persistenten PKI-Mount des Proxys. Ein DNS-SAN, ein Storage-Mount ohne Trustimport oder eine
abgeschaltete Zertifikatspruefung reicht nicht. Der Doctor prueft bei einem
IP-Literal direkt diese Adresse und deren IP-SAN ohne DNS; er ersetzt nicht
den separaten Erreichbarkeits- und Vertrauensbeleg vom Nextcloud-Server.

### Optionaler Stackdienst und private PKI

Jan hat die Integration in den bestehenden Agentenstack freigegeben. Der
Dienst `talk-proxy` im Compose-Profil `talk` verwendet das bereits gemeinsam
signierte `OPENCLAW_PROXY_IMAGE`; kein viertes Releaseimage und keine
Hostinstallation. Caddy `2.11.7` wird als digestgebundener Buildinput aufgenommen.
`OPENCLAW_TALK_PROXY_ENABLED=false` bleibt der Default. Nur ein separat
autorisiertes Deployment mit exakter privater Agent- und Nextcloud-IP darf die
Hostbindung `192.168.2.38:443 -> 8443/tcp` aktivieren. Der integrierte Dienst
implementiert diesen IP-Weg, keinen frei konfigurierbaren allgemeinen Proxy.

Der Proxy erhaelt ein dediziertes Ingress-Bridge-Netz `talk-ingress` und teilt
nur mit dem Gateway das interne `talk-link`-Netz. Das Ingress-Netz ist keine
Egress-Sperre. Er prueft die direkte Quelladresse, nicht HTTP-Forwarded-Header;
Docker muss die Nextcloud-Quelladresse erhalten, sonst bleibt der Zugriff
fail-closed. HTTP-Redirectport, HTTP/3, Remote-Admin und andere Gatewayrouten
werden nicht publiziert.

Ein lokaler Guard hinter Caddy prueft den unveraenderten Requestpfad samt
Methode: nur `POST /nextcloud-talk-webhook` ohne Query. Er begrenzt Bodys auf
1 MiB, parallele Bearbeitungen auf vier und die globale Rate auf einen Request
pro Sekunde mit Burst zehn. Header, Empfangszeit, Upstreamzeit und Antworten
sind begrenzt; keine Redirectverfolgung oder automatische Wiederholung. Nur die
erforderlichen Talk-Header und die unveraenderten signierten Bodybytes werden
weitergereicht. Logs enthalten weder Header noch Nachrichteninhalte.

Caddy erzeugt und erneuert mit `tls internal` das IP-SAN-Zertifikat. Die private
CA liegt unter `/srv/openclaw/secrets/talk-proxy-pki` ausschliesslich im
Proxy-Mount. Der Proxy erhaelt weder Mailcredentials noch Botsecret,
Gatewaytoken oder Docker-Socket. Das oeffentliche Rootzertifikat wird separat
unter `/srv/openclaw/config/talk-proxy-public/ca.crt` bereitgestellt;
automatische Trustinstallation ist ausgeschaltet. Eine CA-Abweichung zum
bestehenden Export fuehrt zum Fehler, nicht zu einer stillen Trustrotation.

Backup und Rollback umfassen PKI und oeffentlichen Export. Vor dem Backup
wird der Proxy wegen seiner Zertifikatserneuerungen gestoppt; ein manuelles
Backup bei laufendem Proxy wird abgelehnt. Fuer eine fehlgeschlagene
Erstaktivierung bleibt der vorherige tatsaechliche Laufzustand massgeblich,
nicht nur das neue Enable-Flag. Alte Backups ohne Proxyzustand bedeuten AUS.
Nextclouds Trustimport ist ein eigener externer Write und wird durch das
lokale Backup nicht rueckgaengig gemacht.

Das vorhandene Nextcloud-Zertifikat und die TrueNAS-`Certificate ID` werden
nicht ersetzt. Nur das gepruefte oeffentliche CA-Zertifikat wird spaeter mit
`occ security:certificates:import` in Nextcloud importiert und mit
`occ security:certificates` kontrolliert. Der Trust gilt fuer Nextclouds
entsprechende HTTP-Clients und wird daher einer dedizierten CA gewaehrt.
Android erreicht nur Nextcloud und benoetigt keinen Trustimport fuer diese
serverseitige Verbindung. TLS-Pruefung und Nextclouds globale SSRF-Grenzen
bleiben unveraendert.

Die erste Aktivierung verwendet genau einen dedizierten Talk-Raum. Sowohl die
stabile Nextcloud-Benutzer-ID als auch der Raumtoken muessen allowlisted sein;
Gruppenwildcards, Gaeste und offene DMs bleiben deaktiviert. Diese doppelte
Bindung kompensiert, dass die gepinnte Connectorversion eingehende Nachrichten
als Raumkontext klassifiziert und keine belastbare DM-Erkennung bereitstellt.

Botsecret und optionales Nextcloud-App-Passwort sind getrennte dateibasierte
Secrets. Sie werden nur in der Gatewayrolle read-only gemountet. Das
Webhooksecret ist kein Gatewaycredential und erteilt keine Toolberechtigung.

## Freigabe- und Inhaltsvertrag

Eine gueltig signierte Talk-Nachricht belegt Transport und Senderkontext, aber
keine Werkzeugfreigabe. Der bestehende risikobasierte Toolvertrag bleibt
kanalunabhaengig:

- Read-only und bereits konfigurierte begrenzte Operationen behalten ihren
  aktuellen Vertrag.
- Ein eindeutiges `JA` kann nur genau eine aktuelle unveraenderte Aktion der
  Risikostufe 2 in derselben Sitzung bestaetigen.
- Risikostufe 3 bleibt an native Allow-once-Evidenz oder den exakten technischen
  Fallback gebunden. Talk-Reaktionen, Zitate oder blosses `JA` reichen nicht.

Der MVP akzeptiert nur Text. Dateianhaenge, Sprache und ausgehende lokale Dateien
bleiben deaktiviert, bis ein eigener ClamAV-, Groessen-, Ablage- und
Medienidentitaetsvertrag angenommen ist.

## Zustellungs- und Fehlervertrag

HTTP 200 des Connectors `2026.7.1` bedeutet nur, dass Signatur und Payload
angenommen wurden; es beweist weder einen abgeschlossenen Agententurn noch eine
zugestellte Antwort. Der Zustand wird deshalb in Webhookannahme,
Agentenverarbeitung, Toolausfuehrung und Antwortversand getrennt.

Wiederholte Talk-Nachrichten muessen am Connector und nochmals an den
fachspezifischen Idempotenzgrenzen unschaedlich sein. Ein Timeout oder verlorener
Antwortversand wird nicht automatisch in einen zweiten externen Write
uebersetzt. Remote-Write-Erfolg benoetigt weiterhin die jeweilige fachliche
Postcondition.

## Verworfene Alternativen

- Eigene Mobile-App: verworfen wegen doppelter Auth-, Push-, Offline- und
  Updateangriffsflaeche.
- Signal als Primaerkanal: technisch vorhanden, aber nicht lokal ueber die
  bestehende Nextcloud/WireGuard-Struktur und mit eigener Botnummer verbunden.
- Direkter Gatewayzugriff ueber WireGuard: verworfen wegen zu breiter
  Protokoll- und Credentialfreigabe.
- Ungepinnte aktuelle Talk-Pluginversion: verworfen wegen Core-/Plugin-API-Drift.
- Sofortiges Coreupgrade: nicht Teil von M17; benoetigt eigenen
  Migrations-, Regressions- und Releaseumfang.
- Direkte Freigabe von Containerport `8788`: verworfen; TLS, Pfad- und
  Quellgrenzen gehoeren vor den Listener.

## Konsequenzen

Das Runtime-Image wird um den gepinnten Connector und dessen gebuendelte
Abhaengigkeit groesser. Gateway-Speicher, Startzeit und Imagegroesse werden gegen
M17.0 gemessen, ohne Limits anzuheben. Ein zweiter Host-Loopback-Port wird in
Compose, Hardeningmatrix und Negativtests explizit erfasst.

Der optionale Talk-Proxy hat ein eigenes begrenztes Rollenbudget von 128 MiB,
0,5 CPU und 128 PIDs, ohne Limits bestehender Rollen anzuheben. Er laeuft
non-root mit read-only Rootfilesystem, ohne Linux-Capabilities und mit
`no-new-privileges`. Sein Healthcheck belegt lokale TLS- und Guard-Bereitschaft,
nicht die Erreichbarkeit aus Nextcloud oder eine zugestellte Agentenantwort.

Produktive Aktivierung benoetigt den separat freigegebenen Stack-Proxy, einen
Nextcloud-Talk-Bot und zwei exakte Allowlistwerte. Entwicklung und signiertes
Release duerfen diese externen Aenderungen nicht nebenbei ausfuehren.

Ein spaeteres Coreupgrade darf den Listener auf die neue Gatewayroute migrieren,
aber nur mit eigener ADR, Bot-Callback-Migration, Dedupe-/Durabilitytest und
Rollback. Diese ADR autorisiert keine automatische Migration.

## Verifikation

- Supply-Chain-Lock, npm-Lock, Pluginvertrag und Rollenimage-Smoke fuer die exakte
  Version und Integritaet.
- Compose-/Hardeningtests fuer Host-Loopback `8788` und unveraendertes Loopback
  `18789`, deaktivierten Default, private Portbindung, getrennte Netze,
  PKI-Mount und eigene unveraenderlich begrenzte Proxyrolle.
- Gepinnter Caddy, echter TLS-IP-SAN-Handshake, CA-Persistenz bei Restart,
  ausschliesslich oeffentlicher Export, gestopptes Backup sowie Rollback einer
  Erstaktivierung und Ablehnung inkompatibler Proxyimages.
- Adress-/TLS-Tests fuer RFC1918/ULA, exakte erwartete IP, passenden IP-SAN
  und DNS-freien IP-Webhook; Negativtests fuer andere Adressklassen,
  mehrdeutige/abweichende Ziele und falsche Zertifikatsidentitaet. Der
  alternative DNS-Webhook bleibt regressionsgetestet.
- HMAC-, Backend-, Pfad-, Methode-, Bodylimit-, Authrate-, Replay- und
  Restart-Negativtests.
- Benutzer- plus Raumallowlist, Cross-User-, Cross-Room- und Cross-Channel-Tests.
- Approvaltests fuer eindeutige Stufe 2 und fail-closed Stufe 3.
- Produktive Botanlage, Proxyroute und Pairing erst nach signiertem Release,
  Backup und separater Betriebsfreigabe.
