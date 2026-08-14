# M9-Roadmap: Mail-Qualitaet und Review-Triage

Stand: 2026-08-14  
Arbeitsbranch: `development/mail-quality-review-triage`  
Status: Planung; keine produktive Aktivierung

## Ziel

M9 reduziert vermeidbare Eintraege in `Agent/Pruefen`, ohne Spam-, Weiterleitungs-,
Antivirus- oder Freigabegrenzen zu lockern. Der Ordner soll am Ende echte
Klassifikationsunsicherheit und klar bezeichnete Sicherheitsblockaden enthalten,
nicht mehrere fachlich unterschiedliche Zustaende vermischen. Jan kann einen
einzelnen Prueffall nachvollziehen und nach expliziter Bestaetigung korrigieren;
diese Korrektur verbessert kontrolliert spaetere Entscheidungen.

M9 ist kein Modell-Finetuning und keine pauschale Absenkung der aktuellen
Konfidenzgrenzen. Mailinhalte bleiben unvertrauenswuerdige Daten. Kein Paket darf
Mails loeschen, `EXPUNGE` ausfuehren, Nachrichten versenden, bestehende externe
Objekte ueberschreiben oder einen zweiten produktiven Mail-Writer starten.

## Gemessener Ausgangsstand

Die Diagnose vom 2026-08-14 liefert folgende Baseline. Die Werte sind ein
Ausgangspunkt und noch keine willkuerlichen Freigabegrenzen.

| Messwert | Ausgangswert |
| --- | ---: |
| Mails im Sieben-Tage-Fenster | 269 |
| `review` oder `uncertain` im Sieben-Tage-Fenster | 41 (15 %) |
| Durchschnittliche Klassifikationskonfidenz | 0,98 |
| Kategorie-Korrekturen | 697 |
| Genauigkeit des sicheren Absender-/Betreffmuster-Lerners | 97,67 % |
| Abdeckung des sicheren Muster-Lerners | 12,34 % |
| Verpasste relevante Mails in der Walk-forward-Auswertung | 2 |
| Spam-als-relevant-Risiko in der Walk-forward-Auswertung | 0 |
| Eindeutige Absender-/Betreffmuster | 576 |
| Einmalig beobachtete Muster | 506 |
| Gemischte Absender | 28 |
| Widerspruechliche Muster | 6 |
| Erfolgreiche Mail-Schedulerlaeufe in sieben Tagen | 325 von 330 (98,48 %) |

Die 35 Korrekturen mit unveraenderlicher Originalentscheidung sind eine kleine,
bewusst fehlerangereicherte Stichprobe und kein Schaetzer fuer die allgemeine
Modellgenauigkeit. Sie zeigen jedoch ein konkretes Kalibrierungsthema: Sieben als
Routine korrigierte Nachrichten waren zuvor als Spam eingestuft worden.

Weitere Betriebsbefunde werden getrennt behandelt: Der konfigurierte
Nextcloud-Kalender ist nicht mehr auffindbar, und der Wissensindex ist wegen eines
fehlgeschlagenen read-only-Zugriffs auf die Mail-SQLite-Daten rund 157 Stunden
veraltet. Der Mail-Writer selbst ist aktiv und gesund.

## Sicherheits- und Arbeitsvertrag

- Jedes Paket wird einzeln implementiert, getestet, dokumentiert und committet.
- Ein Paket beginnt erst, wenn die Abnahme des vorherigen Pakets gruen ist.
- Entwicklung und Integration verwenden Fixtures, temporaere SQLite-Datenbanken
  und einen hermetischen Fake-IMAP-Server. Produktive Mails und `/srv/openclaw`
  werden dabei nicht veraendert.
- Neue Agentenfunktionen muessen gleichzeitig stabile CLI, typisierten
  Toolkatalog, Skill-Referenz und Verhaltensregressionstest besitzen.
- Ein allgemeines Verschiebewerkzeug wird nicht fuer Review-Nachrichten
  freigeschaltet. Eine Korrektur erhaelt einen eigenen, engeren Vertrag.
- Jede externe Mailbewegung adressiert genau einen aktuellen Ordner, eine aktuelle
  Mailbox-ID und einen erwarteten Betreff. Bulk-Korrektur, Loeschen und Versand
  bleiben verboten.
