from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from personal_assistant.cli import parser
from personal_assistant.connectors.nextcloud.calendar import CalendarObject, NextcloudCalendar
from personal_assistant.connectors.nextcloud.client import DavResponse
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection, NextcloudDiscovery
from personal_assistant.connectors.nextcloud.tasks import NextcloudTasks, TaskObject
from personal_assistant.ical_edit import component_properties, first_value, update_component
from personal_assistant.models import ActionPlan, Resource
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import (
    DirectCalendarToolSettings,
    DirectTasksToolSettings,
    NextcloudToolSettings,
    ToolSettings,
)


CAL_ID = "calendar-edit"
TASK_ID = "tasks-edit"
CAL_HREF = "/remote.php/dav/calendars/openclaw/personal/"
TASK_HREF = "/remote.php/dav/calendars/openclaw/tasks/"


def action(action_type: str, resource_id: str, payload: dict, status: str = "proposed") -> ActionPlan:
    return ActionPlan(
        id="update-1",
        idempotency_key="key",
        action_type=action_type,
        resource_id=resource_id,
        payload=payload,
        status=status,
        requires_approval=True,
        created_at="2026-07-27T00:00:00+00:00",
        updated_at="2026-07-27T00:00:00+00:00",
        error="",
    )


class ICalendarEditTests(unittest.TestCase):
    def test_event_update_preserves_alarm_attendee_and_recurrence(self) -> None:
        raw = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "UID:event-1\r\nDTSTART:20260730T100000Z\r\nDTEND:20260730T110000Z\r\n"
            "SUMMARY:Alt\r\nATTENDEE;CN=Max:mailto:max@example.com\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=4\r\nX-CUSTOM:bleibt\r\n"
            "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        with self.assertRaisesRegex(ValueError, "Wiederkehrender"):
            update_component(raw, "VEVENT", "event-1", {"SUMMARY": ("SUMMARY:Neu",)})
        updated, selected = update_component(
            raw,
            "VEVENT",
            "event-1",
            {"SUMMARY": ("SUMMARY:Neu",), "LOCATION": ("LOCATION:Kiel",)},
            allow_recurring=True,
        )
        self.assertTrue(selected.recurring)
        self.assertIn("SUMMARY:Neu", updated)
        self.assertIn("LOCATION:Kiel", updated)
        self.assertNotIn("SUMMARY:Alt", updated)
        self.assertIn("ATTENDEE;CN=Max:mailto:max@example.com", updated)
        self.assertIn("RRULE:FREQ=WEEKLY;COUNT=4", updated)
        self.assertIn("X-CUSTOM:bleibt", updated)
        self.assertIn("BEGIN:VALARM", updated)
        self.assertIn("TRIGGER:-PT15M", updated)

    def test_task_update_can_remove_due_without_losing_unknown_fields(self) -> None:
        raw = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:task-1\r\n"
            "SUMMARY:Alt\r\nDUE;VALUE=DATE:20260730\r\nSTATUS:NEEDS-ACTION\r\n"
            "X-NEXTCLOUD-CUSTOM:bleibt\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n"
            "TRIGGER:-P1D\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
        )
        updated, selected = update_component(
            raw,
            "VTODO",
            "task-1",
            {"SUMMARY": ("SUMMARY:Neu",), "DUE": None},
        )
        self.assertFalse(selected.recurring)
        props = component_properties(updated, "VTODO", "task-1")
        self.assertEqual(first_value(props, "SUMMARY"), "Neu")
        self.assertNotIn("DUE", props)
        self.assertIn("X-NEXTCLOUD-CUSTOM:bleibt", updated)
        self.assertIn("BEGIN:VALARM", updated)


class UpdateClient:
    def __init__(self, content: str, *, conflict: bool = False) -> None:
        self.content = content
        self.conflict = conflict
        self.etag = '"etag-1"'
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "PUT":
            if self.conflict:
                return DavResponse(412, "Precondition Failed", {}, b"", path)
            self.content = bytes(kwargs["data"]).decode("utf-8")
            self.etag = '"etag-2"'
            return DavResponse(204, "No Content", {}, b"", path)
        if method == "GET":
            return DavResponse(200, "OK", {"ETag": self.etag}, self.content.encode("utf-8"), path)
        raise AssertionError(method)


class ConnectorUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = DiscoveredCollection(
            kind="calendar", href=CAL_HREF, name="Personal", resource_id=CAL_ID,
            can_read=True, can_create=True, can_update=True,
        )
        self.tasks = DiscoveredCollection(
            kind="calendar", href=TASK_HREF, name="Tasks", resource_id=TASK_ID,
            can_read=True, can_create=True, can_update=True,
        )

    def test_calendar_put_uses_if_match_and_verifies_uid(self) -> None:
        old = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:event-1\r\n"
            "DTSTART:20260730T100000Z\r\nDTEND:20260730T110000Z\r\nSUMMARY:Alt\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        new, _ = update_component(old, "VEVENT", "event-1", {"SUMMARY": ("SUMMARY:Neu",)})
        client = UpdateClient(old)
        connector = NextcloudCalendar(SimpleNamespace(), client)
        verified = connector.update_event(
            self.calendar,
            href=CAL_HREF + "event-1.ics",
            uid="event-1",
            ics=new,
            etag='"etag-1"',
        )
        put = next(call for call in client.calls if call[0] == "PUT")
        self.assertEqual(put[2]["headers"]["If-Match"], '"etag-1"')
        self.assertEqual(verified.uid, "event-1")
        self.assertEqual(verified.summary, "Neu")

    def test_task_put_uses_if_match_and_conflict_is_visible(self) -> None:
        raw = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:task-1\r\n"
            "SUMMARY:Test\r\nSTATUS:NEEDS-ACTION\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
        )
        connector = NextcloudTasks(UpdateClient(raw, conflict=True))
        with self.assertRaisesRegex(RuntimeError, "zwischenzeitlich"):
            connector.update_task(
                self.tasks,
                href=TASK_HREF + "task-1.ics",
                uid="task-1",
                ics=raw,
                etag='"etag-1"',
            )

    def test_object_outside_selected_collection_is_rejected(self) -> None:
        raw = "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:x\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
        connector = NextcloudTasks(UpdateClient(raw))
        with self.assertRaises(PermissionError):
            connector.update_task(
                self.tasks,
                href="/remote.php/dav/calendars/other/tasks/x.ics",
                uid="x",
                ics=raw,
                etag='"etag-1"',
            )


class FakePolicy:
    def decide(self, resource_id, action_name, payload):
        return SimpleNamespace(allowed=True, requires_approval=action_name.endswith(".update"), reason="ok")


class FakeRegistry:
    def __init__(self, resource: Resource) -> None:
        self.resource = resource

    def get(self, resource_id: str) -> Resource:
        self.asserted = resource_id
        return self.resource


class FakeCalendar:
    def __init__(self, current: CalendarObject) -> None:
        self.current = current

    def find_events_by_uid(self, collection, uid):
        return [self.current] if uid == self.current.uid else []

    def read_event(self, href, fallback_uid=""):
        return self.current


class FakeTasks:
    def __init__(self, current: TaskObject) -> None:
        self.current = current

    def find_tasks_by_uid(self, collection, uid):
        return [self.current] if uid == self.current.uid else []

    def read_task(self, href, fallback_uid=""):
        return self.current


class FakeActions:
    def __init__(self, calendar: FakeCalendar | None = None, tasks: FakeTasks | None = None) -> None:
        self.calendar = calendar
        self.tasks = tasks
        self.current: ActionPlan | None = None
        self.approved = False

    def plan(self, action_type, resource_id, payload, idempotency_key=""):
        self.current = action(action_type, resource_id, payload)
        self.current = replace(self.current, idempotency_key=idempotency_key)
        return self.current

    def approve_configured_calendar_update(self, action_id, *, evidence):
        self.approved = True
        self.current = replace(self.current, status="approved")
        return self.current

    def approve_configured_tasks_update(self, action_id, *, evidence):
        self.approved = True
        self.current = replace(self.current, status="approved")
        return self.current

    def execute_calendar_update(self, action_id):
        payload = self.current.payload
        parsed = NextcloudCalendar._parse_ics(
            payload["ics"], payload["href"], '"etag-2"', fallback_uid=payload["uid"]
        )
        self.calendar.current = parsed
        self.current = replace(self.current, status="completed")
        return self.current, False

    def execute_task_update(self, action_id):
        payload = self.current.payload
        parsed = NextcloudTasks._parse_task_object(payload["ics"], payload["href"], '"etag-2"')
        self.tasks.current = parsed
        self.current = replace(self.current, status="completed")
        return self.current, False


