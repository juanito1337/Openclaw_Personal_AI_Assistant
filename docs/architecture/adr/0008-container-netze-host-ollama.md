# ADR-0008: Explizite Bridge-Netze mit engem Hostzugang fuer Ollama

- Status: Accepted
- Datum: 2026-08-05
- Entscheider: Security Maintainers und Operations Maintainers
- Betroffene Milestones: M4-M8
- Naechste Pruefung: M7 oder bei Verlagerung von Ollama in einen Container

## Kontext

Bis M3 verwendeten alle Rollen `network_mode: host`. Dadurch waren Hostdienste und
alle Hostports aus jedem kompromittierten Prozess erreichbar. Der lokale Ollama-
Daemon laeuft weiterhin auf dem Host und muss vom Prioritaetsproxy erreichbar sein.

## Entscheidung

`network_mode: host` wird vollstaendig entfernt. `backend` ist ein internes
Bridge-Netz fuer Gateway, Proxy und interne Aufrufer; `egress` ist das Netz fuer
Rollen mit begruendeten externen Reads/Writes. Nur Gateway veroeffentlicht Port
18789, standardmaessig auf `127.0.0.1`. Supervisor besitzt nur `backend`.

Die einzige Hostausnahme ist `host.docker.internal:host-gateway` am
`ollama-proxy`. Der Proxy lauscht intern auf `0.0.0.0:11435`, wird nicht am Host
veroeffentlicht und ist der einzige Weg der anderen Rollen zum Ollama-Upstream.
Mail- und Modelkonfiguration werden bei der Migration auf
`http://ollama-proxy:11435` normalisiert.

## Bedrohung und Kompensation

Ein kompromittierter Proxy kann den Host-Gateway erreichen. Er erhaelt jedoch
keine Secrets, keine schreibbaren Fachdaten, keine Linux-Capabilities, einen
read-only Rootfs sowie PID-/CPU-/RAM- und Loggrenzen. Seine einzige Instanzsicht
ist read-only. Netzwerk-Negativtests belegen, dass Container verschiedener Netze
einander nicht aufloesen oder erreichen.

## Konsequenzen

Ein abweichender Ollama-Host wird weiterhin ueber
`OLLAMA_PRIORITY_UPSTREAM` konfiguriert. Wer Gateway bewusst ausserhalb des Hosts
erreichbar machen will, muss `OPENCLAW_GATEWAY_BIND_ADDRESS` explizit aendern und
TLS/Reverse-Proxy sowie Firewall separat pruefen. Das ist keine automatische
Berechtigungserweiterung.

Ein nur auf Host-Loopback lauschender Ollama-Daemon ist aus dem Bridge-Netz nicht
erreichbar. Der Host muss den Dienst auf einer kontrollierten, vom Docker-Gateway
erreichbaren Adresse bereitstellen und den Zugriff per Firewall auf das Docker-
Quellnetz begrenzen. Ohne diesen Nachweis bleibt Proxy-Health rot und der Stack
startet nicht weiter.

## Verifikation

`runtime-hardening.json`, `tests/test_container_hardening_m4.py` und
`scripts/check-container-hardening.sh` pruefen Port-, Netz- und Hostausnahmen
statisch sowie gegen isolierte, temporaere Docker-Netze.
