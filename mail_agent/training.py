from __future__ import annotations

import json
import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from .storage import Storage
from .utils import normalize_address


RULE_KEYS: dict[str, tuple[str, ...]] = {
    "spam": ("addresses", "domains", "sender_names", "subject_phrases"),
    "important": ("addresses", "domains"),
    "routine": ("addresses", "domains"),
}

KIND_ALIASES = {
    "address": "addresses",
    "addresses": "addresses",
    "adresse": "addresses",
    "domain": "domains",
    "domains": "domains",
    "sender-name": "sender_names",
    "sender_name": "sender_names",
    "sender_names": "sender_names",
    "absendername": "sender_names",
    "subject-phrase": "subject_phrases",
    "subject_phrase": "subject_phrases",
    "subject_phrases": "subject_phrases",
    "betreff": "subject_phrases",
}


@dataclass(slots=True)
class RuleChange:
    changed: bool
    category: str
    kind: str
    value: str
    backup: Path | None = None


class TrainingManager:
    """Safe management surface for rule-based and feedback-based learning.

    The underlying Ollama model is not fine-tuned. The mail agent learns from
    explicit rule lists and correction records. This class deliberately avoids
    exposing raw mail bodies and always creates a backup before rewriting rules.
    """

    def __init__(self, rules_path: Path, storage: Storage) -> None:
        self.rules_path = rules_path
        self.storage = storage

    @staticmethod
    def _normalize_category(value: str) -> str:
        category = (value or "").strip().casefold()
        if category not in RULE_KEYS:
            allowed = ", ".join(sorted(RULE_KEYS))
            raise ValueError(f"Unbekannte Kategorie {value!r}; erlaubt: {allowed}")
        return category

    @staticmethod
    def _normalize_kind(category: str, value: str) -> str:
        kind = KIND_ALIASES.get((value or "").strip().casefold())
        if not kind or kind not in RULE_KEYS[category]:
            allowed = ", ".join(RULE_KEYS[category])
            raise ValueError(f"Regeltyp {value!r} ist fuer {category} nicht erlaubt; erlaubt: {allowed}")
        return kind

    @staticmethod
    def _normalize_value(kind: str, value: str) -> str:
        result = " ".join((value or "").strip().split()).casefold()
        if not result:
            raise ValueError("Regelwert darf nicht leer sein")
        if "\r" in result or "\n" in result:
            raise ValueError("Regelwert darf keine Zeilenumbrueche enthalten")
        if kind == "addresses":
            parsed = normalize_address(parseaddr(result)[1] or result)
            if "@" not in parsed or parsed.startswith("@") or parsed.endswith("@"):
                raise ValueError(f"Keine gueltige E-Mail-Adresse: {value!r}")
            return parsed
        if kind == "domains":
            result = result.removeprefix("https://").removeprefix("http://").strip("./@")
            if "/" in result or "@" in result or "." not in result:
                raise ValueError(f"Keine gueltige Domain: {value!r}")
            return result
        if len(result) > 300:
            raise ValueError("Regelwert ist zu lang (maximal 300 Zeichen)")
        return result

    def _load(self) -> dict[str, dict[str, list[str]]]:
        try:
            with self.rules_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except FileNotFoundError:
            raw = {}
        result: dict[str, dict[str, list[str]]] = {}
        for category, keys in RULE_KEYS.items():
            section = raw.get(category, {})
            if not isinstance(section, dict):
                section = {}
            result[category] = {}
            for key in keys:
                values = section.get(key, [])
                if not isinstance(values, list):
                    raise ValueError(f"{category}.{key} muss in rules.toml eine Liste sein")
                normalized: list[str] = []
                for item in values:
                    text = str(item).strip().casefold()
                    if text and text not in normalized:
                        normalized.append(text)
                result[category][key] = normalized
        return result

    @staticmethod
    def _toml_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def _write(self, data: dict[str, dict[str, list[str]]]) -> Path | None:
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if self.rules_path.exists():
            backup_dir = self.storage.path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / (
                self.rules_path.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            )
            shutil.copy2(self.rules_path, backup)
            backup.chmod(0o600)
            for stale in sorted(backup_dir.glob(self.rules_path.name + ".backup-*"))[:-20]:
                stale.unlink(missing_ok=True)

        lines = [
            "# Explizite Regeln haben Vorrang vor dem Modell.",
            "# Aenderungen bevorzugt mit ./scripts/mail-agent.sh training rule-add/rule-remove vornehmen.",
            "# Nutzerkorrekturen aus den IMAP-Ordnern werden getrennt in SQLite gelernt.",
            "",
        ]
        comments = {
            "spam": "# Klare Werbung, Newsletter, unerwuenschte Akquise oder bekannte Spam-Muster.",
            "important": (
                "# Wichtige Absender. Diese Liste ist zugleich ein Vertrauenssignal fuer bestaetigte Termine."
            ),
            "routine": "# Legitime automatische Nachrichten ohne unmittelbaren Handlungsbedarf.",
        }
        for category in ("spam", "important", "routine"):
            lines.extend([comments[category], f"[{category}]"])
            for key in RULE_KEYS[category]:
                values = sorted(set(data[category][key]))
                lines.append(f"{key} = [")
                lines.extend(f"  {self._toml_string(item)}," for item in values)
                lines.append("]")
            lines.append("")

        temp = self.rules_path.with_suffix(self.rules_path.suffix + ".tmp")
        temp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.rules_path)
        self.rules_path.chmod(0o600)
        return backup

    def rules(self) -> dict[str, dict[str, list[str]]]:
        return self._load()

    def add_rule(self, category: str, kind: str, value: str) -> RuleChange:
        normalized_category = self._normalize_category(category)
        normalized_kind = self._normalize_kind(normalized_category, kind)
        normalized_value = self._normalize_value(normalized_kind, value)
        data = self._load()
        values = data[normalized_category][normalized_kind]
        if normalized_value in values:
            return RuleChange(False, normalized_category, normalized_kind, normalized_value)
        values.append(normalized_value)
        backup = self._write(data)
        return RuleChange(True, normalized_category, normalized_kind, normalized_value, backup)

    def remove_rule(self, category: str, kind: str, value: str) -> RuleChange:
        normalized_category = self._normalize_category(category)
        normalized_kind = self._normalize_kind(normalized_category, kind)
        normalized_value = self._normalize_value(normalized_kind, value)
        data = self._load()
        values = data[normalized_category][normalized_kind]
        if normalized_value not in values:
            return RuleChange(False, normalized_category, normalized_kind, normalized_value)
        values.remove(normalized_value)
        backup = self._write(data)
        return RuleChange(True, normalized_category, normalized_kind, normalized_value, backup)

    def status(self) -> dict[str, Any]:
        rules = self._load()
        return {
            "mode": "rules-plus-feedback-not-model-finetuning",
            "rules_file": str(self.rules_path),
            "rule_counts": {
                category: {kind: len(values) for kind, values in section.items()}
                for category, section in rules.items()
            },
            "feedback_counts": self.storage.feedback_summary(),
            "feedback_database": str(self.storage.path),
        }

    def export(self, output: Path, *, feedback_limit: int = 5000) -> Path:
        payload = {
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "explanation": (
                "Der Agent wird nicht feinabgestimmt. Dieses Exportpaket enthaelt harte Regeln und "
                "explizite Nutzerkorrekturen ohne Mailtext oder Anhaenge."
            ),
            "rules": self._load(),
            "feedback_summary": self.storage.feedback_summary(),
            "feedback": self.storage.list_feedback(limit=feedback_limit),
        }
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(output)
        output.chmod(0o600)
        return output
