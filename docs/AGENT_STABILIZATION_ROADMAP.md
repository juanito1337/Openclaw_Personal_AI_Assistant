# M16-Roadmap: Betriebsstabilitaet, Leistungsfaehigkeit und Releasekonsolidierung

Stand: 2026-09-20
Vorgesehener Arbeitsbranch: `development/stabilization-performance-m16`
Status: in Umsetzung; M16.0 bis M16.6 abgeschlossen, keine produktive Aktivierung

## Ausgangslage

Der Agent besitzt nach M0 bis M15 eine breite, sicherheitsorientierte
Funktionsbasis. Der produktive Stack laeuft mit getrennten Rollen, einem
autoritativen Mailindex, nativen Agentenwerkzeugen, fail-closed Antivirus,
belegten Schreibworkflows und signierten Commitimages. Die aktuelle Analyse hat
jedoch gezeigt, dass technische Gesundheit, Betriebsmetriken, fachliche
Datenqualitaet und Releasezustand noch nicht durchgehend dasselbe Bild liefern.

M16 ist deshalb kein neuer Funktionsmilestone. M16 konsolidiert den vorhandenen
Stand, macht seine Leistung reproduzierbar messbar und beseitigt die konkreten
Restprobleme, bevor der getestete Entwicklungsstand als neuer Main- und
Releasezustand gilt.

## Gemessene Ausgangsprobleme

Die folgenden Werte sind eine datenschutzarme Momentaufnahme des analysierten
Commits. Sie sind noch keine willkuerlichen Qualitaetsgrenzen. M16.0 muss sie auf
dem exakten Arbeitscommit mit reproduzierbaren Befehlen bestaetigen oder sichtbar
korrigieren.

### Release- und Git-Zustand

- Der produktive Commit entspricht dem getesteten M15-Stand, laeuft aber noch
  unter der Releaseidentitaet `3.4.0-r28`.
- Die Releasebeschreibung deckt im Wesentlichen M0 bis M10 ab; M11 bis M15
  stehen weiterhin als unreleased beziehungsweise nur entwicklungsseitig
  dokumentiert.
- Der produktive Stand liegt auf einem Testbranch. Remote- und lokaler
  `main`-Branch liegen deutlich hinter diesem Stand.
- `installed_at` und `installation_id` sind im Image erwartungsgemaess leer,
  werden aber noch nicht durch einen klaren Installationsnachweis ergaenzt.

### Betriebs- und Leistungswahrheit

- Die aktuell laufenden Rollen und der tiefe Jobstatus sind gesund; zugleich
  zeigt die historische Scheduler-Sicht fuer sieben Tage viele `degraded`- und
  `interrupted`-Eintraege.
- In derselben Stichprobe standen 1.033 Laeufen nur 421 als `completed`
  klassifizierte Laeufe gegenueber. Die durchschnittliche Wartezeit lag bei
  rund 95 Sekunden, das p95 bei rund 336 Sekunden.
- Der Sync-Job kann ungefaehr elf Minuten CPU- und I/O-intensiv laufen. Bei
  globaler Scheduler-Parallelitaet eins blockiert er dadurch zeitkritischere
  Mailarbeit mehrere Minuten.
- Mail-Performance-Telemetrie meldet Fehler oder Unterbrechungen, obwohl
  aktuelle Mail- und Indexpfade funktionieren. Verschachtelte Messungen,
  Zwischenzustaende oder Checkpoint-Kollisionen sind als Ursache zu pruefen.
- Nextcloud ist live erreichbar und alle Sync-Scopes sind gesund; der Monitor
  wertet die Komponente dennoch wegen alter Aktualisierungszeitpunkte als
  eingeschraenkt. No-op-Synchronisation und Datenfrische werden noch nicht
  ausreichend getrennt.
- Der Supervisor-Container ist aktuell gesund, traegt aber ein historisches
  `OOMKilled=true`. Ursache, Zeitpunkt und betroffener Prozess sind nicht
  ausreichend belegt.
- Das lokale Modellgateway ist stabil und hat keine relevante Queuezeit. Die
  eigentlichen Modellantworten benoetigen in der Stichprobe im Mittel rund 69
  Sekunden und in Einzelfaellen ueber 100 Sekunden.

### Konfiguration und Fachsysteme

- Direkte Kalender-, Aufgaben- und Kontaktwerkzeuge sind gesund. Der alte
  Kalenderpfad im Mail-Doctor referenzierte jedoch eine nicht mehr vorhandene
  Ressourcen-ID und widersprach damit der aktiven Kalenderkonfiguration. Der
  vor M16 ergaenzte exakte read-only Recovery-Pfad verhindert den operativen
  Fehler; die dauerhaft gespeicherte Konfigurationsdrift und vergleichbare
  Ressourcenabweichungen bleiben Gegenstand von M16.4.
- Der Mailindex ist lexikalisch vollstaendig und autoritativ. Semantische
  Embeddings sind bewusst deaktiviert; inhaltlich aehnliche Suchen jenseits von
  Volltext, Tags und Threadkontext sind daher noch keine zugesicherte
  Faehigkeit.
- Die Mailklassifikation arbeitet fuer aktuelle Nachrichten mit hoher
  Konfidenz, besitzt aber zu wenig belastbare historische Originalentscheidungen
  und viele schwach gestuetzte Einzelmuster. Es bestehen weiterhin relevante,
  nicht weitergeleitete Reviewfaelle.
- Der Rechnungsbestand enthaelt einen grossen Altbestand im Reviewstatus,
  haeufig fehlende Rechnungsnummern oder Betraege sowie Register-/Jahrespfad-
  Abweichungen. Eine blinde Massenkorrektur ist nicht zulaessig.
- Das Portfolio ist bewusst ausgeschaltet beziehungsweise fachlich
  eingeschraenkt. Zwei Instrumente besitzen keine bestaetigte Zuordnung;
  kostenpflichtige Research-Endpunkte antworten mit Entitlement-Fehlern und das
  deklarierte Anlageprofil ist nicht konfiguriert.

### Code-, Test- und Architekturzustand

- Die Testcollection umfasst 1.046 pytest-Items; der letzte vollstaendige
  Bericht enthaelt 1.157 Faelle inklusive Subtests ohne Fehler.
- Die kombinierte Coverage liegt bei rund 68,5 Prozent. Sicherheitskritische
  Orchestrierungs- und Bridgepfade besitzen deutlich geringere direkte
  Abdeckung; der Container-Jobloop wird vor allem durch Integrationstests
  abgedeckt.
- Ruff und mypy sind durch exakte, nicht wachsende Altlast-Baselines gruen,
  enthalten aber noch mehrere hundert bekannte Ruff-Befunde und ueber hundert
  bekannte Typfehler.
