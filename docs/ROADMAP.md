# Roadmap

Diese Datei beschreibt die fachliche Produktentwicklung. Die priorisierte technische
Modernisierung des Docker-Stacks, einschliesslich eigenstaendiger Entwicklungs-Prompts
und Milestone-Tests, steht in
[`CONTAINER_ARCHITECTURE_ROADMAP.md`](CONTAINER_ARCHITECTURE_ROADMAP.md).
Der anschliessende Qualitaetsmilestone fuer nachvollziehbare Mail-Prueffaelle,
kontrollierte Einzelkorrekturen und messbares Lernen steht in
[`MAIL_QUALITY_REVIEW_ROADMAP.md`](MAIL_QUALITY_REVIEW_ROADMAP.md).
Der darauf aufbauende Rechnungsqualitaets-Milestone fuer belegte Feldextraktion,
Plausibilitaetspruefung und sichere Neubewertung steht in
[`INVOICE_QUALITY_REPROCESSING_ROADMAP.md`](INVOICE_QUALITY_REPROCESSING_ROADMAP.md).
Der geplante Folgemilestone fuer eine vollstaendige, schnelle und kontextuelle
Mail-Suche mit lokalem Volltextindex, belegten Tags, Threadkontext und evaluierten
lokalen Embeddings steht in
[`MAIL_SEARCH_INDEXING_ROADMAP.md`](MAIL_SEARCH_INDEXING_ROADMAP.md).
Der darauf aufbauende Milestone fuer einen autoritativen read-only
IMAP-Connector, inkrementelles Move-Tracking und einen kontrollierten
Produktivrollout steht in
[`MAIL_IMAP_RECONCILIATION_ROADMAP.md`](MAIL_IMAP_RECONCILIATION_ROADMAP.md).
Der geplante M13-Milestone fuer native strukturierte Agentenwerkzeuge,
deterministische Werkzeugpflicht, turngebundene Evidenz und belegte Antworten
steht in
[`AGENT_TOOL_ORCHESTRATION_ROADMAP.md`](AGENT_TOOL_ORCHESTRATION_ROADMAP.md).
Der darauf folgende M14-Milestone behebt den real gemessenen ClamAV-
Performanceengpass mit einem gehaerteten residenten Daemon und fuehrt den
autoritativen Mailindex anschliessend kontrolliert bis zur produktiven
Jobfreigabe. Die getrennten Entwicklungs- und Betriebsprompts stehen in
[`MAIL_ANTIVIRUS_INDEX_ROLLOUT_ROADMAP.md`](MAIL_ANTIVIRUS_INDEX_ROLLOUT_ROADMAP.md).
Der geplante M15-Milestone schliesst die nach M13 verbliebene Luecke zwischen
einem ausdruecklichen Schreibwunsch und dessen tatsaechlicher Ausfuehrung. Eine
turngebundene Aktionsverpflichtung, eine begrenzte Workflowzustandsmaschine und
ein technischer Completion-Guard verhindern, dass der Agent nach einer blossen
Toolankuendigung endet. Der erste sichere Vertikalschnitt fuehrt von einer
belegten Mail ueber eine read-only Terminvorschau bis zum separat freigegebenen
und remote verifizierten Kalendereintrag. Roadmap und Entwicklungsprompts stehen
in [`AGENT_ACTION_COMPLETION_ROADMAP.md`](AGENT_ACTION_COMPLETION_ROADMAP.md).
Der anschliessende M16-Milestone konsolidiert den gesamten Stand vor der
naechsten Main- und Release-Promotion. Er behebt widerspruechliche Telemetrie,
Scheduler- und Sync-Engpaesse, Ressourcen-Drift, ungeklaerte OOM- und
Latenzbefunde, gezielte Test- und Typaltlasten sowie die noch offenen Mail-,
Rechnungs- und Portfoliobestaende. Semantic Search und ein minimal
privilegierter Tool-Executor werden dabei getrennt gemessen und entschieden,
nicht still aktiviert. Roadmap und eigenstaendige Entwicklungsprompts stehen in
[`AGENT_STABILIZATION_ROADMAP.md`](AGENT_STABILIZATION_ROADMAP.md).

M0 bis M10 sind kumulativ in `3.4.0-r28` enthalten. Die Roadmaps bleiben als
Umsetzungs- und Testevidenz bestehen; die aktuelle Release-, Upgrade- und
Rollbackbeschreibung steht im
[`3.4.0-r28`-Releasebericht](RELEASE_3_4_0_R28.md). Eine Main-Promotion ist noch
keine produktive Installation oder Freigabe historischer Mail-/Rechnungsdaten.
M11.0 bis M11.8 und M12.0 bis M12.8 sind als Entwicklungsstand implementiert.
Messwerte und bekannte Ausgangsluecken stehen in
[`MAIL_SEARCH_BASELINE_M110.md`](MAIL_SEARCH_BASELINE_M110.md) und
[`MAIL_IMAP_RECONCILIATION_BASELINE.md`](MAIL_IMAP_RECONCILIATION_BASELINE.md).
Produktiver M12-Vollbackfill, Jobstart und Beobachtungsfenster bleiben getrennte
Betriebsaktionen. M13.0 bis M13.7 sind als Entwicklungsstand implementiert:
Kataloggenerierte native Agentenwerkzeuge, argv-only Bridge, read-only Router,
gebundene Einzelfreigaben und turnbezogener Antwortguard sind vorhanden. Die
lokale/hermetische M13.8-Abnahme steht unter
[`AGENT_TOOL_ORCHESTRATION_M13_ACCEPTANCE.md`](AGENT_TOOL_ORCHESTRATION_M13_ACCEPTANCE.md);
signiertes CI-Image und produktiver read-only Canary bleiben getrennt und sind
nicht durch den Entwicklungsstand aktiviert.
M14.0 bis M14.7 sind als Entwicklungsstand implementiert: Der private
Unix-Socket-Scanner, der fail-closed Index-Preflight und die gehärteten
ClamAV-Rollen sind lokal und hermetisch geprüft. Die Abgrenzung zum noch offenen
produktiven Deploy, Canary, Vollbackfill und Jobstart steht in
[`MAIL_ANTIVIRUS_INDEX_M14_ACCEPTANCE.md`](MAIL_ANTIVIRUS_INDEX_M14_ACCEPTANCE.md).
M15.0 bis M15.8 sind als Entwicklungsstand implementiert und lokal/hermetisch
geprueft: Das generierte Aktionsschema, der turngebundene Completion-Guard und
der quellengebundene Mail-zu-Kalender-Workflow sind vorhanden. Das signierte
Commitimage und jeder produktive Canary bleiben getrennt; Abnahme und
Rolloutgrenze stehen in
[`AGENT_ACTION_COMPLETION_M15_ACCEPTANCE.md`](AGENT_ACTION_COMPLETION_M15_ACCEPTANCE.md).

## 3.4.x foundation

- stabilize the Personal Assistant core and Nextcloud synchronization
- validate search quality and source citation
- add an explicit semantic-search provider only after selecting a local embedding model
- add authenticated Signal approval and query channel

## 3.5 multi-resource operations

- multiple mail accounts through connector resources
- additional Nextcloud instances, calendars, address books, and task lists
- controlled setup workflows without source-code changes
- project and case assignment

## 3.6 documents and finance

- invoice metadata extraction and review
- immutable original-document archive
- property, tax-year, vendor, and project tags
- task and deadline proposals with source references

## 4.0 property and personal operations

- properties, units, tenants, contracts, meters, insurance, loans, maintenance cases
- project timelines and source-grounded status summaries
- annual tax document preparation and completeness checks
