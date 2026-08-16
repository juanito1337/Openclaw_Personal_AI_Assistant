# M10-Rolloutvertrag fuer Rechnungsqualitaet und Einzel-Reprocessing

Status: freigegebener Ablaufplan nach erfolgreicher Entwicklungsabnahme. Dieser
Plan wurde in M10.8 nicht produktiv ausgefuehrt. Er ist weder eine Freigabe fuer
ein Deployment noch fuer Backfill, Registerersatz oder Reprocessing.

Der allgemeine Release-, Single-Writer- und Recoveryvertrag unter
[`architecture/RECOVERY_AND_RELEASE.md`](architecture/RECOVERY_AND_RELEASE.md)
bleibt vorrangig. Die Rechnungsgrenzen stehen unter
[`INVOICE_OCR_REGISTER.md`](INVOICE_OCR_REGISTER.md).

## 1. Freigabevoraussetzungen

Vor einem spaeteren produktiven Auftrag muessen alle folgenden Nachweise fuer
denselben unveraenderlichen Git-Commit und dieselben drei OCI-Digests vorliegen:

- `version --verify`, `check-repo.sh`, Wheel-Pruefung, beide Compose-Renderings,
  Rollen-Smokes, Rootfs-Artefaktscan, SBOM, Provenance, Signaturen,
  Schwachstellen-/Secretscan und hermetische Containerintegration sind gruen.
- Runtime-, Proxy- und Maintenance-Digest tragen Release, Rolle und exakte
  Quellrevision. Ein Tag allein ist keine Deploymentreferenz.
- Aktiver Runtime-Typ und beobachtete Writer stimmen ueberein; alle Legacy-Writer
  sind inaktiv und ihre Timer deaktiviert. Es existiert nie ein Parallel-Canary.
- Lokaler Speicherplatz, SQLite-Integritaet und die geschuetzten State-, Config-
  und Secret-Wurzeln wurden read-only geprueft.
- Fuer eine moegliche Nextcloud-Registeraenderung existieren ausfuehrbare und
  zuletzt erfolgreich getestete externe Backup- und Restore-Hooks. Ohne externen
  Snapshot darf kein schreibender Rechnungs-Canary freigegeben werden.
- Jan erteilt einen neuen ausdruecklichen Auftrag fuer das Deployment. Die
  Entwicklungsabnahme M10.8 erteilt diese Freigabe nicht.

## 2. Gesicherte Installation

Das Deployment erfolgt ausschliesslich ueber den bestehenden digest-,
signatur- und revisionsgeprueften `deploy.sh`-Pfad. Er stoppt Writer, prueft den
Single-Writer-Zustand, erzeugt den externen Snapshot und das lokale Releasebackup,
verifiziert Archiv-SHA, SQLite und Test-Restore und startet den Kandidaten
gestuft. Weder Image noch Workspace werden im laufenden Container gepatcht.

Vor jedem spaeteren Rechnungs-Apply ist die ID eines fuer diesen Zustand
verifizierten lokalen Backups sowie die externe Snapshotreferenz festzuhalten.
Das lokale Backup umfasst State, Konfiguration und Secrets, aber keine
vollstaendige Wiederherstellung bereits erfolgter Nextcloud-Aenderungen.

## 3. Read-only Baseline

Nach erfolgreichem Produktsmoke werden ausschliesslich die registrierten
read-only Befehle ausgefuehrt:

```bash
/opt/openclaw-agent/scripts/assistant.sh version --verify
/opt/openclaw-agent/scripts/assistant.sh invoices status
/opt/openclaw-agent/scripts/assistant.sh invoices audit
```

Zu protokollieren sind Release/Revision, SQLite- und Registerstatus, getrennte
Kohorten, Pflichtfeldluecken, Plausibilitaetsfehler, Extraktorversionen und
Pfadabweichungszaehler. Die Baseline enthaelt keine PDF-/OCR-Texte, Dateinamen,
Pfade, Lieferanten, Rechnungsnummern, Mailinhalte oder Zugangsdaten. Ein
`review_outside_review_subfolder`-Befund loest keine Verschiebung aus.

## 4. Canary-Vorschau

Aus Audit und anschliessender begrenzter Reviewliste wird genau ein fachlich
unkritischer Datensatz mit Status `review` oder `unclassified` und einem exakten
Quelljahr ausgewaehlt. Danach wird nur die Vorschau ausgefuehrt:

```bash
/opt/openclaw-agent/scripts/assistant.sh invoices reprocess \
  --status "<review|unclassified>" --source-year <YYYY> --limit 100 --dry-run
```

Jan werden fuer genau einen Vorschlag der PDF-Hash, `preview_sha256`, Status,
Altwerte, Neuwertkandidaten, Evidenztypen, Extraktor-/Regelversion,
Klassifikation und alle typisierten Konflikte gezeigt. Fehlende Werte entstehen
nicht aus Erinnerung, Dateiname, Mailtext oder Ollama. `confirmed` und
`confirmed-manual` sind keine Canary-Kandidaten.

## 5. Ausdrueckliche Einzeluebernahme

Die Vorschau ist keine Freigabe. Erst ein neuer ausdruecklicher Nutzerauftrag fuer
den unveraenderten einzelnen Hash und Digest erlaubt exakt diesen Aufruf:

```bash
/opt/openclaw-agent/scripts/assistant.sh invoices reprocess-apply \
  --hash "<SHA256>" --expected-preview-sha256 "<Digest>" --yes
```

Der Agent ergaenzt `--yes` nie autonom und kombiniert keine Datensaetze. Drift an
PDF, Zeile, Status, Version oder Vorschlag, ein offener Konflikt, eine Regression,
unplausible Arithmetik oder manueller Schutz beendet den Apply vor der Aenderung.
Ein lokaler Erfolg mit fehlgeschlagenem Registersync lautet
`local-applied-register-failed` und darf nicht als Gesamterfolg erscheinen.

## 6. Nachmessung

Nach genau einem freigegebenen Apply werden erneut `invoices status` und
`invoices audit` ausgefuehrt. Zusaetzlich werden SQLite-Integritaet,
inhaltsfreies Reprocessing-Audit, Jahresregisterschema, SHA, ETag-Ergebnis,
Containerhealth, Jobzustand und Single-Writer-Vertrag geprueft. Alt-/Neumetriken
und verbleibende Konflikte werden berichtet; Dokumentinhalte werden nicht in
Logs, Chat oder CI kopiert.

Weitere Rechnungen bleiben jeweils neue Einzelentscheidungen mit neuer Vorschau,
neuem Digest und neuer ausdruecklicher Freigabe. Es gibt keinen Bulk-Apply und
keine automatische Abarbeitung des historischen Backlogs.

## 7. Teilfehler und Rollback

Bei Unsicherheit, Drift, ETag-Konflikt, Remote-Ausfall oder fehlgeschlagener
Nachmessung werden keine weiteren Rechnungen bearbeitet. Writer werden ueber den
dokumentierten Betriebsweg gestoppt, der aktuelle Zustand wird erneut gelesen und
der passende Recoverypfad anhand der gesicherten Backup-ID gewaehlt.

Ein Image-Rollback allein stellt keine bereits erfolgte Nextcloud-
Registeraenderung wieder her. Das lokale Backup darf nur offline und nach
Pruefsummen-/SQLite-Pruefung restauriert werden; fuer externe Aenderungen ist die
zugehoerige Snapshotreferenz samt Restore-Hook erforderlich. Scheitert der externe
Restore, bleibt der Remotezustand ausdruecklich unklar, auch wenn der verifizierte
lokale alte Stand wieder startet.