- Mehrere zentrale Module und Funktionen sind sehr gross. Das erschwert
  gezielte Tests, Review, Fehlerisolation und spaetere Erweiterungen.
- Die Multi-Container- und Single-Writer-Architektur ist fuer einen lokalen
  Einzelknoten robust. SQLite, lokales Dateisystem und ein globaler Scheduler
  setzen jedoch eine bewusste Grenze gegen horizontale Skalierung.
- Der LLM-nahe Gatewayprozess besitzt fuer die heutige Funktionsbreite viele
  Schreibmounts und Credentials. Die vorhandenen nativen Tool-, Approval- und
  Policygrenzen reduzieren das Risiko, aber der technische Blast Radius bleibt
  groesser als bei einem getrennten, minimal privilegierten Tool-Executor.

## Architektonisches Ziel

M16 schafft einen kohärenten, reproduzierbaren und freigabefaehigen
Betriebszustand:

```text
exakter Quellcommit
      |
      v
reproduzierbare Baseline und ehrliche Telemetrie
      |
      v
inkrementelle Arbeit + priorisierter Scheduler
      |
      v
eine konsistente Ressourcen- und Laufzeitkonfiguration
      |
      v
belegte Ressourcen-, Speicher-, I/O- und Latenzgrenzen
      |
      v
gezielt getestete und verkleinerte Risikopfad-Altlasten
      |
      v
kontrolliert bearbeitete fachliche Restbestaende
      |
      v
separate Architekturentscheidungen fuer Semantic Search
und privilegiengetrennte Toolausfuehrung
      |
      v
vollstaendige Abnahme -> Release -> Main -> separates Deployment
```

Der Agent soll danach nicht nur viele Funktionen besitzen, sondern fuer jede
kritische Funktion belegen koennen:

1. welcher aktuelle Zustand gilt;
2. wann und mit welchem Aufwand er ermittelt wurde;
3. welche Quelle, Ressource und Berechtigung verwendet wurde;
4. ob das Ergebnis vollstaendig, aktuell oder eingeschraenkt ist;
5. welcher sichere Diagnose- oder Wiederaufnahmeweg bei einem Fehler gilt.

## Globale Sicherheits- und Arbeitsgrenzen

- Jeder Teilmilestone beginnt mit dem vollstaendigen Lesen von `AGENTS.md` und
  der betroffenen Personal-Assistant-Referenzen.
- Entwicklungsarbeit veraendert keine Dateien unter `/srv/openclaw`, keine
  produktiven Jobs, Container, Secrets, Ressourcenrechte oder externen Daten.
- Ein produktiver Read-only-Canary, ein Jobstart, eine Datenkorrektur, eine
  Main-Promotion und ein Deployment bleiben jeweils eigene ausdrueckliche
  Freigaben.
- Bestehende Benutzerveraenderungen im Worktree bleiben erhalten. Keine
  Force-Pushes, keine destruktiven Gitbefehle und kein Umschreiben publizierter
  Historie.
- Single-Writer, ActionPlan, Approval, Audit, ETag, Idempotenz, TLS und
  fail-closed Antivirus werden nicht abgeschwaecht.
- Testdaten und Benchmarks sind synthetisch. Mailinhalte, Adressen,
  Kalenderdaten, Portfoliobestaende, Rechnungen und Secrets duerfen nicht in
  Fixtures, Logs oder Git uebernommen werden.
- Eine Leistungsoptimierung darf keine Vollstaendigkeits-, Frische-,
  Sicherheits- oder Nachzustandsevidenz entfernen.
- Neue Grenzwerte werden aus M16.0 abgeleitet und begruendet. Bestehende
  Messwerte werden nicht nachtraeglich passend gemacht.
- Jeder Teilmilestone aktualisiert Tests, Dokumentation, Komponenteninventar
  und Quellmanifest mit den vorhandenen Generatoren. Er endet vor dem naechsten
  Paket.

## Paketuebersicht und Reihenfolge

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M16.0 | Reproduzierbare Gesamtbaseline und priorisiertes Risikoregister | M15-Stand |
| M16.1 | Verbindlicher Git-, Release- und Promotionsvertrag | M16.0 |
| M16.2 | Widerspruchsfreie Betriebs- und Leistungstelemetrie | M16.0 |
| M16.3 | Inkrementeller Sync und priorisierter, fairer Scheduler | M16.2 |
| M16.4 | Konsistente Ressourcen- und Konfigurationsidentitaet | M16.2 |
| M16.5 | Belegte Speicher-, I/O-, OOM- und Latenzgrenzen | M16.2, M16.3 |
| M16.6 | Risikobasierter Testausbau und modulare Codekonsolidierung | M16.2 bis M16.5 |
| M16.7 | Messbare Mail-Lern- und Reviewqualitaet | M16.6 |
| M16.8 | Sichere Rechnungs- und Portfolio-Restbestandsbehandlung | M16.6 |
| M16.9 | ADRs und Prototypgrenzen fuer Semantic Search und Tool-Executor | M16.0 bis M16.6 |
| M16.10 | Unabhaengige Gesamtabnahme, Release und Main-Promotion | M16.1 bis M16.9 |

M16.2 und M16.4 koennen nach der gemeinsamen Baseline technisch unabhaengig
entwickelt werden. M16.10 bleibt trotzdem gesperrt, bis alle Pakete abgenommen
sind oder eine bewusst verschobene Aufgabe mit begruendeter Folge-Roadmap
dokumentiert wurde.

## M16.0 – Reproduzierbare Gesamtbaseline und Risikoregister

Status: abgeschlossen am 2026-09-20. Die reproduzierbare Evidenz liegt in
`docs/architecture/m16-baseline.json`; fehlende Produktivmessungen sind bewusst
`not-measured` und kein impliziter Erfolgsnachweis.

### Ziel

Alle im Ausgangsbericht genannten Probleme auf einem exakten Commit mit
maschinenlesbarer Evidenz bestaetigen, ohne bereits Verhalten zu veraendern.

### Scope

- Quellcommit, Branch, Releaseidentitaet, Image-Revision und Manifestzustand
  getrennt erfassen.
- Testcollection, Subtests, Coverage, Ruff-, mypy-, Shell-, Dockerfile-, Compose-
  und Artefaktstatus reproduzieren.
- Containerzustand, Restarts, Health, Ressourcenlimits, Speicher, CPU, I/O,
  Swap, Startzeit und Imagegroesse datenschutzarm messen.
- Scheduler-Wartezeiten und Resultate nach Job, Laufart und Ursache ausweisen.
- Sync in Voll-, Delta- und No-op-Anteile zerlegen; Mail-Liste, -Suche und
  Indexabfrage mit Warm-/Cold-Cache und p50/p95 messen.
