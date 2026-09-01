# M13-Roadmap: Verlaessliche Werkzeugsteuerung und belegte Agentenantworten

Stand: 2026-08-31
Vorgesehener Arbeitsbranch: `development/reliable-tool-orchestration-m13`
Status: M13.0 bis M13.7 als Entwicklungsstand implementiert; M13.8 lokal und
hermetisch pruefbar, CI-Signatur und produktiver read-only Canary bleiben
getrennte Abnahmeaktionen

## Ausgangslage

Der Personal Assistant besitzt bereits stabile `assistant.sh`-Kommandos, einen
typisierten Toolkatalog, Live-Capabilities, Fachreferenzen, Sicherheitsregeln und
Regressionstests. Der installierte `personal-assistant`-Skill ist fuer das Modell
sichtbar. Trotzdem verwendet das lokale Modell die Werkzeuge nicht immer
zuverlaessig:

- eine gepunktete Tool-ID wird mit einem ausfuehrbaren CLI-Kommando verwechselt,
- ein vorhandener Nextcloud-Connector wird ohne vorherigen Toolaufruf verneint,
- eine Postfachsuche wird durch einen rohen Himalaya-/Shellpfad ersetzt,
- eine vorhandene Provider-Suche wird nicht benutzt und stattdessen nach einer
  ISIN gefragt,
- nach einem Fehler werden Konfigurationsdateien gesucht oder bearbeitet,
- unvollstaendige Suchergebnisse werden in eine definitive Negativaussage
  uebersetzt,
- ein erfolgreicher oder fehlgeschlagener Zustand wird gelegentlich aus Erinnerung
  statt aus aktueller Werkzeugevidenz beantwortet.

Das ist keine fehlende Fachfunktion, sondern eine Luecke zwischen Toolkatalog und
Agentenlaufzeit. Aus Sicht des Modells ist `exec` weiterhin ein sehr allgemeines
Werkzeug. Das Modell muss den Skill laden, den richtigen Vertrag finden, eine
Tool-ID in CLI-Syntax uebersetzen, Argumente korrekt quoten und das Ergebnis
fachlich auswerten. Ein laengerer Prompt allein macht diese Kette nicht
maschinenfest.

## Architektonisches Ziel

M13 macht den bestehenden typisierten Toolvertrag zu einer echten
Laufzeitschnittstelle fuer den Agenten. Das Modell erhaelt kleine, strukturierte
Werkzeuge mit festen Argumenten und Ergebnisschemata. Ein deterministischer Router
erkennt klar benannte Fachdomaenen und verlangt vor einer zustandsbezogenen Antwort
passende aktuelle Evidenz. Ein Antwort-Guard verhindert unbelegte Erfolgs-,
Fehler- und Negativaussagen.

Das Modell bleibt fuer Sprachverstaendnis, Auswahl zwischen erlaubten Lesewegen und
Erklaerung zustaendig. Es ist weder Sicherheitsgrenze noch Shell-Compiler. CLI,
Policy, ActionPlan, Approval, ETag, Audit, ClamAV, Single Writer und Rollenmounts
bleiben die verbindlichen Ausfuehrungsgrenzen.

```text
Nutzeranfrage
     |
     v
deterministischer Intent-/Domaenenrouter
     |  - nur bekannte Domaenen und Effekte
     |  - keine freie Shellerzeugung
     v
strukturierte OpenClaw-Werkzeuge
     |  - feste JSON-Schemata
     |  - generiert aus dem typisierten Katalog
     v
versionierte Personal-Assistant-Bridge
     |  - argv statt Shell
     |  - bestehende CLI/Services
     |  - Policy, Approval und Audit unveraendert
     v
Werkzeugergebnis + Evidenzhuelle
     |  - ok/complete/fresh/coverage
     |  - erlaubte Aussagen und Folgeaktionen
     v
Antwort-Guard
     |  - aktueller Lauf und passende Domaene
     |  - keine unbelegte Negativ-/Erfolgsaussage
     v
belegte natuerlichsprachliche Antwort
```

## Begriffe und Source of Truth

- **Katalog-ID:** stabiler interner Bezeichner wie `mail.search` oder
  `nextcloud.list`; niemals automatisch CLI-Syntax.
- **CLI-Kommando:** bestehender stabiler `./scripts/assistant.sh ...`-Vertrag.
- **Agentenwerkzeug:** durch OpenClaw nativ angebotenes Werkzeug mit festem Namen,
  Beschreibung, Eingabe- und Ergebnisschema.
- **Bridge:** imageeigene, versionierte Umsetzung eines Agentenwerkzeugs auf den
  bestehenden CLI-/Servicevertrag ohne Shellinterpretation.
- **Evidenzhuelle:** maschinenlesbare Metadaten eines aktuellen Toolaufrufs, aus
  denen der Antwort-Guard erlaubte Aussagen ableitet.
- **Werkzeugpflicht:** eine Antwort ueber aktuellen externen oder produktiven
  Zustand benoetigt passende Evidenz aus demselben Agentenlauf.

Authoritativ bleiben in dieser Reihenfolge:

