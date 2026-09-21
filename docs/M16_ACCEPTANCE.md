# M16.10 – unabhaengige Gesamtabnahme

Stand: 2026-09-21  
Kandidatenidentitaet: `3.4.0-r29`; exakter Commitnachweis entsteht nach dem
finalen Commit als externes CI-/Release-Artefakt
Urteil: **M16 NICHT ABGENOMMEN**

## Ergebnis

Die lokale Entwicklungsabnahme ist technisch gruen. Der vollstaendige
Test-/Qualitaetspfad, das aus einem sauberen Snapshot installierte Wheel, drei
isolierte Rollenimages, Quell- und Image-Secret-Scans, SBOM, lokale Provenance,
CVE-Policy, Rollen-Smokes, reproduzierbare OCI-Exporte und die hermetischen
M8-/M11- bis M15-Szenarien wurden ohne produktive Daten oder externe Writes
ausgefuehrt.

Die konsistente r29-Quellidentitaet ist jetzt vorbereitet und der gemeinsame
lokale Repository- sowie Wheel-Pfad wurde erneut vollstaendig ausgefuehrt. M16
ist trotzdem nicht abgeschlossen: Die Promotionskette verlangt fuer den exakt
getesteten Releasecommit einen beobachteten CI-Lauf, einen
kryptografisch verifizierten Git-Tag, drei in der Registry veroeffentlichte und
Cosign-verifizierte Digests sowie einen vollstaendigen r28-Rollbackvertrag.
Die drei r28-Registryrollen sind inzwischen signatur- und
attestierungsverifiziert. Der r28-Git-Tag ist jedoch nicht kryptografisch
signiert, und produktive Backup-/Restoreevidenz wurde innerhalb der
Entwicklungsgrenze nicht gelesen. Der getrackte Promotionsvertrag bleibt als
selbstreferenzfreie Vorlage `draft`, `main` bleibt unveraendert und es fand kein
Produktivdeployment statt.

Die maschinenlesbare Evidenz steht in
[`m16.10-acceptance.json`](architecture/m16.10-acceptance.json).

## Gefundener und korrigierter Artefaktfehler

Der erste Wheel-Lauf baute und installierte das Paket, scheiterte aber mit
zwölf Tests. Drei Checkout-Annahmen waren die Ursache:

- ein verschachtelter Architekturtest rief fest `.venv/bin/python` auf;
- Release-Fixtures verlangten auch im installierten Testmodus ein Git-Objekt;
- das Runtime-Kapazitaetsbudget lag nur unter `docs/` und fehlte im Wheel.

Der Test verwendet nun den aktiven Interpreter und trennt den absichtlichen
Quellbaumtest vom installierten Produktimport. Installierte Release-Fixtures
verwenden eine deterministische Commitidentitaet; Gleichheit ist ohne
Git-Subprozess korrekt als Vorfahrenbeziehung definiert. Das dokumentierte
Kapazitaetsbudget wird ausserdem unveraendert als Package-Resource ausgeliefert
und durch einen Gleichheitstest gegen die Architekturdokumentation gebunden.

## Lokale Qualitaet und Vergleich zu M16.0

| Messung | M16.0 | M16.10 lokal | Bewertung |
| --- | ---: | ---: | --- |
| pytest-Collection | 1.057 | 1.221 | +164, keine unbemerkte Verringerung |
| ausgefuehrt inkl. Subtests | 1.168 | 1.332 | +164, keine Fehler/Skips |
| kombinierte Coverage | 68,502 % | 70,272 % | +1,770 Prozentpunkte |
| Branch-Coverage | 55,804 % | 57,657 % | +1,853 Prozentpunkte |
| Ruff-Altbefunde | 486 | 470 | 16 weniger |
| mypy-Altbefunde | 108 | 108 | unveraendert, keine neuen |