- Maillernen, Review, Rechnungsqualitaet, Portfolioverfuegbarkeit und
  Konfigurationsdrift als strukturierte Ausgangswerte erfassen.
- Ein Risikoregister mit Schweregrad, Nutzerwirkung, Sicherheitswirkung,
  Reproduzierbarkeit, Owner und Zielpaket erstellen.

### Pflichttests und Abnahme

- Jeder Baselinewert besitzt Befehl, Commit, Umgebung, Stichprobengroesse und
  Zeitstempel.
- Inhalts- und Geheimnisfelder werden vor Speicherung technisch verworfen.
- Fehlende Docker-, Provider- oder Produktivevidenz wird als `not-measured`
  ausgewiesen und niemals als Erfolg interpretiert.
- Mindestens drei Wiederholungen trennen einmalige Ausreisser von p50/p95.
- Ein schema- und regressionstestbarer Baselinebericht liegt im Repository.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.0 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Lies AGENTS.md und alle relevanten
Personal-Assistant-Referenzen vollstaendig. Veraendere keine Dateien unter
/srv/openclaw, keine produktiven Jobs und keine externen Daten. Erzeuge einen
datenschutzarmen, maschinenlesbaren Baseline-Harness fuer Commit-, Release-,
Test-, Coverage-, Lint-, Image-, Container-, Scheduler-, Sync-, Mail-,
Nextcloud-, Rechnungs- und Portfoliozustand. Trenne Full-, Delta- und No-op-
Arbeit sowie Warm-/Cold-Cache. Erfasse Befehle, Werkzeugversionen,
Stichprobengroesse, p50/p95 und nicht messbare Werte explizit. Erstelle ein
priorisiertes Risikoregister mit Owner und Zielpaket. Aendere noch kein
Produktverhalten und beginne nicht mit M16.1.
```

## M16.1 – Git-, Release- und Promotionsvertrag

Status: abgeschlossen am 2026-09-20. Der Kandidat bleibt bis M16.10 ein
technisch blockierter Draft; es erfolgten weder Main-Promotion noch Tag,
Imageveroeffentlichung oder Deployment.

### Ziel

Eine einzige nachvollziehbare Kette von getestetem Commit ueber signiertes
Image und Releaseidentitaet bis zum Main-Branch definieren, ohne den aktuellen
Stand vorzeitig zu promoten.

### Scope

- Abweichungen zwischen lokalem Main, Remote-Main, Entwicklungs-/Testbranch,
  produktivem Commit, Releasemanifest und Image-Labels maschinenlesbar machen.
- Den naechsten Releasebezeichner, Changelogumfang M11 bis M16,
  Dokumentationsstatus, Tagging und Rollbackanker festlegen.
- Fast-forward-, Merge- und signierte-Tag-Strategie dokumentieren; Force-Push
  und nachtraegliches Aendern getesteter Commits ausschliessen.
- CI muss Quellcommit, Buildrevision, Image-Digest, Attestierungen und
  Releasemanifest als identische Promotionseinheit pruefen.
- `installed_at` und Installations-ID als Installerevidenz behandeln, nicht in
  ein unveraenderliches Buildmanifest vortaeuschen.
- Main-Promotion, Imageveroeffentlichung und produktives Deployment als drei
  getrennte, freizugebende Aktionen beschreiben.

### Pflichttests und Abnahme

- Drift zwischen Git-Commit, `RELEASE.json`, OCI-Revision und
  Deploymentevidenz schlaegt fehl.
- Ein alter oder fremder Main-Commit kann nicht still als aktuelles Release
  bezeichnet werden.
- Changelog und Releasebericht decken alle tatsaechlich enthaltenen Milestones
  ab.
- Rollbackziel und dessen signierter Digest sind vor Promotion eindeutig.
- In M16.1 findet noch keine Main-Promotion und kein Deployment statt.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.1 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Nutze die M16.0-Baseline und veraendere
keine Produktivsysteme. Definiere und teste den Git-/Releasevertrag fuer den
exakten Quellcommit, RELEASE.json, Changelog, Releasebericht, OCI-Revision,
Image-Digest, Attestierungen, Tag und Main-Promotion. Trenne Buildidentitaet
von installererzeugtem installed_at und installation_id. Dokumentiere eine
Fast-forward-/Merge-Strategie ohne Force-Push sowie ein eindeutiges
Rollbackziel. Implementiere Drift- und Fremdcommit-Negativtests. Promoviere
noch keinen Branch, publiziere kein Release, deploye nichts und beginne nicht
mit M16.2.
```

## M16.2 – Widerspruchsfreie Betriebs- und Leistungstelemetrie

### Ziel

Status, Doctor, Monitor, Scheduler und Performanceberichte muessen denselben
realen Lauf gleich bewerten und technische Zwischenzustaende von echten
fachlichen Fehlern unterscheiden.

### Scope

- Eine eindeutige Run-, Attempt-, Parent-/Child- und Job-Identitaet fuer
  Scheduler-, Worker- und Fachtelemetrie verwenden.
- `completed`, `degraded`, `interrupted`, `skipped-not-due`, `in-progress`,
  `blocked` und `failed` mit geschlossenen Kriterien definieren.
- Verschachtelte Mail-/Drain-/Indexlaeufe nicht doppelt als Fehler zaehlen.
- Laufende Checkpoints nicht als historischen Fehler und alte Checkpoints nicht
  als aktuelle Aktivitaet interpretieren.
- Nextcloud-Datenfrische, letzte erfolgreiche Pruefung und letzter fachlicher
  Datenwechsel getrennt messen. Ein erfolgreicher No-op bleibt gesund.
- Aktive, neue und geloeste Alerts mit stabiler Ursache und Ablaufzeit
  verwalten; ein aktuell gesunder Zustand darf keine erledigte Altursache als
  gegenwaertigen Fehler melden.
- Metriken bleiben inhaltsarm und enthalten keine Nutzerdaten oder Secrets.

### Pflichttests und Abnahme

- Identische synthetische Laeufe ergeben in Status, Doctor, Monitor und
  Performance dieselbe Klassifikation.
- Parent-/Child-Laeufe, Retry, Abbruch, Crash, No-op und Wiederaufnahme sind
  echte Verhaltenstests, keine Textsuche.
- Ein gesunder Nextcloud-No-op ist frisch und gesund, ohne einen erfundenen
  Datenwechsel einzutragen.
- Ein echter Teilfehler bleibt sichtbar und kann nicht durch einen spaeteren
  oberflaechlichen Healthcheck verdeckt werden.