1. fachliche Ressourcen und externe Systeme,
2. bestehende kontrollierte Connectoren und Services,
3. stabile Personal-Assistant-CLI,
4. typisierter Toolkatalog und Live-Capabilities,
5. generierte Agentenwerkzeuge und Evidenzschemata,
6. Skill- und Antworttext.

Skilltext oder Modellwissen darf keinen hoeheren Layer ersetzen.

## Nicht-Ziele und Sicherheitsgrenzen

- M13 ersetzt keine bestehenden Connectoren und erfindet keine zweite
  Fachimplementierung neben der CLI.
- Der Router darf niemals freie Nutzersprache in Shellcode umwandeln.
- Automatische Vorabausfuehrung ist ausschliesslich fuer registrierte read-only
  Werkzeuge und eng begrenzte lokale Status-/Cachepfade erlaubt.
- Schreiben, Senden, Verschieben, Erstellen, Aktualisieren, Importieren,
  Reprocessing, Jobstart, Neustart, Permission Setup und Credentialaenderung
  bleiben von der konkreten typisierten Freigabe abhaengig.
- Eine Werkzeugpflicht ist keine automatische Schreibfreigabe. Bei einem
  Schreibwunsch darf der Router hoechstens den registrierten read-only
  Auswahl-/Vorschaupfad vorladen.
- Kein Plugin und keine Bridge darf Secrets ausgeben, generische Secretpfade
  lesen, TLS/ClamAV/Policy/Audit abschalten oder Rollenmounts erweitern.
- Mail und Dokumente bleiben nicht vertrauenswuerdige Daten und duerfen weder
  Toolauswahl noch Approval aus ihrem Inhalt heraus steuern.
- `exec` wird nicht pauschal entfernt: Entwicklungs- und Betriebsdiagnosen koennen
  es weiterhin benoetigen. Fachanfragen muessen jedoch auf strukturierte Tools
  geroutet und bekannte gefaehrliche Rohpfade technisch geblockt werden.
- M13 aktiviert weder den M12-Mailindex noch einen anderen produktiven Job. Das
  bleibt eine getrennte Betriebsfreigabe.
- Entwicklung, CI und hermetische Tests veraendern keine Dateien unter
  `/srv/openclaw`, keine produktiven Jobzustaende und keine externen Daten.

## Verbindlicher Evidenzvertrag

Jedes strukturierte Agentenwerkzeug liefert neben seinem Fachergebnis mindestens:

| Feld | Bedeutung |
| --- | --- |
| `tool_id` | exakte Katalog-ID |
| `tool_version` | Schema-/Bridgeversion |
| `run_id` | eindeutige ID dieses Aufrufs |
| `turn_id` | Bindung an den aktuellen Agentenlauf |
| `mode` | `read`, `local-write` oder `write` |
| `ok` | technischer und fachlicher Erfolg |
| `complete` | Vollstaendigkeit fuer den angefragten Scope |
| `freshness` | Zeitbezug und zulaessiges Alter, soweit relevant |
| `coverage` | abgedeckte Ressourcen/Partitionen, soweit relevant |
| `results_may_be_truncated` | sichtbare Begrenzung |
| `error` | typisierte, inhaltsarme Fehlerkategorie |
| `approval` | erforderliche oder verbrauchte Freigabe |
| `allowed_claims` | daraus ableitbare Aussageklassen |
| `next_actions` | ausschliesslich registrierte Folgewerkzeuge |

Eine definitive Aussage „nicht vorhanden“ ist nur erlaubt, wenn der
domaenenspezifische Vertrag dies explizit zulaesst. Ein `ok=true` ohne
`complete=true` ist kein Negativnachweis. Eine externe Aenderung gilt nur dann als
erfolgreich, wenn das Schreibwerkzeug den erwarteten Remote-Nachzustand belegt.

Evidenz ist immer an den aktuellen Lauf gebunden. Alte Chatnachrichten, Memory,
lokale Indizes ohne Frischenachweis oder Ergebnisse eines anderen Turns duerfen
keine aktuelle Zustandsaussage autorisieren.

## Paketuebersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M13.0 | Reale Toolnutzungs- und Fehlverhaltensbaseline | abgenommener Entwicklungsstand M12 |
| M13.1 | Versionierter Intent-, Tool- und Evidenzvertrag | M13.0 |
| M13.2 | Minimaler read-only OpenClaw-Toolbridge-Canary | M13.1 |
| M13.3 | Vollstaendige strukturierte read-only Fachdomaenen | M13.2 |
| M13.4 | Deterministische Werkzeugpflicht und sichere Vorabrouten | M13.3 |
| M13.5 | Strukturierte Schreibwerkzeuge ohne Freigabeerosion | M13.4 |
| M13.6 | Maschinenfester Antwort- und Fehler-Guard | M13.5 |
| M13.7 | Kompakter Skill, Telemetrie und Verhaltens-Evaluierung | M13.6 |
| M13.8 | Gesamt-Abnahme, signiertes Image, Canary und Rolloutgrenze | M13.7 |

## M13.0 – Toolnutzungsbaseline und Fehlerkorpus

### Ziel

