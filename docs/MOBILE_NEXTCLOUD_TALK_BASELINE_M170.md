# M17.0-Baseline: Mobile Kommunikation ueber Nextcloud Talk

Datum: 2026-10-05

Status: `M17.0 TEILWEISE ABGENOMMEN`. Die lokale und produktive Runtimebaseline
ist read-only erhoben. Jan hat am 2026-10-06 Android als primaeres
Telefonbetriebssystem sowie Nextcloud `35.0.1` und die aktivierte serverseitige
Talk-App `25.0.5` bestaetigt. Offen sind noch Standort/Eigner des geplanten
HTTPS-Reverse-Proxys und die Erreichbarkeit seines privaten Webhookhosts vom
Nextcloud-Server. Es wurde kein Plugin installiert, kein Bot angelegt, kein
Secret geaendert und kein Port freigegeben.

## Quell- und Releaseidentitaet

| Feld | Evidenz |
| --- | --- |
| Ausgangscommit | `8e63d9f865a103ce034ca0504f39914bf904b99b` |
| Entwicklungsbranch | `feature/m17-nextcloud-talk` |
| Produkt | OpenClaw Local Personal Assistant |
| Produktrelease | `3.4.0-r29.0.3` |
| eingebetteter OpenClaw-Core | `2026.7.1` |
| produktives Runtime-Image | Release `3.4.0-r29.0.3`, Revision `7f6c57628750846cb38c5351b2fe0211eb052dd6` |
| Quellmanifest vor M17-Aenderungen | 646 Eintraege, verifiziert |

Der Entwicklungscheckout ist absichtlich keine installierte Instanz. `status`,
`tools list` und `capabilities` brechen dort mit der belegten Meldung
`Personal-Assistant-Konfiguration fehlt` ab. Es wurde kein `setup init` als
Diagnosereparatur ausgefuehrt. Produktive read-only Runtimewerte wurden nur ueber
die laufende Gatewayrolle erhoben.

## Produktive Runtimebaseline

Am Messzeitpunkt liefen Gateway, alle Fachworker, Ollama-Proxy und ClamAV seit
elf Tagen gesund. Der Gateway-Port war ausschliesslich als
`127.0.0.1:18789 -> 18789/tcp` publiziert.

| Messwert | Wert |
| --- | ---: |
| Gateway Memory Current | 613.953.536 Byte |
| Gateway Memory Peak | 1.095.118.848 Byte |
| Gateway Memory Limit | 2.147.483.648 Byte |
| Gateway PIDs | 15 / 512 |
| Cgroup OOM / OOM-Kill | 0 / 0 |
| Limits geaendert | nein |

`docker stats` zeigte bei einer getrennten Momentaufnahme 438,4 MiB. Dieser Wert
und die registrierte Cgroup-Messung sind unterschiedliche Messzeitpunkte und
werden nicht als Widerspruch oder Peaknachweis vermischt.

Der produktive Pluginstatus enthielt Brave, Signal und die
Personal-Assistant-Tools, aber kein Nextcloud-Talk-Plugin. Es war kein
Konversationskanal konfiguriert. Die Pluginregistry meldete eine bestehende
`persisted-registry-stale-policy`-Warnung und verwendete den abgeleiteten Index.
M17 repariert diesen produktiven Zustand nicht direkt; Kandidatenmigration und
Registrynormalisierung muessen ihn hermetisch abdecken.

Die registrierte Ressourcenpruefung war nicht vollstaendig gruen: Eine bereits
vor M17 bestehende Kalender-Ressourcenabweichung und eine fehlende bestaetigte
Workspace-Move-Berechtigung wurden sichtbar. M17 veraendert oder verdeckt diese
unabhaengigen Befunde nicht.

## Plugin-Kompatibilitaet

Die npm-Registry enthaelt eine exakt zum Core gebaute Version:

| Feld | Wert |
| --- | --- |
| Paket | `@openclaw/nextcloud-talk` |
| ausgewaehlte Version | `2026.7.1` |
| Peer-Abhaengigkeit | `openclaw >=2026.7.1` |
| Plugin-API | `>=2026.7.1` |
| Build-Core | `2026.7.1` |
| npm SHA-1 | `1fc4b4938ae8785d547d9a8d1c222532f2550f4c` |
| npm Integrity | `sha512-a+ret5WnmxLjEBwU26wl5YicOcDSEY3E34yoakQ8OKlDrwH5jIM2UbKUnq4kkoMJtROw/FF3j/GFA7ISlIXQIA==` |
| direkte Abhaengigkeit | `zod 4.4.3`, im Paket gebuendelt |

