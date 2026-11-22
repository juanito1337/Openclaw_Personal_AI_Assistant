# Externe Komponenten und Herkunftsnachweise

Das Register beschreibt die öffentlich dokumentierten Upstreams und die im
Repository festgelegten Build-Eingaben. Versionen, Digests und Prüfsummen kommen
aus den bestehenden Locks, Verträgen und Build-Dateien. Die
[Herkunftsdatei](upstream-sources.json) hält Zuständigkeiten, Updatequellen,
Prüfbelege und lokale Anpassungen fest. Die [JSON-Projektion](external-components.json)
wird daraus erzeugt. Der Generator verwendet Python 3.11 oder neuer und arbeitet
offline. `check-repo.sh` prüft die Projektion zusammen mit dem bestehenden
Supply-Chain-Vertrag.

```sh
python3 scripts/component_registry.py generate
python3 scripts/component_registry.py verify
```

`verify` meldet eine fehlende, ungültige oder veraltete Projektion mit einem
Fehlercode und verändert keine Dateien. `--root <Pfad>` wählt einen separaten
Quellbaum, etwa für Tests. Die Eingaben sind im Generator festgelegt. Im
Workflow-Verzeichnis werden `.yml`- und `.yaml`-Dateien geprüft. Actions müssen
in Blocksyntax deklariert sein. Unbekannte Deklarationsformen führen zu einem
Prüffehler.

## Pflege

1. Version oder Digest an der führenden Quelle ändern und deren vorhandene
   Konsistenzprüfungen ausführen. Die Projektion enthält keine separat
   gepflegten Versionsnummern.
2. Für jede neue ID die Herkunft und zuständige Rolle in `upstream-sources.json`
   festhalten. Ein Eintrag darf gemeinsame Metadaten seiner Komponentenart
   verwenden. `update_source` gibt an, wo die zuständige Rolle die nächste
   Änderung bewertet. Die Aufnahme in das Register erteilt keine Updatefreigabe.
3. Lizenz am festgelegten Stand prüfen und einen nachvollziehbaren Beleg
   hinterlegen. `declared` kennzeichnet eine Angabe im npm-Lock, `reviewed`
   einen dokumentierten Prüfnachweis und `unknown` eine offene Prüfung.
4. Lokale Patches, Austauschpfade und eigenständige Implementierungen in
   `local_changes` dokumentieren. `source_files` nennt die Belegdateien und
   `tests` die zugeordneten Repository-Tests. Leere Listen bedeuten, dass für
   diese Komponente keine Anpassung beziehungsweise kein eigener Prüfbeleg
   verzeichnet ist. Eine vorhandene Testreferenz belegt die Zuordnung. Das
   Ergebnis eines Testlaufs stammt aus dem jeweiligen Build oder CI-Lauf.
5. Nur eine tatsächlich belegte Prüfung in `review` datieren. Umfang und Beleg
   nennen. Für offene Prüfungen bleibt der Status `unknown`. Danach die
   Projektion erzeugen, den Diff prüfen und `verify` ausführen.

## Abdeckung

| Umfang | Aussage des Registers |
| --- | --- |
| `product` | Gepinnte Basisimages mit den verwendenden `FROM`- und externen `COPY --from`-Stages, OpenClaw-Upstream, kopierte Binärdateien, direkte Alpine-Pins je Dockerfile-Stage, offizielle Plugins und aus den Lockdateien ableitbare npm-Pakete und Overlays. Der Control-UI-Eintrag enthält die Hashes der Ausgangs- und Patchdateien sowie des Browsermoduls. |
| `build` | Scannerimages, GitHub Actions, Build-Helfer, Dockerfile-Frontend, Python-Pakete für Entwicklung und Wheel-Build sowie nur im npm-Buildbaum geführte Pakete. npm-Manifesteinträge, Workflow-Python-Selektoren und der Frontend-Tag haben die in ihrer jeweiligen Quelle angegebene Auflösung. |
| `external` | Nextcloud und die verwendeten Apps, mögliche Talk-Clients, Ollama und Modelle, der Host mit Docker/Compose und gegebenenfalls GPU-Treibern sowie der ClamAV-Signaturfeed. `pin: null` hält die installierte Version beziehungsweise Auswahl ausdrücklich offen. Die jeweilige Betreiberrolle entscheidet über Wartung. |
| `reference` | Der in der Herkunftsdatei festgehaltene Stand einer Quellreferenz. Der [Nextcloud-Funktionsvergleich vom 21. November 2026](../NEXTCLOUD_REUSE_COMPARISON.md) dokumentiert die betrachteten Upstream-Parser und Testquellen. R0 verwendet eine eigenständige Python-Implementierung mit lokalen Regressionstests. Der Vergleich enthält den Prüfungsumfang. |

Die npm-Lockdateien beschreiben die beiden verwalteten npm-Bäume einschließlich
gebündelter und optionaler Abhängigkeiten. Das tatsächlich installierte Paketset
hängt vom Build ab. Transitive Alpine-Pakete, Bestandteile der Basisimages sowie
weitere Upstream-Abhängigkeiten des eingebetteten OpenClaw stammen aus den
gebauten Rollenimages. Dafür erzeugt CI die
[SPDX-SBOMs und Herkunftsnachweise](IMAGE_SUPPLY_CHAIN.md) unter
`build/m7/{runtime,proxy,maintenance}/image.spdx.json`. Die Veröffentlichung
hinterlegt rollen- und digestgebundene SBOM-Attestierungen. Ihre Abdeckung hängt
vom jeweiligen Build sowie von der Erkennung durch den SBOM-Generator ab. Für die
Paketliste eines konkreten Images ist dessen SBOM der passende Nachweis.

Das Register liest keine Hostinstallation, privaten Konfigurationen oder
Dienstkonten aus. Verfügbarkeit, installierte Versionen, Rechte und aktuelle
Aktivität der externen Komponenten bleiben Gegenstand ihrer jeweiligen
Diagnosewege. Der [Release- und Recovery-Prozess](RECOVERY_AND_RELEASE.md)
regelt Validierung und Freigabe eines Updates.