- Alte Alertzustände laufen kontrolliert aus oder werden belegt aufgeloest.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.2 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Lies AGENTS.md sowie
runtime-security.md und die betroffenen Domaenenreferenzen vollstaendig.
Vereinheitliche Run-, Attempt-, Parent-/Child- und Job-Identitaet und definiere
geschlossene Resultatklassen fuer completed, degraded, interrupted,
skipped-not-due, in-progress, blocked und failed. Korrigiere doppelte oder
verschachtelte Mail-/Drain-/Indexmessungen, Checkpoint-Lebensdauer und
Nextcloud-No-op-Frische. Status, Doctor, Monitor, Scheduler und Performance
muessen denselben Lauf gleich bewerten. Ergaenze synthetische Verhaltenstests
fuer Erfolg, No-op, Retry, Teilfehler, Crash, Wiederaufnahme und Alertablauf.
Telemetrie darf keine Inhalte oder Secrets enthalten. Beginne nicht mit M16.3.
```

## M16.3 – Inkrementeller Sync und priorisierter Scheduler

### Ziel

Unveraenderte Daten duerfen keine teure Vollverarbeitung ausloesen, und lange
Hintergrundarbeit darf interaktive beziehungsweise zeitkritische Mailarbeit
nicht minutenlang blockieren.

### Scope

- Sync in Discovery, Remote-Metadatenvergleich, Download, Parsing,
  Indexaktualisierung und Commit zerlegen und je Stufe messen.
- Stabile ETags, Modifikationszeiten, Digests und dokumentierte Provider-
  Cursor nutzen, um unveraenderte Objekte vor Download und Parsing zu stoppen.
- Entfernen, Verschieben, Umbenennen und Provider-Cursor-Verlust weiterhin
  korrekt und fail-closed behandeln.
- Schedulerklassen fuer interaktiv, zeitkritisch, normal, maintenance und
  background definieren; bestehende Prioritaeten wiederverwenden, nicht eine
  zweite Queue schaffen.
- Begrenzte Parallelitaet nur zwischen unabhaengigen Read-Phasen zulassen.
  Single-Writer-, SQLite- und externe Schreibgrenzen bleiben seriell.
- Leases, Backpressure, Fairness, Alterungsprioritaet und nicht-praemptive
  gesunde In-flight-Arbeit beibehalten.
- Sync-Arbeit in begrenzte, resumierbare Batches schneiden, damit der
  Scheduler zwischen Batches hoehere Prioritaeten bedienen kann.

### Pflichttests und Abnahme

- Ein No-op-Sync verarbeitet keinen unveraenderten Payload erneut und schreibt
  keine unveraenderte Wissensprojektion neu.
- Eine Einzelveraenderung bearbeitet nur das betroffene Objekt und notwendige
  Ableitungen; Delete und Move bleiben korrekt.
- Zeitkritische Mailarbeit kann zwischen Sync-Batches anlaufen, ohne einen
  aktiven sicheren Write zu unterbrechen.
- Fairness verhindert dauerhaftes Verhungern von Backgroundjobs.
- Crash, Leaseverlust, Cursorreset, Providerfehler und Wiederaufnahme erzeugen
  weder Doppelwrites noch verlorene Aenderungen.
- M16.0-Baselines fuer Sync-Walltime, CPU, I/O und Scheduler-p95 verschlechtern
  sich nicht; die erzielte Verbesserung wird gemessen und dokumentiert.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.3 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Nutze die korrigierte M16.2-Telemetrie.
Instrumentiere den Sync nach Discovery, Metadatenvergleich, Download, Parsing,
Index und Commit. Implementiere einen fail-closed inkrementellen Pfad auf
Basis belegter ETags, Zeiten, Digests oder Provider-Cursor und zerlege lange
Arbeit in resumierbare Batches. Integriere interaktive, zeitkritische, normale,
Maintenance- und Backgroundprioritaeten in den bestehenden Scheduler. Erlaube
Parallelitaet nur fuer unabhaengige Read-Phasen; Single-Writer, SQLite-Leases
und externe Writes bleiben seriell. Teste No-op, Einzelupdate, Delete, Move,
Cursorreset, Crash, Leaseverlust, Fairness, Backpressure und Wiederaufnahme.
Vergleiche p50/p95, CPU und I/O mit M16.0. Starte keine produktiven Jobs und
beginne nicht mit M16.4.
```

## M16.4 – Konsistente Ressourcen- und Konfigurationsidentitaet

### Ziel

Jede Domaene referenziert dieselbe aktive, eindeutig aufgeloeste Ressource;
stale IDs und parallele Altpfade koennen keinen falschen Fehler oder Write auf
das falsche Ziel erzeugen.

### Scope

- Ressourcenreferenzen fuer Kalender, Aufgaben, Kontakte, Nextcloud-Dateien,
  Mailordner, Portfolio und Rechnungen zentral inventarisieren.
- Den bereits fail-closed abgefangenen veralteten Mail-Kalenderpfad nach
  expliziter Konfigurationsfreigabe dauerhaft gegen die aktive registrierte
  Ressource migrieren oder nachweislich entfernen.
- Stabile Ressourcen-ID, fachlicher Name, Komponentenart, Rechte und aktueller
  Remote-Identifier getrennt behandeln.
- Discovery darf nie den ersten oder fuzzy Treffer automatisch waehlen.
  Mehrdeutigkeit bleibt ein typisierter Blocker.
- Status-/Doctorpfade muessen sowohl Konfigurationsdrift als auch fehlende
  Credentials, Rechte oder Remote-Komponenten exakt unterscheiden.
- Migrationen sind Vorschau, Backup, Validierung, atomare Publikation und
  Rollback; sie duerfen keine Ressource neu freigeben.

### Pflichttests und Abnahme

- Direkter Kalenderstatus und Mail-Kalenderstatus referenzieren dieselbe
  konfigurierte Ressourcen-ID und denselben Rechtestand.
- Alte, fehlende, doppelte und mehrdeutige IDs schlagen mit unterscheidbaren
  Fehlercodes fehl.
- Eine Migration veraendert keine externe Ressource und erweitert keine
  Berechtigung.
