from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import WORKSPACE_ROOT, Config, load_config

RECOMMENDED_VALUES: dict[tuple[str, str], object] = {
    ("ollama", "timeout_seconds"): 600,
    ("ollama", "max_body_chars"): 6000,
    ("ollama", "batch_enabled"): True,
    ("ollama", "batch_size"): 3,
    ("ollama", "batch_prefetch"): 9,
    ("ollama", "batch_timeout_seconds"): 300,
    ("ollama", "batch_max_body_chars"): 4000,
    ("ollama", "batch_max_total_chars"): 18000,
    ("ollama", "batch_fallback_to_smaller_groups"): True,
    ("ollama", "num_ctx"): 16384,
    ("ollama", "num_predict"): 512,
    ("ollama", "batch_num_predict"): 2048,
    ("ollama", "keep_alive"): "1h",
    ("ollama", "think"): False,
    ("thresholds", "spam"): 0.95,
    ("thresholds", "relevant"): 0.90,
    ("thresholds", "routine"): 0.90,
    ("thresholds", "calendar"): 0.95,
    ("thresholds", "min_forward_importance"): 7,
}


def configuration_fingerprint(config: Config) -> str:
    """Hash configuration, rules, and all routing/application source files.

    A code update must invalidate an older dry-run approval just like a rule
    change. This keeps the productive safety gate tied to the exact version that
    was tested.
    """

    digest = hashlib.sha256()
    source_dir = Path(__file__).resolve().parent
    workspace_root = WORKSPACE_ROOT
    paths = [config.path, config.runtime.rules_file, config.runtime.learning_folders_file, *sorted(source_dir.glob("*.py"))]
    for path in paths:
        digest.update(str(path).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")

    # Bind the approval only to the executable Nextcloud bridge and its local
    # origin metadata. Hashing the complete ClawHub lock or every similarly named
    # skill caused unrelated workspace changes to invalidate a successful dry-run.
    if config.nextcloud.enabled:
        configured_root = config.nextcloud.skill_dir.resolve()
        script = configured_root / "scripts" / "nextcloud.js"
        if not script.exists():
            default_root = (workspace_root / "skills" / "openclaw-nextcloud").resolve()
            if configured_root == default_root:
                candidates = sorted((workspace_root / "skills").glob("*/scripts/nextcloud.js"))
                script = next(
                    (candidate for candidate in candidates if "nextcloud" in candidate.parent.parent.name.casefold()),
                    script,
                )
        skill_root = script.parent.parent
        for path in (
            script,
            skill_root / ".clawhub" / "origin.json",
            skill_root / "package.json",
            skill_root / "package-lock.json",
        ):
            digest.update(str(path).encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"<missing>")

    digest.update(b"personal-assistant-v11-3.4.0-search-snapshot-fingerprint")
    return digest.hexdigest()


def setup_state_path(config: Config) -> Path:
    return config.runtime.database.parent / "setup_state.json"


def read_setup_state(config: Config) -> dict[str, Any]:
    path = setup_state_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def record_dry_run(config: Config, *, processed: int, errors: list[str], limit: int) -> None:
    path = setup_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_dry_run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "last_dry_run_ok": not errors,
        "processed": processed,
        "limit": limit,
        "errors": errors,
        "config_fingerprint": configuration_fingerprint(config),
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def invalidate_dry_run(config: Config, reason: str) -> None:
    """Mark a previously successful dry-run as stale after non-file training changes."""

    path = setup_state_path(config)
    state = read_setup_state(config)
    state.update({
        "last_dry_run_ok": False,
        "invalidated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "invalidated_reason": str(reason)[:500],
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def productive_run_blockers(config: Config, checks: dict[str, object]) -> list[str]:
    blockers: list[str] = []
    required_checks = ["himalaya", "folders", "mail_sources", "ollama", "database", "config"]
    if config.invoices.enabled:
        required_checks.append("invoices")
    if config.nextcloud.enabled and config.calendar.enabled and config.calendar.backend == "nextcloud_skill":
        required_checks.append("calendar")
    for name in required_checks:
        value = checks.get(name)
        if isinstance(value, dict) and not bool(value.get("ok")):
            detail = value.get("detail") or value.get("error") or value.get("missing") or "nicht bereit"
            blockers.append(f"{name}: {detail}")
    state = read_setup_state(config)
    if not state.get("last_dry_run_ok"):
        blockers.append("Noch kein erfolgreicher Dry-Run protokolliert")
    elif state.get("config_fingerprint") != configuration_fingerprint(config):
        blockers.append("Konfiguration oder Regeln wurden seit dem letzten Dry-Run geaendert")
    return blockers


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def update_toml_values(path: Path, changes: dict[tuple[str, str], object]) -> Path:
    text = path.read_text(encoding="utf-8")
    backup_dir = path.parent / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    for stale in sorted(backup_dir.glob(path.name + ".backup-*"))[:-20]:
        stale.unlink(missing_ok=True)

    by_section: dict[str, dict[str, object]] = {}
    for (section, key), value in changes.items():
        by_section.setdefault(section, {})[key] = value

    for section, values in by_section.items():
        section_pattern = re.compile(
            rf"(?ms)(^\[{re.escape(section)}\][ \t]*\n)(.*?)(?=^\[|\Z)"
        )
        match = section_pattern.search(text)
        if not match:
            lines = [f"[{section}]", *[f"{key} = {_toml_literal(value)}" for key, value in values.items()], ""]
            text = text.rstrip() + "\n\n" + "\n".join(lines)
            continue
        body = match.group(2)
        for key, value in values.items():
            key_pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*$")
            line = f"{key} = {_toml_literal(value)}"
            if key_pattern.search(body):
                body = key_pattern.sub(line, body, count=1)
            else:
                # A comment block immediately before the next TOML section often
                # documents that following section. Insert new keys before such a
                # trailing block so configuration comments remain readable.
                trailing = re.search(r"(?ms)(\n(?:[ \t]*#.*\n|[ \t]*\n)+)\Z", body)
                if trailing:
                    body = body[: trailing.start()].rstrip() + "\n" + line + trailing.group(1)
                else:
                    body = body.rstrip() + "\n" + line + "\n"
        text = text[: match.start(2)] + body + text[match.end(2) :]

    # Validate syntax before replacing the active file and write atomically.
    tomllib.loads(text)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    os.chmod(path, 0o600)
    return backup


def ollama_models(base_url: str, timeout: int = 8) -> tuple[list[str], str]:
    request = urllib.request.Request(base_url.rstrip("/") + "/api/tags")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return [], str(exc)
    models = data.get("models", []) if isinstance(data, dict) else []
    names = [str(item.get("name") or item.get("model") or "") for item in models if isinstance(item, dict)]
    return [name for name in names if name], ""


def _prompt(label: str, current: str) -> str:
    value = input(f"{label} [{current}]: ").strip()
    return value or current


def _yes_no(label: str, default: bool = True) -> bool:
    suffix = "[J/n]" if default else "[j/N]"
    answer = input(f"{label} {suffix}: ").strip().casefold()
    if not answer:
        return default
    return answer in {"j", "ja", "y", "yes"}


def job_information() -> dict[str, str]:
    command = [
        str(WORKSPACE_ROOT / "scripts/assistant.sh"),
        "jobs",
        "status",
        "--target",
        "mail",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"desired": "unbekannt", "state": "unverfuegbar", "detail": ""}
    try:
        payload = json.loads(result.stdout)
        job = payload.get("jobs", [])[0]
    except (IndexError, json.JSONDecodeError, TypeError, AttributeError):
        detail = (result.stderr.strip() or result.stdout.strip())[-500:]
        return {"desired": "unbekannt", "state": "unverfuegbar", "detail": detail}
    return {
        "desired": str(job.get("desired") or "unbekannt"),
        "state": str(job.get("state") or "unbekannt"),
        "detail": "; ".join(str(item.get("detail") or "") for item in job.get("issues", [])),
    }


def lock_information(config: Config) -> dict[str, object]:
    path = config.runtime.lock_file
    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (FileNotFoundError, ValueError, OSError):
        return {"active": False, "pid": None, "path": str(path)}
    try:
        os.kill(pid, 0)
        active = True
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
            if "mail_agent" not in command and "mail-agent" not in command:
                active = False
        except OSError:
            pass
    except ProcessLookupError:
        active = False
    except PermissionError:
        active = True
    return {"active": active, "pid": pid, "path": str(path)}


HELP_TOPICS = (
    "overview",
    "setup",
    "files",
    "config",
    "performance",
    "training",
    "nextcloud",
    "invoices",
    "calendar",
    "automation",
    "security",
    "openclaw",
)


def extended_help(topic: str = "overview", config: Config | None = None) -> str:
    selected = (topic or "overview").strip().casefold().replace("_", "-")
    aliases = {
        "hilfe": "overview",
        "help": "overview",
        "start": "setup",
        "konfiguration": "config",
        "speed": "performance",
        "schneller": "performance",
        "batch": "performance",
        "leistung": "performance",
        "lernen": "training",
        "train": "training",
        "caldav": "nextcloud",
        "carddav": "nextcloud",
        "rechnung": "invoices",
        "rechnungen": "invoices",
        "invoice": "invoices",
        "invoices": "invoices",
        "pdf": "invoices",
        "timer": "automation",
        "dateien": "files",
        "sicherheit": "security",
        "safety": "security",
        "commands": "overview",
        "topics": "overview",
        "workspace": "files",
        "plugin": "openclaw",
        "plugins": "openclaw",
        "skill": "openclaw",
        "skills": "openclaw",
    }
    selected = aliases.get(selected, selected)

    pages: dict[str, str] = {
        "overview": """MAIL-AGENT HELP-MODUS
=====================

Der Help-Modus erklärt, welche Datei und welcher geprüfte Befehl für eine Änderung
zuständig ist. Für eine zustandsabhängige To-do-Liste zuerst ausführen:

  ./scripts/mail-agent.sh guide

Themen:
  help setup       Einrichtung bis zum sicheren Testlauf
  help files       Bedeutung der Workspace-Dateien
  help config      Betriebsparameter in config.toml
  help performance Batch-Klassifizierung und Geschwindigkeitsoptionen
  help training    Regeln, Korrekturordner und Lernsignale
  help nextcloud   Nextcloud, CalDAV, CardDAV und App-Passwort
  help invoices    Rechnungs-PDFs sicher in Nextcloud archivieren
  help openclaw    Unterschied zwischen Skill und Plugin
  help calendar    Schutz für automatisch erkannte Termine
  help automation  Containerjobs, Locks und produktive Läufe
  help security    Secrets und Sicherheitsgrenzen

Assistenten:
  ./scripts/mail-agent.sh onboard
  ./scripts/mail-agent.sh configure
  ./scripts/mail-agent.sh nextcloud setup

Ohne Argument zeigt ./scripts/mail-agent.sh automatisch den zustandsabhaengigen guide.
""",
        "setup": """MAIL-AGENT SETUP
================

Komplett interaktiv:
  ./scripts/mail-agent.sh onboard

Oder zustandsabhaengig und einzeln:
  ./scripts/mail-agent.sh guide
  1. ./scripts/assistant.sh jobs off standard
     (nur nach ausdruecklichem Auftrag; stoppt die produktiven Containerjobs)
  3. ./scripts/mail-agent.sh configure
  4. ./scripts/mail-agent.sh setup --dry-run
  5. ./scripts/mail-agent.sh setup
  6. ./scripts/mail-agent.sh doctor
  7. ./scripts/mail-agent.sh nextcloud setup        # optional
  8. ./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
  9. Zuordnungen prüfen
 10. ./scripts/assistant.sh jobs on standard
     (nur nach ausdruecklichem Auftrag und erfolgreicher Pruefung)

Ein produktiver Lauf wird blockiert, solange kein erfolgreicher Dry-Run zur aktuellen
config.toml und rules.toml gespeichert wurde. --force ist kein normaler Setup-Schritt.
""",
        "files": """WORKSPACE-DATEIEN
================

mail_agent/config.toml
  Betriebsparameter: Mailbox, Ollama, Schwellwerte, Weiterleitung, Kalender,
  Nextcloud-Auswahl, Rechnungsarchiv, Digest und Laufzeitpfade. Bevorzugt mit 'configure', 'onboard'
  oder 'nextcloud setup' ändern.

mail_agent/rules.toml
  Harte Regeln für Spam, wichtige und Routine-Absender. Bevorzugt mit
  'training rule-add' bzw. 'training rule-remove' ändern. Backups liegen begrenzt unter mail_agent/data/backups/.

mail_agent/data/mail_agent.sqlite3
  Verarbeitungsstatus und Nutzerkorrekturen. Nicht direkt editieren. Verwende
  'training status', 'training feedback', 'training forget-feedback' und
  'training forget-sender'.

~/.config/mail-agent.env
  Secrets außerhalb des Workspace. Enthält das Nextcloud-App-Passwort und optionale
  direkte CalDAV-Zugangsdaten. Modus 0600; niemals in Git, persönliche Markdown-Dateien oder Chats kopieren.

AGENTS.md / HEARTBEAT.md
  Schlanke Entwicklungs- und Heartbeat-Regeln. Persönliche OpenClaw-Dateien wie
  MEMORY.md, USER.md, SOUL.md oder memory/ bleiben lokal und unversioniert.

skills/personal-assistant/SKILL.md
  Beschreibt den einzigen aktiven Agenten und verweist auf registrierte Assistant-Befehle.

skills/openclaw-nextcloud/
  Optional lokal installierter Community-Skill. Nicht in Git aufnehmen oder manuell
  editieren; Installation und Verifizierung ausschließlich über die Mail-Agent-CLI.
""",
        "config": """KONFIGURATION ÄNDERN
=====================

Mit Backup und Modell-Erkennung:
  ./scripts/mail-agent.sh configure

Direkt relevante Bereiche in mail_agent/config.toml:
  [mailbox]     Quelle, Weiterleitungs-Absender und Zieladresse
  [ollama]      Modell, Timeout, Batchgröße, Promptbudgets und Ausgabegrenzen
  [thresholds]  spam/relevant/routine/calendar, min_forward_importance
  [forwarding]  Weiterleitung und Originalmail-Anhang
  [calendar]    Bestätigungsmail, Zukunftsprüfung, Backend und Zeitzone
  [nextcloud]   Skill-Pfad, Kalender, Adressbuch und Kontakt-Signale
  [invoices]    Routine-Pflicht, Sicherheitsschwelle und Nextcloud-Zielordner
  [digest]      Tagesübersicht
  [runtime]     Datenbank, Regeln, Log und Lock

Empfohlene Lernphase:
  spam = 0.95
  relevant = 0.90
  calendar = 0.95
  min_forward_importance = 7
  calendar.require_trusted_sender = true

Nach jeder Änderung:
  ./scripts/mail-agent.sh test-config
  ./scripts/mail-agent.sh doctor
  ./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
""",
        "performance": """GESCHWINDIGKEIT / BATCH-KLASSIFIZIERUNG
=========================================

Der Agent wendet harte Regeln zuerst an. Nur Mails, die danach noch das Modell
brauchen, werden gebündelt. Bei nur einer neuen Mail bleibt es automatisch bei
einem Einzelaufruf.

Empfohlene Werte in mail_agent/config.toml:
  [ollama]
  batch_enabled = true
  batch_size = 3
  batch_prefetch = 9
  batch_timeout_seconds = 300
  batch_max_body_chars = 4000
  batch_max_total_chars = 18000
  batch_fallback_to_smaller_groups = true
  num_ctx = 16384
  num_predict = 512
  single_retry_num_predict = 1024
  batch_num_predict = 2048
  keep_alive = "1h"
  think = false

Bedeutung:
- batch_size: höchstens so viele ungeklärte Mails pro Ollama-Anfrage.
- batch_prefetch: so viele Mails werden maximal vorgelesen, damit trotz harter
  Regelentscheidungen volle Modell-Batches entstehen können.
- batch_timeout_seconds: ein langsamer Batch wird in kleinere Gruppen geteilt.
- single_retry_num_predict: nur eine nachweislich am Tokenlimit abgeschnittene
  Einzelantwort erhaelt genau einen groesseren Schema-Retry.
- batch_max_body_chars / batch_max_total_chars: begrenzen Newsletter und lange
  Threads, ohne das globale OpenClaw-Modell umzubauen.
- num_ctx = 16384: begrenzter, stabiler Kontext fuer die strukturierte Mailanalyse.
- think = false: reine strukturierte Klassifizierung ohne lange Denk-Ausgabe.
- Fällt ein Batch aus oder ist unvollständig, wird er in kleinere Gruppen geteilt.
  Eine einzelne fehlerhafte Mail kann dadurch die übrigen nicht falsch zuordnen.

Nach dem Lauf zeigt das JSON-Feld classifier unter anderem model_requests,
batch_requests, single_requests, rule_only_messages und batch_splits. Seit r18
enthaelt es zusaetzlich die von Ollama gelieferten Laufzeit- und Tokenzaehler.

Privacy-sichere Messwerte der letzten produktiven und Dry-Run-Laeufe:
  ./scripts/mail-agent.sh performance --limit 20

Unverdichtete Messdaten (ohne Absender, Betreff, Mailtext oder Kommandoargumente):
  ./scripts/mail-agent.sh performance --limit 5 --raw

Prüfen:
  ./scripts/mail-agent.sh test-config
  time ./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
""",
        "training": """TRAINING / LERNEN
=================

Das Ollama-Modell wird nicht feinabgestimmt. Der Agent lernt kontrolliert durch:

1. Korrekturordner für einzelne Fehlentscheidungen
   Falsch als Spam           -> Agent/Korrektur-Kein-Spam
   Wichtige Mail übersehen   -> Agent/Korrektur-Wichtig
   Unwichtige weitergeleitet -> Agent/Korrektur-Unwichtig
   Spam nicht erkannt        -> Agent/Korrektur-Spam

2. Harte Regeln für stabile Muster
   ./scripts/mail-agent.sh training rules
   ./scripts/mail-agent.sh training rule-add spam domain newsletter.example
   ./scripts/mail-agent.sh training rule-add important address person@example.org
   ./scripts/mail-agent.sh training rule-add routine address noreply@example.org
   ./scripts/mail-agent.sh training rule-remove spam domain newsletter.example

Lernstand prüfen/exportieren:
  ./scripts/mail-agent.sh training status
  ./scripts/mail-agent.sh training feedback --limit 30
  ./scripts/mail-agent.sh training export --output ~/mail-agent-training.json

Falsches Absender-Feedback entfernen:
  ./scripts/mail-agent.sh training feedback --limit 100
  ./scripts/mail-agent.sh training forget-feedback 123 --yes

Alle Korrekturen eines Absenders entfernen:
  ./scripts/mail-agent.sh training forget-sender person@example.org --yes

Regeländerungen und gelöschtes Feedback erfordern anschließend einen neuen Dry-Run.
Ein Nextcloud-Kontakt ist nur ein unterstützendes Identitätssignal; er wird nicht
automatisch wichtig, weitergeleitet oder kalendervertrauenswürdig.
""",
        "nextcloud": """NEXTCLOUD / CALDAV / CARDDAV
============================

Einrichten:
  ./scripts/mail-agent.sh nextcloud setup

Der Assistent:
  1. prüft die ClawHub-Trust-Entscheidung,
  2. installiert @keithvassallomt/openclaw-nextcloud workspace-lokal,
  3. fragt URL, Benutzer und ein separates App-Passwort mit versteckter Eingabe ab,
  4. speichert Secrets in ~/.config/mail-agent.env mit Modus 0600,
  5. lässt Kalender und Adressbuch auswählen,
  6. aktiviert calendar.backend = "nextcloud_skill",
  7. speichert lokal nur Kontakt-E-Mail-Adressen als Cache.

Diagnose und Verwaltung:
  ./scripts/mail-agent.sh nextcloud verify-skill
  ./scripts/mail-agent.sh nextcloud skill-card
  ./scripts/mail-agent.sh nextcloud install-skill --yes
  # Nur nach eigener Code-/Skill-Card-Pruefung bei Registry-Entscheidung "review":
  ./scripts/mail-agent.sh nextcloud install-skill --yes --allow-review
  ./scripts/mail-agent.sh nextcloud doctor
  ./scripts/mail-agent.sh nextcloud calendars
  ./scripts/mail-agent.sh nextcloud addressbooks
  ./scripts/mail-agent.sh nextcloud contacts --query person@example.org
  ./scripts/mail-agent.sh nextcloud sync-contacts
  ./scripts/mail-agent.sh nextcloud clear-contact-cache
  ./scripts/mail-agent.sh nextcloud disable --yes

Sichere Standardwerte:
  contacts_prevent_spam = true
  trust_contacts_for_calendar = false
  contact_importance_boost = 1

Der Community-Skill wird nur für Kalender und Kontakte verwendet. Rechnungs-PDFs
werden über eine getrennte, eingeschränkte WebDAV-Brücke gespeichert. Diese kann nur
Ordner erzeugen und neue PDF-Dateien mit Überschreibschutz hochladen; sie besitzt im
Mail-Agent-Code keine Lösch-, Verschiebe- oder Freigabefunktion.

Nach dem Setup prüfen:
  ./scripts/mail-agent.sh nextcloud doctor
  ./scripts/mail-agent.sh doctor
  ./scripts/mail-agent.sh help invoices

Für geringere Rechte ist ein eigener Nextcloud-Benutzer empfehlenswert, dem nur der
benötigte Kalender, das Adressbuch und der Rechnungsordner freigegeben werden.
""",
        "invoices": """RECHNUNGS-PDFS / NEXTCLOUD
===========================

Aktivierung erfolgt über:
  ./scripts/mail-agent.sh nextcloud setup

Automatisch archiviert wird nur, wenn alle Bedingungen erfüllt sind:
  1. Die Mail ist mit ausreichender Sicherheit als routine klassifiziert.
  2. Mindestens ein echter PDF-Anhang ist vorhanden.
  3. PDF-Dateiname, Mailinhalt und/oder strukturierte Modellantwort weisen die Datei
     mit hoher Sicherheit als Rechnung aus.
  4. Bei mehreren PDFs ist die Rechnungsdatei eindeutig bestimmbar.

Mehrdeutige Fälle werden nicht hochgeladen, sondern nach Agent/Pruefen verschoben.
Lieferscheine, AGB, Angebote, Bestellbestätigungen und Werbe-PDFs werden nicht als
Rechnung behandelt. Dateien werden per SHA-256 dedupliziert und mit If-None-Match
geschützt; vorhandene Dateien werden nicht überschrieben.

Relevante Werte in mail_agent/config.toml:
  [invoices]
  enabled = true
  require_routine = true
  min_confidence = 0.90
  nextcloud_folder = "Mail-Agent/Rechnungen"
  organize_by_year_month = true

Prüfen:
  ./scripts/mail-agent.sh test-config
  ./scripts/mail-agent.sh doctor
  ./scripts/mail-agent.sh run --dry-run --no-digest --limit 20
""",
        "openclaw": """OPENCLAW: SKILL ODER PLUGIN?
============================

Für CalDAV/CardDAV wird der Community-Skill
  @keithvassallomt/openclaw-nextcloud
verwendet. Er wird unter skills/openclaw-nextcloud installiert und über eine eng
begrenzte Python-Brücke angesprochen.

  ./scripts/mail-agent.sh nextcloud verify-skill
  ./scripts/mail-agent.sh nextcloud skill-card
  ./scripts/mail-agent.sh nextcloud install-skill --yes
  # Nur nach eigener Code-/Skill-Card-Pruefung bei Registry-Entscheidung "review":
  ./scripts/mail-agent.sh nextcloud install-skill --yes --allow-review
  openclaw skills check

Ein OpenClaw-Skill ist hier passender als ein Gateway-Plugin: Der Skill stellt die
CalDAV-/CardDAV-Befehle bereit, während die Python-Brücke exakt begrenzt, welche davon
der Mail-Agent automatisch verwenden darf. Nach der Installation mit 'skills check'
prüfen, ob der Workspace-Skill sichtbar und bereit ist.
""",
        "calendar": """KALENDER-SICHERHEIT
====================

Backends:
  queue            Nur ICS-Dateien zur Prüfung; keine externe Änderung
  nextcloud_skill  Termin über die eingeschränkte Nextcloud-Brücke anlegen
  caldav           Direkter CalDAV-PUT
  khal             Lokaler khal-Import
  command          Benutzerdefinierter Importbefehl

Der Agent kann Termine sowohl aus einem ICS-Anhang als auch aus dem Textkontext einer
Mail erkennen. Bei ausreichender Mail- und Terminkonfidenz sendet er zuerst eine
Freigabemail an calendar.approval_recipient. Es wird noch nichts eingetragen.

Antwortablauf:
  - auf die Freigabemail antworten; Reply-To zeigt auf das verwaltete Postfach
  - erste nichtleere Zeile exakt JA oder NEIN
  - Antwort muss von calendar.approval_reply_from kommen
  - beim nächsten Lauf prüft der Agent Token, Absender, Ablaufzeit und Startdatum
  - nur JA und ein weiterhin zukünftiger Termin führen zu calendar create

Termine in der Vergangenheit werden weder angefragt noch eingetragen. Eine Freigabe
läuft standardmäßig nach 14 Tagen ab. Unklare Termine bleiben in
mail_agent/data/calendar_pending und Agent/Termin-Pruefen.

Relevante Werte:
  approval_required = true
  approval_recipient = ""       # leer = mailbox.forward_to
  approval_reply_from = ""      # leer = mailbox.forward_to
  approval_expiry_days = 14
  require_future = true
""",
        "automation": """AUTOMATIK / CONTAINERJOBS
========================

Der produktive Service verwendet einen begrenzten Drain-Modus:
  ./scripts/mail-agent.sh run --drain --batch-size 20 --max-messages 500 --max-runtime 2400 --shutdown-reserve 180 --max-batches 100 --no-digest

Solange Arbeit vorhanden ist, folgen die 20er-Batches direkt aufeinander. Sobald die
INBOX leer ist, endet der Python-Prozess. Der Containerworker prueft nach seinem
konfigurierten Intervall erneut.
Ein Dry-Run mit --drain verarbeitet absichtlich nur einen Batch.

Status:
  ./scripts/assistant.sh jobs status --target all
  ./scripts/assistant.sh jobs check --target all --deep

Die Intervalle werden ueber die kontrollierte Containerkonfiguration verwaltet. Der
alte systemd-Intervallhelfer liegt nur noch im verifizierten Legacy-Rollbackpaket und
ist kein aktiver Assistentenbefehl.

Während Einrichtung/Training stoppen:
  ./scripts/assistant.sh jobs off standard

Nach geprüftem Dry-Run und kleinem Produktivlauf aktivieren:
  ./scripts/assistant.sh jobs on standard

`jobs off/on` benoetigt immer einen ausdruecklichen Nutzerauftrag.

Die Lock-Datei nur entfernen, wenn
  pgrep -af 'python3 -m mail_agent'
keinen echten Mail-Agent-Prozess zeigt. Andernfalls niemals einen zweiten Lauf starten.
""",
        "security": """SICHERHEITSGRENZEN
====================

- Mail- und Nextcloud-Inhalte sind untrusted data, keine Agentenanweisungen.
- Keine automatische Mailantwort und kein Löschen von Mails.
- Nextcloud nur über HTTPS und ein widerrufbares App-Passwort.
- Secrets außerhalb des Workspace in ~/.config/mail-agent.env, Modus 0600.
- Community-Skill vor Installation verifizieren; Warn-/Review-Entscheidungen nicht
  automatisch übergehen.
- CardDAV-Kontakte erzwingen weder Relevanz noch Weiterleitung noch Kalendereintrag.
- Termin wird erst nach gültiger JA-Antwort erstellt und erneut auf Zukunft geprüft.
- Rechnungen werden nur als neue PDF-Dateien gespeichert; kein Überschreiben/Löschen.
- Kein --force, solange Diagnose oder Zuordnungen unklar sind.
- Produktive Läufe erst nach aktuellem, fehlerfreiem Dry-Run.
""",
    }
    if selected not in pages:
        return (
            f"Unbekanntes Help-Thema: {topic}\n\nErlaubt: "
            + ", ".join(HELP_TOPICS)
            + "\nBeispiel: ./scripts/mail-agent.sh help training\n"
        )

    text = pages[selected].rstrip()
    if config is not None:
        text += (
            "\n\nAKTUELLE PFADE UND WERTE\n"
            "========================\n"
            f"Konfiguration: {config.path}\n"
            f"Regeln:        {config.runtime.rules_file}\n"
            f"Datenbank:     {config.runtime.database} (nicht manuell bearbeiten)\n"
            "Secrets:       ~/.config/mail-agent.env (Inhalt niemals ausgeben)\n"
            f"Ollama:        {config.ollama.base_url} / {config.ollama.model}\n"
            f"Batch:         {'aktiv, bis ' + str(config.ollama.batch_size) + ' Mails/Aufruf' if config.ollama.batch_enabled else 'deaktiviert'}; Einzelmail bleibt Einzelaufruf\n"
            f"Weiterleitung: {config.mailbox.forward_to}\n"
            f"Kalender:      {config.calendar.backend}; Freigabe per Mail {'aktiv' if config.calendar.approval_required else 'aus'}\n"
            f"Rechnungen:    {'aktiv -> ' + config.invoices.nextcloud_folder if config.invoices.enabled else 'deaktiviert'}\n"
            f"Nextcloud:     {'aktiv' if config.nextcloud.enabled else 'deaktiviert'}"
        )
    return text + "\n"

def build_guide(config: Config, checks: dict[str, object]) -> str:
    lines: list[str] = [
        "MAIL-AGENT EINRICHTUNGSASSISTENT",
        "================================",
        "",
        "Aktuelle Konfiguration:",
        f"  Ollama:        {config.ollama.base_url}",
        f"  Modell:        {config.ollama.model}",
        f"  Batch:         {'aktiv, bis ' + str(config.ollama.batch_size) + ' Mails/Aufruf' if config.ollama.batch_enabled else 'deaktiviert'}",
        f"  Weiter an:     {config.mailbox.forward_to}",
        f"  Nextcloud:     {'aktiv' if config.nextcloud.enabled else 'deaktiviert'}",
        f"  Rechnungen:    {'aktiv -> ' + config.invoices.nextcloud_folder if config.invoices.enabled else 'deaktiviert'}",
        f"  Terminfreigabe:{' per Mail' if config.calendar.approval_required else ' deaktiviert'}",
        f"  Konfiguration: {config.path}",
        "",
        "Pruefergebnis:",
    ]

    labels = {
        "himalaya": "Himalaya",
        "folders": "Mailordner",
        "ollama": "Ollama/Modell",
        "database": "Datenbank",
        "config": "Konfiguration",
        "calendar": "Kalender",
        "nextcloud": "Nextcloud/Contacts",
        "invoices": "Nextcloud/Rechnungsarchiv",
    }
    for key in ("config", "himalaya", "folders", "ollama", "database", "nextcloud", "invoices", "calendar"):
        value = checks.get(key, {})
        ok = bool(value.get("ok")) if isinstance(value, dict) else False
        if key == "nextcloud" and not config.nextcloud.enabled or key == "invoices" and not config.invoices.enabled:
            marker = "AUS"
        else:
            marker = "OK" if ok else ("OPTIONAL" if key in {"calendar", "nextcloud", "invoices"} else "FEHLT")
        detail = ""
        if isinstance(value, dict):
            detail_value = value.get("detail") or value.get("error")
            missing = value.get("missing") or value.get("missing_environment")
            if missing:
                detail_value = "fehlend: " + ", ".join(str(item) for item in missing)
            if detail_value:
                detail = f" - {detail_value}"
        lines.append(f"  [{marker:8}] {labels[key]}{detail}")

    job = job_information()
    lock = lock_information(config)
    state = read_setup_state(config)
    dry_run_current = bool(
        state.get("last_dry_run_ok")
        and state.get("config_fingerprint") == configuration_fingerprint(config)
    )
    lines.extend([
        "",
        "Automatik:",
        f"  Mailjob Soll:  {job['desired']}",
        f"  Mailjob Ist:   {job['state']}",
        f"  Lauf aktiv:    {'ja, PID ' + str(lock['pid']) if lock['active'] else 'nein'}",
        f"  Letzter Dry-Run: {state.get('last_dry_run_at', 'noch keiner')}"
        + (" (gueltig und erfolgreich)" if dry_run_current else (" (veraltet)" if state.get("last_dry_run_ok") else "")),
        "",
        "Was du jetzt tun musst:",
    ])

    steps: list[str] = []
    himalaya = checks.get("himalaya", {})
    folders = checks.get("folders", {})
    ollama = checks.get("ollama", {})
    database = checks.get("database", {})
    nextcloud = checks.get("nextcloud", {})
    invoices = checks.get("invoices", {})

    if lock.get("active"):
        steps.append(
            "Es laeuft bereits ein Agent-Prozess. Nicht parallel starten. Status pruefen mit:\n"
            f"     ps -o pid,etime,cmd -p {lock['pid']}"
        )
    if job.get("desired") == "on":
        steps.append(
            "Waehren der Einrichtung den Mailjob nach ausdruecklicher Freigabe stoppen:\n"
            "     ./scripts/assistant.sh jobs off standard"
        )
    if isinstance(himalaya, dict) and not himalaya.get("ok"):
        steps.append("Himalaya installieren oder mailbox.himalaya_binary in mail_agent/config.toml korrigieren.")
    if isinstance(folders, dict) and not folders.get("ok"):
        steps.append(
            "Agent-Ordner anlegen:\n"
            "     ./scripts/mail-agent.sh setup --dry-run\n"
            "     ./scripts/mail-agent.sh setup"
        )
    if isinstance(ollama, dict) and not ollama.get("ok"):
        steps.append(
            "Ollama-Adresse oder Modell korrigieren:\n"
            "     ./scripts/mail-agent.sh configure"
        )
    if isinstance(database, dict) and not database.get("ok"):
        steps.append("Lokale Datenbank vorbereiten:\n     ./scripts/mail-agent.sh setup")
    if config.nextcloud.enabled and isinstance(nextcloud, dict) and not nextcloud.get("ok"):
        steps.append(
            "Nextcloud-Verbindung reparieren/pruefen:\n"
            "     ./scripts/mail-agent.sh nextcloud setup\n"
            "     ./scripts/mail-agent.sh nextcloud doctor"
        )
    elif not config.nextcloud.enabled:
        steps.append(
            "Optional CalDAV/CardDAV und Rechnungsarchiv mit Nextcloud einrichten:\n"
            "     ./scripts/mail-agent.sh nextcloud setup"
        )
    if config.invoices.enabled and isinstance(invoices, dict) and not invoices.get("ok"):
        steps.append(
            "Nextcloud-Rechnungsarchiv reparieren/pruefen:\n"
            "     ./scripts/mail-agent.sh nextcloud doctor\n"
            "     ./scripts/mail-agent.sh help invoices"
        )

    required_for_run = ["config", "himalaya", "folders", "ollama", "database"]
    if config.invoices.enabled:
        required_for_run.append("invoices")
    if config.nextcloud.enabled and config.calendar.backend == "nextcloud_skill":
        required_for_run.append("calendar")
    core_ready = all(
        isinstance(checks.get(name), dict) and bool(checks[name].get("ok"))
        for name in required_for_run
    )
    if core_ready and not dry_run_current and not lock.get("active"):
        steps.append(
            "Sicheren Testlauf starten:\n"
            "     ./scripts/mail-agent.sh run --dry-run --no-digest --limit 20"
        )
    elif core_ready and dry_run_current and not lock.get("active"):
        steps.append(
            "Pflichtpruefungen sind bestanden. Klein produktiv testen:\n"
            "     ./scripts/mail-agent.sh run --no-digest --limit 10\n"
            "Danach:\n"
            "     ./scripts/mail-agent.sh status"
        )

    calendar = checks.get("calendar", {})
    if isinstance(calendar, dict) and not calendar.get("ok"):
        steps.append(
            "Kalender bleibt sicher im Pruefmodus. ICS-Dateien liegen unter\n"
            f"     {config.calendar.pending_dir}\n"
            "Details:\n"
            "     ./scripts/mail-agent.sh help calendar"
        )

    if not steps:
        steps.append("Keine Aktion erforderlich. Der Agent ist betriebsbereit.")
    for index, step in enumerate(steps, 1):
        lines.append(f"  {index}. {step}")

    lines.extend([
        "",
        "Help-Modus:",
        "  ./scripts/mail-agent.sh help",
        "  ./scripts/mail-agent.sh help files",
        "  ./scripts/mail-agent.sh help performance",
        "  ./scripts/mail-agent.sh help training",
        "  ./scripts/mail-agent.sh help nextcloud",
        "  ./scripts/mail-agent.sh help invoices",
        "  ./scripts/mail-agent.sh help calendar",
    ])
    return "\n".join(lines)


def interactive_configure(config: Config) -> tuple[Config, Path]:
    print("MAIL-AGENT KONFIGURATION")
    print("========================")
    print("Enter behaelt den angezeigten Wert. Vor jeder Aenderung wird eine Sicherung erstellt.\n")

    changes: dict[tuple[str, str], object] = {}
    base_url = _prompt("Ollama-Adresse", config.ollama.base_url).rstrip("/")
    changes[("ollama", "base_url")] = base_url

    models, error = ollama_models(base_url)
    model = config.ollama.model
    if models:
        print("\nGefundene Ollama-Modelle:")
        for index, name in enumerate(models, 1):
            current = " (aktuell)" if name == model else ""
            print(f"  {index}. {name}{current}")
        selection = input(f"Modell waehlen [Name oder Nummer, aktuell: {model}]: ").strip()
        if selection:
            if selection.isdigit() and 1 <= int(selection) <= len(models):
                model = models[int(selection) - 1]
            elif selection in models:
                model = selection
            else:
                print("Auswahl nicht in der Modellliste; der eingegebene Name wird trotzdem gespeichert.")
                model = selection
    else:
        print(f"\nOllama konnte noch nicht abgefragt werden: {error}")
        model = _prompt("Ollama-Modell", model)
    changes[("ollama", "model")] = model

    changes[("mailbox", "from_header")] = _prompt("Absender fuer Weiterleitungen", config.mailbox.from_header)
    changes[("mailbox", "forward_to")] = _prompt("Zweite Mailadresse fuer wichtige Mails", config.mailbox.forward_to)

    if _yes_no("Empfohlene sichere Lernphase-Werte setzen", default=True):
        changes.update(RECOMMENDED_VALUES)

    if _yes_no("Tageszusammenfassung waehrend der Testphase deaktivieren", default=True):
        changes[("digest", "enabled")] = False

    print("\nKalender kann spaeter mit './scripts/mail-agent.sh nextcloud setup' verbunden werden.")
    if _yes_no("Kalender bis dahin nur als sichere ICS-Pruefliste verwenden", default=True):
        changes[("calendar", "backend")] = "queue"

    backup = update_toml_values(config.path, changes)
    try:
        updated = load_config(config.path)
    except Exception:
        shutil.copy2(backup, config.path)
        raise
    print(f"\nKonfiguration gespeichert. Sicherung: {backup}")
    return updated, backup
