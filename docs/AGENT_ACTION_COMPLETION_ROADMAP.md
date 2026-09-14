# M15-Roadmap: Verbindliche Aktionsausfuehrung und belegter Workflowabschluss

Stand: 2026-09-13
Vorgesehener Arbeitsbranch: `development/action-completion-m15`
Status: M15.0 bis M15.8 entwicklungsseitig implementiert und lokal/hermetisch
geprueft; signiertes Commitimage und produktive Aktivierung weiterhin getrennt

## Ausgangslage

M13 stellt native strukturierte Werkzeuge, feste Argumente, turngebundene
Evidenz und einen Antwortguard bereit. Damit werden falsche Toolnamen, rohe
Fach-Shellpfade und unbelegte Erfolgsbehauptungen weitgehend technisch
verhindert. Der produktive Kalender-Canary zeigt jedoch eine verbleibende
Luecke zwischen erkanntem Schreibwunsch und tatsaechlicher Ausfuehrung:

- Der Agent kann eine konkrete Aktion in Zukunftsform ankuendigen, ohne danach
  ein Werkzeug aufzurufen.
- Die Antwort kann mit einer Meta-Aussage wie „ich muss das Tool noch
  aufrufen“ enden, obwohl weder Erfolg noch ein echter technischer Blocker
  vorliegt.
- Ein fachlich mehrstufiger Wunsch wie „Flugdaten aus einer Mail als Termine
  eintragen“ benoetigt Mail-Suche, eindeutiges Lesen, belegte Extraktion,
  Kalenderauswahl, Duplikatpruefung, Create und Remote-Read-back. M13 prueft
  einzelne Tools und Claims, fuehrt diese Kette aber noch nicht als
  verpflichtenden Workflowzustand.
- Mehrere gewuenschte Schreibaktionen koennen teilweise erfolgreich sein.
  Ohne expliziten Workflowvertrag drohen ein falscher Gesamterfolg, ein
  stiller Abbruch oder eine unzulaessige automatische Kompensation.

Die Live-Diagnose des ausloesenden Vorfalls hat gleichzeitig belegt, dass die
Fachfunktion vorhanden ist: Der ausgewaehlte Kalender ist erreichbar und
unterstuetzt Create, List und Update. Es fehlte nicht die Berechtigung, sondern
der verbindliche Uebergang vom angekuendigten Vorhaben zum nativen Toolaufruf.
Produktive Namen, Mailinhalte und Identifikatoren werden nicht in Test-Fixtures
uebernommen.

## Architektonisches Ziel

M15 erweitert M13 um einen kleinen, technisch erzwungenen
Aktionsabschlussvertrag. Eine ausdrueckliche Nutzeranweisung erzeugt eine
turngebundene **Aktionsverpflichtung**. Solange diese offen ist, darf der Agent
den Turn nur auf eine der folgenden Arten beenden:

1. die Aktion wurde durch das registrierte Werkzeug ausgefuehrt und der
   erwartete Nachzustand ist belegt;
2. das Werkzeug verlangt eine konkrete, noch fehlende Freigabe und der Agent
   zeigt exakt Aktion, Argumente und Freigabegrund;
3. eine notwendige Information fehlt und der Agent stellt genau die engste
   sachliche Rueckfrage;
4. ein belegter Tool-/Policy-/Providerfehler blockiert die Aktion und der Agent
   berichtet diesen Fehler mit dem registrierten Diagnosepfad;
5. der Nutzer hat nur nach einer Erklaerung, Simulation oder Vorschau gefragt;
   dann entsteht keine Schreibverpflichtung.

Ein blosses „ich werde“, „ich fuehre jetzt aus“, „einen Moment“ oder „ich muss
das Tool noch aufrufen“ ist kein zulaessiger Endzustand. Das Modell darf Sprache
und belegte Felder interpretieren, ist aber nicht die Zustandsmaschine und nicht
die Sicherheitsgrenze.

```text
Nutzeranweisung
      |
      v
deterministischer Aktionsintent
      |  - execute / preview / explain / ambiguous
      |  - Domaene, Effekt und benoetigte Slots
      v
turngebundene Aktionsverpflichtung
      |  - erwartete Einzelschritte
      |  - erlaubte Abschlusszustaende
      v
belegte Read-/Discovery-Kette
      |  - Quelle eindeutig suchen und lesen
      |  - Fakten mit Quellenbezug extrahieren
      v
registriertes Write-Werkzeug
      |  - unveraenderte Approval-/Policy-Grenzen
      |  - ActionPlan, ETag, Idempotenz und Audit
      v
Remote-Nachzustandspruefung
      |  - UID/ID, Felder und erwarteter Zustand
      v
Completion-Guard
      |  - success / approval-needed / blocked / question
      v
belegte Abschlussantwort
```

