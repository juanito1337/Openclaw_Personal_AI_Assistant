# ADR 0039: Turngebundene Aktionsverpflichtung und belegter Abschluss

- Status: Accepted
- Datum: 2026-09-13
- Milestone: M15.0 bis M15.8

## Kontext

ADR 0036 bindet Fachantworten an native Werkzeugevidenz. Ein ausdruecklicher
Schreibauftrag konnte trotzdem mit einer Zukunftsankuendigung enden, bevor das
Modell ein Werkzeug aufgerufen hatte. Einzelne belegte Toolresultate modellieren
ausserdem noch nicht den Gesamtzustand eines mehrstufigen Workflows wie
Mailauswahl, Terminvorschau, zwei getrennte Kalenderanlagen und Remote-Read-back.

## Entscheidung

`before_prompt_build` klassifiziert ausschliesslich den aktuellen Nutzerprompt
als `execute`, `preview`, `explain` oder `ambiguous`. Nur `execute` erzeugt eine
turngebundene `action_obligation`. Das generatorgestuetzte geschlossene Schema
enthaelt Domaenen, Effekt, Zielanzahl, begrenzte Schritte, benoetigte Slots,
ausgefuehrte Write-Operationen und Nachbedingungen. Es kann keine Freigabe
erteilen und keine Operation ausserhalb des typisierten Katalogs erzeugen.

Der Pluginpfad fuehrt den Zustand nur aus echter aktueller Werkzeugevidenz fort:

1. `before_prompt_build` erzeugt Route und Verpflichtung ohne Remoteinhalt.
2. `before_tool_call` validiert Operation und Argumente und bindet jeden Write
   weiterhin an einen eigenen kurzlebigen Allow-once-Nonce. Die von OpenClaw
   eingefrorenen genehmigten Parameter tragen zusaetzlich die opake Run-Bindung
   des Hooks, weil eine nach der Freigabe fortgesetzte Tool-Factory keinen
   identischen Laufkontext liefern muss.
3. Die argv-only Bridge fuehrt genau die registrierte Operation aus.
4. Das Toolresultat aktualisiert die Verpflichtung. Evidenz eines anderen Turns,
   eine falsche Write-Domaene oder ein Write ohne Nachzustand schliesst sie nicht.
5. `before_agent_finalize` erlaubt bei offener Verpflichtung genau eine
   kontrollierte Revision.
6. `reply_payload_sending` ersetzt einen erneuten Meta-Abbruch fail-closed durch
   einen inhaltsarmen typisierten Blocker.

Zulaessige Terminalzustaende sind ausschliesslich `completed`,
`approval-required`, `information-required`, `blocked` und
`cancelled-by-user`. `announced`, `working`, Wartebitten und Zukunftsversprechen
sind keine Terminalzustaende. Eine Rueckfrage ist nur dann legitim, wenn ein
registrierter Read-/Previewpfad die fehlenden Quelldaten belegt hat.

Der erste vertikale Workflow liest eine exakt gebundene Mail, scannt Raw-Mail
und alle physischen Anlagen fail-closed, erzeugt eine deterministische
Mail-zu-Kalender-Vorschau und legt nach Einzelfreigabe genau einen unveraenderten
Kandidaten an. Jeder Kandidat besitzt eigene ID, Digest, deterministische UID,
ActionPlan und Idempotenz. Erfolg erfordert einen anschliessenden eindeutigen
CalDAV-Read-back mit UID, ETag und identischen Kernfeldern.

Mehrere Termine bleiben getrennte Einzelwrites. Nach einem Fehler stoppt der
Workflow; bereits bestaetigte Ziele bleiben als Teilerfolg sichtbar. Es gibt
kein automatisches Delete, Update oder Remote-Rollback.

## Failure Modes

- Unvollstaendige oder widerspruechliche Quelldaten enden als
  `information-required`; Werte werden nicht ergaenzt oder geraten.
- Stale Quelle, anderer Preview-Digest, anderer Kandidat oder Fremdturn werden
  abgewiesen.
- ClamAV-, Connector-, Policy-, Approval- oder Toolfehler enden als `blocked`.
- Ein erfolgreicher PUT ohne eindeutigen UID/ETag-Read-back wird als
  zustellungsunsicher behandelt und autorisiert keinen Erfolgsclaim.
- Nach dem ersten erfolgreichen und einem fehlgeschlagenen zweiten Create ist
  der Zustand partiell und niemals `completed`.
- Guard- oder Schemafehler bleiben fail-closed; es wird keine Freigabe erzeugt.
- Ein fehlender, abgelaufener, veraenderter oder bereits verbrauchter Nonce wird
  vor Prozessstart als `approval-required` mit `executed=false` belegt. Er darf
  nicht automatisch erneut benutzt werden. Ein blosses `/approve` ist keine
  gebundene Entscheidung; Chatfreigaben verwenden ausschliesslich die aktuelle
  Dialog-ID mit `allow-once`.
- Ein Mailentwurf ist nur lokale Vorbereitung. Er beendet den Turn als
  `approval-required`; erst eine spaetere ausdrueckliche Versandanweisung darf
  den unveraenderten Draft mit einer neuen eigenen Einzelfreigabe senden.

## Datenschutz und Telemetrie

Runtime-Metriken enthalten nur Zaehler fuer Verpflichtungen, Completion,
Blocker, Informationsbedarf, Teilerfolg und Guardrevision. Prompts,
Mailinhalte, Termine, Adressen, Argumentwerte und Secrets werden nicht
persistiert. Die Verpflichtung lebt nur fuer den aktuellen Run.

## Rollback

Der Code-Rollback erfolgt durch das vorhandene signierte Image-/State-Verfahren.
Er loescht keine bereits erzeugten CalDAV-Objekte. Da kein Delete-Werkzeug
registriert ist, existiert keine automatische Remote-Kompensation. Produktive
Read-only-Abnahme und jeder produktive Einzelschreibtest bleiben getrennte
Freigaben.

## Konsequenzen

- Eine ausdrueckliche Aktion kann nicht mehr als blosses Versprechen enden.
- Mehrstufige Aktionen benoetigen mehr aktuelle Read-Evidenz und einzelne
  Freigaben, behalten dafuer nachvollziehbare Zustands- und Fehlergrenzen.
- Der Zustandsvertrag ergaenzt Skilltext und Modellprompt; er ersetzt weder
  ActionPlan/Policy noch Connector- und Freigabegrenzen.
