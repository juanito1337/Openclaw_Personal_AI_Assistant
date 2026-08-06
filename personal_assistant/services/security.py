from __future__ import annotations

from pathlib import Path
from typing import Any

from personal_assistant.tool_registry import build_tool_registry


class SecurityApplicationMixin:
    antivirus: Any
    tool_settings: Any

    def antivirus_doctor(self, *, live_scan: bool = True) -> dict[str, Any]:
        return self.antivirus.doctor(live_scan=live_scan)

    def antivirus_self_test(self) -> dict[str, Any]:
        return self.antivirus.self_test()

    def antivirus_scan_path(self, path: str | Path, *, use_cache: bool = True) -> dict[str, Any]:
        candidate = Path(path).expanduser().resolve()
        outbox = self.tool_settings.nextcloud.workspace.outbox.expanduser().resolve()
        try:
            candidate.relative_to(outbox)
        except ValueError as exc:
            raise PermissionError(
                f"Manueller Agenten-Scan ist nur innerhalb der kontrollierten Outbox erlaubt: {outbox}"
            ) from exc
        return self.antivirus.scan_path(candidate, source_type="agent-manual", use_cache=use_cache).to_dict()

    def tools(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in build_tool_registry(self.tool_settings)]