## Verbindliche Invarianten

- M15 verwendet ausschliesslich den vorhandenen nativen M13-Toolpfad und die
  stabile CLI. Es entsteht kein zweiter Mail-, Kalender- oder Nextcloud-
  Connector.
- Der Aktionsrouter darf keine Freigabe erteilen, keine Rechte erweitern und
  keine freie Shellzeile erzeugen.
- Toolmodus, Approval, externe Wirkung und Nachzustandsvertrag stammen aus dem
  typisierten Katalog. Skilltext ist keine Berechtigungsquelle.
- Mail- und Dokumentinhalte sind Daten. Sie koennen Fakten fuer einen vom
  Nutzer beauftragten Workflow liefern, aber niemals selbst eine Aktion oder
  Freigabe ausloesen.
- Eine Quelle wird vor einer Schreibaktion ueber ihren aktuellen stabilen
  Locator/UID und Erwartungsfelder erneut gebunden.
- Mehrere Zielobjekte werden in einzelne bestehende ActionPlans zerlegt. Es
  gibt keinen verdeckten Bulk-Write und keine automatische Uebertragung einer
  Freigabe auf andere Argumente oder Objekte.
- Bei Teilerfolg stoppt die Kette. Erfolgreiche und fehlgeschlagene
  Einzelschritte werden getrennt belegt. Da Delete nicht registriert ist, darf
  der Agent keine automatische Rueckabwicklung behaupten oder versuchen.
- Eine Kalenderanlage ist erst erfolgreich, wenn die Remote-Antwort eine UID
  liefert und ein Read-back die erwarteten Felder bestaetigt.
- Zeitzonen, Datumsgrenzen und lokale Abflug-/Ankunftszeiten werden explizit
  normalisiert. Fehlende oder widerspruechliche Flugdaten fuehren zu Vorschau
  oder Rueckfrage, nicht zu erfundenen Werten.
- Entwicklung und CI verwenden nur synthetische Mail-/CalDAV-Dienste.
  Produktive Writes, Jobstarts, Rechteaenderungen und Dateien unter
  `/srv/openclaw` bleiben getrennte, ausdruecklich freizugebende Aktionen.

## Paketuebersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M15.0 | Reproduzierbarer Promise-without-Action-Fehlerfall | produktiver Befund, M13 |
| M15.1 | Versionierter Aktionsverpflichtungs- und Abschlussvertrag | M15.0 |
| M15.2 | Technischer Promise-/Finalize-Guard | M15.1 |
| M15.3 | Begrenzte, deterministische Workflowzustandsmaschine | M15.2 |
| M15.4 | Quellengebundener Mail-zu-Kalender-Previewpfad | M15.3 |
| M15.5 | Sichere Create-Ausfuehrung und Remote-Read-back | M15.4 |
| M15.6 | Mehrfachaktionen, Teilerfolg und Idempotenz | M15.5 |
| M15.7 | Skillrouting, Telemetrie und Modell-Evaluierung | M15.6 |
| M15.8 | Gesamt-Abnahme, signiertes Image und produktive Rolloutgrenze | M15.7 |

## M15.0 – Fehlerkorpus und reale Baseline

### Ziel

Den neuen Fehlertyp reproduzierbar vom bereits geloesten M13-Fehlerkorpus
abgrenzen und messen, ohne produktive Daten zu uebernehmen.

### Scope

- Synthetische deutsche und spanische Faelle fuer „jetzt ausfuehren“,
  „Vorschau“, „was wuerdest du tun?“ und mehrdeutige Wuensche anlegen.
- Den beobachteten Abbruch nach Zukunftsversprechen als echten Tool-Loop-Test
  nachbilden.
- Phasen getrennt messen: Aktionsintent, Read-Evidenz, Argumentvollstaendigkeit,
  Approval, Write-Aufruf, Nachzustand und Abschlussclaim.
- Kalender-aus-Mail als ersten Vertikalschnitt verwenden; zusaetzliche
  generische Faelle fuer Aufgabe abschliessen, Mail senden und Datei anlegen
  pruefen, damit der Vertrag nicht kalenderspezifisch wird.
- Keine allgemeine Erfolgsquote erfinden. Jeder kritische Fall wird einzeln als
  bestanden oder offen ausgewiesen.

### Abnahme