Das aktuelle Verhalten reproduzierbar messen, bevor Toolnamen, Skilltexte oder
Laufzeit erweitert werden. Die Baseline trennt Skill-Trigger, Toolauswahl,
Argumentsynthese, Ausfuehrung und Ergebnisinterpretation.

### Scope

- Ein datenschutzsicheres Korpus typischer deutscher und spanischer
  Nutzerformulierungen fuer alle Fachdomaenen erstellen.
- Bereits beobachtete Fehlerklassen als synthetische Faelle abbilden:
  gepunktete Tool-ID als Kommando, Nextcloud-Verneinung, rohe Himalaya-Suche,
  fehlende Provider-Suche, Konfigurationsedit nach Fehler, falsche Negativaussage,
  falsche Produktversion und unbelegter Schreiberfolg.
- Pro Fall Soll-Domaene, zulaessige erste Tools, verbotene Tools, erforderliche
  Ergebnisfelder und zulaessige Antwortklasse maschinenlesbar festhalten.
- Einen deterministischen Replay-Harness mit Fake-Connectoren und einem
  skriptbaren Modelladapter erstellen. Ein optionaler lokaler Gemma-Lauf darf die
  reale Baseline ergaenzen, aber CI nicht von Ollama abhaengig machen.
- Messwerte erfassen: Skill geladen, erstes Tool korrekt, gueltige Argumente,
  unzulaessiger Fallback, Toolfehlerbehandlung, Evidenznutzung, Latenz,
  Toolaufrufzahl und Kontextgroesse.
- Keine willkuerliche allgemeine Qualitaetsquote festlegen; kritische
  Sicherheitsfaelle werden einzeln ausgewiesen.

### Abnahme

- Jeder bekannte reale Fehlertyp besitzt mindestens einen echten
  Verhaltensfall, nicht nur eine Textsuche im Skill.
- Fixtures enthalten keine produktiven Mailadressen, Dateinamen, Inhalte,
  Credentials oder Portfoliozahlen.
- Replay ist reproduzierbar und veraendert keine externe Ressource.
- Baseline dokumentiert Messbefehl, Modell-/Toolversionen und Unsicherheiten.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.0 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um. Lies
AGENTS.md und alle Personal-Assistant-Referenzen vollstaendig. Veraendere keine
Produktivkonfiguration, keine Jobs, keine Dateien unter /srv/openclaw und keine
externen Daten. Erstelle ein datenschutzsicheres, maschinenlesbares
Toolrouting-Fehlerkorpus fuer Version, Mail, Nextcloud, Rechnungen, Kontakte,
Kalender, Aufgaben, Bestellungen, Portfolio und Runtime. Trenne Skill-Trigger,
erstes Tool, Argumente, Ausfuehrung, Ergebnisinterpretation und Antwortclaim.
Implementiere einen deterministischen Fake-Connector-/Scripted-Model-Replay;
Ollama darf nur optional gemessen werden und ist kein CI-Zwang. Bilde die real
beobachteten Fehler als Verhaltenspruefungen ab, nicht als reine Textsuchen.
Dokumentiere Baseline, Befehle, Tool-/Modellversionen und Unsicherheiten. Setze
keine allgemeine willkuerliche Erfolgsquote und beginne nicht mit M13.1.
```

## M13.1 – Intent-, Tool- und Evidenzvertrag

### Ziel

Vor der Runtimeimplementierung einen kleinen, versionierten Vertrag fuer
Toolnamen, Argumente, Effekte, Evidenz und Aussagen schaffen und die tatsaechlich
unterstuetzten Erweiterungspunkte der gepinnten OpenClaw-Version belegen.

### Scope

- Native Toolregistrierung, JSON-Schema, Hook-/Middleware-Lebenszyklus,
  Toolresultate und Antwortabschluss der eingebetteten OpenClaw-Version aus dem
  installierten Code und hermetischen Tests pruefen.
- Eine ADR fuer imageeigene Toolbridge, Runtimegrenzen, Datenfluss,
  Failure Modes und Rollback erstellen.
- Agentenwerkzeugnamen und Schemata deterministisch aus dem bestehenden
  typisierten Katalog ableiten; manuelle Parallellisten verhindern.
- Katalog-ID, Agentenwerkzeugname und CLI-Kommando klar trennen.
- Den oben beschriebenen Evidenzvertrag als JSON-Schema versionieren.
- Domaenenmatrix fuer erforderliche Evidenz und erlaubte Claims anlegen.
- Falls die gepinnte OpenClaw-Version keine belastbare Tool-/Antwort-Interzeption
  erlaubt, M13.1 mit einer belegten ADR und Upgradeentscheidung stoppen. Kein
  unsicherer Shell- oder Prompt-Hack darf als technische Erzwingung bezeichnet
  werden.

### Abnahme

- Ein Schema kann keine unbekannte Tool-ID, keinen unbekannten Effekt und keine
  freie Shellzeile ausdruecken.
- Read-, Local-write- und Write-Effekte bleiben typisiert getrennt.
- Tool- und Evidenzschema besitzen Generator-/Driftchecks.
- Die ADR benennt exakt, was technisch erzwungen wird und was weiterhin
  Modellverhalten bleibt.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.1 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Verwende die M13.0-Baseline. Pruefe im gepinnten OpenClaw-Code die echten APIs
fuer native Tools, JSON-Schemata, Toolresultate, Hooks und Antwortabschluss;
erfinde keine Plattformfaehigkeit. Dokumentiere die Entscheidung in einer ADR.
Erzeuge versionierte Agentenwerkzeug- und Evidenzschemata deterministisch aus dem
bestehenden Toolkatalog. Trenne Katalog-ID, nativen Toolnamen und exaktes
CLI-Kommando. Das Schema darf keine freie Shell und keinen unbekannten Effekt
zulassen. Definiere pro Domaene erforderliche aktuelle Evidenz und erlaubte
Claims. Wenn eine echte technische Antwortinterzeption fehlt, stoppe mit einer
belegten Upgradeentscheidung statt Promptzwang als Enforcement auszugeben.
Ergaenze Drift-, Schema- und Negativtests und beginne nicht mit M13.2.
```

