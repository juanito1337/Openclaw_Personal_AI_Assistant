from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .models import ParsedMessage
from .utils import now_utc_iso, safe_filename


_SCHEMA_VERSION = 1
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LABEL_RE = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class LearningFolder:
    folder: str
    parent: str
    verdict: str
    label: str
    active: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LearningFolderRegistry:
    """Persistent, controlled registry for correction subfolders.

    Only one level below one of the configured correction roots is allowed. The
    registry never deletes IMAP folders; disabling a mapping only stops consuming
    it as training input.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.runtime.learning_folders_file

    def _parents(self) -> dict[str, tuple[str, str]]:
        return {
            "routine": (self.config.folders.feedback_unimportant, "routine"),
            "unimportant": (self.config.folders.feedback_unimportant, "routine"),
            "important": (self.config.folders.feedback_important, "relevant"),
            "relevant": (self.config.folders.feedback_important, "relevant"),
            "spam": (self.config.folders.feedback_spam, "spam"),
            "not-spam": (self.config.folders.feedback_not_spam, "not_spam"),
            "not_spam": (self.config.folders.feedback_not_spam, "not_spam"),
        }

    @staticmethod
    def _safe_name(value: str) -> str:
        name = " ".join((value or "").strip().split())
        if not name or len(name) > 80:
            raise ValueError("Ordnername muss 1 bis 80 Zeichen lang sein")
        if "/" in name or "\\" in name or _CONTROL_RE.search(name):
            raise ValueError("Ordnername darf keine Pfadtrenner oder Steuerzeichen enthalten")
        if name in {".", ".."}:
            raise ValueError("Ungueltiger Ordnername")
        return name

    @staticmethod
    def _safe_label(value: str, fallback: str) -> str:
        raw = (value or fallback).strip().casefold().replace(" ", "-")
        label = _LABEL_RE.sub("-", raw).strip("-_")
        if not label:
            label = safe_filename(fallback, "mailtyp").casefold()
        return label[:80]

    def _empty(self) -> dict[str, Any]:
        return {"schema_version": _SCHEMA_VERSION, "folders": []}

    def _load_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Lernordner-Datei ist ungueltig: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("Lernordner-Datei hat eine nicht unterstuetzte Version")
        if not isinstance(payload.get("folders"), list):
            raise ValueError("Lernordner-Datei enthaelt keine gueltige Ordnerliste")
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(self.path)
        self.path.chmod(0o600)

    def list(self, *, active_only: bool = False) -> list[LearningFolder]:
        payload = self._load_payload()
        result: list[LearningFolder] = []
        for raw in payload["folders"]:
            if not isinstance(raw, dict):
                continue
            try:
                item = LearningFolder(
                    folder=str(raw.get("folder") or ""),
                    parent=str(raw.get("parent") or ""),
                    verdict=str(raw.get("verdict") or ""),
                    label=str(raw.get("label") or ""),
                    active=bool(raw.get("active", True)),
                    created_at=str(raw.get("created_at") or ""),
                )
            except (TypeError, ValueError):
                continue
            if not item.folder or not item.verdict:
                continue
            if active_only and not item.active:
                continue
            result.append(item)
        return result

    def create(self, *, parent: str, name: str, label: str = "") -> LearningFolder:
        alias = (parent or "").strip().casefold()
        parents = self._parents()
        if alias not in parents:
            raise ValueError("parent muss routine, important, spam oder not-spam sein")
        parent_folder, verdict = parents[alias]
        safe_name = self._safe_name(name)
        folder = f"{parent_folder}/{safe_name}"
        safe_label = self._safe_label(label, safe_name)
        payload = self._load_payload()
        rows = payload["folders"]
        for raw in rows:
            if isinstance(raw, dict) and str(raw.get("folder") or "").casefold() == folder.casefold():
                if bool(raw.get("active", True)):
                    return LearningFolder(
                        folder=folder,
                        parent=parent_folder,
                        verdict=str(raw.get("verdict") or verdict),
                        label=str(raw.get("label") or safe_label),
                        active=True,
                        created_at=str(raw.get("created_at") or ""),
                    )
                raw.update({"active": True, "verdict": verdict, "label": safe_label})
                self._write_payload(payload)
                return LearningFolder(folder, parent_folder, verdict, safe_label, True, str(raw.get("created_at") or ""))
        item = LearningFolder(folder, parent_folder, verdict, safe_label, True, now_utc_iso())
        rows.append(item.to_dict())
        self._write_payload(payload)
        return item

    def disable(self, folder: str) -> bool:
        target = (folder or "").strip().casefold()
        payload = self._load_payload()
        changed = False
        for raw in payload["folders"]:
            if isinstance(raw, dict) and str(raw.get("folder") or "").casefold() == target:
                if bool(raw.get("active", True)):
                    raw["active"] = False
                    changed = True
                break
        if changed:
            self._write_payload(payload)
        return changed

    def active_folders(self) -> list[str]:
        return [item.folder for item in self.list(active_only=True)]

    def feedback_mappings(self) -> list[tuple[str, str, str]]:
        return [(item.folder, item.verdict, item.label) for item in self.list(active_only=True)]

    def resolve(self, folder: str) -> LearningFolder | None:
        folded = (folder or "").casefold()
        for item in self.list(active_only=True):
            if item.folder.casefold() == folded:
                return item
        return None


def message_feature_metadata(message: ParsedMessage) -> dict[str, Any]:
    attachment_types: list[str] = []
    for item in message.attachments:
        filename = (item.filename or "").casefold()
        suffix = Path(filename).suffix.lstrip(".")
        content_type = (item.content_type or "").casefold()
        token = suffix or content_type.split("/", 1)[-1]
        if token and token not in attachment_types:
            attachment_types.append(token[:40])
    return {
        "attachment_count": len(message.attachments),
        "attachment_types": sorted(attachment_types)[:12],
        "calendar_invite": bool(message.calendar_invites),
        "recipient_count": len(message.recipients),
    }
