from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_assistant.tool_settings import CalendarMailToolSettings

from .assistant_bridge import PersonalAssistantActionBridge
from .command import CommandRunner
from .config import Config
from .models import CalendarEvent, Classification, OperationResult, ParsedMessage
from .nextcloud import NextcloudSkillClient
from .storage import Storage
from .utils import atomic_write_bytes, clean_single_line, normalize_address, safe_filename


@dataclass(slots=True)
class NormalizedEvent:
    event: CalendarEvent
    start: datetime | date
    end: datetime | date
    uid: str
    event_key: str
    fingerprint: str
    ics: str


def _unfold_ics(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def _parse_ics_datetime(value: str, params: str, default_tz: str) -> tuple[datetime | date, bool]:
    value = value.strip()
    if "VALUE=DATE" in params.upper() or (len(value) == 8 and "T" not in value):
        return datetime.strptime(value[:8], "%Y%m%d").date(), True
    tz_name = default_tz
    for part in params.split(";"):
        if part.upper().startswith("TZID="):
            # Quoted TZID parameters are legal in iCalendar and are emitted by
            # some groupware products. ZoneInfo expects the bare IANA name.
            tz_name = part.split("=", 1)[1].strip().strip('"')
    if value.endswith("Z"):
        raw = value[:-1]
        fmt = "%Y%m%dT%H%M%S" if len(raw) >= 15 else "%Y%m%dT%H%M"
        parsed = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
    else:
        raw = value[:15] if len(value) >= 15 else value[:13]
        fmt = "%Y%m%dT%H%M%S" if len(raw) >= 15 else "%Y%m%dT%H%M"
        parsed = datetime.strptime(raw, fmt)
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed, False


def event_from_ics(text: str, default_tz: str) -> CalendarEvent | None:
    in_event = False
    values: dict[str, tuple[str, str]] = {}
    attendees: list[str] = []
    for line in _unfold_ics(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            continue
        if upper == "END:VEVENT":
            break
        if not in_event or ":" not in line:
            continue
        left, value = line.split(":", 1)
        name, _, params = left.partition(";")
        name = name.upper()
        if name == "ATTENDEE":
            attendees.append(value.removeprefix("mailto:").removeprefix("MAILTO:"))
        elif name in {"UID", "SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION", "STATUS"}:
            values[name] = (value, params)
    if "SUMMARY" not in values or "DTSTART" not in values:
        return None
    start_value, start_params = values["DTSTART"]
    try:
        start, all_day = _parse_ics_datetime(start_value, start_params, default_tz)
    except (ValueError, KeyError, ZoneInfoNotFoundError):
        return None
    end_iso: str | None = None
    if "DTEND" in values:
        try:
            end, _ = _parse_ics_datetime(values["DTEND"][0], values["DTEND"][1], default_tz)
            end_iso = end.isoformat()
        except (ValueError, KeyError, ZoneInfoNotFoundError):
            end_iso = None
    status_value = values.get("STATUS", ("CONFIRMED", ""))[0].upper()
    status = "confirmed" if status_value == "CONFIRMED" else "tentative"
    return CalendarEvent(
        title=values["SUMMARY"][0].replace("\\,", ",").replace("\\n", "\n"),
        start=start.isoformat(),
        end=end_iso,
        all_day=all_day,
        timezone=default_tz,
        location=values.get("LOCATION", ("", ""))[0].replace("\\,", ","),
        participants=attendees,
        notes=values.get("DESCRIPTION", ("", ""))[0].replace("\\n", "\n"),
        confidence=0.99,
        status=status,
        uid=values.get("UID", ("", ""))[0],
    )


def _escape_ics(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


class CalendarManager:
    def __init__(
        self,
        config: Config,
        storage: Storage,
        runner: CommandRunner,
        dry_run: bool = False,
        *,
        nextcloud: NextcloudSkillClient | None = None,
        send_mail: Callable[..., OperationResult] | None = None,
        assistant_bridge: PersonalAssistantActionBridge | None = None,
        command_settings: CalendarMailToolSettings | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.runner = runner
        self.dry_run = dry_run
        self.nextcloud = nextcloud
        self.send_mail = send_mail
        self.assistant_bridge = assistant_bridge
        self.command_settings = command_settings or CalendarMailToolSettings()
        self.log = logging.getLogger(__name__)

    def process_command_mail(
        self,
        message: ParsedMessage,
        classification: Classification,
    ) -> OperationResult | None:
        settings = self.command_settings
        prefix = settings.subject_prefix.strip()
        if not settings.enabled or not prefix or not message.subject.casefold().startswith(prefix.casefold()):
            return None
        sender = normalize_address(message.sender_addr)
        allowed = {normalize_address(value) for value in settings.sender_addresses}
        if not sender or sender not in allowed:
            return OperationResult(
                False,
                "calendar-command-sender-rejected",
                "Befehlsmail stammt nicht von einer freigegebenen Eigentuermer-Adresse.",
            )
        if self.assistant_bridge is None:
            return OperationResult(False, "calendar-command-bridge-missing", "Personal-Assistant-ActionBridge fehlt")
        event = self._extract_event(message, classification)
        if event is None:
            return OperationResult(False, "calendar-command-no-event", "Aus der Befehlsmail konnte kein eindeutiger Termin extrahiert werden")
        event.status = "confirmed"
        event.confidence = max(event.confidence, classification.confidence)
        normalized = self._normalize(event, message)
        if normalized is None:
            return OperationResult(False, "calendar-command-invalid-event", "Terminangaben der Befehlsmail sind nicht valide")
        if self.config.calendar.require_future and not self._is_future(normalized):
            return OperationResult(True, "past-event", "Termin liegt in der Vergangenheit; kein Eintrag")
        result = self.assistant_bridge.create_calendar_event(
            message=message,
            resource_id=settings.calendar_resource_id,
            ics=normalized.ics,
            uid=normalized.uid,
            fingerprint=normalized.fingerprint,
            sender=sender,
        )
        if result.ok and result.status in {"created", "duplicate", "would-create-command-event"} and not self.dry_run:
            self.storage.record_event(
                normalized.event_key,
                message.stable_key,
                uid=normalized.uid,
                fingerprint=normalized.fingerprint,
                title=event.title,
                starts_at=event.start,
                ends_at=event.end or "",
                status="created" if result.status == "created" else "duplicate",
                backend="personal-assistant-action",
                path=settings.calendar_resource_id,
            )
        return result

    def process(
        self,
        message: ParsedMessage,
        classification: Classification,
        *,
        trusted_sender: bool = False,
    ) -> OperationResult:
        if not self.config.calendar.enabled:
            return OperationResult(False, "calendar-disabled", "Kalenderverarbeitung ist deaktiviert")

        event = self._extract_event(message, classification)
        if event is None:
            return OperationResult(False, "no-event", "Kein belastbarer Termin extrahiert")
        normalized = self._normalize(event, message)
        if not normalized:
            return OperationResult(False, "invalid-event", "Terminangaben sind nicht valide")
        if self.config.calendar.require_future and not self._is_future(normalized):
            return OperationResult(
                True,
                "past-event",
                "Termin liegt in der Vergangenheit; es wurde weder eine Freigabemail gesendet noch ein Kalendereintrag erstellt.",
            )

        low_confidence = (
            classification.confidence < self.config.thresholds.calendar
            or event.confidence < self.config.thresholds.calendar
        )
        if low_confidence:
            path = self._queue(normalized, created=False)
            if not self.dry_run:
                self.storage.record_event(
                    f"pending:{normalized.event_key}:{normalized.fingerprint[:16]}",
                    message.stable_key,
                    uid=normalized.uid,
                    fingerprint=normalized.fingerprint,
                    title=event.title,
                    starts_at=event.start,
                    ends_at=event.end or "",
                    status="pending-low-confidence",
                    backend="queue",
                    path=str(path),
                )
            return OperationResult(
                True,
                "pending-review",
                "Mail-/Terminkonfidenz liegt unter dem Schwellwert; keine Freigabemail wurde gesendet.",
                path=str(path),
            )

        if self.config.calendar.approval_required:
            return self._request_approval(message, normalized, classification)

        if event.status != "confirmed":
            path = self._queue(normalized, created=False)
            if not self.dry_run:
                self._record(normalized, message, "pending", "queue", path)
            return OperationResult(True, "pending-review", "Termin ist nur vorgeschlagen oder vorlaeufig", path=str(path))

        if self.config.calendar.require_trusted_sender and not trusted_sender:
            path = self._queue(normalized, created=False)
            if not self.dry_run:
                self._record(normalized, message, "untrusted-pending", "queue", path)
            return OperationResult(
                True,
                "pending-review",
                "Bestaetigter Termin von einem noch nicht vertrauenswuerdigen Absender; kein automatischer Kalendereintrag.",
                path=str(path),
            )
        return self._create_normalized(normalized, message)

    def _extract_event(
        self, message: ParsedMessage, classification: Classification
    ) -> CalendarEvent | None:
        for invite in message.calendar_invites:
            event = event_from_ics(invite, self.config.calendar.timezone)
            if event:
                return event
        return classification.calendar_event

    def _is_future(self, normalized: NormalizedEvent, *, now: datetime | None = None) -> bool:
        zone = ZoneInfo(normalized.event.timezone or self.config.calendar.timezone)
        current = now or datetime.now(zone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=zone)
        if isinstance(normalized.start, datetime):
            start = normalized.start
            if start.tzinfo is None:
                start = start.replace(tzinfo=zone)
            return start > current.astimezone(start.tzinfo)
        return normalized.start >= current.astimezone(zone).date()

    @staticmethod
    def _approval_token() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(16))

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.strip().upper().encode("ascii", errors="ignore")).hexdigest()

    def _approval_recipient(self) -> str:
        return (self.config.calendar.approval_recipient or self.config.mailbox.forward_to).strip()

    def _approval_reply_sender(self) -> str:
        return normalize_address(
            parseaddr(self.config.calendar.approval_reply_from or self.config.mailbox.forward_to)[1]
        )

    def _request_approval(
        self,
        message: ParsedMessage,
        normalized: NormalizedEvent,
        classification: Classification,
    ) -> OperationResult:
        existing_event = self.storage.get_event(normalized.event_key)
        if existing_event and str(existing_event["status"] or "") == "created":
            return OperationResult(True, "duplicate", "Termin ist bereits im Kalender erfasst")
        pending = self.storage.pending_calendar_approval(normalized.event_key, normalized.fingerprint)
        if pending:
            return OperationResult(
                True,
                "approval-pending",
                f"Freigabe wurde bereits angefragt (Token-Ende {pending['token_hint']}).",
                path=str(pending["ics_path"] or ""),
            )
        path = self._queue(normalized, created=False)
        if self._select_backend() == "queue":
            return OperationResult(
                True,
                "pending-review",
                "Kein Kalender-Backend ist eingerichtet; ein Termin von einem noch nicht vertrauenswuerdigen Absender bleibt deshalb nur zur Pruefung vorgemerkt und es wurde keine Freigabemail gesendet.",
                path=str(path),
            )
        if self.dry_run:
            return OperationResult(
                True,
                "would-request-approval",
                f"Wuerde eine Terminfreigabe an {self._approval_recipient()} senden; kein Kalendereintrag im Dry-Run.",
                path=str(path),
            )
        if self.send_mail is None:
            return OperationResult(False, "approval-mail-unavailable", "Mailversand fuer Terminfreigaben ist nicht initialisiert")

        token = self._approval_token()
        token_hash = self._token_hash(token)
        expires = datetime.now(UTC) + timedelta(days=self.config.calendar.approval_expiry_days)
        event_json = self._normalized_to_json(normalized)
        approval_id = self.storage.create_calendar_approval(
            token_hash=token_hash,
            token_hint=token[-4:],
            event_key=normalized.event_key,
            fingerprint=normalized.fingerprint,
            stable_key=message.stable_key,
            source_subject=message.subject,
            event_json=event_json,
            ics_path=str(path),
            requester_email=self._approval_recipient(),
            status="sending",
            expires_at=expires.isoformat(timespec="seconds"),
        )
        subject = f"[MAIL-AGENT TERMIN {token}] Freigabe: {clean_single_line(normalized.event.title, 250)}"
        body = self._approval_body(token, message, normalized, classification, expires)
        reply_to = parseaddr(self.config.mailbox.from_header)[1]
        sent = self.send_mail(
            subject,
            body,
            recipient=self._approval_recipient(),
            reply_to=reply_to,
        )
        if not sent.ok:
            self.storage.update_calendar_approval(approval_id, "send-error", error=sent.detail)
            return OperationResult(False, "approval-mail-failed", sent.detail, path=str(path))
        self.storage.update_calendar_approval(approval_id, "pending")
        self.storage.record_event(
            f"approval:{normalized.event_key}:{normalized.fingerprint[:16]}",
            message.stable_key,
            uid=normalized.uid,
            fingerprint=normalized.fingerprint,
            title=normalized.event.title,
            starts_at=normalized.event.start,
            ends_at=normalized.event.end or "",
            status="approval-pending",
            backend="email-approval",
            path=str(path),
        )
        return OperationResult(
            True,
            "approval-requested",
            f"Freigabemail an {self._approval_recipient()} gesendet. Antworte mit 'JA' oder 'NEIN'.",
            path=str(path),
        )

    def _approval_body(
        self,
        token: str,
        message: ParsedMessage,
        normalized: NormalizedEvent,
        classification: Classification,
        expires: datetime,
    ) -> str:
        zone = ZoneInfo(normalized.event.timezone or self.config.calendar.timezone)
        if isinstance(normalized.start, datetime):
            start_text = normalized.start.astimezone(zone).strftime("%d.%m.%Y %H:%M %Z")
            end_text = normalized.end.astimezone(zone).strftime("%d.%m.%Y %H:%M %Z") if isinstance(normalized.end, datetime) else ""
        else:
            start_text = normalized.start.strftime("%d.%m.%Y (ganztags)")
            end_text = normalized.end.strftime("%d.%m.%Y") if isinstance(normalized.end, date) else ""
        return "\n".join([
            "Der Mail-Agent hat einen zukuenftigen Termin erkannt.",
            "",
            f"Titel: {normalized.event.title}",
            f"Beginn: {start_text}",
            f"Ende: {end_text or 'nicht angegeben'}",
            f"Ort: {normalized.event.location or 'nicht angegeben'}",
            f"Quelle: {message.subject}",
            f"Absender: {message.sender_name or message.sender_addr} <{message.sender_addr}>" if message.sender_name else f"Absender: {message.sender_addr}",
            f"Begruendung: {classification.reason}",
            "",
            "Soll dieser Termin in den ausgewaehlten Nextcloud-Kalender eingetragen werden?",
            "Antworte auf diese Mail in der ERSTEN ZEILE ausschliesslich mit:",
            "",
            "JA",
            "",
            "oder",
            "",
            "NEIN",
            "",
            f"Freigabecode: {token}",
            f"Gueltig bis: {expires.astimezone(zone).strftime('%d.%m.%Y %H:%M %Z')}",
            "",
            "Ohne eine gueltige Antwort wird kein Kalendereintrag erstellt.",
        ])

    def handle_approval_reply(self, message: ParsedMessage) -> OperationResult | None:
        match = re.search(r"\[MAIL-AGENT\s+TERMIN\s+([A-Z2-9]{12,24})\]", message.subject.upper())
        if not match:
            return None
        token = match.group(1)
        expected_sender = self._approval_reply_sender()
        if not expected_sender or normalize_address(message.sender_addr) != expected_sender:
            return OperationResult(
                False,
                "approval-sender-rejected",
                "Terminfreigabe stammt nicht von der konfigurierten Bestaetigungsadresse.",
            )
        approval = self.storage.get_calendar_approval(self._token_hash(token))
        if not approval:
            return OperationResult(False, "approval-token-unknown", "Unbekannter oder bereits ersetzter Freigabecode")
        decision = self._reply_decision(message.body_text)
        if decision is None:
            return OperationResult(
                False,
                "approval-answer-invalid",
                "Erste nichtleere Zeile muss exakt JA oder NEIN lauten.",
            )
        if self.dry_run:
            if decision == "reject":
                return OperationResult(True, "would-reject-approval", "Wuerde den Termin ablehnen")
            try:
                preview = self._normalized_from_json(str(approval["event_json"]))
            except Exception as exc:
                return OperationResult(False, "approval-event-invalid", str(exc))
            if self.config.calendar.require_future and not self._is_future(preview):
                return OperationResult(True, "past-event", "Termin liegt in der Vergangenheit; kein Eintrag")
            return OperationResult(True, "would-create-approved-event", "Wuerde den freigegebenen Termin eintragen")
        status = str(approval["status"] or "")
        if status == "created":
            return OperationResult(True, "approval-already-created", "Termin wurde bereits angelegt", path=str(approval["created_path"] or ""))
        if status == "rejected":
            return OperationResult(True, "approval-already-rejected", "Termin wurde bereits abgelehnt")
        try:
            expires_at = datetime.fromisoformat(str(approval["expires_at"]).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires_at.astimezone(UTC):
            self.storage.update_calendar_approval(
                int(approval["id"]), "expired", response_stable_key=message.stable_key, responded=True
            )
            self._send_result("Terminfreigabe abgelaufen", "Der Freigabecode ist abgelaufen. Es wurde kein Termin erstellt.")
            return OperationResult(True, "approval-expired", "Freigabe ist abgelaufen; kein Termin erstellt")
        if decision == "reject":
            self.storage.update_calendar_approval(
                int(approval["id"]), "rejected", response_stable_key=message.stable_key, responded=True
            )
            self._send_result("Termin nicht eingetragen", f"Der Termin aus '{approval['source_subject']}' wurde abgelehnt.")
            return OperationResult(True, "approval-rejected", "Termin wurde auf Nutzerwunsch nicht erstellt")

        try:
            normalized = self._normalized_from_json(str(approval["event_json"]))
        except Exception as exc:
            self.storage.update_calendar_approval(
                int(approval["id"]), "create-error", response_stable_key=message.stable_key, error=str(exc), responded=True
            )
            return OperationResult(False, "approval-event-invalid", str(exc))
        if self.config.calendar.require_future and not self._is_future(normalized):
            self.storage.update_calendar_approval(
                int(approval["id"]), "past", response_stable_key=message.stable_key, responded=True
            )
            self._send_result("Termin liegt bereits in der Vergangenheit", "Es wurde kein Kalendereintrag erstellt.")
            return OperationResult(True, "past-event", "Termin liegt inzwischen in der Vergangenheit; kein Eintrag erstellt")

        self.storage.update_calendar_approval(
            int(approval["id"]), "approved-creating", response_stable_key=message.stable_key, responded=True
        )
        source_message = ParsedMessage(
            stable_key=str(approval["stable_key"]),
            mailbox_id="",
            source_folder="",
            raw=b"",
            subject=str(approval["source_subject"] or ""),
        )
        result = self._create_normalized(normalized, source_message)
        if result.ok and result.status in {"created", "duplicate"}:
            self.storage.update_calendar_approval(
                int(approval["id"]),
                "created",
                backend=self._select_backend(),
                created_path=result.path,
            )
            self._send_result(
                "Termin eingetragen",
                f"Der Termin '{normalized.event.title}' wurde in den Kalender eingetragen.",
            )
            return OperationResult(True, "approval-created", result.detail, path=result.path)
        self.storage.update_calendar_approval(
            int(approval["id"]), "create-error", backend=self._select_backend(), error=result.detail
        )
        self._send_result("Termin konnte nicht eingetragen werden", result.detail or result.status)
        return OperationResult(False, "approval-create-failed", result.detail, path=result.path)

    @staticmethod
    def _reply_decision(body: str) -> str | None:
        for raw in (body or "").splitlines():
            line = raw.strip()
            if not line or line.startswith(">"):
                continue
            normalized = line.casefold()
            if normalized in {"ja", "yes"}:
                return "approve"
            if normalized in {"nein", "no"}:
                return "reject"
            return None
        return None

    def _send_result(self, subject: str, body: str) -> None:
        if not self.config.calendar.send_result_mail or self.send_mail is None:
            return
        result = self.send_mail(
            f"{self.config.calendar.approval_subject_prefix} {subject}",
            body,
            recipient=self._approval_recipient(),
            reply_to=parseaddr(self.config.mailbox.from_header)[1],
        )
        if not result.ok:
            self.log.warning("Ergebnismail fuer Terminfreigabe konnte nicht gesendet werden: %s", result.detail)

    def _normalized_to_json(self, normalized: NormalizedEvent) -> str:
        payload = {
            "event": {
                "title": normalized.event.title,
                "start": normalized.event.start,
                "end": normalized.event.end,
                "all_day": normalized.event.all_day,
                "timezone": normalized.event.timezone,
                "location": normalized.event.location,
                "participants": normalized.event.participants,
                "notes": normalized.event.notes,
                "confidence": normalized.event.confidence,
                "status": normalized.event.status,
                "uid": normalized.event.uid,
            },
            "start": normalized.start.isoformat(),
            "end": normalized.end.isoformat(),
            "uid": normalized.uid,
            "event_key": normalized.event_key,
            "fingerprint": normalized.fingerprint,
            "ics": normalized.ics,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _normalized_from_json(self, value: str) -> NormalizedEvent:
        payload = json.loads(value)
        event = CalendarEvent.from_dict(payload.get("event"))
        if event is None:
            raise ValueError("Gespeicherte Terminangaben sind unvollstaendig")
        if event.all_day:
            start: datetime | date = date.fromisoformat(str(payload["start"])[:10])
            end: datetime | date = date.fromisoformat(str(payload["end"])[:10])
        else:
            start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00"))
        return NormalizedEvent(
            event=event,
            start=start,
            end=end,
            uid=str(payload["uid"]),
            event_key=str(payload["event_key"]),
            fingerprint=str(payload["fingerprint"]),
            ics=str(payload["ics"]),
        )

    def _create_normalized(self, normalized: NormalizedEvent, message: ParsedMessage) -> OperationResult:
        existing = self.storage.get_event(normalized.event_key)
        if existing:
            if str(existing["fingerprint"] or "") != normalized.fingerprint:
                path = self._queue(normalized, created=False)
                return OperationResult(
                    True,
                    "pending-review",
                    "Termin mit gleicher UID, aber geaenderten Daten erkannt; keine automatische Ueberschreibung.",
                    path=str(path),
                )
            if str(existing["status"] or "") == "created":
                return OperationResult(True, "duplicate", "Termin ist bereits im Kalender erfasst", path=str(existing["path"] or ""))
        if self.dry_run:
            return OperationResult(True, "would-create", path=str(self.config.calendar.pending_dir / f"{safe_filename(normalized.uid)}.ics"))
        if not self.config.calendar.auto_create:
            path = self._queue(normalized, created=False)
            self._record(normalized, message, "pending", "queue", path)
            return OperationResult(True, "pending-review", "Automatisches Eintragen ist deaktiviert", path=str(path))

        backend = self._select_backend()
        if backend == "nextcloud_skill":
            if self.nextcloud is None:
                result = OperationResult(
                    False,
                    "nextcloud-calendar-missing",
                    "Native Nextcloud-Bruecke ist nicht initialisiert",
                )
            else:
                result = self.nextcloud.create_event(normalized)
        elif backend == "caldav":
            result = self._put_caldav(normalized)
        elif backend == "command":
            result = self._run_command(normalized)
        elif backend == "khal":
            result = self._run_khal(normalized)
        else:
            path = self._queue(normalized, created=False)
            self._record(normalized, message, "pending", "queue", path)
            return OperationResult(True, "pending-review", "Kein Kalender-Backend gefunden; ICS wurde zur Pruefung abgelegt", path=str(path))

        if result.ok:
            path = self._queue(normalized, created=True)
            self._record(normalized, message, "created", backend, path)
            result.path = str(path)
            return result
        path = self._queue(normalized, created=False)
        self.storage.record_event(
            normalized.event_key,
            message.stable_key,
            uid=normalized.uid,
            fingerprint=normalized.fingerprint,
            title=normalized.event.title,
            starts_at=normalized.event.start,
            ends_at=normalized.event.end or "",
            status="error",
            backend=backend,
            path=str(path),
            error=result.detail,
        )
        result.path = str(path)
        return result

    def _normalize(self, event: CalendarEvent, message: ParsedMessage) -> NormalizedEvent | None:
        try:
            try:
                zone = ZoneInfo(event.timezone or self.config.calendar.timezone)
            except ZoneInfoNotFoundError:
                event.timezone = self.config.calendar.timezone
                zone = ZoneInfo(self.config.calendar.timezone)
            if event.all_day:
                start: datetime | date = date.fromisoformat(event.start[:10])
                end: datetime | date = date.fromisoformat((event.end or "")[:10]) if event.end else start + timedelta(days=1)
            else:
                start_dt = datetime.fromisoformat(event.start.replace("Z", "+00:00"))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=zone)
                start = start_dt
                if event.end:
                    end_dt = datetime.fromisoformat(event.end.replace("Z", "+00:00"))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=zone)
                    end = end_dt
                else:
                    end = start_dt + timedelta(hours=1)
            if end <= start:
                return None
        except (ValueError, TypeError):
            return None
        fingerprint_source = f"{event.title.strip().lower()}|{start.isoformat()}|{end.isoformat()}|{message.sender_addr}"
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        uid = event.uid.strip() or f"{fingerprint[:32]}@local-mail-agent"
        event_key = "uid:" + uid if event.uid.strip() else "fp:" + fingerprint
        ics = self._build_ics(event, start, end, uid)
        return NormalizedEvent(event, start, end, uid, event_key, fingerprint, ics)

    def _build_ics(self, event: CalendarEvent, start: datetime | date, end: datetime | date, uid: str) -> str:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Local Mail Agent//DE",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{_escape_ics(uid)}",
            f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        ]
        if isinstance(start, datetime):
            zone_name = event.timezone or self.config.calendar.timezone
            local_start = start.astimezone(ZoneInfo(zone_name))
            local_end = end.astimezone(ZoneInfo(zone_name)) if isinstance(end, datetime) else start + timedelta(hours=1)
            lines += [
                f"DTSTART;TZID={zone_name}:{local_start.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID={zone_name}:{local_end.strftime('%Y%m%dT%H%M%S')}",
            ]
        else:
            lines += [
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
            ]
        event_status = {
            "confirmed": "CONFIRMED",
            "tentative": "TENTATIVE",
            "proposed": "TENTATIVE",
        }.get(event.status, "TENTATIVE")
        lines += [
            f"SUMMARY:{_escape_ics(event.title)}",
            f"LOCATION:{_escape_ics(event.location)}",
            f"DESCRIPTION:{_escape_ics(event.notes)}",
            f"STATUS:{event_status}",
        ]
        for participant in event.participants:
            if "@" in participant:
                lines.append(f"ATTENDEE:mailto:{participant}")
        lines += ["END:VEVENT", "END:VCALENDAR", ""]
        return "\r\n".join(lines)

    def health(self, *, nextcloud_health: dict[str, object] | None = None) -> tuple[bool, str, str]:
        backend = self._select_backend()
        if backend == "queue":
            return False, backend, "Kein Kalender-Importer gefunden; Termine werden nur als ICS zur Pruefung abgelegt"
        if backend == "nextcloud_skill":
            if self.nextcloud is None:
                return False, backend, "Native Nextcloud-Bruecke ist nicht initialisiert"
            health = nextcloud_health if nextcloud_health is not None else self.nextcloud.health(live=True)
            return bool(health.get("ok")), backend, str(health.get("detail") or "Nextcloud-Status unbekannt")
        if backend == "caldav":
            missing = [
                name for name in (
                    self.config.calendar.caldav_url_env,
                    self.config.calendar.caldav_username_env,
                    self.config.calendar.caldav_password_env,
                )
                if not os.environ.get(name)
            ]
            if missing:
                return False, backend, "Fehlende Umgebungsvariablen: " + ", ".join(missing)
            return True, backend, "CalDAV-Konfiguration vorhanden"
        if backend == "command":
            command_text = self.config.calendar.command.strip()
            if "{ics_path}" not in command_text:
                return False, backend, "calendar.command enthaelt keinen {ics_path}-Platzhalter"
            parts = shlex.split(command_text)
            if not parts or not shutil.which(parts[0]):
                return False, backend, "Kalender-Importbefehl wurde nicht gefunden"
            return True, backend, "Externer Kalender-Importbefehl ist verfuegbar"
        if backend == "khal":
            return (bool(shutil.which("khal")), backend, "khal ist verfuegbar" if shutil.which("khal") else "khal wurde nicht gefunden")
        return False, backend, "Unbekanntes Kalender-Backend"

    def _select_backend(self) -> str:
        configured = self.config.calendar.backend.lower().strip()
        if configured not in {"auto", "nextcloud_skill", "caldav", "command", "khal", "queue"}:
            return "queue"
        if configured != "auto":
            return configured
        if (
            self.config.nextcloud.enabled
            and self.nextcloud is not None
            and self.nextcloud.script_path.exists()
            and not self.nextcloud.missing_environment()
        ):
            return "nextcloud_skill"
        if os.environ.get(self.config.calendar.caldav_url_env):
            return "caldav"
        if self.config.calendar.command.strip():
            return "command"
        if shutil.which("khal"):
            return "khal"
        return "queue"

    def _queue(self, event: NormalizedEvent, *, created: bool) -> Path:
        directory = self.config.calendar.created_dir if created else self.config.calendar.pending_dir
        path = directory / f"{safe_filename(event.uid, 'event')}.ics"
        atomic_write_bytes(path, event.ics.encode("utf-8"))
        return path

    def _run_command(self, event: NormalizedEvent) -> OperationResult:
        path = self._queue(event, created=False)
        command_text = self.config.calendar.command.strip()
        if "{ics_path}" not in command_text:
            return OperationResult(False, "calendar-command-invalid", "calendar.command muss {ics_path} enthalten")
        args = [part.replace("{ics_path}", str(path)) for part in shlex.split(command_text)]
        result = self.runner.run(args)
        return OperationResult(result.ok, "created" if result.ok else "calendar-command-failed", result.combined)

    def _run_khal(self, event: NormalizedEvent) -> OperationResult:
        path = self._queue(event, created=False)
        result = self.runner.run(["khal", "import", "--batch", str(path)])
        return OperationResult(result.ok, "created" if result.ok else "khal-failed", result.combined)

    def _put_caldav(self, event: NormalizedEvent) -> OperationResult:
        base_url = os.environ.get(self.config.calendar.caldav_url_env, "").rstrip("/") + "/"
        username = os.environ.get(self.config.calendar.caldav_username_env, "")
        password = os.environ.get(self.config.calendar.caldav_password_env, "")
        if not base_url.strip("/") or not username or not password:
            return OperationResult(False, "caldav-config-missing", "CalDAV-URL/Benutzer/Passwort fehlen in Umgebungsvariablen")
        target = urllib.parse.urljoin(base_url, urllib.parse.quote(safe_filename(event.uid)) + ".ics")
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        request = urllib.request.Request(
            target,
            data=event.ics.encode("utf-8"),
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "text/calendar; charset=utf-8",
                "If-None-Match": "*",
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if 200 <= response.status < 300:
                    return OperationResult(True, "created", f"CalDAV HTTP {response.status}")
                return OperationResult(False, "caldav-failed", f"CalDAV HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code == 412:
                return OperationResult(True, "duplicate", "CalDAV-Termin existiert bereits")
            return OperationResult(False, "caldav-failed", f"CalDAV HTTP {exc.code}: {exc.reason}")
        except urllib.error.URLError as exc:
            return OperationResult(False, "caldav-failed", str(exc))

    def _record(self, event: NormalizedEvent, message: ParsedMessage, status: str, backend: str, path: Path) -> None:
        self.storage.record_event(
            event.event_key,
            message.stable_key,
            uid=event.uid,
            fingerprint=event.fingerprint,
            title=event.event.title,
            starts_at=event.event.start,
            ends_at=event.event.end or "",
            status=status,
            backend=backend,
            path=str(path),
        )