- Aufgaben-, Kontakt-, Rechnungs- und Mailpfade zeigen keine Regression.
- Generierter Toolvertrag, Skillreferenzen, Status und Doctor bleiben driftfrei.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.4 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Lies groupware.md, mail.md,
records.md und tool-contract.md vollstaendig. Inventarisiere alle stabilen
Ressourcenreferenzen und beseitige insbesondere die Drift zwischen dem alten
Mail-Kalenderpfad und der aktiven Kalenderressource. Waehle niemals den ersten
oder einen fuzzy Discoverytreffer. Implementiere Vorschau, Backup,
Validierung, atomare Konfigurationsmigration und Rollback ohne externe Writes
oder Rechteerweiterung. Unterscheide stale ID, Mehrdeutigkeit, fehlende
Credentials, fehlende Komponente und fehlendes Recht. Ergaenze positive und
negative Verhaltenstests und halte Toolkatalog, Skill, Status und Doctor
generatorgestuetzt konsistent. Beginne nicht mit M16.5.
```

## M16.5 – Laufzeitkapazitaet, OOM-, I/O- und Latenzgrenzen

Status: abgeschlossen am 2026-09-20. Bestehende Rollenlimits blieben
unverändert; produktive Allrollen-Peaks bleiben bis zu einem gesondert
freigegebenen Operatorlauf ausdrücklich `not-measured`.

### Ziel

Ressourcenengpaesse und lange Antwortzeiten muessen einem konkreten Prozess,
Arbeitsschritt und Budget zugeordnet werden koennen; Health darf historische
und aktuelle OOM-Zustaende nicht vermischen.

### Scope

- Supervisor-`OOMKilled` mit Container-, Cgroup-, Kernel- und Childprozess-
  Evidenz reproduzierbar erklaeren.
- Pro Rolle Speicherlimit, Working Set, Peak, CPU, I/O, PIDs, Restarts,
  Startzeit und Healthlatenz erfassen.
- Host-Swap und Fremdlast von agenteneigenem Verbrauch unterscheiden.
- ClamAV-Daemon, Signaturupdate, Gateway, Workers und Toolcontainer getrennt
  budgetieren; Antivirus bleibt resident und fail-closed.
- Modelllaufzeit in Queue, Promptaufbereitung, Upstream-Inferenz,
  Toolschleifen und Antwortfinalisierung zerlegen.
- Begrenzte Context-/Toolergebnisprojektion, Caching und fruehe
  deterministische Routingentscheidungen optimieren, ohne Evidenz zu verlieren.
- Cold start, Warm start, Healthcheck und Shutdown/Leasefreigabe messen.

### Pflichttests und Abnahme

- OOM-, Child-OOM-, normaler Exit, Healthfehler und manueller Stop sind
  unterscheidbar und werden nicht automatisch gegenseitig abgeleitet.
- Jede Rolle bleibt unter ihrem dokumentierten Budget oder meldet einen
  begruendeten Blocker; Limits werden nicht blind angehoben.
- ClamAV-Frische, Scanneridentitaet und fail-closed Verhalten bleiben erhalten.
- Modell- und Toollatenzberichte summieren sich konsistent zur Turnlatenz.
- Optimierungen bestehen Worst-case-, grosse Ergebnis-, Timeout- und
  Concurrencytests ohne abgeschnittene Evidenz.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.5 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Nutze M16.0 bis M16.3 als Baseline.
Ergaenze pro Rollencontainer belegte Speicher-, Cgroup-, CPU-, I/O-, PID-,
Restart-, Start- und Healthmetriken. Klaere den historischen Supervisor-
OOMKilled-Zustand technisch und unterscheide Container-OOM, Child-OOM,
normalen Exit und Healthfehler. Trenne Host-Swap/Fremdlast vom Agenten.
Budgetiere ClamAV, Gateway, Worker und Tools, ohne Limits blind anzuheben oder
fail-closed Scans abzuschwaechen. Zerlege Modelllatenz in Queue,
Promptaufbereitung, Inferenz, Toolschleifen und Finalisierung und optimiere nur
verlustfrei. Teste Peaks, grosse Ergebnisse, Timeout, Cold/Warm start,
Shutdown und Concurrency. Beginne nicht mit M16.6.
```

## M16.6 – Risikobasierter Testausbau und Codekonsolidierung

Status: abgeschlossen am 2026-09-20. Die priorisierten Risikomodule besitzen
direkte Verhaltenstests und einen nicht regressiven Coveragevertrag; Jobprofile
und Runentscheidung sind ohne neue Architektur aus dem I/O-Loop extrahiert.

### Ziel

Die sicherheits- und betriebsrelevanten Pfade erhalten direkte
Verhaltenstests; grosse Module werden entlang bestehender Vertraege
verkleinert, ohne eine neue Architektur oder versteckte Kompatibilitaetsschicht
einzufuehren.

### Scope

- Testluecken in Actions, Assistant-Bridge, Jobloop, Mail-CLI,
  Nextcloud-Connectoren und Fehler-/Rollbackpfaden priorisieren.
- Unit-, Contract-, Integration- und hermetische E2E-Verantwortung pro Pfad
  explizit zuordnen; Integration ist kein Ersatz fuer fehlende
  deterministische Kernlogiktests.
- Sehr grosse Funktionen zuerst durch Charakterisierungstests absichern und
  danach entlang klarer Domaenen-/I/O-Grenzen extrahieren.
- Keine neue zyklische Abhaengigkeit und keinen Core-Import konkreter
  Infrastruktur zulassen.
- Ruff- und mypy-Altlasten paketweise reduzieren. Baselines duerfen niemals
  wachsen oder pauschal ignoriert werden.
- Collection-Untergrenze, Mutation/Gegenprobe kritischer Guards und
  Branch-Coverage fuer Sicherheitsentscheidungen ausbauen.

### Pflichttests und Abnahme

- Die bestehende Testcollection sinkt nicht unbemerkt; neue Tests sind echte
  Verhaltenstests.
- Kritische, bisher schwach abgedeckte Module verbessern Statement- und
  Branch-Coverage gegenueber M16.0, ohne Gesamtregression.
- Jede Modulteilung besitzt Import-, CLI-, Contract- und Rollbacktests.
- Ruff-/mypy-Baselines sind kleiner oder unveraendert, niemals groesser.
- Zyklus-, Schicht-, Toolvertrag-, Manifest-, Wheel- und Imagechecks bleiben
  gruen.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.6 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Verwende die M16.0-Risikorangfolge.
