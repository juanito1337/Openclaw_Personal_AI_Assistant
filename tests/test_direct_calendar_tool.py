from __future__ import annotations

import tempfile
import tomllib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from personal_assistant.actions import ActionService
from personal_assistant.connectors.nextcloud.calendar import CalendarObject
from personal_assistant.models import ActionPlan, Resource
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import (
    AntivirusToolSettings,
    DirectCalendarToolSettings,
    MailToolSettings,
    NextcloudToolSettings,
    NextcloudWorkspaceToolSettings,
    PortfolioToolSettings,
    SecurityToolSettings,
    ToolSettings,
)
from personal_assistant.tool_setup import _write_tools


def plan(status: str, payload: dict | None = None) -> ActionPlan:
    return ActionPlan(
        id="action-1",
        idempotency_key="key-1",
        action_type="calendar.create",
        resource_id="cal-personal",
        payload=payload
        or {
            "uid": "event-1",
            "title": "Synthetischer Termin",
            "starts_at": "2027-02-06T10:05:00+00:00",
            "ends_at": "2027-02-06T15:10:00+00:00",
            "location": "",
            "description": "",
            "direct_calendar_tool": True,
        },
        status=status,
        requires_approval=True,
        created_at="2026-07-22T00:00:00+00:00",
        updated_at="2026-07-22T00:00:00+00:00",
        error="",
    )


class FakeDirectActions:
    def __init__(self) -> None:
        self.planned_payload = None
        self.approved = False
        self.executed = False

    def plan(self, action_type, resource_id, payload, idempotency_key=""):
        self.planned_payload = payload
        return ActionPlan(
            id="action-1",
            idempotency_key=idempotency_key,
            action_type=action_type,
            resource_id=resource_id,
            payload=payload,
            status="proposed",
            requires_approval=True,
            created_at="x",
            updated_at="x",
            error="",
        )

    def approve_configured_calendar_tool(self, action_id, *, evidence):
        self.approved = True
        return plan("approved", self.planned_payload)

    def execute_calendar_create(self, action_id):
        self.executed = True
        return plan("completed", self.planned_payload), False

    def verify_calendar_create(self, action_id):
        payload = self.planned_payload
        return CalendarObject(
            uid=payload["uid"],
            summary=payload["title"],
            starts_at=payload["starts_at"],
            ends_at=payload["ends_at"],
            description=payload["description"],
            location=payload["location"],
            status="CONFIRMED",
            recurring=False,
            all_day=False,
            raw_ics=payload["ics"],
            href="/cal/event.ics",
            etag='"m15-etag"',
        )


class DirectCalendarToolTests(unittest.TestCase):
    def assistant(self):
        assistant = object.__new__(PersonalAssistant)
        assistant.tool_settings = SimpleNamespace(
            nextcloud=SimpleNamespace(
                calendar=DirectCalendarToolSettings(
                    enabled=True,
                    resource_id="cal-personal",
                    allow_create=True,
                    timezone="Europe/Berlin",
                    default_duration_minutes=60,
                    max_duration_hours=168,
                    max_future_days=730,
                )
            )
        )
        assistant.actions = FakeDirectActions()
        return assistant

    def test_direct_create_builds_utc_ics_and_uses_narrow_approval(self):
        assistant = self.assistant()
        start = (datetime.now(UTC) + timedelta(days=2)).astimezone().replace(second=0, microsecond=0)
        result = assistant.calendar_create(
            title="Werkstatttermin",
            start=start.isoformat(),
            duration_minutes=45,
            location="Kiel",
            description="Direkter Test",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["postcondition_verified"])
        self.assertEqual(result["remote"]["etag"], '"m15-etag"')
        self.assertTrue(assistant.actions.approved)
        self.assertTrue(assistant.actions.executed)
        payload = assistant.actions.planned_payload
        self.assertTrue(payload["direct_calendar_tool"])
        self.assertIn("BEGIN:VEVENT", payload["ics"])
        self.assertIn("SUMMARY:Werkstatttermin", payload["ics"])
        self.assertIn("LOCATION:Kiel", payload["ics"])
        self.assertTrue(payload["uid"].startswith("assistant-"))

    def test_invalid_interval_is_rejected_before_action_plan(self):
        assistant = self.assistant()
        start = datetime.now(UTC) + timedelta(days=2)
        with self.assertRaises(ValueError):
            assistant.calendar_create(
                title="Falsch",
                start=start.isoformat(),
                end=(start - timedelta(minutes=1)).isoformat(),
            )
        self.assertIsNone(assistant.actions.planned_payload)

    def test_registry_exposes_direct_create_tool(self):
        settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            mail=MailToolSettings(),
            nextcloud=NextcloudToolSettings(
                workspace=NextcloudWorkspaceToolSettings(enabled=False),
                calendar=DirectCalendarToolSettings(enabled=True, resource_id="cal-personal"),
            ),
            security=SecurityToolSettings(antivirus=AntivirusToolSettings(enabled=False)),
        )
        ids = {tool.id for tool in build_tool_registry(settings)}
        self.assertIn("nextcloud.calendar.status", ids)
        self.assertIn("nextcloud.calendar.create", ids)

    def test_tools_writer_preserves_antivirus_and_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tools.toml"
            settings = ToolSettings(
                path=path,
                nextcloud=NextcloudToolSettings(
                    workspace=NextcloudWorkspaceToolSettings(enabled=False, outbox=Path(tmp)),
                    calendar=DirectCalendarToolSettings(enabled=True, resource_id="cal-personal"),
                ),
                security=SecurityToolSettings(
                    antivirus=AntivirusToolSettings(enabled=True, temp_dir=Path(tmp) / "av")
                ),
                portfolio=PortfolioToolSettings(
                    enabled=True,
                    database=Path(tmp) / "portfolio.sqlite3",
                    import_root=Path(tmp) / "portfolio_inbox",
                    provider="eodhd",
                    interval_minutes=15,
                ),
            )
            _write_tools(path, settings)
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            self.assertTrue(data["nextcloud"]["calendar"]["enabled"])
            self.assertEqual(data["nextcloud"]["calendar"]["resource_id"], "cal-personal")
            self.assertTrue(data["security"]["antivirus"]["enabled"])
            self.assertTrue(data["portfolio"]["enabled"])
            self.assertEqual(data["portfolio"]["provider"], "eodhd")
            self.assertEqual(data["portfolio"]["interval_minutes"], 15)