Die zum Messzeitpunkt neueste stabile Paketversion war `2026.9.8`. Sie wird
nicht verwendet, weil M17 keinen stillen Core- oder Plugin-API-Sprung einfuehrt.

## Versionsspezifischer Webhookvertrag

Der reale Paketinhalt von `2026.7.1` weicht von der aktuellen
Upstream-Dokumentation ab:

- der Connector startet einen eigenen HTTP-Listener;
- Defaultport ist `8788`, Defaulthost `0.0.0.0`;
- der exakte Defaultpfad ist `/nextcloud-talk-webhook`;
- nur `POST` auf diesem Pfad wird verarbeitet; `/healthz` ist separat;
- eingehende Requests benoetigen Backend-, Random- und
  HMAC-SHA256-Signaturheader;
- Bodygroesse, Lesezeit und fehlgeschlagene Authentisierung sind begrenzt;
- Replay-Schutz verwendet Account-ID, Raumtoken und Message-ID;
- der Connector bestaetigt einen gueltigen Request vor Abschluss des
  Agententurns mit HTTP 200;
- alle eingehenden Unterhaltungen werden in dieser Version als Raumkontext
  behandelt; eine belastbare DM-Erkennung ist nicht vorhanden.

Die neuere dokumentierte Route ueber den Gateway-Port `18789`, dauerhafte
Annahme-Markierung und `legacyWebhook`-Migration sind deshalb keine Eigenschaften
des gepinnten Pakets. M17 muss den Listener `8788` im Container verwenden, ihn
auf Host-Loopback begrenzen und ausschliesslich ueber einen engen HTTPS-Reverse-
Proxy bereitstellen. Alternativ waere ein eigener Coreupgrade-Milestone noetig.

## Netzwerk- und Mobilstatus

| Voraussetzung | Zustand |
| --- | --- |
| Gateway auf Host-Loopback | vorhanden und belegt |
| WireGuard-Interface am Entwicklungsrechner | zum Messzeitpunkt nicht beobachtet; keine Negativaussage ueber andere Hosts oder inaktive Profile |
| HTTPS-Reverse-Proxy fuer Talk-Webhook | Standort, Eigner und privater Erreichbarkeitspfad noch nicht festgelegt |
| Nextcloud-Version | `35.0.1` (`Nextcloud Hub 26 Spring`), von Jan am 2026-10-06 bestaetigt; technische Liveabfrage noch ausstehend |
| Nextcloud-Talk-App serverseitig | installiert und aktiviert, Version `25.0.5`, von Jan am 2026-10-06 bestaetigt; technische Liveabfrage noch ausstehend |
| primaeres Telefonbetriebssystem | Android, von Jan am 2026-10-06 bestaetigt |
| mobile Talk-App | nicht gemessen |

Secretpfade, produktive Nextcloud-Zugangsdaten und Gatewaykonfiguration wurden
nicht durchsucht oder ausgegeben.

## Entscheidung fuer die Folgestufen

- M17 bleibt auf Core und Talk-Plugin `2026.7.1`.
- Der separate Listener wird intern auf `0.0.0.0:8788` betrieben, aber von Docker
  nur auf `127.0.0.1:8788` des Hosts publiziert.
- Ein hostlokaler HTTPS-Reverse-Proxy darf nur
  `/nextcloud-talk-webhook` an diesen Loopbackport weiterleiten.
- Der Gateway-Port `18789` bleibt unveraendert loopbackgebunden und wird nicht
  zum Mobilendpoint.
- Der erste produktive Schnitt behandelt den freigegebenen Talk-Raum als
  persoenlichen Einzelraum. Exakte Raum- und Benutzer-Allowlist sind beide
  Pflicht, weil der Connector keine verifizierte DM-Klassifikation liefert.
- Eingehende Medien bleiben deaktiviert. Text ist der einzige M17-MVP-Inhalt.

## Offene Abnahmepunkte

Android, Nextcloud `35.0.1` und die aktivierte Talk-App `25.0.5` sind
betriebsseitig bestaetigt. Eine technische Liveabfrage bleibt Teil der spaeteren
realen Abnahme. M17.0 kann erst `ABGENOMMEN` werden, wenn ausserdem read-only
geklaert ist:

- Standort/Eigner des HTTPS-Reverse-Proxys;
- private IP beziehungsweise DNS-Name des geplanten Webhookhosts;
- Erreichbarkeit dieses Webhookhosts vom Nextcloud-Server.

Diese Angaben autorisieren noch keine Botanlage, Portfreigabe oder
Konfigurationsaenderung.
