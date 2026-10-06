# ADR-0050: Nextcloud Talk als privater Mobilkanal ueber begrenzten Webhook

- Status: Accepted
- Datum: 2026-10-05
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
`127.0.0.1:8788`. Ein hostlokaler TLS-Reverse-Proxy leitet ausschliesslich den
exakten Pfad `/nextcloud-talk-webhook` weiter. Port `18789` und alle anderen
Gatewaypfade bleiben unveraendert auf Host-Loopback und werden nicht durch diese
Route erreichbar.

Nextcloud und OpenClaw laufen auf getrennten Rechnern. Der Reverse-Proxy wird
deshalb vom OpenClaw-Rechner betrieben und nimmt die Webhookverbindung nur an
einer privaten, vom Nextcloud-Server erreichbaren Adresse an. Beide Rechner
befinden sich im selben lokalen Netzwerk; ein Server-zu-Server-WireGuard-Tunnel
ist deshalb fuer M17 nicht erforderlich. Seine Firewall- und Proxy-Allowlist
wird auf die stabile Nextcloud-Quelladresse `192.168.2.3` begrenzt. Am
OpenClaw-Rechner wurde `192.168.2.38/24` read-only beobachtet. Nextcloud ist
ueber `1337-cloud.ddns.net` erreichbar; der Name loest im lokalen Netz auf
`192.168.2.3` auf und seine TLS-Kette sowie DNS-Identitaet werden auf dem
OpenClaw-Rechner akzeptiert. Fuer den separaten Webhookhost wurde zunaechst der
lokale Alias `home-agent` angelegt; der autoritative DNS-Server liefert dafuer
`192.168.2.38`. Der einteilige Name wird jedoch von Dockers Standardresolver
mit `SERVFAIL` abgelehnt und ist deshalb keine produktive TLS-Identitaet. Vor
Aktivierung werden ein vollstaendig qualifizierter LAN-Name, bevorzugt
`home-agent.home.arpa`, und ein vom Nextcloud-Server validierbares Zertifikat
fuer exakt diesen Namen belegt.

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

Produktive Aktivierung benoetigt einen hostlokalen Reverse-Proxy, einen
Nextcloud-Talk-Bot und zwei exakte Allowlistwerte. Entwicklung und signiertes
Release duerfen diese externen Aenderungen nicht nebenbei ausfuehren.

Ein spaeteres Coreupgrade darf den Listener auf die neue Gatewayroute migrieren,
aber nur mit eigener ADR, Bot-Callback-Migration, Dedupe-/Durabilitytest und
Rollback. Diese ADR autorisiert keine automatische Migration.

## Verifikation

- Supply-Chain-Lock, npm-Lock, Pluginvertrag und Rollenimage-Smoke fuer die exakte
  Version und Integritaet.
- Compose-/Hardeningtests fuer Host-Loopback `8788` und unveraendertes Loopback
  `18789`.
- HMAC-, Backend-, Pfad-, Methode-, Bodylimit-, Authrate-, Replay- und
  Restart-Negativtests.
- Benutzer- plus Raumallowlist, Cross-User-, Cross-Room- und Cross-Channel-Tests.
- Approvaltests fuer eindeutige Stufe 2 und fail-closed Stufe 3.
- Produktive Botanlage, Proxyroute und Pairing erst nach signiertem Release,
  Backup und separater Betriebsfreigabe.
