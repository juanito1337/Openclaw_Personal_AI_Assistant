# M14-Entwicklungsabnahme und produktive Stopppunkte

Stand: 2026-09-07

M14.0 bis M14.7 ersetzen den minutenlangen Standalone-Scan im Mailindex durch
einen residenten, gehärteten `clamd` und einen verpflichtenden fail-closed
Preflight. Diese Entwicklungsabnahme ist noch keine produktive Aktivierung.

## Abgenommener Entwicklungsumfang

- privater Unix-INSTREAM-Transport ohne Mailpfad- oder TCP-Freigabe,
- getrennte Rollen fuer Signaturupdate, Socketinitialisierung und Scanner,
- non-root `clamd`, read-only Rootfs/Signaturen, keine Capabilities und kein Netz,
- typisierte Socket-, Timeout-, Protokoll- und Groessenfehler,
- kanonische Engine-/Signaturidentitaet mit Cachetrennung,
- sichtbarer Standalone-Fallback fuer Einzeloperationen, aber niemals als
  Indexbereitschaft,
- verpflichtender Daemon-/Signatur-Preflight vor Backfill und Reconcile,
- unveraenderte atomare M12-Projektion: Scannerfehler veroeffentlichen keine
  partielle Generation,
- Compose-, Deployment-, Rollback-, Rollen-, Supply-Chain- und CI-Integration.

Der lokale Echt-Daemontest verwendet ausschließlich synthetische Daten:

```bash
docker build --target maintenance-runtime -t openclaw-agent:m14-maintenance .
OPENCLAW_M14_MAINTENANCE_IMAGE=openclaw-agent:m14-maintenance \
  ./scripts/check-m14-integration.sh
```

Die festen Sicherheits- und Kapazitaetsgrenzen stehen maschinenlesbar in
`docs/architecture/clamd-operating-budget-m14.json`. Produktive Durchsatzwerte
werden nicht erfunden; sie entstehen erst im Canary.

## Getrennte produktive Freigaben

M14.8 besitzt vier Stopppunkte, die jeweils eine ausdrueckliche Freigabe
benoetigen:

1. signiertes Kandidatenimage deployen und Scanner-Smoke ausfuehren,
2. begrenzten, benannten Mailordner als Canary lokal indizieren,
3. nach Auswertung und Dimensionierung den resumierbaren Vollbackfill starten,
4. erst bei vollständiger autoritativer Generation den inkrementellen
   `mail-index`-Job aktivieren.

Vor jedem produktiven Schritt gelten Release-/Digestprüfung, verifiziertes
Backup, freier Platz, genau ein Writer und die in
`docs/MAIL_SEARCH_M11_ACCEPTANCE_AND_ROLLOUT.md` dokumentierten Such- und
Rollbackregeln. Weder Image- noch Indexrollback verändern externe Mail.

## Noch offen

- produktive Ready-Zeit, Peak-RAM, Scanlatenz und Durchsatz,
- Capacity-Nachweis fuer rund 8.500 Nachrichten,
- vollständige Indexgeneration und aktuelle Locatorabdeckung,
- positive Absender-/Body-/Zeitraumsuche und kontrollierter Negativfall,
- inkrementelle New-Mail-/Move-/No-op-Kosten,
- sieben Tage Beobachtung von Signaturreload, Ressourcen und Jobgesundheit.

Solange diese Punkte fehlen, ist M14.8 ausdrücklich **nicht abgenommen**.