- Produktive Ordnererstellung, Kalenderauswahl, Aktivierung und Backlog-Triage sind
  getrennte, explizit freizugebende Betriebsschritte.
- Die Schwellen `spam = 0.95`, `relevant = 0.90` und `routine = 0.90` werden vor der
  Auswertung in M9.5 nicht veraendert.
- Bei jeder behaupteten Lernverbesserung werden Stichprobe, Abdeckung, Genauigkeit,
  `relevant_missed` und `spam_forward_risk` gemeinsam berichtet.

## Paketuebersicht

| Paket | Ergebnis | Voraussetzung |
| --- | --- | --- |
| M9.0 | Reproduzierbare Baseline und eindeutige Review-Taxonomie | aktueller `main` |
| M9.1 | Dauerhafter, migrationssicherer Review-Grund | M9.0 |
| M9.2 | Read-only Review-Status, Liste und begruendeter Vorschlag | M9.1 |
| M9.3 | Fachlich getrennte Zielordner fuer relevante und unsichere Mail | M9.2 |
| M9.4 | Explizit bestaetigte Einzelkorrektur aus der Review-Triage | M9.3 |
| M9.5 | Sicherere Musternormalisierung und messbare Lernabdeckung | M9.4 |
| M9.6 | Belastbare Kalender- und Mailindex-Abhaengigkeiten | M9.5 |
| M9.7 | Gesamt-Abnahme, Dokumentation und kontrollierter Rollout | M9.6 |

## M9.0 – Baseline und Review-Taxonomie

### Scope

- Die oben genannten Betriebswerte mit dokumentierten, registrierten Befehlen
  reproduzierbar erfassen.
- Alle heutigen Wege nach `Agent/Pruefen`, `Agent/Termin-Pruefen` und `Agent/Fehler`
  durch Charakterisierungstests festhalten.
- Eine geschlossene Review-Grundmenge definieren, mindestens:
  `classification-uncertain`, `spam-below-threshold`,
  `routine-below-threshold`, `relevant-not-forwarded`,
  `invoice-review`, `appointment-review`, `safety-blocked` und `unknown-legacy`.
- Noch keine Datenbank-, Routing- oder Schwellenwertaenderung.

### Pflichttests und Abnahme

- Jede aktuelle Route in einen Pruef- oder Fehlerordner besitzt einen positiven
  Charakterisierungstest.
- Taxonomie-Werte sind typisiert und koennen nicht still per Freitext erweitert
  werden.
- Baseline-Befehle und ihre Datenschutzgrenzen sind dokumentiert.
- Vollstaendiger Repository-Check bleibt gruen.

### Arbeitsprompt

```text
Setze ausschliesslich Paket M9.0 aus docs/MAIL_QUALITY_REVIEW_ROADMAP.md um.
Lies AGENTS.md, skills/personal-assistant/references/mail.md und den generierten
Toolvertrag vollstaendig. Fuehre zuerst version --verify und git status --short aus.
Erfasse die Mailqualitaets-Baseline reproduzierbar und schreibe
Charakterisierungstests fuer jeden bestehenden Weg nach Pruefen, Termin-Pruefen
oder Fehler. Definiere nur eine geschlossene Review-Taxonomie; aendere noch keine
persistente Datenbank, Routingentscheidung, Konfidenzgrenze oder produktive
Konfiguration. Nutze keine produktiven Mails. Aktualisiere Dokumentation und
Quellmanifest, fuehre check-repo.sh und git diff --check aus und beende die Arbeit
nach M9.0.
```

## M9.1 – Persistenter Review-Grund und sichere Migration

### Scope

- `review_reason` getrennt von Kategorie, Status und Freitextgrund speichern.
- Schema-Migration mit Backup-/Integritaetsvertrag ergaenzen; bestehende Zeilen
  bleiben erhalten und erhalten nur dann einen abgeleiteten Grund, wenn dieser
  eindeutig beweisbar ist. Sonst gilt `unknown-legacy`.
- Originalkategorie, Originalkonfidenz, Quelle und Schwellenergebnis fuer neue
  Prueffaelle unveraenderlich festhalten.
