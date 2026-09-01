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