Ergaenze direkte Verhaltenstests fuer Actions, Assistant-Bridge, Jobloop,
Mail-CLI, Nextcloud-Connectoren sowie Fehler-, Idempotenz- und Rollbackpfade.
Sichere grosse Module und Funktionen zuerst mit Charakterisierungstests und
extrahiere danach nur entlang bestehender Domaenen- und I/O-Grenzen. Erzeuge
keine zyklischen Imports, keine zweite Toolliste und keine versteckte
Kompatibilitaetsschicht. Reduziere Ruff- und mypy-Altlasten eng begrenzt;
Baselines duerfen nicht wachsen. Miss Statement- und Branch-Coverage der
Risikopfade, Collection und Laufzeit. Fuehre den vollstaendigen lokalen
Qualitaetspfad aus und beginne nicht mit M16.7.
```

## M16.7 – Mail-Lern-, Review- und Antwortqualitaet

### Ziel

Mailklassifikation und Lernen werden anhand unveraenderlicher, repraesentativer
Originalentscheidungen bewertet; gute aktuelle Konfidenz darf schwache
Abdeckung oder historische Altlasten nicht verdecken.

### Scope

- Neue Entscheidungen mit unveraenderlichem Feature-/Regel-/Modell-Snapshot,
  Quellenart und spaeterem Feedback speichern, ohne Mailinhalt zu duplizieren.
- Legacy-Feedback ohne Originalsnapshot getrennt kennzeichnen und nie als
  gleichwertige Ground Truth ausgeben.
- Sender-, Pattern-, Regel-, Modell- und kombinierte Entscheidung separat nach
  Accuracy, Coverage, False Positive, False Negative und Abstention bewerten.
- Singleton-, Mischsender- und Konfliktmuster sichtbar machen und nur mit
  ausreichender Evidenz aktivieren.
- Reviewfaelle nach Wirkung priorisieren, insbesondere relevant-nicht-
  weitergeleitet und Spam-Forward-Risiko.
- Lernvorschlaege bleiben Vorschau beziehungsweise explizite Einzelkorrektur;
  keine selbsttaetige Regelaktivierung und keine historische Massenverschiebung.
- Sprachliche Agentenantworten muessen `complete`, `folder_errors`,
  `results_may_be_truncated` und Abstention korrekt wiedergeben.

### Pflichttests und Abnahme

- Zeitlich getrennte Train-/Eval-Splits verhindern Auswendiglernen desselben
  Threads oder Absenders.
- Fehlender Originalsnapshot wird nicht in die belastbare Metrik gemischt.
- Patternaktivierung verlangt dokumentierte Mindestbelege und Konfliktfreiheit;
  Schwellen werden aus Baseline/Evaluation begruendet.
- False-Negative- und Forward-Risiko-Faelle besitzen Regressionstests.
- Eine unvollstaendige Suche oder Klassifikation erlaubt keine negative
  Vollstaendigkeitsaussage.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.7 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Lies mail.md und den M9-/M11-/M12-
Qualitaetsvertrag vollstaendig. Fuehre unveraenderliche, inhaltsarme Snapshots
fuer neue Originalentscheidungen und Feedback ein und trenne Legacydaten ohne
Snapshot. Evaluiere Sender, Pattern, Regeln, Modell und Kombination mit
zeitlich sowie thread-/sendergetrennten Splits nach Accuracy, Coverage,
Fehlertyp und Abstention. Mache Singleton-, Mischsender- und Konfliktmuster
sichtbar und priorisiere Review nach Nutzerwirkung. Aendere keine produktiven
Mails oder Regeln, aktiviere nichts automatisch und fuehre keine
Massenverschiebung aus. Teste insbesondere relevant-nicht-weitergeleitet,
Spam-Forward-Risiko sowie unvollstaendige Such-/Antwortclaims. Beginne nicht
mit M16.8.
```

## M16.8 – Rechnungs- und Portfolio-Restbestaende

### Ziel

Fachliche Altbestaende werden ehrlich diagnostiziert und mit sicheren,
einzelobjektgebundenen Werkzeugen bearbeitbar gemacht; fehlende Providerdaten
oder Belegfelder werden nicht erfunden.

### Scope Rechnungen

- Reviewgruende, fehlende Rechnungsnummern, Datumsrollen, Betraege,
  Jahreszuordnung und Registerpfad je Beleg getrennt ausweisen.
- Legacy-Extractor und aktuelle Extraktionsversion vergleichbar evaluieren.
- Vorschau und sichere Einzel-Neuverarbeitung mit Originalhash,
  Scanneridentitaet, Extraktorversion und Feldprovenienz anbieten.
- Register-/Pfadabweichungen als kontrollierten Migrationsplan mit Backup,
  Konfliktpruefung und No-overwrite darstellen.
- Kein ungeprueftes Bulk-Apply; identische, beweisbare mechanische Faelle
  duerfen erst nach eigener Vorschau und separater Freigabe gebuendelt werden.

### Scope Portfolio

- `off`, `configured-limited`, `healthy`, `stale`, `mapping-required` und
  `provider-entitlement-denied` sauber unterscheiden.
- Unbestaetigte Instrumentzuordnungen als Vorschlag mit Providerbeleg und
  expliziter Freigabe behandeln; keine erfundenen Ticker oder Webersatzkurse.
- Kostenpflichtig gesperrte Researchendpunkte nicht automatisch wiederholen
  und nicht durch unbelegte Fremddaten ersetzen.
- Das deklarierte Anlageprofil als eigene, versionierte Nutzerkonfiguration
  behandeln. Beobachtungen duerfen es nicht still veraendern.
- Ein bewusst ausgeschalteter Portfoliojob darf den Gesamtstack nicht als
  defekt markieren.

### Pflichttests und Abnahme

- Rechnungs-Neuverarbeitung ist deterministisch, beleggebunden, idempotent und
  fail-closed bei Scanner-, OCR-, Nextcloud- oder Konfliktfehlern.
- Falsches Jahr, doppeltes Registerziel, anderer Hash und bestehende Datei
  werden sicher blockiert.
- Portfolio-Doctor berichtet Providerrecht, Mapping, Frische und Jobzustand
  getrennt.
- HTTP 401/402/403, Rate Limit, leere Antwort, stale Quote und fehlende
  Zuordnung besitzen eigene Tests und keine erfundene Fallbackantwort.
- Produktive Belege, Watchlists, Mappings, Kurse oder Jobs werden in M16.8 nicht
  veraendert.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.8 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Lies records.md und portfolio.md