- Mindestens ein Test reproduziert einen Turn, der nach „ich fuehre jetzt aus“
  ohne Toolaufruf enden wuerde.
- Vorschau- und Erklaerungsanfragen erzeugen nachweislich keinen Write.
- Fixtures enthalten keine produktiven Adressen, Buchungsnummern, Termine oder
  Kalender-UIDs.
- Baseline dokumentiert Toolaufrufe, Abschlusszustand, Latenz und Grenzen.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.0 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Lies AGENTS.md, die M13-Roadmap,
deren ADR/Abnahme sowie die relevanten Personal-Assistant-Referenzen
vollstaendig. Veraendere keine Dateien unter /srv/openclaw, keine produktiven
Jobs und keine externen Daten. Ergaenze ein synthetisches, datenschutzsicheres
Fehlerkorpus fuer Aktionsankuendigung ohne Toolaufruf, Meta-Abbruch, fehlenden
Nachzustand, Teilerfolg, Vorschau und Erklaerung. Trenne Intent, Read-Evidenz,
Argumente, Approval, Write, Read-back und Abschlussclaim. Nutze Kalender aus
Mail als ersten Vertikalschnitt und wenige generische Gegenproben fuer andere
Domaenen. Implementiere nur den reproduzierbaren Baseline-Harness und echte
Verhaltenstests; beginne nicht mit M15.1.
```

## M15.1 – Aktionsverpflichtungs- und Abschlussvertrag

### Ziel

Einen versionierten, maschinenlesbaren Vertrag fuer offene Nutzeraktionen und
deren einzig zulaessige Abschlusszustaende definieren.

### Scope

- `execute`, `preview`, `explain` und `ambiguous` als geschlossene Intentklasse
  definieren.
- Eine turngebundene `action_obligation` mit Domaene, Effekt, Zielanzahl,
  benoetigten Slots, Read-Schritten, Write-Schritten und Nachbedingungen
  spezifizieren.
- Abschlusszustaende auf `completed`, `approval-required`,
  `information-required`, `blocked` und `cancelled-by-user` begrenzen.
- `announced`, `working`, `later` oder freie Zukunftstexte ausdruecklich nicht
  als Endzustand zulassen.
- Den Vertrag generatorgestuetzt mit dem M13-Katalog, Approval und
  Evidenzschema verbinden, ohne eine zweite manuelle Toolliste.
- Eine ADR fuer Zustandslebensdauer, Hookreihenfolge, Failure Modes und
  Rollback erstellen.

### Abnahme

- Kein unbekannter Tool-, Effekt-, Approval- oder Endzustand ist darstellbar.
- Alte Turns, andere Domaenen und geaenderte Argumente koennen keine
  Verpflichtung abschliessen.
- Der Vertrag kann keinen Write autorisieren; er beschreibt nur den
  ausstehenden oder belegten Zustand.
- Schema-, Generator- und Drift-Negativtests sind vorhanden.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.1 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Verwende die M15.0-Baseline und den
vorhandenen M13-Katalog. Definiere ein versioniertes Schema fuer execute,
preview, explain und ambiguous sowie eine turngebundene action_obligation mit
bekannten Domaenen, Effekten, benoetigten Slots, Einzelschritten und
Nachbedingungen. Erlaube als Endzustand nur completed, approval-required,
information-required, blocked und cancelled-by-user. Zukunftsversprechen und
Meta-Ankuendigungen sind keine Endzustaende. Leite Tool-, Modus- und
Approvaldaten generatorgestuetzt ab und dokumentiere Hookreihenfolge,
Failure Modes und Rollback in einer ADR. Implementiere Schema-, Drift-,
Fremdturn- und Argumentaenderungstests. Fuehre noch keinen Workflow aus und
beginne nicht mit M15.2.
```

## M15.2 – Promise- und Finalize-Guard

### Ziel

Technisch verhindern, dass ein Turn mit offener Aktionsverpflichtung als
blosse Ankuendigung oder Meta-Antwort endet.

### Scope

- Den nachgewiesenen OpenClaw-`before_agent_finalize`- und
  `reply_payload_sending`-Pfad erweitern.
- Bei offener Verpflichtung genau einen kontrollierten Revisionslauf erlauben,
  der ausschliesslich den naechsten registrierten Schritt oder einen erlaubten
  Blockerzustand anfordert.
- Nach dem Revisionslauf fail-closed eine strukturierte Abschlussantwort
  erzeugen, falls das Modell erneut nur verspricht oder schweigt.
- Planungs-/Erklaerungssprache von echter Ausfuehrungsabsicht unterscheiden;
  allgemeine Zukunftsformulierungen duerfen nicht versehentlich Writes
  erzwingen.