## M13.2 – Minimaler read-only Toolbridge-Canary

### Ziel

Den kompletten nativen Werkzeugpfad mit wenigen risikoarmen Leseoperationen
beweisen, bevor alle Fachdomaenen migriert werden.

### Scope

- Eine imageeigene, unveraenderliche OpenClaw-Erweiterung fuer Personal-Assistant-
  Werkzeuge implementieren und in den Supply-Chain-Vertrag aufnehmen.
- Zunaechst nur `version --verify`, `status`, `tools list`, `capabilities`,
  `nextcloud list` und eine synthetische Mail-Suche exponieren.
- Argumente als Array/typisierte Werte an die bestehende CLI oder denselben
  Serviceport uebergeben; keine Shell, kein `eval`, keine Pipeline.
- Rollen-, Timeout-, CA-, Env- und Secretverhalten mit dem normalen Gateway-
  Entrypoint identisch halten.
- Ergebnisgroessen begrenzen, ohne `complete`, Coverage, Freshness,
  Trunkierung oder Fehlerdetails zu verlieren.
- Toolbridge-Status und Schema-/Releaseidentitaet read-only diagnostizierbar
  machen.

### Abnahme

- Das Modell sieht die Canary-Werkzeuge als echte strukturierte Tools.
- Eine gepunktete Tool-ID kann nicht als Kommando ausgefuehrt werden.
- Boesartige Argumente werden als Daten behandelt und niemals durch eine Shell
  interpretiert.
- Nextcloud verwendet den kombinierten CA-Truststore des normalen Entrypoints.
- Fehlende Credentials, TLS-Fehler und unvollstaendige Ergebnisse bleiben
  typisiert und fail-closed.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.2 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Implementiere den in M13.1 entschiedenen imageeigenen OpenClaw-Toolbridge-Canary
nur fuer verifizierte Version, Status, Toolkatalog, Live-Capabilities,
Nextcloud-Liste und synthetische Mail-Suche. Verwende strukturierte Argumente und
argv oder einen typisierten Serviceport, niemals Shell, eval oder Pipeline.
Erhalte CA-, Rollen-, Secret-, Timeout- und Read-only-Grenzen des normalen
Entrypoints. Rueckgaben muessen die M13-Evidenzhuelle sowie bestehende
Vollstaendigkeits- und Fehlerfelder enthalten. Teste Argumentinjektion,
unbekannte Tools, Timeout, TLS, Trunkierung, Schema-/Release-Drift und
unverfuegbare Ressourcen verhaltensbasiert. Aktualisiere Imagevertrag,
Dokumentation und Manifest. Exponiere keine Schreibwerkzeuge und beginne nicht
mit M13.3.
```

## M13.3 – Strukturierte read-only Fachdomaenen

### Ziel

Alle normalen Lese-, Such-, Status- und Vorschaupfade als native Werkzeuge
bereitstellen, sodass das Modell keine Fachkommandos mehr synthetisieren muss.

### Scope

- Mail: Status, Doctor, Liste, Hybrid-/lokale Suche, Lesen, Review, Indexstatus.
- Nextcloud: Dateien listen/synchronen Status, ohne generischen WebDAV-Zugang.
- Groupware: Kontakte, Kalender und Aufgaben status/list/search/discover.
- Records: Rechnungsstatus, Dateien, Audit, Liste, Review und read-only Vorschauen;
  Bestellstatus und Liste.
- Portfolio: Holdings, Status, Doctor, Quotes, EUR-Bewertung, Mappingvorschlag,
  Analyse, Researchstatus/-modelle und Philosophie-Reads.
- Runtime: Release, Status, Jobs, Scheduler, Ollama, Monitoring und Antivirus-
  Lesewege.
- Parametergrenzen, Enums, Datum/Zeit, ISIN, Pfade und Resultlimits in den
  JSON-Schemata abbilden.
- Generische `exec`-Fallbacks fuer diese Domaenen sichtbar ablehnen und den
  passenden nativen Toolnamen liefern.

### Abnahme

- Jede read-only Katalog-ID ist entweder nativ exponiert oder mit begruendetem,
  getesteten Ausschluss dokumentiert.
- Katalog, Agentenwerkzeug, CLI, Skillreferenz und Verhaltenstest stimmen
  generatorgestuetzt ueberein.
- Nulltreffer, Teilabdeckung, Stale, Truncation, Providerfehler und mehrere Treffer
  bleiben unterscheidbar.
- Toolresultate enthalten keine Secrets und keine unnoetigen Rohinhalte.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.3 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Erweitere die abgenommene read-only Bridge auf alle registrierten Lese-, Such-,
Status-, Discovery- und Vorschauwerkzeuge fuer Mail, Nextcloud, Kontakte,
Kalender, Aufgaben, Rechnungen, Bestellungen, Portfolio, Runtime und Sicherheit.
Generiere Namen und Schemata aus dem typisierten Katalog; pflege keine zweite
manuelle Kommandoliste. Validiere Enums, Pfade, Limits, Datum, ISIN und erwartete
Identitaeten vor der Ausfuehrung. Erhalte alle fachlichen Felder fuer Coverage,
Freshness, Complete, Truncation, Locator und Providerfehler in der Evidenzhuelle.
Blockiere bekannte rohe Fachfallbacks mit einem strukturierten naechsten Tool,
aber entferne generisches exec nicht pauschal. Ergaenze echte Handler-, Schema-,
Fehler- und End-to-End-Tests. Exponiere noch keine Schreibwerkzeuge und beginne
nicht mit M13.4.
```