- Wiederholte Verarbeitung derselben Mail bleibt idempotent.

### Pflichttests und Abnahme

- Migration einer realistischen Alt-SQLite-Fixture ohne Datenverlust.
- Neue und bereits migrierte Datenbanken liefern dasselbe aktuelle Schema.
- Unbekannte, fehlende und ungueltige Review-Gruende schlagen kontrolliert fehl.
- Kein Freitext und kein Mailinhalt gelangt in technische Telemetrie.
- Rollback-/Restore-Fixture und SQLite-Integritaetspruefung sind gruen.

### Arbeitsprompt

```text
Setze nur M9.1 um. Fuehre die M9.0-Abnahme zuerst aus. Ergaenze einen typisierten,
persistenten review_reason samt vorwaerts- und wiederholbar ausfuehrbarer
SQLite-Migration. Bewahre alle bestehenden Mail-, Feedback-, Korrektur- und
Auditdaten. Leite historische Werte nur bei eindeutiger Evidenz ab und verwende
sonst unknown-legacy. Veraendere noch kein produktives Routing und keine
Schwellenwerte. Teste frische Datenbank, Alt-Fixture, wiederholte Migration,
ungueltige Werte, Idempotenz und Integritaet. Aktualisiere Manifest und
Dokumentation und stoppe nach M9.1.
```

## M9.2 – Read-only Review-Diagnose und Vorschlaege

### Scope

- Registrierte Werkzeuge einfuehren:
  - `mail review status --days 7` fuer aggregierte Gruende, Quellen,
    Konfidenzbaender und Datenqualitaet ohne Mailinhalte,
  - `mail review list --reason "<Grund>" --limit 50` fuer aktuelle Metadaten,
  - `mail review suggest --folder "<Ordner>" --message-id "<ID>"
    --expected-subject "<Betreff>"` fuer eine read-only Neueinschaetzung.
- Vorschlaege muessen Originalentscheidung, belegte Regeln/Feedbackmuster,
  Unsicherheit und den naechsten erlaubten Schritt ausweisen.
- Ein Vorschlag darf weder Feedback speichern noch eine Mail bewegen oder senden.
- Unvollstaendige IMAP-Ergebnisse muessen mit `complete`, `folder_errors` und
  `results_may_be_truncated` sichtbar bleiben.

### Pflichttests und Abnahme

- CLI, Parser, Service, Toolkatalog, generierter Skillvertrag und Capability-Ausgabe
  stimmen ueberein.
- Falscher Ordner, falsche Mailbox-ID und falscher erwarteter Betreff schlagen fehl.
- Statusausgabe enthaelt keine Bodies, Anhaenge oder Geheimnisse.
- Vorschlagstests pruefen Verhalten mit Regeln, konsistentem Feedback, Konflikten,
  gemischten Absendern, Ollama-Fehler und Timeout.
- Read-only Werkzeuge veraendern weder IMAP noch Feedbackdatenbank.

### Arbeitsprompt

```text
Setze nur M9.2 um. Implementiere die drei registrierten read-only Review-Werkzeuge
aus der Roadmap. Nutze den typisierten Toolkatalog als Source of Truth und erzeuge
abgeleitete Skill- und Befehlsdokumentation nur ueber die vorhandenen Generatoren.
Ein Vorschlag muss streng evidenzbasiert sein, Konflikte als Unsicherheit melden
und bei Ollama- oder IMAP-Fehlern fail-closed bleiben. Fuehre negative
Verhaltenstests fuer Identitaetswaechter und Seiteneffektfreiheit aus. Aendere
keine Ordnerstruktur und verschiebe keine Mail. Stoppe nach M9.2.
```

## M9.3 – Getrennte Ablage statt Sammelordner

### Scope

- Einen konfigurierbaren Zielordner `Agent/Relevant` fuer sicher relevante, aber
  nicht weitergeleitete Nachrichten einfuehren.
- `Agent/Pruefen` nur noch fuer echte Klassifikationsunsicherheit,
  Schwellengrenzen und ausdrueckliche Sicherheitsblockaden verwenden.
- Rechnungspruefung mit eigenem Review-Grund klar von allgemeiner Klassifikation
  trennen; `Agent/Termin-Pruefen` bleibt eigenstaendig.