- Bestehenden Tool-Loop-Circuit-Breaker und M13-Claim-Guard erhalten.
- Inhaltsfreie Guard-Metriken fuehren; keine Nutzertexte oder Argumentwerte
  protokollieren.

### Abnahme

- Der reproduzierte reale Fehler endet entweder im echten Toolschritt oder in
  einem typisierten Blocker, niemals im Versprechen.
- Es gibt hoechstens einen Guard-Revisionslauf und keine Endlosschleife.
- Preview, Erklaerung, Ablehnung und Smalltalk bleiben ohne Schreibwirkung.
- Hookfehler bleiben fail-closed und erteilen keine Freigabe.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.2 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Erweitere die real vorhandenen
OpenClaw-Finalize- und Reply-Hooks um den M15.1-Vertrag. Eine offene
execute-Verpflichtung darf nicht mit Zukunftsversprechen, Wartebitte,
Meta-Kommentar oder leerer Antwort enden. Erlaube genau einen kontrollierten
Revisionslauf zum naechsten registrierten Schritt; danach liefere fail-closed
completed, approval-required, information-required oder blocked mit aktueller
Evidenz. Erhalte M13-Evidenzguard, Approval und Loop Detection. Trenne
Ausfuehrung sicher von Preview, Erklaerung und allgemeiner Sprache. Teste
deutsche/spanische Varianten, Hookfehler, Schweigen, wiederholtes Versprechen
und Endlosschleifen. Starte noch keinen fachlichen Mehrschrittworkflow und
beginne nicht mit M15.3.
```

## M15.3 – Deterministische Workflowzustandsmaschine

### Ziel

Mehrstufige Nutzerauftraege begrenzt ueber registrierte Einzelwerkzeuge fuehren,
ohne dem Modell einen freien Agentenplan oder neue Rechte zu geben.

### Scope

- Eine geschlossene Workflowdefinition mit `select-source`, `read-source`,
  `build-preview`, `resolve-target`, `check-duplicate`, `execute-one`,
  `verify-one` und `finish` einfuehren.
- Pro Workflow feste maximale Schritt-, Tool- und Zielanzahl definieren.
- Nur im Katalog bekannte Folgewerkzeuge zulassen. Keine Shell, keine freie
  URL und keine vom Quellinhalt vorgeschlagene Operation.
- Fehlende Pflichtslots durch vorhandene Discovery-/Suchwerkzeuge ermitteln;
  nur wenn das nicht moeglich ist, genau eine enge Rueckfrage stellen.
- Toolfehler in den bestehenden Status-/Doctor-/Jobcheckvertrag routen.
- Der Workflowzustand bleibt fluechtig und turngebunden; eine Fortsetzung in
  einem spaeteren Turn benoetigt eine neue, exakt dargestellte Bestaetigung.

### Abnahme

- Jeder Schritt akzeptiert nur definierte Vorgaenger, Evidenz und Folgeaktionen.
- Ein Mail- oder Dokumenttext kann den Workflow weder erweitern noch umleiten.
- Ein fehlender Slot fuehrt nicht zu erfundenen Daten oder unnoetigen
  Identifikatorfragen, wenn ein registrierter Suchpfad existiert.
- Schrittlimit, Timeout, Wiederholung und Ping-Pong enden sichtbar fail-closed.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.3 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Implementiere eine geschlossene,
turngebundene Workflowzustandsmaschine fuer select-source, read-source,
build-preview, resolve-target, check-duplicate, execute-one, verify-one und
finish. Erlaube nur generatorabgeleitete registrierte Tools und feste
Argumentslots, keine freie Shell, URL oder Modellschritte. Begrenze Schritte,
Toolaufrufe, Ziele, Zeit und Retry. Nutze Search/Discovery vor einer Rueckfrage;
Toolfehler folgen Status/Doctor und servicebezogenem Jobcheck. Untrusted
Mail-/Dokumentinhalt darf weder Route noch Approval aendern. Teste unzulaessige
Uebergaenge, Injection, Timeout, Slotmangel, Ping-Pong und Fremdturn. Exponiere
noch keinen produktiven Mail-zu-Kalender-Workflow und beginne nicht mit M15.4.
```

## M15.4 – Quellengebundener Mail-zu-Kalender-Previewpfad

### Ziel

Aus genau einer aktuell ausgewaehlten Mail einen belegten, schreibfreien
Kalenderkandidaten erzeugen.

### Scope