## M13.4 – Deterministische Werkzeugpflicht und sichere Vorabrouten

### Ziel

Klar benannte Fachdomaenen technisch zu einem passenden aktuellen Leseaufruf
fuehren, bevor das Modell eine Zustandsantwort formuliert.

### Scope

- Einen deterministischen, versionierten Intentrouter fuer explizite Domaenen-
  und Operationsbegriffe implementieren.
- Der Router darf nur Katalog-IDs und strukturierte Argumentslots liefern, niemals
  Befehlszeilen.
- Fuer eindeutige read-only Anfragen das erforderliche erste Tool verpflichtend
  vorladen oder als `required` in den nachgewiesenen OpenClaw-Toolpfad geben.
- Bei fehlendem notwendigen Argument nur eine enge Rueckfrage erlauben; ein
  vorhandener Discovery-/Suchpfad muss vor einer unnoetigen Identifikatorfrage
  benutzt werden.
- Mehrdomaenenanfragen in einen begrenzten read-only Plan zerlegen, Ergebnisse
  getrennt halten und keine Freigabe zwischen Domaenen uebertragen.
- Schreibwuensche nur bis zum read-only Auswahl-, Status-, Such- oder
  Vorschauwerkzeug vorladen. Der eigentliche Write bleibt gesperrt.
- Unklare allgemeine Unterhaltung darf ohne Fachtool weiterlaufen.

### Abnahme

- Explizite kritische Baselinefaelle waehlen deterministisch die richtige
  Domaene und ein zulaessiges erstes Tool.
- Der Router kann keinen Write, Jobstart, Versand, Move oder Permission Setup
  automatisch ausloesen.
- Mail-/Dokumentinhalt kann die vom Nutzertext bestimmte Route nicht veraendern.
- Unbekannte oder mehrdeutige Intents werden sichtbar `unresolved`, nicht als
  erfundene Toolroute behandelt.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.4 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Implementiere einen deterministischen, versionierten Intentrouter fuer klar
benannte Personal-Assistant-Domaenen. Seine Ausgabe darf nur bekannte Tool-IDs,
strukturierte Slots und eine read-only Pflicht enthalten, niemals Shelltext.
Fuehre eindeutige Status-, Listen- und Suchanfragen vor der Antwort ueber das
passende native Tool. Nutze registrierte Discovery-/Suchwege, bevor du nach
einem Identifikator fragst. Bei Schreibwuenschen darf nur der read-only
Auswahl-/Vorschaupfad automatisch laufen; Versand, Move, Create, Update,
Reprocessing, Import, Jobsteuerung, Permission Setup und Credentials bleiben
gesperrt. Teste Mehrdomaenenanfragen, Mehrdeutigkeit, Promptinjection aus
Mail/Dokumenten und alle M13.0-Fehlerfaelle. Beginne nicht mit M13.5.
```

## M13.5 – Strukturierte Schreibwerkzeuge und unveraenderte Freigaben

### Ziel

Nach erfolgreichem read-only Routing auch registrierte Schreibwege nativ
anbieten, ohne deren fachliche oder sicherheitstechnische Freigaben abzuschwaechen.

### Scope

- Write-/Local-write-Schemata aus Modus, Effekt und Approval des bestehenden
  Katalogs generieren.
- Auswahl, Vorschau, ActionPlan, explizite Freigabe und Ausfuehrung als getrennte
  Zustandsuebergaenge modellieren.
- Exakt eine aktuelle Ressource/UID/ID, Erwartungsfelder, ETag und unveraenderten
  Vorschau-Digest binden.
- Mailentwurf und Versand, bestehende Objektupdates, Portfolio-Mapping,
  Rechnungsreprocessing, Nextcloud-Create/Move und Jobsteuerung jeweils an ihrem
  bestehenden Vertrag belassen.
- Approval ist an aktuellen Turn, exaktes Tool, Argumentdigest und Ablaufzeit
  gebunden und nicht auf andere Aktionen uebertragbar.
- Technisch verbotene Aktionen bleiben auch bei Modell- oder Pluginfehler
  unausdrueckbar.

### Abnahme

- Kein Write kann durch Intentrouter oder read-only Evidenz automatisch
  freigegeben werden.
- Veraenderte Argumente, stale ETags, abweichende Titel, wiederverwendete
  Approvals und unklare Zustellung stoppen fail-closed.
- Bestehende idempotente ActionPlan- und Auditbelege bleiben erhalten.
- Delete, Overwrite, Share, Bulk, Merge und unregistrierte Cross-Resource-Moves
  bleiben technisch blockiert.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.5 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Exponiere registrierte Local-write- und Write-Werkzeuge strukturiert aus dem
bestehenden Katalog. Erhalte fuer jedes Werkzeug Modus, externe Wirkung,
Approval, ActionPlan, stabile ID/UID, Erwartungsfelder, ETag, Idempotenz, Audit
und Nachzustandspruefung. Binde eine Freigabe an aktuellen Turn, exakte Tool-ID,
Argumentdigest und Ablaufzeit. Der Router darf niemals den Write selbst
ausloesen. Teste veraenderte Argumente, Approval-Replay, stale ETags,
Mehrfachtreffer, Sendestatus unknown, Policy-Denial und alle hart verbotenen
Aktionen. Keine Freigabe darf von einem Tool auf ein anderes uebergehen. Beginne
nicht mit M13.6.
```