- Bestehenden Bestand nicht automatisch oder massenhaft umsortieren. Die neue
  Regel gilt zuerst nur fuer neu verarbeitete Nachrichten.
- Setup-, Doctor- und Migrationsvorschau muessen einen fehlenden neuen Ordner
  erkennen. Produktive Erstellung erfolgt erst im spaeteren freigegebenen Rollout.

### Pflichttests und Abnahme

- Sichere relevante Mail ohne Weiterleitung landet nicht mehr in
  `Agent/Pruefen`.
- Unklare, knapp unter der Schwelle liegende oder sicherheitsblockierte Mail bleibt
  in `Agent/Pruefen` und behaelt ihren exakten Grund.
- Keine Aenderung an Versand-, Antivirus-, Quarantaene- oder Kalenderfreigaben.
- Dry-run und produktive Routing-Fixtures liefern dieselbe Zielentscheidung.
- Alte Konfigurationen laden weiterhin sicher und melden die erforderliche
  explizite Ordneraktivierung.

### Arbeitsprompt

```text
Setze nur M9.3 um. Trenne sicher relevante, nicht weitergeleitete Mail von echter
Review-Unsicherheit durch den konfigurierbaren Ordner Agent/Relevant. Bewahre alle
Weiterleitungs-, Antivirus-, Quarantaene- und Kalendergrenzen. Verschiebe keinen
historischen Bestand und erstelle keinen produktiven IMAP-Ordner. Ergaenze Setup-
Vorschau, Doctor, Dry-run und Routingtests fuer alte und neue Konfigurationen.
Dokumentiere den spaeteren expliziten Aktivierungsschritt und stoppe nach M9.3.
```

## M9.4 – Explizite Einzelkorrektur aus Review

### Scope

- Ein eigenes Werkzeug `mail review correct` einfuehren. Es darf genau eine
  eindeutig ausgewaehlte Nachricht aus einem freigegebenen Review-Ordner in genau
  einen passenden Korrekturordner bewegen.
- Erlaubte Urteile: `relevant`, `routine` und `spam`; Nicht-Spam bleibt der
  gesonderten Quarantaene-/Korrekturregel unterstellt.
- Pflichtparameter: Quellordner, aktuelle Mailbox-ID, erwarteter Betreff, Urteil
  und `--yes` nach ausdruecklicher Nutzerfreigabe. Ein optionales typisiertes Label
  darf keine neue Berechtigung erzeugen.
- Das allgemeine `mail move` bleibt fuer Review-Quellen gesperrt.
- Feedback wird erst ueber den bestehenden Korrekturordnervertrag verarbeitet;
  unklare IMAP-Zustaende duerfen keinen Erfolg vortaeuschen.

### Pflichttests und Abnahme

- Ohne `--yes`, bei falschem Betreff, unbekanntem Urteil, falscher Quelle oder
  verbotenem Ziel findet keine Bewegung statt.
- Genau-eine-Mail-Grenze, Wiederholungsfall, IMAP-Fehler und unklarer
  Zustell-/Move-Status sind getestet.
- Kein Delete, `EXPUNGE`, Versand, Bulk-Modus oder freie Zielordnerwahl.
- Ein erfolgreich korrigierter Fall wird im naechsten hermetischen Maillauf genau
  einmal als Feedback erfasst und nach bestehendem Vertrag geroutet.
- Toolvertrag und Skill nennen die explizite Freigabe und den Seiteneffekt exakt.

### Arbeitsprompt

```text
Setze nur M9.4 um. Implementiere ein eng begrenztes mail review correct fuer genau
eine per Ordner, Mailbox-ID und erwartetem Betreff identifizierte Review-Mail.
Verlange ein typisiertes Urteil und --yes nach ausdruecklicher Freigabe. Lasse den
allgemeinen Mail-Move-Vertrag unveraendert gesperrt. Nutze ausschliesslich
allowlistete Korrekturziele, nie Delete, EXPUNGE, Versand oder Bulk. Teste alle
negativen Identitaets-, Freigabe-, Ziel-, Wiederholungs- und IMAP-Fehlerpfade mit
Fake-IMAP. Stoppe nach M9.4.
```

