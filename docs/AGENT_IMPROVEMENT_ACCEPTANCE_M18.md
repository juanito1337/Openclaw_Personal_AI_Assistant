# M18 Entwicklungsabnahme der Agentenverbesserungen

Stand: 2026-10-13. Branch: `feature/m17-nextcloud-talk`.
Status: M18 ENTWICKLUNGSSEITIG ABGENOMMEN. Gesamt-, Wheel- und lokale
Rollenimage-Abnahme bestanden. Keine Veroeffentlichung oder produktive Aktivierung.

Dieser Bericht gehoert zur [M18-Roadmap](AGENT_IMPROVEMENT_ROADMAP.md).
Er beschreibt eine risikobasierte Gesamtpruefung aller fachlichen Bereiche,
keinen Beweis, dass jede moegliche Eingabe fehlerfrei ist. Entwicklungsbelege,
Buildartefakte und produktive Abnahme bleiben getrennt.

## Pruefmatrix und konkrete Verbesserungen

| Bereich | Gepruefter Vertrag | Verbesserung oder beibehaltene Absicherung | Reproduzierbarer Nachweis |
| --- | --- | --- | --- |
| Nextcloud-Wissensprojektion | Vollstaendigkeit, Delta, No-op, Kopien, Inhalt und ETag | Limit/Fehler ist Teilabdeckung; keine Loeschreconciliation; neue Locator erst nach If-Match-Download und Inhaltsvergleich wiederverwenden | `tests/test_incremental_sync_m163.py` |
| Modellkoordination | Interaktivprioritaet, Queue, Reservierung und Ausfaelle | Unerwarteter Gatefehler liefert 503 ohne Upstream; Slots bleiben nach Telemetrie-/Clientfehler wiederverwendbar | `tests/test_ollama_priority_proxy.py`, `tests/test_r261_ollama_reliability.py` |
| Scheduler und Container | Lease, Rollenbudget, OOM und Fremdlast | Bestehende Prioritaets-/Lease-/Rollenvertraege bleiben erhalten; kein Limit wurde fuer M18 erhoeht | Scheduler-, Telemetrie- und Runtime-Capacity-Suite |
| Native Aktionen | Vorbereitung, Domaenen, Zielumfang, Freigabe und Erfolg | Kataloggebundene vorbereitende Reads; kein Gesamtabschluss nach einem Teilziel; Kandidatennachweise nicht doppelt zaehlen | `tests/test_agent_execution_reliability.py`, `tests/test_action_completion_m15.py` |
| Laufzustand | Anzahl, Alter, Prompts und Freigabewiederaufnahme | Begrenzte Run-Maps/Nonces; Ablauf und Eviction widerrufen Authority; mehrere Reply-Payloads bleiben geschuetzt | Echte Plugin-Hooks im synthetischen Node-Harness |
| Mail und Lernen | Suchabdeckung, Frische, unveraenderliche Entscheidung, Abstention, Originalanhaenge | Positive Belege bleiben nutzbar; unvollstaendige Suche erlaubt keine negative Behauptung; keine automatische Lernaktivierung | `test_mail_learning_quality_m167.py`, `test_learning_quality.py`, `test_mail_search_acceptance_m118.py`, `test_mail_search_contract_m111.py`, `test_mail_forward_tool.py` |
| Kontakte, Kalender und Aufgaben | Auswahl, UID, Konflikt, If-Match und Ruecklesung | Keine Erweiterung von Rechten oder Verlust nicht angeforderter Felder; mehrstufige Auswahl bleibt ausfuehrbar | `test_carddav_contact_update.py`, `test_direct_calendar_tool.py`, `test_direct_tasks_tool.py` |
| Rechnungen und Bestellungen | Extraktionsqualitaet, Nachvollziehbarkeit, Export und Freigabe | CSV-Text mit gefaehrlichem Formelbeginn wird sichtbar markiert; Rohdaten und Zahlen bleiben unveraendert | Rechnungs-/Order-Suite und `test_invoice_csv_safety_m18.py` |
| Portfolio | Exakte Importquelle, Antivirus, finite Werte, Provider und Herkunft | Scan und Parser verwenden identische begrenzte Bytes; NaN/Infinity werden vor Uebernahme abgewiesen | `test_portfolio_import_safety_m18.py` und Portfolio-/Research-Suite |
| CLI und Auslieferung | Statische Introspektion, Paketidentitaet, aktuelle Qualitaetsbelege | Spaete Fachimporte; echter Wheel-Herkunftsnachweis; laufexklusive Coverage; erneute Ready- und digestgebundene Aktionspruefung | `test_cli_startup.py`, `test_quality_risk_m166.py`, `test_release_promotion_m161.py` |

Der importierte Portfolio-Snapshot bleibt auch dann der gescannte Snapshot,
wenn die Quelldatei waehrend des Scans ersetzt wird. Es wird weder ein fremder
Scan-Hash akzeptiert noch nach der Pruefung die Quelldatei erneut eingelesen.
Die Rechnungsmarkierung `Text: ` aendert ausschliesslich die Exportansicht;
sie ist keine Aenderung der erkannten Rechnungsnummer oder ihrer Provenienz.