## M13.6 – Antwort-, Negativaussage- und Fehler-Guard

### Ziel

Verhindern, dass der Agent trotz korrekter Tools eine unbelegte oder zum Ergebnis
widerspruechliche Antwort sendet.

### Scope

- Aktuelle Evidenz pro Turn in einem fluechtigen, inhaltsarmen Ledger fuehren.
- Zustands-, Negativ-, Erfolgs-, Versions- und Aenderungsclaims gegen Domaene,
  Tool-ID, Turn, Freshness, Coverage und `allowed_claims` pruefen.
- Bei fehlender Evidenz genau einen kontrollierten erneuten Tool-/Antwortversuch
  erlauben; danach eine sichere strukturierte Fehlermeldung statt Halluzination.
- Toolfehler fuehren zum registrierten Status-/Doctorpfad und bei Servicebezug
  zum Jobcheck, ohne Credentials, Policy oder Konfiguration automatisch zu
  aendern.
- Teilresultate duerfen positive Treffer erklaeren, aber keine globale
  Negativaussage autorisieren.
- Ein behaupteter Remote-Write benoetigt den belegten Nachzustand.
- Der Guard protokolliert nur Tool-ID, Status, Fehlerkategorie, Zeit und
  Claimklasse, niemals Nutzdaten oder Queries.

### Abnahme

- Kritische unbelegte Claims werden in deterministischen Tests zu 100 Prozent
  geblockt.
- Evidenz aus einem alten Turn oder einer anderen Domaene wird abgelehnt.
- Der Guard kann weder neue Tools waehlen noch Rechte erweitern.
- Ein Guardfehler endet fail-closed mit einer hilfreichen, nicht erfundenen
  Antwort und ohne Endlosschleife.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.6 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Implementiere den in M13.1 belegten Antwort-Hook mit einem fluechtigen