## M9.5 – Musternormalisierung und sichere Lernabdeckung

### Scope

- Wechselnde Datumswerte, Uhrzeiten, Betragswerte, Bestell-/Rechnungsnummern,
  Trackingcodes, UUIDs und lange numerische IDs in Betreffmustern typisiert
  normalisieren.
- Eine `pattern_version` einfuehren, damit alte Korrekturen nicht still mit neuer
  Semantik interpretiert werden.
- Exaktes Feedback und Konflikt-/Mixed-Sender-Sperren beibehalten. Routine und Spam
  duerfen erst nach mindestens zwei aelteren konsistenten Treffern deterministisch
  werden; relevante Gegenbelege bleiben Schutzsignale.
- Konflikte read-only erklaeren und eine explizite, einzeln bestaetigte
  Bereinigung vorbereiten; keine automatische Feedbackloeschung.
- Vor Aktivierung einen chronologischen Vergleich zwischen alter und neuer
  Normalisierung erzeugen.

### Pflichttests und Abnahme

- Golden-Tests fuer deutsche und englische Betreffmuster sowie Unicode, Antworten,
  leere und sehr lange Betreffe.
- Keine Selbsttest-Leakage: Eine Korrektur darf sich nicht selbst vorhersagen.
- Bericht nennt Stichprobe, Abdeckung, Genauigkeit, relevante Fehlentscheidungen
  und Spam-als-relevant-Risiko fuer beide Versionen.
- Neue Logik wird nicht aktiviert, wenn `relevant_missed` oder
  `spam_forward_risk` gegenueber der belegten Baseline schlechter werden.
- Keine pauschale Absender- oder Domainregel wird automatisch erzeugt.

### Arbeitsprompt

```text
Setze nur M9.5 um. Versioniere und verbessere die Betreffmusternormalisierung fuer
volatile IDs, Daten, Betraege und Trackingwerte. Migriere alte Korrekturen nicht
still in eine neue Bedeutung. Bewahre Zwei-Beleg-Regel, Relevant-Schutz,
Mixed-Sender- und Konfliktsperren. Erweitere die chronologische Walk-forward-
Auswertung um einen direkten Alt-/Neu-Vergleich ohne Self-Leakage. Aktiviere keine
Variante mit schlechteren Sicherheitsfehlern und senke keine allgemeinen
Konfidenzschwellen. Stoppe nach M9.5.
```

## M9.6 – Kalender- und Mailindex-Abhaengigkeiten

### Scope

- Einen verschwundenen konfigurierten Kalender als eindeutigen, handlungsfaehigen
  Doctor-Befund melden. Eine neue Kalenderauswahl bleibt eine separate explizite
  Nutzeraktion.
- Terminextraktion darf bei ungueltigen Daten keine normale Mail faelschlich als
  erfolgreichen Termin behandeln; Ursache und Zielordner bleiben getrennt.
- Den Sync-Worker ueber eine mail-owner-erzeugte, konsistente read-only Projektion
  oder einen gleichwertig sicheren Snapshot versorgen. Der Sync-Worker erhaelt
  kein Schreibrecht auf die produktive Mail-Datenbank.
- WAL, laufender Mail-Writer, Snapshot-Aktualitaet und Crash zwischen Erzeugung und
  Veroeffentlichung explizit testen.

### Pflichttests und Abnahme

- Fehlender Kalender liefert Ressource, Problem und erlaubten Discovery-Schritt,
  ohne automatisch Konfiguration oder Rechte zu aendern.
- Mailindex-Sync funktioniert bei read-only Mail-State und gleichzeitigem Writer.
- Teilweise, veraltete oder korrupte Projektionen werden nicht veroeffentlicht.
- Wissensindex meldet Alter und letzte vollstaendige Quellgeneration.
- Containerrollen behalten ihre M3/M4-Mount- und Single-Writer-Grenzen.

### Arbeitsprompt

