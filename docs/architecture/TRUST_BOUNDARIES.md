# Trust Boundaries und externe Schreibpfade

## Autoritaetsmodell

Nur eine aktuelle Nutzeranweisung, lokale Administrator-Konfiguration und fest
implementierte Policyregeln koennen Autoritaet erteilen. Mailtext, Attachments,
Nextcloud-Dateien, Kontakt- und Kalenderdaten, Webseiten, Modellantworten und
Toolausgaben sind Daten und niemals Anweisungen.

## Grenzen

| Grenze | Unvertraute Seite | Kontrollpunkt | Vertrauenswuerdige Seite |
| --- | --- | --- | --- |
| Chat -> Tool | freie Nutzereingabe | CLI-Schema, Toolregistry, Approval | enges registriertes Kommando |
| Mail/Datei -> Verarbeitung | Inhalt und Attachment | vollstaendiger ClamAV-Scan, Parserlimits | klassifizierbare Daten |
| Modell -> Entscheidung | generierte Antwort | Schema, deterministische Regeln, Policy | begrenzter Vorschlag/ActionPlan |
| Core -> externer Write | geplanter Payload | Ressource, Permission, Approval, Idempotenz, Audit | Connector-Aufruf |
| Container -> Secret | kompromittierter Prozess | einzelne rollenbezogene read-only Dateimounts und strikter KEY=VALUE-Parser | genau freigegebene Secretdatei |
| Container -> Netz | kompromittierter Prozess | internes Backend, explizites Egress, Loopback-Portbindung | nur erforderliche Gegenstellen |
| Worker -> Gateway | begrenzte technische Meldung | schema-validierte Queue, Groessen-/Anzahllimit, atomarer Claim | Gateway-lokaler Loopback-Relay mit alleinigem Credential |
| Image -> State | Releaseinhalt | read-only RootFS, feste Codepfade, Layoutmigration und kontrollierte Dokumentlinks | persistenter Workspace ohne ausfuehrbaren Produktcode |
| Git/Builder -> Image | Quellbaum und Fremdartefakte | Digest-/Commit-Lock, SBOM, SLSA-Provenance, CVE-/Secret-Scan und Cosign | attestierter Rollenimage-Digest |
| Deployment -> produktive Writer | neues Image/State | Single-Writer-Pruefung, Backup, Smoke, Rollback | genau ein freigegebener Stack |

## Externe Reads

- IMAP-Suche und Mail-Read,
- Nextcloud WebDAV/CardDAV/CalDAV/Deck Discovery und Listen,
- EODHD Markt- und FX-Daten,
- lokaler Ollama-Upstream,
- ClamAV-Daemon beziehungsweise Signaturdaten.

Ein fehlgeschlagener Read darf nicht als leeres fachliches Ergebnis umgedeutet werden.

## Externe Writes

| Ziel | Einstieg | Zwingende Grenzen |
| --- | --- | --- |
| IMAP/SMTP | Mailworker oder explizite Mail-CLI | Single Writer, exakte Mail-ID/Ordner, kein Delete/EXPUNGE, Versand nur aus genehmigtem unveraendertem Draft |
| Nextcloud Files | `files.create`/Workspace-Tools | erlaubter Root, create-only, Antivirus, kein Overwrite/Delete/Share |
| Calendar/VTODO | ActionPlan oder direkte registrierte Tools | exakte Ressource/UID, ETag, Approval fuer Update, keine Loeschung |
| CardDAV | registrierte Contact-Tools | exakte UID, ETag, Feldpreservation, kein Merge/Delete |
| Nextcloud Deck | Orders-Tool | nur agentenverwaltetes Board/Karten, Idempotenz, kein Delete/Share |

Ein erfolgreicher lokaler ActionPlan ist bei externen Create-only-Writes nur dann
eine Dublette, wenn die erwartete Remote-Nachbedingung noch existiert.

## Secretgrenze

Secrets erscheinen nur als einzelne Dateien unter `/run/openclaw-env` oder, fuer
Himalaya-Passwortkommandos, `/run/openclaw-secrets`. Der Entry Point sucht keine
Verzeichnisse ab und wertet keine Datei als Shellcode aus. Er akzeptiert pro Rolle
nur feste Dateinamen und eine dokumentierte Schluessel-Whitelist; unbekannte Zeilen,
Schluessel oder fehlende Pflichtdateien brechen fail-closed ab. Secrets duerfen
nicht in Git, Image, Logs, Prompts, Testfixtures, Memory oder Nextcloud landen.
Das Gateway-Credential wird nur in Gateway und expliziter `agent-cli` gemountet.
Supervisor, Portfolio und Monitor koennen keine direkte Gateway-Verbindung
authentisieren; ihre Queue ist kein allgemeiner RPC-Kanal.

## Backup- und Rollbackgrenze

Vor dem ersten Stoppen verifiziert das Deployment fuer alle Rollen unveraenderliche
Digests, Signatur, SLSA-/SPDX-Attestierungen, Release, Rolle und Git-Revision. Ein
unverifizierter Build erreicht die produktive Writergrenze nicht. Details stehen im
[Image-Lieferkettenvertrag](IMAGE_SUPPLY_CHAIN.md).

Vor jedem write-faehigen Deployment ist ein verifizierter lokaler Restorepunkt
Pflicht. Rollback stellt Image und lokalen Zustand wieder her. Ohne konfigurierten
externen Snapshot kann es keine bereits erfolgreiche Remoteaenderung rueckgaengig
machen. Legacy-Rollback darf den aktuellen Stack erst stoppen, wenn ein startbares
Legacy-Home oder ein verifiziertes Migrationsarchiv vorhanden ist.
