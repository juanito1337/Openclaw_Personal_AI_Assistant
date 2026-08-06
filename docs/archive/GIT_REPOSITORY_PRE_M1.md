# Historische Git-Anleitung vor M1

> Archiviert in M1. Aktuelle Git-, Review- und Release-Regeln stehen in
> `CONTRIBUTING.md` im Repository-Root.

Dieser Ordner ist der bereinigte Quellstand des produktiven OpenClaw-Agenten.
Produktive Laufzeitdaten und Zugangsdaten gehoeren nicht in Git.

## Trennung

- Git-Arbeitskopie: zum Beispiel `~/repos/openclaw-agent`
- Produktiver Workspace: `~/.openclaw/workspace`
- Geheimnisse: `~/.config/personal-assistant/secrets.env`

Die Dateien `mail_agent/config.toml`, `mail_agent/rules.toml` sowie die lokalen
Dateien unter `personal_assistant/*.toml` werden aus Vorlagen erzeugt und durch
`.gitignore` ausgeschlossen.

## Erstes lokales Repository

```bash
cd ~/repos/openclaw-agent
git init -b main
git add .
git status
git commit -m "R26.4: produktiver Ausgangsstand"
git tag -a r26.4 -m "OpenClaw R26.4"
```

## Privates GitHub-Repository

Nach der Anmeldung mit der GitHub CLI:

```bash
gh auth login
gh repo create openclaw-agent --private --source=. --remote=origin --push
git push origin r26.4
```

Vor jedem Commit:

```bash
./scripts/check-repo.sh
git status --short
git diff --check
```

## Neue lokale Installation

```bash
./scripts/bootstrap-local.sh
./scripts/assistant.sh setup init
```

Danach werden Nextcloud, Kalender, Aufgaben, Kontakte und Mailkonten lokal neu
konfiguriert. Die produktiven Konfigurationsdateien werden nicht aus Git
ueberschrieben.
