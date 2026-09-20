# ADR-0040: Getrennte Release-Promotion als unveraenderliche Einheit

Status: Accepted

Datum: 2026-09-20

## Kontext

Quellcommit, Releasebezeichner, Rollenimages, Attestierungen, Main-Branch und
produktive Installation waren einzeln abgesichert, aber nicht als eine
maschinenlesbar geschlossene Promotionseinheit beschrieben. Dadurch konnte
ein technisch funktionierender Entwicklungsstand weiterhin die alte
Releaseidentitaet tragen oder ein vorhandener Image-Tag vor seiner
Registryveroeffentlichung als installierbar erscheinen.

## Entscheidung

Eine Release-Promotion bindet exakt einen getesteten Quellcommit an den Digest
von `RELEASE.json`, einen signierten Git-Tag sowie Runtime-, Proxy- und
Maintenance-Image. Jedes Rollenimage muss dieselbe OCI-Revision und Version
tragen und besitzt einen unveraenderlichen Digest, SBOM, Provenance und
verifizierte Cosign-Signatur.

Main-Promotion, Imageveroeffentlichung und produktives Deployment sind drei
getrennte Freigaben. Der Main-Stand wird ausschließlich fast-forward bewegt;
Force-Push und nachtraegliches Umschreiben des getesteten Commits sind
verboten. Vor der Promotion ist ein eindeutiger, signaturverifizierter
Rollbackrollensatz verpflichtend.

Buildidentitaet und Installerevidenz bleiben getrennt. `installed_at` und
`installation_id` werden nicht in das unveraenderliche Buildmanifest
eingetragen, sondern erst außerhalb des Images durch den Installer belegt.

## Konsequenzen

- Ein Draft darf weder fehlende Digests noch offene Freigaben als Erfolg
  ausgeben.
- Ein fremder Main-Commit, Tag-Drift, OCI-Drift oder fehlendes Rollbackimage
  blockiert die Promotion.
- Testbranchimages bleiben Testartefakte und sind kein Release allein aufgrund
  eines Tags.
- M16.10 muss den Draft mit realer Abnahme- und Rollbackevidenz vervollständigen;
  M16.1 führt selbst keine Promotion oder Installation aus.
