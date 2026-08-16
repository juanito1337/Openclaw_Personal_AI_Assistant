# Architecture Decision Records

ADRs dokumentieren dauerhafte Architekturentscheidungen und ihre Konsequenzen.
Nummern werden nie wiederverwendet. Eine ersetzte Entscheidung bleibt erhalten und
verweist mit Status `Superseded` auf den Nachfolger.

| ADR | Status | Entscheidung |
| --- | --- | --- |
| [0001](0001-modularer-monolith-multi-container.md) | Accepted | modularer Monolith im Multi-Container-Betrieb |
| [0002](0002-single-writer.md) | Accepted | genau ein produktiver Writer pro externer Schreibdomaene |
| [0003](0003-sqlite-datenowner.md) | Accepted | SQLite-Grenzen folgen fachlichen Datenownern |
| [0004](0004-unveraenderlicher-programmcode.md) | Accepted | Programmcode wird nur aus dem Image ausgefuehrt |
| [0005](0005-legacy-rollback-untergrenze.md) | Accepted | Legacy-Rollback nur aus verifizierter startbarer Quelle |
| [0006](0006-toolvertrag-source-of-truth.md) | Superseded | zentraler Registry-Builder als vorlaeufige Quelle des Toolinventars |
| [0007](0007-shared-sqlite-scheduler.md) | Accepted | der hostlokale Scheduler bleibt bei eng geteilter SQLite/WAL-Koordination |
| [0008](0008-container-netze-host-ollama.md) | Accepted | explizite Bridge-Netze; nur der Proxy erhaelt engen Hostzugang zu Ollama |
| [0009](0009-typisierte-domaenen-toolvertraege.md) | Accepted | typisierte domaenennahe Toolvertraege und neutrale Portgrenzen |
| [0010](0010-m6-legacy-und-upgradegrenze.md) | Accepted | Legacy-systemd isoliert; direkte Upgrades ab r26.1 |
| [0011](0011-reproduzierbare-rollenimages.md) | Accepted | drei minimale Rollenimages mit attestierter Freigabe |
| [0012](0012-m8-recovery-und-agentenvertrag.md) | Accepted | verifizierte lokale Recovery, Single-Writer-Canary und generierter Skillvertrag |
| [0013](0013-native-nextcloud-und-aktive-runtime-normalisierung.md) | Accepted | native Nextcloud-Bruecke und Normalisierung der aktiven Layout-3-Konfiguration |
| [0014](0014-abgeschlossenes-workspace-profil.md) | Accepted | abgeschlossenes Identitaetsprofil bleibt aktive Instanzkonfiguration |
| [0015](0015-geschuetzte-gateway-konfiguration.md) | Accepted | Gateway-Konfiguration read-only; Setup nur in kurzlebiger Adminrolle |
| [0016](0016-providergebundenes-portfolio-research.md) | Accepted | EODHD-belegtes Research, deterministische Scores und freigegebene Profilversionen |
| [0017](0017-mail-suchprojektion-statt-wal-leser.md) | Accepted | atomare Mail-Suchprojektion statt SQLite/WAL-Zugriff des Sync-Workers |
| [0018](0018-belegte-rechnungsnummern-und-datumsrollen.md) | Accepted | belegte Rechnungsnummern und getrennte Datumsrollen |
| [0019](0019-typisierte-rechnungsbetraege-und-plausibilitaet.md) | Accepted | typisierte Rechnungsbetraege und fail-closed Plausibilitaet |
| [0020](0020-begrenzte-lokale-ocr-feldfusion.md) | Accepted | begrenzte lokale Rechnungs-OCR und feldweise Konfliktbehandlung |
| [0021](0021-read-only-reprocessing-vorschau.md) | Accepted | gebundene read-only Rechnungs-Reprocessing-Vorschau |

Neue ADRs beginnen mit der [Vorlage](0000-template.md).