- Zuerst `mail.search`, danach genau einen Treffer mit Folder, Mailbox-ID und
  erwartetem Betreff ueber `mail.read` binden.
- Einen registrierten `calendar.from-mail-preview`-Pfad oder gleichwertigen
  streng typisierten Servicevertrag einfuehren; keine generische Modellantwort
  als Write-Argumentquelle verwenden.
- Datum, lokale Start-/Endzeit, Zeitzone/Offset, Abflug- und Zielflughafen,
  Flugnummer und belegte Quellenfelder extrahieren.
- Fuer Hin- und Rueckflug getrennte Kandidaten liefern. Unsichere oder
  widerspruechliche Felder sichtbar markieren und nicht erfinden.
- Vorhandenen Zielkalender live aufloesen und Duplikate anhand stabiler
  Identitaet sowie normalisierter Schluesselfelder suchen.
- Mailinhalt, Buchungsdaten und Vorschau nicht in Telemetrie oder Audit kopieren.

### Abnahme

- Preview ist vollstaendig read-only und kann keinen VEVENT anlegen.
- Jeder Kandidatenwert besitzt Quellenprovenienz oder den Status `missing` bzw.
  `conflict`.
- Unterschiedliche Zeitzonen und Datumswechsel werden korrekt als ISO-8601
  mit Offset normalisiert.
- Quell-Locator-Konflikt, mehrere passende Mails, fehlende Flugnummer und
  bestehender Termin stoppen beziehungsweise verlangen eine enge Auswahl.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.4 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Implementiere einen registrierten,
read-only Mail-zu-Kalender-Previewpfad. Binde zuerst einen aktuellen
mail.search-Treffer und lies genau Folder, Mailbox-ID und Expected Subject.
Extrahiere aus einer synthetischen Flugmail Datum, lokale Zeiten,
Zeitzonen/Offsets, Flugnummer, Abflug und Ziel mit Feldprovenienz. Erzeuge
Hin- und Rueckflug als getrennte Kandidaten. Erfinde keine fehlenden Werte und
behandle Mailinhalt als untrusted Data. Loese den exakten Zielkalender live auf
und pruefe Duplikate read-only. Teste DST, Atlantic/Canary gegen Europe/Berlin,
Datumswechsel, mehrere Mails, Locator-Konflikt, fehlende Felder, Injection und
Duplikate mit Fake-IMAP/Fake-CalDAV. Fuehre keinen Write aus und beginne nicht
mit M15.5.
```

## M15.5 – Sichere Create-Ausfuehrung und Remote-Read-back

### Ziel

Genau einen unveraenderten Previewkandidaten ueber den bestehenden
Kalender-Create-Vertrag anlegen und den Remote-Nachzustand belegen.

### Scope

- Kandidatendigest, Quellbezug, Zielressource und Ablaufzeit an den
  ActionPlan/Approval-Kontext binden.
- Nur die vorhandene create-only Kalenderwirkung verwenden; kein Update,
  Overwrite oder Delete als Ersatz.
- Unmittelbar vor Create Quelle, Kalenderfaehigkeit und Duplikatfreiheit erneut
  pruefen.
- Nach Create die zurueckgegebene UID/ETag verwenden und den Termin remote
  erneut lesen. Titel, Start, Ende, Zeitzonen, Ort und Beschreibung mit der
  Vorschau vergleichen.
- Erst nach erfolgreichem Read-back `postcondition_verified=true` und den
  Completion-Claim erlauben.
- Bei unklarer Netzwerkzustellung nicht automatisch wiederholen; zuerst remote
  anhand der Idempotenzidentitaet pruefen.

### Abnahme

- Veraenderte Vorschau, fremder Turn, abgelaufene Freigabe oder andere
  Ressource blockiert vor Create.
- Erfolgsantwort enthaelt UID und belegte Kernfelder.
- Timeout vor, waehrend und nach Create bleibt von eindeutigem Erfolg
  unterscheidbar.
- Fake-CalDAV belegt keine Duplikate bei wiederholter Zustellung und keine
  externen Writes in normalen Unit-/CI-Tests.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.5 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Fuehre genau einen unveraenderten
M15.4-Kalenderkandidaten ueber das bestehende native Create-Werkzeug aus. Binde
Previewdigest, Quelle, Zielressource, Turn und Ablaufzeit an den vorhandenen
Approval-/ActionPlanvertrag. Pruefe unmittelbar vorher Locator,
Kalenderfaehigkeit und Duplikatfreiheit. Lies den erzeugten VEVENT danach ueber
UID/ETag remote zurueck und vergleiche Titel, Start, Ende, Zeitzonen, Ort und
Beschreibung. Erlaube Erfolg nur mit postcondition_verified=true. Behandle
Timeout und unklare Zustellung ohne blindes Retry; verwende kein Update,
Overwrite oder Delete. Teste ausschliesslich gegen Fake-CalDAV und beginne
nicht mit M15.6.
```

