# M16-Releaseumfang und Promotionsgrenze

Der Quellstand traegt die Kandidatenidentitaet `3.4.0-r29` (`r29`). Diese
Identitaet allein ist noch kein signierter Tag, keine Imageveroeffentlichung,
keine Main-Promotion und kein Deployment. Der maschinenlesbare
[`release-candidate-m16.json`](architecture/release-candidate-m16.json) bleibt
als selbstreferenzfreie Vorlage im Zustand `draft`. Die commitgebundene
Ready-Evidenz wird nach dem finalen Commit als separates Release-Artefakt
erzeugt. Die lokale Abnahme vom 2026-09-21 ist technisch gruen, bleibt wegen
fehlender CI-, Cosign-/Registry-, Tag- und Rollbackevidenz aber mit dem Urteil
[`M16 NICHT ABGENOMMEN`](M16_ACCEPTANCE.md) gesperrt.

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

`CHANGELOG.md` bleibt die detaillierte Quelle. M16.10 hat den vorgesehenen
Umfang in `RELEASE.json` uebertragen; Kandidatencommit, Manifestdigest,
signierter Tag und Rollenimages werden erst durch die externe Ready-Evidenz
festgesetzt.

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

Die read-only Registry-Pruefung des r28-Satzes ist in
[`m16-rollback-r28.json`](architecture/m16-rollback-r28.json) festgehalten.
Alle drei Rollen, OCI-Identitaeten, Signaturen und Attestierungen sind verifiziert.
Der annotierte Git-Tag r28 ist nicht kryptografisch signiert; produktive
Backup-/Restoreevidenz unter `/srv/openclaw` wurde in der Entwicklungsabnahme
weder gelesen noch veraendert. Der vollstaendige Promotions-Rollbackvertrag
bleibt deshalb bis zu einer getrennten Betriebspruefung offen.

`installed_at` und `installation_id` bleiben im unveraenderlichen
`RELEASE.json` leer. Sie entstehen erst durch den Installer und gehoeren in die
externe Instanzevidenz. Das Build darf eine Installation weder vortaeuschen
noch einen spaeteren Deploymenterfolg vorwegnehmen.

## Pruefung

```bash
.venv/bin/python scripts/release_promotion.py verify-draft
```

Der spaetere `verify-ready`-Pfad verlangt alle unveraenderlichen Identitaeten und
blockiert Drift, Fremdcommit, fehlende Signatur-/Attestierungsevidenz und ein
uneindeutiges Rollbackziel. `verify-action` prueft danach jede der drei
Freigaben einzeln.