class ReconciliationTests(unittest.TestCase):
    def action_service(self, current: ActionPlan, exists: bool):
        class Storage:
            def __init__(self):
                self.current = current
                self.audit_events = []

            def get_action(self, action_id):
                return self.current

            def update_action(self, action_id, status, error=""):
                self.current = ActionPlan(
                    id=self.current.id,
                    idempotency_key=self.current.idempotency_key,
                    action_type=self.current.action_type,
                    resource_id=self.current.resource_id,
                    payload=self.current.payload,
                    status=status,
                    requires_approval=self.current.requires_approval,
                    created_at=self.current.created_at,
                    updated_at=self.current.updated_at,
                    error=error,
                )
                return self.current

            def audit(self, event, payload, **kwargs):
                self.audit_events.append(event)

        class Registry:
            def get(self, resource_id):
                return Resource(
                    id=resource_id,
                    kind="calendar",
                    connector="nextcloud",
                    remote_id="remote.php/dav/calendars/openclaw/personal/",
                    permissions=("read", "create"),
                    metadata={"href": "remote.php/dav/calendars/openclaw/personal/", "name": "Personal"},
                )

        class Calendar:
            def event_exists(self, collection, uid):
                return exists

            def find_events_by_uid(self, collection, uid):
                payload = current.payload
                if not exists:
                    return []
                return [
                    CalendarObject(
                        uid=uid,
                        summary=str(payload.get("title") or ""),
                        starts_at=str(payload.get("starts_at") or ""),
                        ends_at=str(payload.get("ends_at") or ""),
                        description=str(payload.get("description") or ""),
                        location=str(payload.get("location") or ""),
                        status="CONFIRMED",
                        recurring=False,
                        all_day=False,
                        raw_ics=str(payload.get("ics") or ""),
                        href="/calendar/event.ics",
                        etag='"test-etag"',
                    )
                ]

        service = ActionService.__new__(ActionService)
        service.storage = Storage()
        service.registry = Registry()
        service.calendar = Calendar()
        service.execute = lambda action_id: service.storage.update_action(action_id, "completed")
        return service

    def test_completed_event_is_verified_as_duplicate(self):
        service = self.action_service(plan("completed"), True)
        result, duplicate = service.execute_calendar_create("action-1")
        self.assertEqual(result.status, "completed")
        self.assertTrue(duplicate)
        self.assertIn("action.duplicate_verified", service.storage.audit_events)

        conflicting = self.action_service(plan("completed"), True)
        conflicting.calendar.find_events_by_uid = lambda collection, uid: [
            CalendarObject(
                uid=uid,
                summary="Abweichender Termin",
                starts_at="2027-02-06T10:05:00+00:00",
                ends_at="2027-02-06T15:10:00+00:00",
                description="",
                location="",
                status="CONFIRMED",
                recurring=False,
                all_day=False,
                raw_ics="BEGIN:VCALENDAR",
                href="/calendar/conflict.ics",
                etag='"conflict"',
            )
        ]
        conflict_result, conflict_duplicate = conflicting.execute_calendar_create("action-1")
        self.assertEqual(conflict_result.status, "failed")
        self.assertFalse(conflict_duplicate)
        self.assertIn("action.reconciliation_conflict", conflicting.storage.audit_events)

    def test_stale_completed_event_is_recreated(self):
        service = self.action_service(plan("completed"), False)
        result, duplicate = service.execute_calendar_create("action-1")
        self.assertEqual(result.status, "completed")
        self.assertFalse(duplicate)
        self.assertIn("action.completed_stale", service.storage.audit_events)

    def test_approved_retry_accepts_only_matching_remote_postcondition(self):
        payload = {
            "uid": "event-retry",
            "title": "Synthetischer Termin",
            "starts_at": "2027-02-06T10:05:00+00:00",
            "ends_at": "2027-02-06T15:10:00+00:00",
            "location": "DUS -> SPC",
            "description": "Synthetischer Retry",
        }
        service = self.action_service(plan("approved", payload), True)

        result, duplicate = service.execute_calendar_create("action-1")

        self.assertEqual(result.status, "completed")
        self.assertTrue(duplicate)
        self.assertIn("action.postcondition_verified", service.storage.audit_events)
        self.assertIn("action.duplicate_verified", service.storage.audit_events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
