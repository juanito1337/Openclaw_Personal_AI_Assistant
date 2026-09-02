# ADR 0036: Native Agentenwerkzeuge und turngebundener Evidenzguard

- Status: Accepted
- Datum: 2026-09-01
- Milestone: M13.1 bis M13.8

## Kontext

Die stabile Personal-Assistant-CLI und ihr typisierter Katalog waren vorhanden,
das Modell musste eine Katalog-ID aber noch in Shellsyntax übersetzen. Dadurch
konnten rohe Himalaya-Aufrufe, falsche CLI-Formen, Konfigurationssuche und
unbelegte Zustandsaussagen entstehen. Zusätzlicher Skilltext allein ist keine
technische Sicherheitsgrenze.

Der im Dockerfile gepinnte OpenClaw-Core 2026.7.1 wurde im unveränderten
Upstream-Image geprüft. Er bietet die benötigten echten Erweiterungspunkte:

- `api.registerTool` für strukturierte native Werkzeuge,
- `before_prompt_build` für begrenzten aktuellen Nutzerintent,
- `before_tool_call` mit Blockierung, Parameteranpassung und Plugin-Approval,
- `after_tool_call` für aktuelle Werkzeugevidenz,
- `before_agent_finalize` mit genau begrenztem Revisionsversuch,
- `reply_payload_sending` zum fail-closed Ersetzen einer unbelegten Ausgabe.

Nicht gebündelte Plugins müssen den Gesprächszugriff und die Promptinjektion in
`plugins.entries.<id>.hooks` ausdrücklich aktivieren. Diese Rechte erhält nur das
imageeigene Plugin; sie ändern keine Fachdaten- oder Connectorberechtigung.

## Entscheidung

Das unveränderliche Rollenimage enthält das Plugin `personal-assistant-tools`
unter `/opt/openclaw-plugins/personal-assistant-tools`. Ein deterministischer
Generator leitet aus dem existierenden Katalog 19 domänen-/effektbezogene native
Werkzeuge, 158 unterstützte Operationen und das Evidenzschema v1 ab. Die
Katalogoperation `mail.calendar-command` bleibt begründet ausgeschlossen, weil
ihr Vertrag kein eigenständiger CLI-Aufruf ist.

Das Modell übergibt nur `{operation, arguments}`. Die Bridge akzeptiert keine
freie Befehlszeile, startet immer den festen Launcher als `argv` mit
`shell=false`, validiert unbekannte Argumente und behandelt Metazeichen als
Daten. Der einzige historische stdin-Vertrag (`nextcloud.workspace.write-text`)
wird intern als stdin an den festen Prozess übergeben; es gibt keine Pipeline.
Für konfigurationsabhängige Operationen wird die aktuelle Live-Toolprojektion
geprüft.

Die 19 Gruppentools veröffentlichen den Operations-Discriminator und die
Vereinigung ihrer benannten Argumentfelder in einem flachen JSON-Schema. Das ist
mit lokalen Providern kompatibel, die verschachtelte `oneOf`-Zweige nicht
zuverlässig an das Modell vermitteln. Die Vereinigung erteilt keine zusätzliche
Berechtigung: Vor jeder argv-Kompilierung validiert die Bridge weiterhin gegen
das strengere Schema genau der ausgewählten Katalogoperation. Sichtbare
Pflichtfeldsignaturen und die Portfolio-Regel „bekannte ISIN =
`mapping.suggest` mit `arguments.isin`“ verhindern leere, wiederholte
Mappingaufrufe; Websuche bleibt kein Ersatz für den registrierten Providerpfad.

Ungueltige Argumente werden bereits in der Bridge erkannt und als strukturierte
`invalid-arguments`-Evidenz zurueckgegeben, ohne den CLI-Prozess zu starten. Der
erste Fehler darf genau einen korrigierten Folgeversuch anfordern. Ist auch
dieser Aufruf ungueltig, blockiert `before_tool_call` ihn vor der Ausfuehrung;
der defensive Ausfuehrungspfad lautet dann `retry_allowed=false` und
`fatal=true`. Ein syntaktisch ungueltiger Write wird bereits in
`before_tool_call` blockiert und erhaelt keinen Approval-Nonce.