## M15.6 – Mehrfachaktionen, Teilerfolg und Idempotenz

### Ziel

Einen explizit beauftragten Hin- und Rueckflug als zwei getrennte sichere
Einzelaktionen verarbeiten und jeden Ausgang korrekt darstellen.

### Scope

- Einen begrenzten Workflow von hoechstens vier Zielobjekten erlauben, ohne
  ein Bulk-Werkzeug einzufuehren.
- Fuer jeden Kandidaten eigener Digest, ActionPlan, Freigabeentscheid,
  Idempotenzschluessel, Create und Read-back.
- Bei Fehler sofort stoppen und erfolgreiche, fehlgeschlagene sowie noch nicht
  ausgefuehrte Schritte getrennt ausgeben.
- Kein automatisches Delete, Rollback oder Update eines bereits erzeugten
  Remoteobjekts.
- Wiederaufnahme nur mit neuer aktueller Evidenz und expliziter Darstellung der
  noch offenen Einzelaktion; bereits bestaetigte Nachzustaende nicht doppelt
  anlegen.
- Gesamtabschluss nur, wenn alle beauftragten Einzelaktionen belegt sind.

### Abnahme

- Zwei erfolgreiche Creates liefern zwei unterschiedliche UIDs und einen
  belegten Gesamtabschluss.
- Erfolg des ersten und Fehler des zweiten ergibt `partial`, niemals
  `completed` oder eine behauptete Rueckabwicklung.
- Retry nach Zustellungsunsicherheit erkennt den vorhandenen ersten Termin.
- Approval oder Evidenz eines Kandidaten kann nicht fuer den anderen verwendet
  werden.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.6 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Erweitere den abgenommenen
Einzel-Create-Workflow auf hoechstens vier separat modellierte Zielobjekte,
ohne Bulk-Tool oder Approval-Uebertragung. Jeder Kandidat erhaelt eigenen
Digest, ActionPlan, Freigabebindung, Idempotenzschluessel, Create und Read-back.
Stoppe beim ersten Fehler und berichte completed, failed und not-attempted pro
Schritt. Fuehre kein automatisches Delete, Update oder Rollback aus. Eine
Wiederaufnahme benoetigt neue Evidenz und darf bestaetigte Termine nicht
doppeln. Teste zwei Erfolge, Fehler im ersten/zweiten Schritt, Timeout,
Approval-Replay, geaenderte Argumente und Wiederaufnahme mit Fake-CalDAV.
Beginne nicht mit M15.7.
```

## M15.7 – Skillrouting, Telemetrie und Verhaltens-Evaluierung

### Ziel

Dem Modell den neuen Workflow knapp und eindeutig vermitteln und belegen, dass
der technische Guard auch mit dem produktiv gepinnten lokalen Modell wirkt.

### Scope

- Personal-Assistant-Skill und Groupware-/Toolreferenzen um die kompakte Route
  „Quelle suchen → Preview → Write → Read-back → Abschluss“ ergaenzen.
- Keine zweite manuelle Tooltabelle schaffen; Namen und Pflichtfelder bleiben
  generatorgestuetzt.
- Dem Agenten verbieten, Wartebitten oder Toolankuendigungen als Abschluss zu
  verwenden; diese Regel muss den technischen Guard erklaeren, nicht ersetzen.
- Inhaltsfreie Metriken fuer Verpflichtung, Schritt, Toolstatus,
  Abschlusszustand, Guardrevision, Teilerfolg und Latenz erfassen.
- M13- und M15-Fehlerkorpora deterministisch komplett abspielen.
- Einen wiederholten, read-only beziehungsweise Fake-Write Gemma-Canary mit
  gepinnter Modellidentitaet dokumentieren; kein produktiver Kalendereintrag.

### Abnahme

- Alle kritischen Promise-, Approval-, Injection-, Partial- und
  Postcondition-Faelle sind absolute Gates.
- Skill, Katalog, native Werkzeuge und Runtime-Schemata besitzen keinen Drift.
- Telemetrie enthaelt keine Prompts, Maildaten, Termine, Adressen oder Secrets.
- Der Gemma-Canary endet reproduzierbar in Toolausfuehrung oder typisiertem
  Blocker, nicht in einer Meta-Ankuendigung.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.7 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um. Aktualisiere den Personal-Assistant-
Skill und die relevanten Referenzen kompakt fuer Quelle, Preview, Write,
Read-back und Abschluss. Generiere Toolnamen und Pflichtfelder weiterhin aus
dem Katalog. Zukunftsversprechen oder Wartebitten duerfen keinen Workflow
abschliessen; der Text beschreibt nur das technische Enforcement. Ergaenze
inhaltsfreie Metriken fuer Verpflichtungs- und Abschlusszustaende ohne Prompts,
Maildaten, Termine, Adressen oder Secrets. Spiele M13 und M15 vollstaendig
deterministisch ab und fuehre optional wiederholte Gemma-Laeufe nur gegen
synthetische Dienste aus. Dokumentiere Messwerte und Nichtregressionsgrenzen.
Beginne nicht mit M15.8.
```

