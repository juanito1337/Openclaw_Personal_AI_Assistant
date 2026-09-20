# M16-Releaseumfang und Promotionsgrenze

Der naechste vorgesehene Release ist `3.4.0-r29` (`r29`). Diese Festlegung ist
noch kein Release, kein Tag, keine Imageveroeffentlichung, keine Main-Promotion
und kein Deployment. Der maschinenlesbare
[`release-candidate-m16.json`](architecture/release-candidate-m16.json) bleibt
bis M16.10 im Zustand `draft`.

## Geplanter kumulativer Umfang

- M11: autoritativer, schneller und kontextbewusster Mailindex mit sicherem
  Live-Locator-Vertrag;
- M12: native read-only IMAP-Inventur und transaktionale Move-/Copy-/Delete-
  Reconciliation;
- M13: native typisierte Agentenwerkzeuge, Evidenzguard und Loop-Schutz;
- M14: residenter, fail-closed ClamAV-Pfad und kontrollierte Einzelquarantaene;
- M15: turngebundene Aktionsverpflichtung, gebundene Freigaben und belegter
  Workflowabschluss;
- M16: Betriebs-, Leistungs-, Release- und Restbestandskonsolidierung.

`CHANGELOG.md` bleibt waehrend der Entwicklung die detaillierte Quelle. Erst
M16.10 uebertraegt den tatsaechlich abgenommenen Umfang in `RELEASE.json` und
setzt Kandidatencommit, Manifestdigest, signierten Tag und Rollenimages fest.

## Verbindliche Promotionskette

```text
vollstaendig getesteter Commit
  -> RELEASE.json-Digest desselben Commits
  -> drei OCI-Rollen mit identischer Revision und Version
  -> SBOM + Provenance + Cosign je unveraenderlichem Digest
  -> signierter Git-Tag auf genau diesen Commit
  -> separate Main-Freigabe
  -> separate Image-Publish-Freigabe
  -> separate produktive Deployment-Freigabe
```

Der Main-Ausgangscommit muss Vorfahr des Kandidaten sein. Die Promotion erfolgt
fast-forward-only; Force-Push und das nachtraegliche Aendern des getesteten
Commits sind ausgeschlossen. Ein fremder oder alter Commit kann weder durch
einen Releasebezeichner noch durch einen Tag zum Kandidaten werden.

## Rollback und Installerevidenz

Vor der ersten Promotion muss M16.10 den bisherigen produktiven Rollensatz mit
Version, Quellcommit, drei signaturverifizierten Digests und vorhandener lokaler
Backup-/Restoreevidenz eintragen. Solange das fehlt, blockiert der Vertrag die
Promotion.

`installed_at` und `installation_id` bleiben im unveraenderlichen
`RELEASE.json` leer. Sie entstehen erst durch den Installer und gehoeren in die
externe Instanzevidenz. Das Build darf eine Installation weder vortaeuschen
noch einen spaeteren Deploymenterfolg vorwegnehmen.

## Pruefung

```bash
.venv/bin/python scripts/release_promotion.py verify-draft
```

Der spätere `verify-ready`-Pfad verlangt alle unveraenderlichen Identitaeten und
blockiert Drift, Fremdcommit, fehlende Signatur-/Attestierungsevidenz und ein
uneindeutiges Rollbackziel. `verify-action` prueft danach jede der drei
Freigaben einzeln.