```text
Setze nur M9.6 um. Verbessere den fehlenden-Kalender-Befund, ohne eine Ressource
automatisch auszuwaehlen oder Rechte zu erweitern. Beseitige den read-only
SQLite/WAL-Fehler des Sync-Workers ueber eine konsistente, vom Mail-Datenowner
veroeffentlichte Leseprojektion oder einen nachweislich gleich sicheren Vertrag;
gib dem Sync-Worker kein Mail-Schreibrecht. Teste parallelen Writer, Crash,
Korruption, Alter und atomare Veroeffentlichung hermetisch. Aendere keine
Produktivkonfiguration und stoppe nach M9.6.
```

## M9.7 – Gesamt-Abnahme und kontrollierter Rollout

### Scope

- Zentrale Mail-, Test-, Deployment-, Skill- und Changelog-Dokumentation
  aktualisieren.
- Toolvertrag, Befehlsreferenz, Capability-Ausgabe und Regressionstests
  deterministisch neu erzeugen beziehungsweise pruefen.
- Wheel und Rollenimages bauen, auf private Konfiguration/Laufzeitdaten pruefen und
  hermetische Mail-Integration ausfuehren.
- Produktive Aktivierung separat dokumentieren: verifiziertes Backup, neue
  Ordner-Vorschau, explizite Freigabe, Canary, Nachmessung und Rollback.
- Historischen Review-Bestand nicht automatisch migrieren. Zuerst nur neue Mails
  beobachten; eine spaetere Backlog-Triage benoetigt einen eigenen Auftrag.

### Gesamt-Abnahme

- `Agent/Pruefen` besitzt fuer jeden neuen Eintrag einen typisierten,
  auswertbaren Grund.
- Sicher relevante, nicht weitergeleitete Mails werden nicht als unklare Mails
  dargestellt.
- Review-Status und Vorschlag sind read-only; eine Korrektur braucht die exakte
  Mailidentitaet und explizite Freigabe.
- Alle negativen Sicherheits- und Idempotenztests sind gruen.
- `mail learning evaluate` berichtet mindestens die Baseline-Metriken. Eine
  Verbesserung wird nur behauptet, wenn Abdeckung und Sicherheitsfehler gemeinsam
  belegt sind.
- Mail-Job, Kalenderdiagnose und Wissensindex liefern belastbare Zustandsnachweise.
- `version --verify`, `git diff --check`, `check-repo.sh`, Compose-Validierung,
  Wheel-Pruefung, Image-Build und Containerintegration sind erfolgreich.
- Kein Secret, produktiver Mailinhalt, Datenbank-, Log- oder Laufzeitzustand liegt
  in Git, Wheel oder Image.

### Arbeitsprompt

```text
Setze nur M9.7 um und aendere keine fachliche Funktion mehr ausser fuer einen durch
Regressionstest belegten M9-Fehler. Fuehre die Einzelabnahmen M9.0 bis M9.6 erneut
aus. Aktualisiere zentrale Dokumentation, generierte Tool-/Skill-Vertraege,
CHANGELOG und Quellmanifest. Baue und pruefe Wheel sowie Rollenimages, rendere
Compose und fuehre die hermetische Mailintegration aus. Veraendere keine Datei
unter /srv/openclaw, keinen produktiven Job und kein produktives Postfach. Liefere
einen separaten, backup- und rollbackgesicherten Rolloutplan; fuehre ihn nicht ohne
expliziten Nutzerauftrag aus. Berichte Baselinevergleich, Sicherheitsfehler,
Einschraenkungen und ein eindeutiges M9-Urteil. Stoppe nach M9.
```

## Reihenfolge der Commits

Die bevorzugte Commit-Reihenfolge entspricht den Paketen:

1. `docs(mail): baseline and review taxonomy`
2. `feat(mail): persist typed review reasons`
3. `feat(mail): add read-only review diagnostics`
4. `feat(mail): separate relevant mail from review`
5. `feat(mail): add explicit single-message correction`
6. `feat(mail): improve versioned pattern learning`
7. `fix(mail): stabilize calendar and read-only indexing boundaries`
8. `docs(mail): complete M9 acceptance and rollout contract`

Jeder Commit muss fuer sich `git diff --check` bestehen und darf nur zusammen mit
seinen Tests in den Branch gelangen. Ein spaeterer Fehler wird im verursachenden
Paket korrigiert oder als eigener klar benannter Fix-Commit dokumentiert; Pakete
werden nicht zu einem schwer pruefbaren Gesamtcommit vermischt.
