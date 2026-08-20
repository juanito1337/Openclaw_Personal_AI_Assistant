# ADR-0033: M11-Entwicklungsabnahme bleibt vom produktiven Indexrollout getrennt

- Status: Accepted
- Datum: 2026-08-20
- Entscheider: Data Maintainers, Security Maintainers, Release Maintainers
- Bezug: M11.8, ADR-0026 bis ADR-0032

## Kontext

Die einzelnen M11-Pakete besitzen Verhaltensregressionen, konnten aber nur als
zusammenhaengender Containerfluss beweisen, dass Fake-IMAP, Antivirus,
Projektionspublisher, Reconciliation, Wissens-Sync, FTS, Embeddings und
Gatewayrouting dieselbe Zustands- und Fehlergrenze einhalten. Eine gruene
Entwicklungsabnahme darf zugleich nicht die weiterhin fehlende autoritative
Identitaetssemantik des produktiven Himalaya-Connectors oder ein nicht
gemessenes reales Embeddingmodell verdecken.

## Entscheidung

M11.8 fuehrt einen eigenen hermetischen Compose-Stack ohne Hostports, Secrets,
Produktivmounts oder externe Accounts ein. Er verwendet echte M11-Produktmodule
und synthetische Protokolldienste. CI baut zuvor das Runtimeimage und prueft dann
Backfill, Coverage, Incremental, Locatorwiederverwendung, FTS, semantische
Degradation, Netzverlust und Crash/Restart. Wheel und alle Rollenimages bleiben
unter dem vorhandenen Artefakt-, SBOM-, Secret- und Provenancevertrag.

Die M11-Entwicklungsabnahme akzeptiert den sicheren Zustand mit deaktivierter
Semantik. Synthetische Vektoren pruefen nur Vertrag und Messpipeline und sind nie
aktivierungsfaehig. Produktiver Backfill, Indexjob, Connectorwechsel,
Modellaktivierung, Main-Promotion, Tag und Installation bleiben getrennte
explizite Entscheidungen. Ein produktiver Rollout ist vor belegter UID,
UIDVALIDITY und stabiler Ordneridentitaet blockiert.

Historische Contentidentitaeten duerfen nach einem autoritativen Delete fuer
Audit/Wiederverwendung erhalten bleiben. Locatorvollstaendigkeit wird nur gegen
aktive Suchdokumente und nicht tombstonte Occurrences bewertet; sonst wuerde
ein korrekt geloeschter Treffer jede spaetere lokale Suche dauerhaft sperren.

## Konsequenzen

- Der Entwicklungsstand ist reproduzierbar abnehmbar, ohne das produktive
  Postfach zu lesen oder zu veraendern.
- Jeder produktive Rollout beginnt mit Kapazitaetsplan und verifiziertem Backup,
  verwendet Canary und Shadowvergleich und benoetigt eigene Freigaben fuer
  Vollbackfill, Automatikpfad, Modell und inkrementellen Job.
- Bei Verschlechterung bleibt Serversuche der sichere Fallback. Lokaler Rollback
  behauptet nie, externe Mails wiederherzustellen.
- Ein deaktivierter semantischer Provider ist ein ehrlicher, unterstuetzter
  Zustand und kein Grund, Modellwerte oder Aktivierung zu erfinden.

## Verifikation

`tests/test_mail_search_acceptance_m118.py` aggregiert die echten synthetischen
M11-Benchmarks und prueft Datenschutz, Aktivierungsgrenze und Artefaktguard.
`tests/test_mail_hybrid_search_m117.py` reproduziert den Delete-/Locatorfehler.
`scripts/check-m11-integration.sh` prueft den Containerfluss samt Netzwerk- und
SIGKILL-Injektion. Der separate Betriebsablauf steht in
`docs/MAIL_SEARCH_M11_ACCEPTANCE_AND_ROLLOUT.md`.
