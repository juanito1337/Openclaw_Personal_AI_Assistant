# Roadmap

Diese Datei beschreibt die fachliche Produktentwicklung. Die priorisierte technische
Modernisierung des Docker-Stacks, einschliesslich eigenstaendiger Entwicklungs-Prompts
und Milestone-Tests, steht in
[`CONTAINER_ARCHITECTURE_ROADMAP.md`](CONTAINER_ARCHITECTURE_ROADMAP.md).
Der anschliessende Qualitaetsmilestone fuer nachvollziehbare Mail-Prueffaelle,
kontrollierte Einzelkorrekturen und messbares Lernen steht in
[`MAIL_QUALITY_REVIEW_ROADMAP.md`](MAIL_QUALITY_REVIEW_ROADMAP.md).

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
