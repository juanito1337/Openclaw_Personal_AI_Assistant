# ADR-0025: Gateway-lokaler Event-Relay und Mail-Recovery beim Datenowner

- Status: Accepted
- Datum: 2026-08-18
- Entscheider: Operations Maintainers, Security Maintainers
- Betroffene Milestones: M10-Rolloutkorrektur

## Kontext

OpenClaw lehnt unverschluesselte `ws://`-Verbindungen zu Nicht-Loopback-Adressen
absichtlich ab. Der Supervisor und Portfolio-Worker versuchten dennoch, Events
direkt an `ws://gateway:18789` zu senden. Eine Freigabe ueber
`OPENCLAW_ALLOW_INSECURE_PRIVATE_WS` wuerde die Transportpruefung abschalten und
ist nach dem Sicherheitsvertrag unzulaessig.

Der Supervisor versuchte ausserdem die automatische Mail-Produktionsfreigabe
selbst auszufuehren. Seine Rolle besitzt absichtlich keinen Mail-State. Das
Oeffnen von `/var/lib/openclaw/mail` scheiterte daher auf dem schreibgeschuetzten
Root-Dateisystem. Ein Mail-Mount oder Mail-Credentials im Supervisor wuerden die
Single-Writer- und Least-Privilege-Grenze aufweichen.

## Entscheidung

Fachworker legen ausschliesslich begrenzte, schema-validierte JSON-Ereignisse in
`shared/coordination/gateway_events/pending` ab. Die Queue ist auf 256 Eintraege
und 1.800 Zeichen je Event begrenzt; Eintraege werden atomar publiziert. Nur der
Gateway-Container besitzt weiterhin das Gateway-Credential. Sein Relay nimmt
Eintraege atomar in Bearbeitung und ruft `openclaw system event` ueber
`ws://127.0.0.1:18789` auf. Loopback benoetigt keine Abschaltung der OpenClaw-
Transportpruefung. Fehler werden begrenzt wiederholt, ungueltige oder dauerhaft
fehlgeschlagene Eintraege fail-closed separiert, und ein inhaltsfreier
Relay-Status wird fuer Health und Supervisor publiziert. Die technische
Fehlerablage behaelt hoechstens 64 Eintraege. Nach einem Relay-Neustart werden
bereits atomar beanspruchte, aber noch nicht abgeschlossene Eintraege erneut in
die Pending-Queue uebernommen; dadurch ist die Zustellung mindestens einmal
statt hoechstens einmal garantiert.

Der Container-Supervisor ist nur Beobachter. Er liest Worker-Heartbeats,
Scheduler- und Relay-Status, oeffnet aber keine Fach-State-Datenbank und fuehrt
keine Mail-Recovery aus. Die einzige automatische Mail-Recovery laeuft vor einem
produktiven Zyklus im Mail-Worker: `production-check`, nur bei explizit
`auto_recoverable`, begrenzter Dry-Run, erneuter `production-check`, danach erst
der produktive Lauf. Ein echter Fehler erhaelt den bestehenden 30-Minuten-
Cooldown; ein Lockkonflikt bleibt transient. Andere Blocker werden niemals
repariert oder umgangen.

Supervisor, Portfolio und Monitor erhalten kein Gateway-Credential mehr. Der
Supervisor erhaelt auch keine Mail-Konfiguration oder Mail-Secrets.

## Konsequenzen

Event-Zustellung bleibt bei einem kurzzeitigen Gatewayausfall persistent und
enthaelt keine Zugangsdaten in Prozessargumenten. Ein kompromittierter Fachworker
kann nur einen begrenzten Systemevent einstellen, aber weder das Gateway-
Credential lesen noch beliebige Gateway-Kommandos bestimmen. Die Queue ist kein
allgemeiner RPC-Kanal.

Mail-State und automatische Dry-Run-Freigabe verbleiben beim einzigen
Mail-Writer. Manuelle `jobs check`-Aufrufe in der expliziten Adminrolle behalten
den bisherigen Legacy-/Diagnosepfad; der Hintergrund-Supervisor mutiert keinen
Jobzustand.

## Verifikation

Regressionstests pruefen Queuegrenze, atomare Uebergabe, manipulierte Eintraege,
credential-freie Workerkommandos, ausschliessliche Loopback-Zustellung,
Relay-Frische, Crash-Wiederaufnahme, den Observer-only-Supervisor und die
allowlistgebundene Mail-Recovery. Der Container-Healthcheck prueft HTTP-Gateway und Relay gemeinsam.
Compose- und Rollenvertrag pruefen, dass Fachworker keine Gateway-Secrets mounten.