vollstaendig. Ergaenze fuer Rechnungen einen beleggebundenen Audit nach
Reviewgrund, Pflichtfeld, Datumsrolle, Jahr, Registerpfad, Originalhash,
Scanner- und Extraktorversion sowie eine sichere Einzel-Neuverarbeitung und
Migrationsvorschau. Kein ungeprueftes Bulk-Apply und kein Overwrite. Trenne im
Portfolio off, configured-limited, healthy, stale, mapping-required und
provider-entitlement-denied. Mapping bleibt providerbelegt und explizit
freizugebend; erfinde keine Ticker, Kurse oder Researchdaten und kaufe keinen
Tarif. Das Nutzerprofil aendert sich nicht automatisch. Nutze nur synthetische
Fixtures, veraendere keine produktiven Daten oder Jobs und beginne nicht mit
M16.9.
```

## M16.9 – Architekturentscheidungen fuer Semantic Search und Tool-Executor

### Ziel

Zwei langfristige Erweiterungen werden anhand von Messung und Threat Model
entschieden, aber nicht nebenbei produktiv aktiviert.

### Teil A: Semantische Mail-Suche

- Reale anonymisierte Suchintentionen in ein synthetisches Evalset aus
  exakten, kontextuellen, synonymen und negativen Faellen ueberfuehren.
- Lexikalisch, Tags/Threadkontext, lokale Embeddings und Hybrid-Retrieval nach
  Recall, Precision, Abstention, Latenz, Speicher und Rechenlast vergleichen.
- Nur lokale, digest-gebundene Modelle ohne Inhaltsabfluss zulassen.
- Embeddings bleiben Ableitung untrusted Contents, autorisieren keine Aktion
  und ersetzen keine serverseitige Revalidierung.
- Eine Aktivierungsentscheidung benoetigt messbaren Mehrwert und einen
  separaten Canary-/Rebuild-/Rollbackplan. Andernfalls bleibt die Funktion
  dokumentiert deaktiviert.

### Teil B: Privilegiengetrennter Tool-Executor

- Datenfluss und Threat Model fuer LLM-nahes Gateway, nativen Toolrouter,
  Secrets, State-Mounts, Writerrollen und externe Ressourcen erstellen.
- Einen schmalen, typisierten RPC-Vertrag mit Tool-ID, Schema, Approval,
  Evidenz, Deadline, Idempotenz und Antwortgroessenlimit entwerfen.
- Gateway ohne direkte Fach-Secrets und RW-Domainmounts als Zielbild
  prototypisieren; der Executor erhaelt nur pro Rolle notwendige Rechte.
- Authentisierung, Replay-Schutz, Unix-Socket-/Netzgrenze, Crashverhalten,
  Backpressure und Audit nachweisen.
- Der Prototyp darf bestehende Tool- und Approvalgrenzen nicht umgehen und
  wird in M16 nicht produktiv aktiviert.

### Pflichttests und Abnahme

- Semantic-Eval meldet auch Nullmehrwert, Fehlklassifikation und Ressourcen-
  kosten; das Ergebnis ist kein vorgegebenes „aktivieren“.
- Embeddingindex kann vollstaendig neu aufgebaut und entfernt werden, ohne
  Quellmail oder lexikalischen Index zu veraendern.
- Der Gateway-Prototyp funktioniert ohne Domain-Secrets und lehnt unbekannte,
  geaenderte, wiederholte oder abgelaufene Requests ab.
- Executor-Ausfall erzeugt einen typisierten Blocker, keinen Shellfallback.
- Zwei ADRs halten Entscheidung, Alternativen, Risiken, Rollout und
  Rueckbaugrenze fest.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.9 aus
docs/AGENT_STABILIZATION_ROADMAP.md um. Erstelle zwei getrennte ADRs und nur
hermetische Prototypen. Evaluiere fuer Mail lexikalische, Thread-/Tag-, lokale
Embedding- und Hybrid-Suche mit synthetischem Korpus nach Recall, Precision,
Abstention, p50/p95, Speicher und Rechenlast. Ein lokales Modell ist
digest-gebunden; Embeddings autorisieren keine Aktion und ersetzen keine
Server-Revalidierung. Aktivierung ist nicht vorgegeben. Erstelle parallel ein
Threat Model und einen schmalen authentisierten RPC-Prototyp, bei dem das
LLM-nahe Gateway keine Fach-Secrets oder RW-Domainmounts benoetigt und der
Executor nur registrierte Tool-IDs/Schemata/Approvals akzeptiert. Teste Replay,
Fremdtool, geaenderte Argumente, Deadline, Crash, Backpressure und Audit. Keine
produktive Aktivierung, kein Secretumbau und kein Deployment. Beginne nicht
mit M16.10.
```

## M16.10 – Gesamtabnahme, Release und Main-Promotion

### Ziel

Den konsolidierten Stand unabhaengig pruefen, als vollstaendiges Release
publizieren und erst danach kontrolliert zum neuen Main machen. Produktives
Deployment bleibt eine weitere, ausdruecklich freizugebende Aktion.

### Scope

- Alle M16-Pakete gegen Roadmap, ADRs, Risikoregister und Baseline unabhaengig
  auditieren.
- Vollstaendigen lokalen und CI-Pfad fuer Tests, Coverage, Ruff, mypy,
  ShellCheck, Dockerfile, Compose, Manifest, Dokumentation, Wheel und
  Rollenimages ausfuehren.
- SBOM, Provenance, Secret-/CVE-Scan, Reproduzierbarkeit, Cosign-Signatur und
  Digestverifikation fuer den exakten Commit nachweisen.
- Hermetische End-to-End-Szenarien fuer Mailindex, Scheduler, Sync, ClamAV,
  Nextcloud, Action Completion, Rechnungen und Portfoliodiagnose ausfuehren.
- Releasemanifest, AGENTS, README, Changelog, Releasebericht, Skillreferenzen,
  Toolvertrag, Komponenteninventar und Quellmanifest konsistent aktualisieren.
- Beobachtungswerte gegen M16.0 vergleichen. Verbesserungen, unveraenderte
  Werte, Regressionen und nicht gemessene Werte getrennt berichten.
- Den freigegebenen, signierten Commit ohne Historienumschreibung nach `main`
  promoten und Tag/Release/Image aus genau diesem Commit erzeugen.
- Produktives Backup, Deploy, Read-only-Canary, Write-Canary und Jobaktivierung
  bleiben nach der Main-Promotion separate Betriebsschritte.

### Pflichttests und Abnahme

- Release-, Git-, OCI-, Attestierungs- und Dokumentidentitaet stimmen exakt
  ueberein.
- Testcollection, Gesamtcoverage und kritische Branch-Coverage sinken nicht
  unbemerkt; Ruff-/mypy-Altlasten wachsen nicht.
- Status, Doctor, Monitor und Performance widersprechen sich in den
  Abnahmeszenarien nicht.
- Scheduler-, Sync-, Ressourcen-, OOM-, Mail-, Rechnung- und
  Portfolioregressionen sind gruen.
- Wheel und Images enthalten keine Secrets, Konfigurationen, Datenbanken,
  Mail-/Kalender-/Rechnungsdaten, Logs oder Laufzeitstate.
- Kein produktiver Write, Jobstart, Rechtewechsel oder `/srv/openclaw`-Umbau
  wird als Teil der Entwicklungsabnahme ausgefuehrt.
- Das Urteil lautet eindeutig `M16 ABGENOMMEN` oder `M16 NICHT ABGENOMMEN`.

### Entwicklungsprompt