Zusaetzlich normalisieren Migration und jeder Gatewaystart OpenClaws eigenen
`tools.loopDetection`-Vertrag: Warnung ab zwei Wiederholungen, kritische Sperre
ab drei erkannten identischen No-Progress-Ergebnissen und globaler Circuit
Breaker ab vier identischen Ergebnissen desselben Aufrufs. Alle drei gepinnten Detektoren
(`genericRepeat`, `knownPollNoProgress`, `pingPong`) bleiben eingeschaltet.
Unbekannte zukuenftige Konfigurationsfelder werden erhalten; ein typwidriger
Bestandswert ist ein Start-/Migrationsfehler und wird nicht ueberschrieben.

Der Router verarbeitet ausschließlich den aktuellen Nutzerprompt. Er darf
höchstens bekannte Leseoperationen verlangen und kann keinen Write, Jobstart,
Versand oder Permission-Setup auslösen. Mail- und Dokumentinhalt wird nie erneut
geroutet.

Jede Local-write-/Write-Operation benötigt eine OpenClaw-Einzelfreigabe mit den
Entscheidungen `allow-once` oder `deny`. Ein flüchtiger Nonce bindet Freigabe an
Turn, ToolCall, exakte Katalog-ID, kanonischen Argumentdigest und 180 Sekunden.
Er wird vor Ausführung einmalig verbraucht. Die bestehende CLI bleibt danach für
Policy, ActionPlan, UID/ETag, Idempotenz, Audit, ClamAV und Nachzustand
verantwortlich. Das Gateway-Protokoll erhält ausschließlich seine gültigen
Schweregrade `warning` für lokale Zustandsänderungen und `critical` für externe
Writes; die früher verwendeten, protokollfremden Werte `medium`/`high` sind
verboten.

Ein inhaltsarmes, flüchtiges Ledger speichert nur Evidenzfelder des aktuellen
Laufs. Der Antwortguard prüft Versions-, Zustands-, Negativ- und Schreiberfolgs-
Claims. Eine unvollständige Suche autorisiert keinen Nulltreffer; ein Write ohne
verifizierten Nachzustand keinen Erfolg. Bei fehlender Evidenz ist genau ein
Revisionsversuch erlaubt, danach wird die Antwort sicher ersetzt.

## Technisch erzwungen und weiterhin Modellverhalten

Technisch erzwungen sind Werkzeug-/Operationsenum, Argumentvalidierung,
argv-only-Ausführung, Live-Verfügbarkeit, Rohfach-Exec- und Secretpfadblock,
Einzelfreigabe, Replay-/Digest-/Ablaufprüfung sowie der letzte Antwortguard. Das
Modell formuliert weiterhin Suchbegriffe, wählt innerhalb der gerouteten
Leseoperationen und erklärt belegte Ergebnisse. Allgemeine Unterhaltung ohne
erkannten Fachintent bleibt unberührt.

## Fehler- und Rollbackverhalten

- Plugin-, Schema- oder Katalogdrift lässt Repositorycheck beziehungsweise
  Image-Smoke fehlschlagen.
- Fehlende Live-Fähigkeit, Timeout, Konfiguration oder unvollständige Ausgabe
  bleibt typisiert; die Bridge sucht keine Secrets und repariert nichts.
- Ungueltige Pflichtargumente starten keinen Fachprozess. Der zweite identische
  Versuch muss enden; OpenClaws zusaetzlicher Circuit Breaker begrenzt auch
  andere wiederholte beziehungsweise wechselnde No-Progress-Schleifen.
- Approval-Timeout, veränderte Argumente, Wiederverwendung oder fremder Turn
  blockiert vor dem CLI-Aufruf.
- Ein Hookfehler darf keine Berechtigung erzeugen. Der Antwortguard endet nach
  einem Versuch fail-closed.
- Rollback verwendet das vorherige verifizierte Rollenimage. M13 besitzt keine
  neue produktive Datenbankschemaversion und verändert beim Imagewechsel keine
  externen Daten; bereits ausgeführte Remoteaktionen werden wie bisher nicht
  durch einen Imagewechsel zurückgesetzt.

## Konsequenzen

Der Skill wird kürzer und verweist progressiv auf Fachreferenzen und generierten
Vertrag. Die Pluginfläche und der Gesprächshook sind zusätzlicher prüfpflichtiger
Supply-Chain-Code. Deshalb gehören Generatorcheck, tatsächliches
`openclaw plugins inspect`, hermetischer Hook-/Toollauf, Rollenimage-Scan, SBOM,
Provenance und Signatur in denselben Releasepfad.
