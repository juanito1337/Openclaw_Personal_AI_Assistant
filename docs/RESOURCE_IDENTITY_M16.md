# M16.4: eindeutige Ressourcen- und Konfigurationsidentitaet

## Ziel und Grenzen

Kalender, Aufgaben, Kontakte, Nextcloud-Dateien, Rechnungen, Mailordner und der
Nextcloud-Portfolioimport werden als typisierte Referenzen auf die zentrale
Ressourcen-Registry inventarisiert. Eine Referenz besteht getrennt aus stabiler
`resource_id`, fachlichem Namen, Komponentenart, bestaetigten Rechten,
Remote-Identifier und optionalem fachlichem Unterpfad.

M16.4 aendert keine Nextcloud-, CalDAV-, CardDAV- oder IMAP-Ressource und
erweitert keine Rechte. Discovery bleibt read-only. Eine Konfigurationsmigration
schreibt ausschliesslich die lokale `tools.toml`; produktive Ausfuehrung bleibt
ein eigener explizit freizugebender Operatorvorgang.

## Zentrale Diagnose

```bash
./scripts/assistant.sh resources status
./scripts/assistant.sh status
./scripts/assistant.sh doctor
```

`resources status` ist die kanonische, inhaltsfreie Inventarsicht. Status und
Doctor nehmen denselben Bericht auf. Jeder aktive Eintrag nennt die konfigurierte
ID, den fachlichen Namen, die Komponentenart, den Remote-Identifier, die
geforderten und die registrierten Rechte. Deaktivierte Pfade bleiben sichtbar,
werden aber nicht als Fehler bewertet.

Die Fehlercodes sind absichtlich getrennt:

- `resource-id-missing`: in der Toolkonfiguration fehlt eine stabile ID;
- `resource-id-stale`: die konfigurierte ID existiert nicht in der Registry
  beziehungsweise exakt nicht in der Discovery-Antwort;
- `resource-id-duplicate`: die Registry enthaelt dieselbe ID mehrfach;
- `resource-discovery-ambiguous`: Live-Discovery lieferte dieselbe stabile ID
  mehrfach;
- `resource-configuration-drift`: direkter Kalender und Mail-Kalenderpfad
  referenzieren unterschiedliche IDs;
- `resource-credentials-missing`: der native Connector hat nicht alle fest
  benannten Umgebungsvariablen;
- `resource-component-missing`: zum Beispiel VTODO statt VEVENT;
- `resource-permission-missing`: ein benoetigtes bestaetigtes Recht fehlt;
- `resource-kind-mismatch` oder `resource-disabled`: Registrytyp oder Aktivstatus
  widersprechen der Referenz.

Keiner dieser Fehler darf durch Auswahl des ersten, eines aehnlich benannten oder
eines fuzzy passenden Discovery-Treffers repariert werden.

## Kalender-Migration

Der alte Mail-Kalenderselektor ist kein Aufloesungspfad mehr. Solange die
typisierte Mail-Referenz noch von der aktiven direkten VEVENT-Ressource abweicht,
bleibt der bisherige exakte Fallback nur Diagnose und meldet
`resource-configuration-drift`; er gilt nicht als gesunder Status.

Zuerst wird eine neue Vorschau erstellt:

```bash
./scripts/assistant.sh resources calendar-migration --dry-run
```

Sie nennt Alt-/Ziel-ID, Aktion, unveraenderte Registryrechte und den
`preview_sha256`. Nur nach separater ausdruecklicher Freigabe darf genau diese
Vorschau angewendet werden:

```bash
./scripts/assistant.sh resources calendar-migration --yes \
  --expected-preview-sha256 "<Digest>"
```

Vor der atomaren Publikation entsteht ein lokales Backup. Nach dem Austausch wird
die komplette Toolkonfiguration erneut geladen und die gemeinsame Kalender-ID
validiert. Ein geaenderter Preview-Digest, fehlendes Ziel, falsche Komponente,
fehlendes Recht oder doppelte ID blockiert vor der Publikation. Scheitert die
Nachvalidierung, wird das Backup wiederhergestellt.

Ein expliziter lokaler Rollback akzeptiert nur ein Backup derselben `tools.toml`:

```bash
./scripts/assistant.sh resources calendar-migration \
  --rollback "<tools.toml.backup>" --yes
```

Auch der Rollback schreibt keine externe Ressource. Er ersetzt keine Registry,
Credentials, Serverrechte oder Remote-Identifier.

## Abnahme

Die Fixture-Abnahme liegt in `tests/test_resource_identity_m164.py`. Sie prueft
gemeinsame Kalender-ID und Rechte, getrennte Fehlercodes, exakte Discovery,
Digest-Bindung, unveraenderte Registry/Rechte, atomare Publikation und Rollback.
Aufgaben-, Kontakt-, Rechnungs-, Workspace- und Mailtests laufen im gemeinsamen
Repository-Check weiter. Der generierte Toolvertrag wird ausschliesslich aus dem
typisierten Katalog erzeugt.