## M15.8 – Gesamt-Abnahme und produktive Rolloutgrenze

### Ziel

Den vollständigen Aktionsabschlussvertrag unabhaengig pruefen, als signiertes
Rollenimage liefern und produktive Schreibtests weiterhin separat halten.

### Scope

- Repository-, Manifest-, Wheel-, Compose-, Rollenimage-, SBOM-, Provenance-,
  Secret-, CVE-, Reproduzierbarkeits- und Signaturpfad ausfuehren.
- Hermetischen OpenClaw-End-to-End-Test mit echtem Plugin, Scripted Model,
  Fake-IMAP, Fake-CalDAV, Netzwerkfehlern und Teilfehlern betreiben.
- M13- und M15-Korpora unabhaengig auditieren; neue Guards duerfen bestehende
  Negativ-, Approval-, Circuit-Breaker- oder Single-Writer-Grenzen nicht
  abschwaechen.
- Produktiven Canary zuerst read-only ausfuehren: Calendar Status, Mail-Suche,
  exaktes Lesen, Preview und Duplikatpruefung. Keine produktive Mail oder
  Kalenderressource veraendern.
- Einen produktiven Kalender-Write nur nach neuer ausdruecklicher Freigabe fuer
  den exakten Previewkandidaten, signierten Digest, Backup und Rollbackgrenze
  zulassen.
- Beachten, dass ein Image-/State-Rollback bereits erzeugte CalDAV-Termine
  nicht loescht. Ohne registriertes Delete ist eine Remote-Kompensation nicht
  moeglich.

### Abnahme

- Kein offener Execute-Intent kann als Versprechen oder Meta-Antwort enden.
- Jeder behauptete Write besitzt Tool-, Approval-, UID- und
  Nachzustandsevidenz aus demselben Workflow.
- Hermetische Tests decken Erfolg, Blocker, Rueckfrage, Approval,
  Zustellungsunsicherheit, Teilerfolg, Idempotenz und Injection ab.
- Rollen- und Lieferkettenchecks sind gruen; Testcollection sinkt nicht
  unbemerkt.
- Produktiver read-only Canary und produktiver Write bleiben zwei getrennte
  Freigaben. Ohne Write-Freigabe gilt nur die technische M15-Abnahme, nicht die
  reale Terminanlage als bestaetigt.

### Entwicklungsprompt

```text
Setze ausschliesslich M15.8 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md um und pruefe M15.0 bis M15.7
unabhaengig und kritisch. Lies AGENTS.md sowie alle relevanten Referenzen
vollstaendig. Fuehre Repository-, Manifest-, Wheel-, Compose-, Rollenimage-,
SBOM-, Provenance-, Secret-, CVE-, Reproduzierbarkeits- und Signaturchecks aus.
Teste den echten OpenClaw-Pluginpfad hermetisch mit Scripted Model, Fake-IMAP,
Fake-CalDAV, Netzwerkfehler, Timeout, Approval, Injection, Teilfehler,
Idempotenz und Remote-Read-back. Spiele M13 und M15 komplett ab. Definiere einen
separaten produktiven read-only Canary fuer Status, Mailauswahl, Preview und
Duplikatpruefung; fuehre keinen produktiven Kalender-Write, Jobstart oder
Rechtewechsel aus. Ein spaeterer produktiver Write benoetigt eine neue exakte
Freigabe und darf keinen Remote-Rollback durch Imagewechsel behaupten. Berichte
Findings, Tests, Artefakte, Messwerte, offene Grenzen und das eindeutige Urteil
M15 ABGENOMMEN oder M15 NICHT ABGENOMMEN.
```

## Verbindliche Testmatrix