Bei den sieben bereits in M16.0 festgelegten kritischen Modulen steigen
`mail_agent/assistant_bridge.py` von 35,000 % auf 60,000 % und
`personal_assistant/actions.py` von 37,245 % auf 44,048 %. Antivirus, Policy,
Release und Manifest bleiben praktisch unveraendert. `job_control.py` sinkt in
der kombinierten Dateicoverage von 86,863 % auf 86,315 %; das ist als kleine
lokale Regression sichtbar, waehrend Gesamt- und Branch-Coverage steigen und
die risikokritischen M16.6-Verhaltenstests gruen bleiben.

Ruff, mypy, ShellCheck, Hadolint, Compose-Render, Dockerfile-Pruefung,
Python-Kompilierung, Dokumentlinks, Komponenteninventar, Quellmanifest und
`git diff --check` sind Bestandteil desselben `check-repo.sh`-Pfads. Lokaler
Pfad und CI rufen denselben Pruefpfad auf; ein CI-Ergebnis fuer diesen noch
nicht veroeffentlichten Commit ist jedoch ausdrücklich `not-measured`.

## Leistung und Artefakte

Das Wheel wird aus einem sauberen Quellsnapshot gebaut, in eine neue virtuelle
Umgebung installiert und dort mitsamt CLI-, Release- und Gesamttests geprueft.
Das Wheel war 659.369 Byte gross, der reine Build dauerte 4.200 ms und die
vollstaendige installierte Suite bestand mit 1.221 Tests und 111 Subtests in
157,92 s. Build- und Testartefakte unter `build/` sind nicht Teil des Releases.

Der Vorab-Audit des Implementierungscommits `127868f…` pruefte folgende lokale
Rollenimages. Diese Messung belegt den Buildpfad, ist aber ausdruecklich keine
r29-Kandidatenevidenz:

| Rolle | Image-ID | Groesse | Critical / Secrets |
| --- | --- | ---: | ---: |
| runtime | `sha256:8e217c…50ca0c` | 377.307.259 B | 0 / 0 |
| proxy | `sha256:1b34a2…394a0` | 23.426.255 B | 0 / 0 |
| maintenance | `sha256:3f1731…232b9` | 45.638.781 B | 0 / 0 |

Die festgelegte CVE-Policy blockiert Critical-Funde; sie war gruen. Trivy
meldete dennoch 28/13/13 High-Funde fuer runtime/proxy/maintenance. Diese
werden nicht verschwiegen und muessen im normalen Basisimage-Pflegeprozess
weiter beobachtet werden. Alle drei Images erhielten lokale SPDX-SBOMs und
Provenance, bestanden die Inhaltspruefung und waren in zwei sauberen
No-cache-OCI-Bauten byteidentisch. Die Laufzeiten der beiden kompletten
Rollenbuilds betrugen 172 s und 199 s.

Lokale Image-IDs und lokale Provenance sind keine Registry-Digests und keine
Cosign-Attestierungen. Die exakte r29-Imageevidenz wird deshalb erst nach dem
finalen Kandidatencommit extern erzeugt und darf nicht selbstreferenziell in
denselben Commit geschrieben werden.

## Read-only Rollbackpruefung

Die Registrytags `r28`, `r28-proxy` und `r28-maintenance` wurden auf
unveraenderliche Digests aufgeloest. Runtime, Proxy und Maintenance tragen
einheitlich Commit `1c063602…` und Release `3.4.0-r28`; Cosign-Signatur,
SLSA-Provenance und SPDX-Attestierung wurden fuer jede Rolle erfolgreich
verifiziert. Die exakten Digests stehen in
[`m16-rollback-r28.json`](architecture/m16-rollback-r28.json).

`git tag -v r28` meldet dagegen `no signature found`. Der Tag ist annotiert,
aber nicht kryptografisch signiert. Die Entwicklungsabnahme hat zudem keine
produktiven Backups unter `/srv/openclaw` geoeffnet oder veraendert. Registry-
Rollback und produktive Backup-/Restoreevidenz bleiben deshalb zwei getrennte
Nachweise; der vollstaendige Rollbackvertrag ist noch offen.

## Hermetische Funktionsabnahme

- M8: IMAP, SMTP, WebDAV/CardDAV/CalDAV, ETag, Netzverlust, Crash und
  Single-Writer;
