from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .forwarding import Forwarder
from .models import OperationResult
from .storage import Storage
from .utils import clean_single_line


class DigestManager:
    def __init__(self, config: Config, storage: Storage, forwarder: Forwarder, dry_run: bool = False) -> None:
        self.config = config
        self.storage = storage
        self.forwarder = forwarder
        self.dry_run = dry_run

    def send_if_due(self, force: bool = False) -> OperationResult:
        if not self.config.digest.enabled and not force:
            return OperationResult(True, "digest-disabled")
        now = datetime.now(ZoneInfo(self.config.calendar.timezone))
        day = now.date().isoformat()
        if not force and now.hour < self.config.digest.hour_local:
            return OperationResult(True, "digest-not-due")
        if not force and self.storage.digest_sent(day):
            return OperationResult(True, "digest-already-sent")
        zone = ZoneInfo(self.config.calendar.timezone)
        local_start = datetime.combine(now.date(), time.min, tzinfo=zone)
        local_end = local_start + timedelta(days=1)
        rows = self.storage.digest_rows(
            local_start.astimezone(UTC).isoformat(timespec="seconds"),
            local_end.astimezone(UTC).isoformat(timespec="seconds"),
        )
        events = self.storage.digest_events(
            local_start.astimezone(UTC).isoformat(timespec="seconds"),
            local_end.astimezone(UTC).isoformat(timespec="seconds"),
        )
        if len(rows) + len(events) < self.config.digest.min_items and not force:
            return OperationResult(True, "digest-empty")
        body = self._render(day, rows, events)
        subject = self.config.digest.subject.format(date=day)
        result = self.forwarder.send_plain(subject, body)
        if not self.dry_run:
            self.storage.mark_digest(day, "sent" if result.ok else "error", result.detail)
        return result

    @staticmethod
    def _render(
        day: str,
        rows: list[dict[str, object]],
        events: list[dict[str, object]] | None = None,
    ) -> str:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get("status") or "unknown")].append(row)
        labels = [
            ("forwarded", "Wichtig / weitergeleitet"),
            ("appointment-review", "Termine zur Pruefung"),
            ("review", "Unsicher / manuell pruefen"),
            ("routine", "Routine"),
            ("spam", "Spam"),
            ("error", "Fehler"),
        ]
        lines = [f"Mail-Tagesuebersicht fuer {day}", ""]
        event_rows = events or []
        if event_rows:
            lines.append(f"Termine ({len(event_rows)}):")
            for event in event_rows[:30]:
                title = clean_single_line(str(event.get("title") or "(ohne Titel)"), 250)
                start = clean_single_line(str(event.get("starts_at") or "Zeit unbekannt"), 120)
                status = clean_single_line(str(event.get("status") or "unbekannt"), 80)
                lines.append(f"- [{status}] {start} - {title}")
            if len(event_rows) > 30:
                lines.append(f"- ... weitere {len(event_rows) - 30}")
            lines.append("")
        for status, heading in labels:
            items = grouped.get(status, [])
            if not items:
                continue
            lines.append(f"{heading} ({len(items)}):")
            for item in items[:30]:
                sender = item.get("sender_name") or item.get("sender_addr") or "Unbekannt"
                subject = clean_single_line(str(item.get("subject") or "(ohne Betreff)"), 250)
                reason = clean_single_line(str(item.get("reason") or ""), 300)
                importance = item.get("importance") or "-"
                line = f"- [{importance}/10] {sender}: {subject}"
                if reason:
                    line += f" — {reason}"
                lines.append(line)
            if len(items) > 30:
                lines.append(f"- ... weitere {len(items) - 30}")
            lines.append("")

        known_statuses = {status for status, _ in labels}
        technical_items = [
            item
            for status, items in grouped.items()
            if status not in known_statuses
            for item in items
        ]
        if technical_items:
            lines.append(f"Technische Zwischen-/Fehlerzustaende ({len(technical_items)}):")
            for item in technical_items[:30]:
                sender = item.get("sender_name") or item.get("sender_addr") or "Unbekannt"
                subject = clean_single_line(str(item.get("subject") or "(ohne Betreff)"), 250)
                status = clean_single_line(str(item.get("status") or "unknown"), 80)
                error = clean_single_line(str(item.get("last_error") or ""), 300)
                line = f"- [{status}] {sender}: {subject}"
                if error:
                    line += f" — {error}"
                lines.append(line)
            if len(technical_items) > 30:
                lines.append(f"- ... weitere {len(technical_items) - 30}")
            lines.append("")
        if len(lines) == 2:
            lines.append("Heute wurden keine Mails verarbeitet.")
        lines += [
            "Korrekturen erfolgen durch Verschieben der Originalmail in einen Agent/Korrektur-Ordner.",
            "Der Agent loescht und beantwortet keine Mails automatisch.",
        ]
        return "\n".join(lines)