class ServiceUpdateTests(unittest.TestCase):
    def test_calendar_update_uses_exact_uid_etag_and_returns_before_after(self) -> None:
        raw = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:event-1\r\n"
            "DTSTART:20260730T100000Z\r\nDTEND:20260730T110000Z\r\nSUMMARY:Alt\r\n"
            "LOCATION:Hamburg\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        current = NextcloudCalendar._parse_ics(raw, CAL_HREF + "event-1.ics", '"etag-1"')
        fake_calendar = FakeCalendar(current)
        resource = Resource(
            id=CAL_ID, kind="calendar", connector="nextcloud", enabled=True,
            remote_id=CAL_HREF, permissions=("read", "create", "update"),
            metadata={"href": CAL_HREF, "name": "Personal", "server_can_update": True},
        )
        assistant = PersonalAssistant.__new__(PersonalAssistant)
        assistant.tool_settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                calendar=DirectCalendarToolSettings(
                    enabled=True, resource_id=CAL_ID, allow_create=True, allow_list=True,
                    allow_update=True, timezone="Europe/Berlin",
                )
            ),
        )
        assistant.registry = FakeRegistry(resource)
        assistant.policy = FakePolicy()
        assistant.nextcloud_calendar = fake_calendar
        assistant.actions = FakeActions(calendar=fake_calendar)
        result = assistant.calendar_update(
            uid="event-1", title="Neu", location="Kiel", expected_title="Alt"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["before"]["title"], "Alt")
        self.assertEqual(result["after"]["title"], "Neu")
        self.assertEqual(result["after"]["location"], "Kiel")
        self.assertTrue(assistant.actions.approved)
        payload = assistant.actions.current.payload
        self.assertEqual(payload["etag"], '"etag-1"')
        self.assertTrue(payload["optimistic_concurrency"])
        self.assertEqual(payload["expected_sha256"], hashlib.sha256(payload["ics"].encode()).hexdigest())

    def test_task_update_can_complete_task_and_preserve_alarm(self) -> None:
        raw = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:task-1\r\n"
            "SUMMARY:Alt\r\nDUE;VALUE=DATE:20260730\r\nSTATUS:NEEDS-ACTION\r\n"
            "PERCENT-COMPLETE:0\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-P1D\r\n"
            "END:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
        )
        current = NextcloudTasks._parse_task_object(raw, TASK_HREF + "task-1.ics", '"etag-1"')
        fake_tasks = FakeTasks(current)
        resource = Resource(
            id=TASK_ID, kind="calendar", connector="nextcloud", enabled=True,
            remote_id=TASK_HREF, permissions=("read", "create", "update"),
            metadata={"href": TASK_HREF, "name": "Tasks", "server_can_update": True},
        )
        assistant = PersonalAssistant.__new__(PersonalAssistant)
        assistant.tool_settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                tasks=DirectTasksToolSettings(
                    enabled=True, resource_id=TASK_ID, allow_create=True, allow_list=True,
                    allow_update=True, timezone="Europe/Berlin",
                )
            ),
        )
        assistant.registry = FakeRegistry(resource)
        assistant.policy = FakePolicy()
        assistant.nextcloud_tasks = fake_tasks
        assistant.actions = FakeActions(tasks=fake_tasks)
        result = assistant.task_update(
            uid="task-1", status="COMPLETED", expected_title="Alt"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["after"]["status"], "COMPLETED")
        self.assertEqual(result["after"]["percent_complete"], 100)
        self.assertIn("BEGIN:VALARM", assistant.actions.current.payload["ics"])
        self.assertTrue(assistant.actions.approved)


class RegistryCliDiscoveryTests(unittest.TestCase):
    def test_registry_and_cli_expose_calendar_and_task_updates(self) -> None:
        settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                calendar=DirectCalendarToolSettings(
                    enabled=True, resource_id=CAL_ID, allow_list=True, allow_update=True
                ),
                tasks=DirectTasksToolSettings(
                    enabled=True, resource_id=TASK_ID, allow_list=True, allow_update=True
                ),
            ),
        )
        ids = {tool.id for tool in build_tool_registry(settings)}
        self.assertIn("nextcloud.calendar.list", ids)
        self.assertIn("nextcloud.calendar.search", ids)
        self.assertIn("nextcloud.calendar.update", ids)
        self.assertIn("nextcloud.tasks.update", ids)
        parsed = parser().parse_args([
            "calendar", "update", "--uid", "event-1", "--title", "Neu", "--yes"
        ])
        self.assertEqual(parsed.calendar_command, "update")
        parsed_task = parser().parse_args([
            "tasks", "update", "--uid", "task-1", "--status", "COMPLETED", "--yes"
        ])
        self.assertEqual(parsed_task.tasks_command, "update")

    def test_write_content_privilege_enables_updates(self) -> None:
        privilege = ("{DAV:}read", "{DAV:}write-content")
        self.assertTrue(NextcloudDiscovery._can_update(privilege))
        self.assertFalse(NextcloudDiscovery._can_update(("{DAV:}read", "{DAV:}bind")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