- M11: Mailindex, FTS, Embeddings, Locator, Move/Copy/Delete, Netz und Crash;
- M12: nativer read-only IMAP-Connector und autoritative Reconciliation;
- M13: native Tools, Routing, Einmalfreigabe, Antwort- und Loop-Guard;
- M14: residenter ClamAV-Socket, clean/infected, Reload, Last und Recovery;
- M15: Aktionsabschluss, gebundene Approval-Wiederaufnahme, Timeout,
  Netzfehler und sichtbarer Teilerfolg ohne externe Writes;
- M16: Scheduler-, Sync-, Ressourcen-, OOM-, Maillern-, Rechnungs-, Portfolio-,
  Semantic- und Executorvertraege durch die vollstaendige pytest-Suite.

Der wiederholte synthetische 100-Objekt-Sync ergab 567,656 ms Full-p50,
5,435 ms No-op-p50, 9,276 ms Einzel-Delta-p50 und 20,482 ms fuer zeitkritische
Mailarbeit zwischen zwei Background-Batches. No-op blieb bei null Downloads
und null Projektionswrites, Delta bei genau einem Download und einem Write.
M16.0 hatte diese Werte ausdrücklich nicht gemessen; gegen M16.3 zeigen sich
leichte Laufzeitstreuungen, aber keine logische I/O-Regression.

Der synthetische Mailbenchmark behaelt Recall@10 0,65 und MRR 0,6667 exakt bei.
Cold-Latenz sinkt von 5,5345 auf 2,6712 ms, Warm-p50 von 1,5445 auf 1,4133 ms
und Warm-p95 von 4,523 auf 4,458 ms. Diese Werte belegen nur den synthetischen
Fixturepfad, nicht die Latenz eines produktiven IMAP- oder Ollama-Servers.

## Komplexitaet und Restschuld

Der Produktumfang wuchs von 61.369 auf 65.606 Pythonzeilen. Die groessten
Module bleiben `personal_assistant/portfolio.py` (3.447 Zeilen),
`personal_assistant/service.py` (2.713), `mail_agent/cli.py` (1.841),
`mail_agent/invoice_extract.py` (1.807) und `mail_agent/storage.py` (1.762).
Die groessten Funktionen bleiben der Reconcile-Run mit 541 Zeilen, der
Mail-CLI-Einstieg mit 510 und `apply_mail_projection` mit 492. M16.6 reduzierte
Kopplung durch reine Entscheidungsfunktionen und verbesserte Risikocoverage,
hat die absolute Modulgroesse der gewachsenen Fachdomaenen aber nicht geloest.
Das bleibt ein realer Modularisierungsbedarf; die erfolgreiche Abnahme wird
nicht als Beleg fuer kleine oder horizontal skalierbare Module ausgelegt.

Skillreferenzen, nativer Pluginvertrag und generierter Toolvertrag wurden gegen
den typisierten Katalog und die r29-Releaseidentitaet neu erzeugt und geprueft.
M16.10 fuehrt kein neues Agententool und keine neue Approvalklasse ein.

## Verbleibende Blocker und getrennte naechste Schritte

1. Den finalen r29-Kandidatencommit ohne weitere Quelldrift erzeugen, den Branch
   veroeffentlichen und den erweiterten gemeinsamen CI-Pfad beobachten.
2. Nach separater Image-Publish-Freigabe alle drei Rollen aus exakt diesem
   Commit publizieren, SBOM/Provenance attestieren und Cosign je Digest
   verifizieren.
3. Produktive Backup-/Restoreevidenz fuer den bereits signaturverifizierten
   r28-Registryrollensatz binden und den signierten r29-Git-Tag pruefen.
4. Erst nach separater Main-Promotionsfreigabe den unveraenderten Kandidaten
   fast-forward-only nach `main` bringen.
5. Backup, Deployment, Read-only-/Write-Canary und Jobaktivierung bleiben eine
   weitere ausdrueckliche Betriebsfreigabe.

Bis diese Kette vollständig belegt ist, lautet das verbindliche Urteil
**M16 NICHT ABGENOMMEN**.