```text
Setze ausschliesslich M16.10 aus
docs/AGENT_STABILIZATION_ROADMAP.md um und pruefe M16.0 bis M16.9 unabhaengig
und kritisch. Lies AGENTS.md, alle betroffenen Referenzen, ADRs, Baselines und
Abnahmeberichte vollstaendig. Fuehre lokale und CI-Pruefung fuer Tests,
Coverage, Ruff, mypy, ShellCheck, Dockerfile, Compose, Manifest,
Dokumentation, Wheel, Rollenimages, SBOM, Provenance, Secret-/CVE-Scan,
Reproduzierbarkeit, Signatur und Digest aus. Teste Mailindex, Scheduler, Sync,
ClamAV, Nextcloud, Aktionsabschluss, Rechnungen und Portfoliodiagnose
hermetisch. Vergleiche alle Messwerte mit M16.0 und verschweige weder
Regressionen noch not-measured-Werte. Aktualisiere Releaseidentitaet,
Changelog, Releasebericht, AGENTS, README, Skills, Toolvertrag,
Komponenteninventar und Quellmanifest konsistent. Promoviere erst den exakt
getesteten und signierten Commit ohne Force-Push nach main. Fuehre kein
produktives Deployment, keinen Jobstart, keinen Rechtewechsel und keinen
externen Write aus. Berichte Findings, Korrekturen, Tests, Messwerte,
Artefakte, Restrisiken und das eindeutige Urteil M16 ABGENOMMEN oder M16 NICHT
ABGENOMMEN.
```

## Verbindliche Gesamt-Testmatrix

| Bereich | Positive Pruefung | Negative Pruefung |
| --- | --- | --- |
| Release | Commit, Manifest, OCI, Tag und Main identisch | alter/fremder Commit, unpassendes Release |
| Telemetrie | ein Lauf, ein konsistenter Zustand | Doppelzaehlung, stale Checkpoint, verdeckter Teilfehler |
| Sync | No-op und Einzelupdate inkrementell | Cursorverlust, Delete/Move, Providerfehler |
| Scheduler | priorisiert, fair, resumierbar | Verhungern, Leaseverlust, paralleler Writer |
| Ressourcen | exakte stabile ID und Rechte | stale, doppelte, fuzzy oder fehlende Ressource |
| Runtime | belegte Peaks und Exitursache | OOM/Child-OOM/Health falsch gleichgesetzt |
| ClamAV | resident, frisch, fail-closed | stale Signatur, Scannerfehler, Bypass |
| Modell | zerlegte Turnlatenz, begrenzter Kontext | Timeout, riesige Evidenz, Toolloop |
| Tests | Collection und Risikopfad-Coverage wachsen | kleinere Collection, Baselineerweiterung |
| Maillernen | belastbare Snapshots und Abstention | Legacyfeedback als Ground Truth, Konfliktmuster |
| Rechnungen | beleggebundene Einzel-Neuverarbeitung | Overwrite, anderer Hash, falsches Jahr |
| Portfolio | ehrlicher Limited-/Entitlementstatus | erfundener Ticker, Kurs oder Researchfallback |
| Semantic | messbarer Hybridvergleich | Inhaltsabfluss, Aktionsautorisierung durch Embedding |
| Executor | nur registrierte authentisierte Requests | Replay, Fremdtool, freie Shell, Secret im Gateway |
| Artefakte | Wheel/Image sauber und signiert | Secret, Runtime-State, fremder Digest |

## Gesamtdefinition „M16 abgeschlossen“

M16 ist erst abgeschlossen, wenn:

- Ausgangs- und Endzustand auf exakten Commits reproduzierbar gemessen sind;
- historische und aktuelle Betriebsmetriken denselben Zustandsvertrag nutzen;
- No-op- und Delta-Sync nachweislich keine unnoetige Vollverarbeitung
  ausloesen;
- zeitkritische Arbeit nicht mehr unkontrolliert hinter langen Backgroundjobs
  wartet und Single-Writer trotzdem erhalten bleibt;
- alle aktiven Ressourcenreferenzen konsistent, eindeutig und driftgeprueft
  sind;
- OOM-, Speicher-, CPU-, I/O-, Start- und Modelllatenzen technisch erklaert
  und budgetiert sind;
- kritische Orchestrierungs-, Bridge-, Job- und Connectorpfade direkte
  Verhaltenstests besitzen;
- Ruff-/mypy-Altlasten und grosse Module nicht weiter wachsen und messbar
  reduziert wurden;
- Maillernen, Review und negative Suchclaims nur belastbare Evidenz verwenden;
- Rechnungs- und Portfoliorestbestaende ehrlich diagnostiziert und sicher
  einzeln bearbeitbar sind;
- Semantic Search und Executor-Trennung durch getrennte ADRs entschieden, aber
  nicht still produktiv aktiviert wurden;
- Release-, Git-, Dokument-, Manifest-, OCI-, Attestierungs- und
  Signaturidentitaet exakt uebereinstimmen;
- der getestete Commit kontrolliert als neuer Main gilt;
- produktives Deployment und externe Writes weiterhin eine separate
  Freigabegrenze besitzen.

## Unabhaengiger Abschluss-Audit-Prompt

```text
Pruefe M16 aus docs/AGENT_STABILIZATION_ROADMAP.md unabhaengig und kritisch.
Lies AGENTS.md, alle betroffenen Personal-Assistant-Referenzen, ADRs,
Baselines und Abnahmeberichte vollstaendig. Veraendere keine Dateien unter
/srv/openclaw, keine produktiven Jobs, Rechte, Secrets oder externen Daten.
Verifiziere auf dem exakten Commit Release-/Git-/OCI-Identitaet, Telemetrie,
inkrementellen Sync, Schedulerprioritaet und Fairness, Ressourcenkonsistenz,
OOM-/Kapazitaetsevidenz, Modell- und Toollatenz, kritische Testabdeckung,
Ruff-/mypy-Altlasten, Maillernqualitaet, Rechnungsdiagnose,
Portfoliostatus sowie die getrennten Semantic-/Executor-ADRs. Fuehre den
vollstaendigen Test-, Coverage-, Lint-, Typ-, Manifest-, Dokument-, Wheel-,
Image-, SBOM-, Provenance-, Scan-, Reproduzierbarkeits-, Signatur- und
hermetischen E2E-Pfad aus. Vergleiche Endwerte mit M16.0; markiere jede
fehlende dynamische Abnahme als offen und erfinde keinen Erfolg. Eine
Main-Promotion ist nur fuer den exakt getesteten signierten Commit erlaubt;
Deployment, Jobstart und externe Writes bleiben getrennt. Berichte Findings
nach Schweregrad, Korrekturen, Tests, Messwerte, Artefakte, Restrisiken und das
eindeutige Urteil M16 ABGENOMMEN oder M16 NICHT ABGENOMMEN.
```