| Bereich | Positive Pruefung | Negative Pruefung |
| --- | --- | --- |
| Intent | explizites „eintragen“ erzeugt Verpflichtung | Preview/Erklaerung erzeugt keinen Write |
| Finalize | belegter Abschluss oder typisierter Blocker | Zukunftsversprechen, Meta-Abbruch, Schweigen |
| Workflow | feste registrierte Schrittfolge | freie Shell, URL, unbekanntes Tool, Schritt-Sprung |
| Mailquelle | exakter Locator und Expected Subject | mehrere Treffer, verschobene Mail, stale Locator |
| Extraktion | belegte Flugnummern, Zeiten und Zeitzonen | erfundene Felder, DST-/Datumsfehler, Injection |
| Kalenderziel | exakte konfigurierte Ressource | erster fuzzy Treffer, fehlende VEVENT-Rechte |
| Create | ActionPlan und unveraenderter Digest | Approval-Replay, Fremdturn, geaenderte Argumente |
| Read-back | UID/ETag und identische Kernfelder | fehlender Termin, abweichende Zeit, unbekannte Zustellung |
| Mehrfachaktion | zwei getrennte belegte Termine | falscher Gesamterfolg nach Teilerfolg |
| Idempotenz | vorhandenen Zielzustand erkennen | blindes Retry und Duplikat |
| Guard | genau ein Revisionslauf | Endlosschleife und Ping-Pong |
| Datenschutz | inhaltsarme technische Metriken | Prompt-, Mail-, Termin- oder Secretdaten im Log |
| Supply Chain | getestete signierte Rollenimages | ungetestete Schemata oder fremdes Plugin |

## Gesamtdefinition „M15 abgeschlossen“

M15 ist erst abgeschlossen, wenn:

- ein expliziter Execute-Intent eine turngebundene Aktionsverpflichtung erzeugt;
- ein offener Workflow technisch nicht als Zukunftsversprechen, Wartebitte,
  Meta-Antwort oder Schweigen enden kann;
- Preview, Erklaerung und mehrdeutige Unterhaltung keine Schreibwirkung
  ausloesen;
- alle Schritte nur registrierte strukturierte Tools mit unveraenderten
  Approval-, Policy-, ActionPlan-, Audit-, ETag- und Idempotenzgrenzen nutzen;
- Mail-/Dokumentinhalt keine Route oder Freigabe steuern kann;
- Kalenderkandidaten vollstaendig quellengebunden sind und fehlende Fakten
  sichtbar bleiben;
- jeder Schreiberfolg durch UID/ID und Remote-Nachzustand desselben Workflows
  belegt ist;
- Teilerfolg, Zustellungsunsicherheit und Wiederaufnahme ohne erfundenen
  Gesamterfolg oder automatische Remote-Kompensation behandelt werden;
- M13 und M15 als echte Verhaltensregressionen gruen sind;
- Wheel, Rollenimages und Supply Chain reproduzierbar geprueft sind;
- produktiver read-only Canary und produktiver Write weiterhin getrennt
  freigegeben werden.

## Unabhaengiger Abschluss-Audit-Prompt

```text
Pruefe die Implementierung von M15 aus
docs/AGENT_ACTION_COMPLETION_ROADMAP.md unabhaengig und kritisch. Lies AGENTS.md,
die M13-Unterlagen und alle relevanten Personal-Assistant-Referenzen
vollstaendig. Veraendere keine Dateien unter /srv/openclaw, keine produktiven
Jobs und keine externen Daten. Belege im echten OpenClaw-Hookpfad, dass ein
expliziter Execute-Intent nicht als Zukunftsversprechen, Meta-Antwort oder
Schweigen enden kann. Pruefe Verpflichtungsschema, turngebundene Zustandsfolge,
Read-Evidenz, Previewprovenienz, Approval, ActionPlan, UID/ETag,
Remote-Read-back, Idempotenz, unklare Zustellung, Teilerfolg, Retrygrenzen und
Promptinjection. Spiele M13 und M15 vollstaendig ab und ergaenze echte
Regressionstests fuer jeden Befund. Fuehre Repository-, Wheel-, Image-, SBOM-,
Provenance-, Scan-, Signatur- und hermetischen End-to-End-Pfad aus. Ein
produktiver Canary bleibt read-only; produktive Terminwrites benoetigen eine
neue exakte Freigabe. Berichte Findings nach Schweregrad, Korrekturen, Tests,
Messwerte, Artefakte, Sicherheitsgrenzen und das eindeutige Urteil M15
ABGENOMMEN oder M15 NICHT ABGENOMMEN.
```
