from __future__ import annotations

import tomllib
import re
from pathlib import Path
from typing import Any

from .models import PolicyDecision
from .registry import ResourceRegistry


DEFAULT_DENIED_ACTIONS = {
    "files.delete",
    "files.overwrite",
    "files.share",
    "contacts.write",
    "contacts.delete",
    "calendar.delete",
    "tasks.delete",
    "deck.card.delete",
    "deck.board.delete",
    "deck.stack.delete",
    "deck.share",
    "mail.delete",
    "mail.expunge",
    "mail.folder.delete",
    "plugins.install",
    "code.modify",
    "security.disable",
    "audit.disable",
}


class PolicyEngine:
    _MANAGED_INVOICE_REGISTER = re.compile(r"(?:^|/)(20\d{2}|21\d{2})/Rechnungen_\1\.csv$")
    def __init__(self, path: Path, registry: ResourceRegistry) -> None:
        self.path = path
        self.registry = registry
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.data = {}
            return
        with self.path.open("rb") as handle:
            self.data = tomllib.load(handle)

    @staticmethod
    def _path_in_roots(path: str, roots: tuple[str, ...]) -> bool:
        return bool(path) and (not roots or any(path == root or path.startswith(root + "/") for root in roots))

    def decide(self, resource_id: str, action: str, payload: dict[str, Any] | None = None) -> PolicyDecision:
        payload = payload or {}
        resource = self.registry.get(resource_id)
        if not resource.enabled:
            return PolicyDecision(False, False, "Ressource ist deaktiviert")
        denied = set(str(v) for v in self.data.get("deny", {}).get("actions", [])) | DEFAULT_DENIED_ACTIONS
        if action in denied:
            return PolicyDecision(False, False, f"Aktion {action} ist durch die Sicherheitsrichtlinie verboten")
        permission = self._permission_for_action(action)
        if permission and permission not in resource.permissions:
            return PolicyDecision(False, False, f"Ressource {resource_id} besitzt nicht die Berechtigung {permission}")

        roots = tuple(str(v).strip("/") for v in resource.metadata.get("allowed_roots", []) if str(v).strip("/"))
        if action in {"files.create", "files.mkdir"}:
            path = str(payload.get("path") or "").replace("\\", "/").strip("/")
            if not self._path_in_roots(path, roots):
                return PolicyDecision(False, False, f"Zielpfad liegt ausserhalb der erlaubten Wurzeln: {path}")
            if bool(payload.get("overwrite")):
                managed_register = bool(payload.get("managed_invoice_register"))
                expected_year = str(payload.get("year") or "")
                expected_name = f"Rechnungen_{expected_year}.csv" if expected_year else ""
                if not (
                    action == "files.create"
                    and managed_register
                    and expected_year.isdigit()
                    and 2000 <= int(expected_year) <= 2100
                    and path.endswith("/" + expected_year + "/" + expected_name)
                    and self._MANAGED_INVOICE_REGISTER.search(path)
                    and str(payload.get("content_type") or "").startswith("text/csv")
                    and len(str(payload.get("sha256") or "")) == 64
                ):
                    return PolicyDecision(False, False, "Ueberschreiben bestehender Dateien ist verboten")
        if action == "files.move":
            source = str(payload.get("source") or "").replace("\\", "/").strip("/")
            destination = str(payload.get("destination") or "").replace("\\", "/").strip("/")
            if not self._path_in_roots(source, roots):
                return PolicyDecision(False, False, f"Quellpfad liegt ausserhalb der erlaubten Wurzeln: {source}")
            if not self._path_in_roots(destination, roots):
                return PolicyDecision(False, False, f"Zielpfad liegt ausserhalb der erlaubten Wurzeln: {destination}")
            if source == destination:
                return PolicyDecision(False, False, "Quelle und Ziel sind identisch")
            if bool(payload.get("overwrite")):
                return PolicyDecision(False, False, "Ueberschreiben bestehender Dateien ist verboten")

        if action.startswith("deck.card.") and not bool(payload.get("managed")):
            return PolicyDecision(False, False, "Nur vom Personal Assistant verwaltete Bestellkarten duerfen geaendert werden")
        approval_actions = set(str(v) for v in self.data.get("approval", {}).get("actions", []))
        # Existing contact changes are destructive in the sense that they
        # replace selected fields. They always require the narrow explicit
        # approval path, even when an older policies.toml has not yet listed
        # contacts.update.
        requires = action in approval_actions or action in {"contacts.update", "calendar.update", "tasks.update"}
        return PolicyDecision(True, requires, "Aktion ist innerhalb der konfigurierten Rechte erlaubt")

    @staticmethod
    def _permission_for_action(action: str) -> str:
        mapping = {
            "files.read": "read",
            "files.create": "create",
            "files.mkdir": "create",
            "files.move": "organize",
            "contacts.read": "read",
            "contacts.create": "create",
            "contacts.update": "update",
            "calendar.read": "read",
            "calendar.create": "create",
            "calendar.update": "update",
            "tasks.read": "read",
            "tasks.create": "create",
            "tasks.update": "update",
            "deck.read": "read",
            "deck.card.create": "create",
            "deck.card.update": "update",
            "deck.card.move": "move",
            "mail.read": "read",
            "mail.move": "move",
            "mail.send": "forward",
            "settings.safe_update": "configure",
        }
        return mapping.get(action, "")