turngebundenen Evidenzledger. Pruefe Negativ-, Erfolgs-, Versions-,
Remotezustands- und Schreibclaims gegen Tool-ID, Domaene, ok, complete,
freshness, coverage, truncation, approval und allowed_claims. Erlaube bei
fehlender Evidenz hoechstens einen kontrollierten Retry; danach antworte
fail-closed mit erforderlichem registrierten Tool und exakter Fehlerkategorie.
Ein Toolfehler nutzt Status/Doctor und bei Servicebezug den Jobcheck, aendert
aber keine Credentials, Policy, Rechte oder Konfiguration. Protokolliere keine
Inhalte, Queries oder Adressen. Teste alte/fremde Evidenz, Teilabdeckung,
Nulltreffer, widerspruechlichen Modelltext, Promptinjection, Write ohne
Nachzustand und Retryschleifen. Beginne nicht mit M13.7.
```

## M13.7 – Kompakter Skill, Telemetrie und Verhaltens-Evaluierung

### Ziel

Die nun technisch erzwungenen Regeln aus dem langen Hauptskill entfernen oder
stark kuerzen, progressive Fachreferenzen beibehalten und reale Toolnutzung
messbar machen.

### Scope

- `SKILL.md` auf Identitaet, Sicherheitsinvarianten, kompakte Domaenenroute und
  Referenznavigation reduzieren.
- Exakte Toolnamen und Schemata aus dem Generator statt aus manuellen Tabellen
  anzeigen.
- Fachdetails in kleine bedarfsgeladene Referenzen verschieben; keine
  widerspruechlichen Kommandolisten erzeugen.
- Inhaltsfreie Metriken: erkannter Intent, erforderliches/erstes Tool,
  Schemafehler, geblockter Fallback, Guardretry, Claim-Denial, Laufzeit und
  Resultgroessenklasse.
- M13.0-Korpus komplett erneut abspielen und vorher/nachher vergleichen.
- Einen optionalen realen Gemma-Canary mit gepinnter Modellidentitaet,
  festem Seed soweit unterstuetzt und wiederholten Laeufen dokumentieren.
- Erst nach der Baseline begruendete Regressionsgrenzen fuer Latenz und
  Routingqualitaet festlegen. Kritische Sicherheitsfaelle bleiben absolute Gates.

### Abnahme

- Der Hauptskill ist deutlich kleiner, ohne eine Sicherheitsinvariante oder
  Domaenenabdeckung zu verlieren.
- Kein generierter Toolname driftet zwischen Katalog, OpenClaw, Doku und Tests.
- Alle kritischen M13.0-Faelle bestehen deterministisch.
- Nichtkritische Modellqualitaet und Latenz werden gegen die gemessene Baseline
  bewertet, nicht gegen erfundene Zielwerte.
- Telemetrie ist nachweislich frei von Nutzerinhalten und Credentials.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.7 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Kuerze den Hauptskill auf Identitaet, Sicherheitsinvarianten, kompakte
Domaenenroute und progressive Referenzen. Generiere Toolnamen/-schemata aus dem
Katalog und entferne redundante manuelle Kommandolisten erst, wenn Tests deren
Abdeckung beweisen. Ergaenze datenschutzsichere Routing-, Schema-, Guard- und
Latenzmetriken ohne Queries, Inhalte, Adressen oder Secrets. Spiele das gesamte
M13.0-Korpus vorher/nachher ab. Ein realer Gemma-Canary ist optional und darf CI
nicht von Ollama abhaengig machen. Lege erst auf Basis der Messwerte begruendete
Nichtregressionsgrenzen fest; kritische Sicherheitsfaelle muessen vollstaendig
gruen sein. Beginne nicht mit M13.8.
```

## M13.8 – Gesamt-Abnahme, Canary und Rolloutgrenze

### Ziel

Die neue Werkzeugarchitektur unabhaengig pruefen, als attestiertes Rollenimage
veroeffentlichen und erst nach einem read-only Produktcanary zur getrennten
Aktivierung freigeben.

### Scope

- Vollstaendigen Repository-, Manifest-, Wheel-, Compose-, Rollenimage-, SBOM-,
  Provenance-, Secret-, CVE- und Signaturpfad ausfuehren.
- Hermetischen OpenClaw-End-to-End-Test mit echten nativen Tools, scripted model,
  Fake-Mail, Fake-Nextcloud, Fake-EODHD und Fehler-injection betreiben.
- Rollen-, CA-, Env-, Timeout-, Read-only-Workspace- und Single-Writer-Grenzen
  pruefen.
- Unabhaengige kritische Pruefung gegen das gesamte M13.0-Korpus durchfuehren.
- Einen getrennt freizugebenden produktiven read-only Canary definieren:
  Version, Status, Mailpositivtreffer, Mail-Nulltreffer mit Coverage,
  Nextcloud-Liste, Aufgaben-/Kontakt-/Kalenderstatus, Holdings und ein absichtlich
  unvollstaendiges Ergebnis.
- Schreibcanary nur gegen hermetische Fake-Dienste. Keine produktive Mail, Datei,
  Aufgabe, Termin, Kontakt, Karte oder Watchlist wird fuer die Abnahme geaendert.
- Rollback auf vorherige signierte Digests und Rueckbau der Toolbridge testen.
- Betriebs-, Entwickler-, Skill-, Tool-, Git-, Release- und Troubleshootingdoku
  aktualisieren.

### Abnahme

- Lokale Checks und CI sind gruen; Testcollection sinkt nicht unbemerkt.
- Das signierte Image enthaelt exakt die getestete Bridge und Schemata.
- Alle kritischen Routing-/Claim-/Approval-/Injectiontests sind gruen.
- Der read-only Canary belegt, dass der Agent die strukturierten Tools tatsaechlich
  verwendet und unvollstaendige Ergebnisse nicht als Nulltreffer ausgibt.
- Produktive Aktivierung bleibt ein separater expliziter Auftrag mit Backup,
  Digestverifikation, Smoke, Beobachtung und Rollback.

### Entwicklungsprompt

```text
Setze ausschliesslich M13.8 aus docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md um.
Pruefe M13.0 bis M13.7 unabhaengig und kritisch. Fuehre Repository-, Manifest-,
Wheel-, Compose-, Rollenimage-, SBOM-, Provenance-, Secret-, CVE-, Signatur- und
Reproduzierbarkeitschecks aus. Baue einen hermetischen OpenClaw-End-to-End-Stack
mit echten nativen Personal-Assistant-Tools, scripted model, Fake-Mail,
Fake-Nextcloud, Fake-EODHD und Fehler-injection. Pruefe Routing,
Argumentvalidierung, Evidenz, Negativaussagen, Approval, ETag, Sendestatus,
Promptinjection, CA/Env-Paritaet, Rollen und Single Writer. Definiere einen
separat freizugebenden produktiven read-only Canary; fuehre keine produktive
Schreibaktion, keinen Jobstart und keine Permissionaenderung aus. Teste Rollback
auf vorherige signierte Digests. Aktualisiere alle verbindlichen Dokumente und
beende die Arbeit nach M13. Beginne keinen Folgemilestone und installiere nichts
produktiv ohne neuen ausdruecklichen Auftrag.
```