## Gemessener CLI-Effekt

Messmethode: fuenf jeweils frische Prozesse mit `.venv/bin/python -c`, Import
von `personal_assistant.cli`, Messung der Prozessdauer mit `perf_counter` und
des Spitzenspeichers mit `resource.getrusage`. Keine Config, Datenbank,
Zugangsdaten oder Modellanfrage wurde geladen/ausgefuehrt.

| Messung | Vorher | Nach spaetem Fachimport |
| --- | --- | --- |
| Prozessdauer in ms | 631, 603, 605, 626, 601 | 417, 382, 421, 418, 426 |
| Spitzenspeicher in KiB | 33328, 33396, 33660, 33428, 33376 | 28256, 28308, 28220, 28140, 28028 |
| Importierte Module | 334 | 255 |
| `personal_assistant.service` geladen | ja | nein |

Die Einzelwerte sind lokale Beobachtungen auf demselben Entwicklungsrechner,
keine produktiven Antwortzeiten und kein neuer Mindestlaufzeitvertrag.
Parallel laufende Entwicklungstests koennen die Zeiten beeinflussen. Die
deterministische Regression erzwingt stattdessen, dass statische Abfragen weder
Fachdienste noch konfigurierte Laufzeitdaten laden.

Die gezielte Verlagerung selten benoetigter Importe entspricht dem beschriebenen
Initialisierungspfad der [Python-Dokumentation](https://docs.python.org/3/faq/programming.html#what-are-the-best-practices-for-using-import-in-a-module).
Die unveraenderte Konstruktorgrenze erstellt fuer echte Fachaufgaben dieselben
Dienste und Adapter wie zuvor.

## Quellen und Grenzen der Identitaetsnachweise

Ein ETag ist kein dateiuebergreifender Inhalts- oder Objektbezeichner. Auch
identische starke ETags verschiedener Ressourcen garantieren keine identischen
Repraesentationen. [RFC 9110 Abschnitt 8.8.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.1)
stuetzt deshalb die zusaetzliche Inhaltspruefung. Die lokale Statistik `moved`
beschreibt nur die wiederverwendete Projektion, keinen bewiesenen Remote-MOVE.
Ohne stabile Server-Datei-ID kostet ein neuer Locator einen Download; identische
Inhalte muessen danach nicht erneut geparst und in Chunks geschrieben werden.

Allgemeine Mehrfachauftraege besitzen noch keinen universellen strukturierten
Planer. Erkannte, nicht einzeln gebundene Ziele fuehren vor Writes zu
`information-required`. Der vorhandene quellgebundene Mail-Kalenderablauf ist
davon getrennt und behielt Einzelgenehmigungen und verifizierte Nachbedingungen.
Eine Regex-basierte Ambiguitaetspruefung ist keine vollstaendige semantische
Analyse beliebiger natuerlicher Sprache.

128 Runs, 15 Minuten, 64 KiB je Prompt und 512 Genehmigungsnonces begrenzen den
Pluginzustand unabhaengig vom Container-Speicherlimit. Bei Frist-/Kapazitaetsende
kann eine noch nicht gestartete Aktion eine neue explizite Anweisung benoetigen.
Bereits gestartete externe Writes werden nicht automatisch wiederholt oder
zurueckgerollt. Es gibt keinen neuen Hintergrundtimer und keine automatische
Freigabe nach Ablauf.

## Abnahmeprotokoll

- Ausgangssuite vor M18: 1.326 Tests erfolgreich, separater isolierter
  Talk-HTTPS-Containercheck ebenfalls erfolgreich.
- Fachquerschnitt: 159 Tests und 23 Subtests erfolgreich.
- Synchronisation und Laufzeit: 116 Tests und 6 Subtests erfolgreich.
- Rechnungen/Portfolio/Promotion vor der finalen Gate-Nachhaertung:
  309 Tests und 51 Subtests erfolgreich.
- Finale Promotionseinzelpruefung nach Schema-/Versionshaertung: 74 Tests erfolgreich.
- Root-CLI-/Coveragepruefungen nach Frische- und Wheelkorrektur: 51 Tests erfolgreich.
- Bestellungen, Antivirus und Risikopfade: 52 Tests erfolgreich.
- Finale Aktions-/Plugin-/Kapazitaetspruefung: 115 Tests und 24 Subtests erfolgreich;
  unabhaengiger Plugin-Gegenlauf: 95 Tests und 24 Subtests erfolgreich.
- Unabhaengiger Review fand zusaetzlich URI-uebergreifende ETag-Kollisionen,
  alte/gemeinsam ersetzbare Coverageberichte, falsche Wheel-Importherkunft,
  bool/null-Readyfelder, Freigabeablauf waehrend asynchroner Vorbereitung und
  einen faelschlichen Aktionsabschluss nach einer Importvorschau sowie zu breite
  Mehrzielerkennung bei einer einzelnen Aufgabe mit Read-back/Feldvorgaben.
  Diese reproduzierten Befunde sind korrigiert und erneut geprueft.
- Gesamtpruefung `./scripts/check-repo.sh`: Exit 0, 1.573 Tests bestanden,
  ein Docker-Test bewusst separat ausgefuehrt, Laufzeit 350,08 s;
  Gesamtcoverage 70,98 Prozent und alle vertraglichen Risikogates bestanden.
  Ruff: 470 bekannte Altbefunde, keine neuen; mypy: 108 bekannte Altbefunde
  in 20 Dateien, keine neuen. Baselines und Runtime-Limits wurden nicht erhoeht.
  Shellcheck, Hadolint, Compose, generierte Vertraege, Inventar, Manifest,
  M13-/M15-Benchmarks und synthetischer M8-Recovery-Drill ebenfalls bestanden.
- Frische Wheel-Installation `./scripts/check-wheel.sh`: Exit 0,
  1.573 Tests, 247 Subtests und ein separat ausgefuehrter Docker-Test;
  Laufzeit 252,11 s, Wheel 682.422 Bytes, Build 6.034 ms.
  Ein anfangs falscher neuer Test erwartete das separate Docker-Plugin auch
  im reinen Python-Wheel. Er prueft jetzt dessen exakten bestehenden
  MissingPlugin-Fehlervertrag, weiterhin ohne Fachimport oder Configzugriff.
  Die echte `site-packages`-Herkunft wird nicht umgangen.
- Der erste Imagebuild fand einen fehlenden Buildkontext-Eintrag fuer
  `docker/scripts/nextcloud-talk-config.py`. Eine einzelne `.dockerignore`-Ausnahme
  und eine Regression beheben dies; der erneute Dreierbuild und die gesamte
  Rollenimage-Abnahme sind erfolgreich. Secrets bleiben ausgeschlossen.

Gesamtlaufzeiten sind lokale Messungen unter paralleler Entwicklungspruefung,
kein Geschwindigkeitsvergleich. Der in beiden Python-Suiten uebersprungene
`tests/test_talk_proxy_container.py` wurde im Rollenimagepfad wirklich ausgefuehrt.

## Lokale Rollenimage-Abnahme

Aufruf:

```bash
OPENCLAW_SOURCE_REVISION=m18-local-audit ./docker/scripts/build-local.sh \
  openclaw-agent:m18-audit openclaw-agent:m18-audit-proxy openclaw-agent:m18-audit-maintenance
./scripts/check-role-images.sh \
  openclaw-agent:m18-audit openclaw-agent:m18-audit-proxy openclaw-agent:m18-audit-maintenance \
  m18-local-audit
```

Beide Abschlussaufrufe lieferten Exit 0. Die bestehenden Produktlabels bleiben
`3.4.0-r29.0.6`; `m18-local-audit` kennzeichnet ausschliesslich den lokalen,
noch nicht veroeffentlichten Entwicklungsstand, keinen signierten Commit.

| Lokaler Tag | Image-ID |
| --- | --- |
| `openclaw-agent:m18-audit` | `sha256:97f7991f378c90c94806b0e1e3064494cf5f32d56c6ca402c0205b743f414207` |
| `openclaw-agent:m18-audit-proxy` | `sha256:b4d07a5ea8d948610132f1e53f14a59b28595eb9a7aa954af892ae15bdbf2636` |
| `openclaw-agent:m18-audit-maintenance` | `sha256:889164fef4d2a695d54e2433305f2c92c3eb2786d06256ee70a7fcb5baeb1cbb` |

Bestanden: Rollenlabels und -trennung, echter Caddy-TLS-/IP-SAN-/ACL-/Routentest,
CA-Persistenz nach Neustart, unveraenderliche Plugins, Himalaya-Integritaet,
Gateway-Legacyindex/Health/Ereignisrelay, Ollama-Fixturenetz samt Upstream-Health
und Maintenance-Imports/-Binaries. Der ClamAV-Transporttest nutzte einen
begrenzten oeffentlichen HTTPS-HEAD; dies ist kein produktiver Scanbefund.
Testcontainer, Testnetz und Testvolume wurden anschliessend entfernt.
Die `service.py`-Datei des Runtimeimages entspricht per SHA-256 dem Worktree.
Eine reale Nextcloud-/Android-Abnahme und komplette M17-Freigabe folgen daraus nicht.

## Nicht ausgefuehrte Betriebsaktionen

Kein Push, signiertes Release, Main-Promotion, Registry-Publikation oder
produktives Deployment. Keine Jobs, Serverrechte, Weiterleitungen,
Modellkonfigurationen oder Truststores wurden veraendert. Die M17-Stackarbeit
bleibt im Branch enthalten und ist weiterhin separat zu aktivieren.

Produktive Klassifikationsqualitaet braucht aktuelle bestaetigte Labels;
synthetische Tests erfinden keine Verbesserung der realen Modellpraezision.
Die bestehenden deaktivierten Semantik-/Executorprototypen werden nicht
automatisch aktiviert. Bekannte statische Altbefunde bleiben sichtbar und
duerfen durch M18 nicht wachsen.