## Verbindliche Testmatrix

| Bereich | Positive Pruefung | Negative Pruefung |
| --- | --- | --- |
| Toolgenerierung | Katalog und native Schemata stimmen | Drift, unbekannte ID, doppelter Name |
| Argumentsicherheit | strukturierte Werte/argv | Shellmetazeichen, Injection, falscher Typ |
| Routing | explizite Domaene waehlt Solltool | mehrdeutig, unbekannt, Inhaltsprompt |
| Mail | positiver Treffer und belegter Nulltreffer | Teilcoverage, stale, Folderfehler, Raw-Himalaya |
| Nextcloud | native Liste mit CA-Trust | lokaler Mountfallback, TLS-Fehler, Truncation |
| Groupware | Status/List/Search mit UID | fuzzy Write, stale ETag, fremde Ressource |
| Rechnungen | Status/Audit/Vorschau | erfundene Felder, Bulk-Apply, Digestabweichung |
| Portfolio | Holdings/Quotes/Mappingvorschlag | Webersatzkurs, unbestaetigtes Mapping, 403 |
| Version | verifiziertes Produktrelease | Core-/Modellversion als Produktantwort |
| Approval | exakt gebundene Einzelaktion | Replay, Argumentaenderung, Domaenenuebertrag |
| Antwort-Guard | belegter Claim | alte/fremde Evidenz, unbelegter Erfolg/Nulltreffer |
| Fehlerpfad | registrierter Status/Doctor | Configedit, Secretsuche, Endlosschleife |
| Supply Chain | signierte reproduzierbare Rollen | fremdes Plugin, ungetestetes Schema, Secretfund |

## Gesamtdefinition „M13 abgeschlossen“

M13 ist erst abgeschlossen, wenn:

- das Modell die Personal-Assistant-Fachfunktionen als native strukturierte
  Werkzeuge sieht,
- alle exponierten Werkzeuge deterministisch aus dem typisierten Katalog stammen,
- keine Tool-ID mehr als CLI-Kommando synthetisiert werden muss,
- eindeutige read-only Fachanfragen technisch zum erforderlichen aktuellen
  Evidenzpfad fuehren,
- der Antwort-Guard unbelegte Negativ-, Erfolgs-, Versions- und Schreibclaims
  fail-closed verhindert,
- kein automatischer Router eine externe Schreibwirkung oder Rechteerweiterung
  ausloesen kann,
- bestehende Approval-, ETag-, ActionPlan-, Audit-, ClamAV-, TLS-, Secret- und
  Single-Writer-Grenzen unveraendert bestehen,
- die real beobachteten Fehlertypen als Verhaltensregressionen abgedeckt sind,
- Toolrouting und Guard datenschutzsicher messbar sind,
- Wheel und signierte Rollenimages reproduzierbar gebaut und hermetisch getestet
  wurden,
- produktiver Canary, Aktivierung und Rollback weiterhin getrennte explizite
  Betriebsaktionen sind.

## Unabhaengiger Abschluss-Audit-Prompt

```text
Pruefe die Implementierung von M13 aus
docs/AGENT_TOOL_ORCHESTRATION_ROADMAP.md unabhaengig und kritisch. Lies AGENTS.md
und alle Personal-Assistant-Referenzen vollstaendig. Veraendere keine Dateien
unter /srv/openclaw, keine produktiven Jobs und keine externen Daten. Belege, dass
native Agentenwerkzeuge tatsaechlich in OpenClaw registriert sind und nicht nur in
Markdown oder einem internen Katalog stehen. Pruefe Generator- und Schemadrift,
argv ohne Shell, CA-/Env-/Rollenparitaet, alle read-only Domaenen, gebundene
Write-Approvals, ActionPlan/ETag/Audit, turngebundene Evidenz und den
Antwort-Guard. Spiele das vollstaendige M13.0-Fehlerkorpus ab und ergaenze echte
Regressionstests fuer jeden gefundenen Fehler. Eine unbelegte Negativ-, Erfolgs-,
Versions- oder Schreibbehauptung muss fail-closed blockiert werden. Fuehre den
vollstaendigen Repository-, Wheel-, Image-, SBOM-, Provenance-, Scan-, Signatur-
und hermetischen End-to-End-Pfad aus. Wenn Docker, OpenClaw-Hooks oder ein
produktiver read-only Canary nicht belegbar sind, markiere die konkrete Abnahme
offen und erfinde keinen Erfolg. Berichte Findings nach Schweregrad, Korrekturen,
Tests, Toolrouting-Messwerte, Latenz, Artefakte, Sicherheitsgrenzen und ein
eindeutiges Urteil M13 ABGENOMMEN oder M13 NICHT ABGENOMMEN. Beginne keinen
Folgemilestone und aktiviere nichts produktiv.
```
