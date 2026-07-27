from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from personal_assistant.cli import parser
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection, NextcloudDiscovery
from personal_assistant.models import Resource
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import ToolSettings


MULTISTATUS = b"""<?xml version='1.0' encoding='utf-8'?>
<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>
 <d:response>
  <d:href>/remote.php/dav/calendars/openclaw/personal/</d:href>
  <d:propstat><d:prop>
   <d:displayname>Personal</d:displayname>
   <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
   <c:supported-calendar-component-set><c:comp name='VEVENT'/><c:comp name='VTODO'/></c:supported-calendar-component-set>
   <d:current-user-privilege-set>
    <d:privilege><d:read/></d:privilege>
    <d:privilege><d:bind/></d:privilege>
   </d:current-user-privilege-set>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/calendars/openclaw/tasks/</d:href>
  <d:propstat><d:prop>
   <d:displayname>Aufgaben</d:displayname>
   <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
   <c:supported-calendar-component-set><c:comp name='VTODO'/></c:supported-calendar-component-set>
   <d:current-user-privilege-set><d:privilege><d:read/></d:privilege></d:current-user-privilege-set>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/calendars/openclaw/events/</d:href>
  <d:propstat><d:prop>
   <d:displayname>Termine</d:displayname>
   <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
   <c:supported-calendar-component-set><c:comp name='VEVENT'/></c:supported-calendar-component-set>
   <d:current-user-privilege-set><d:privilege><d:read/></d:privilege><d:privilege><d:write/></d:privilege></d:current-user-privilege-set>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>
"""


class FakeResponse:
    status = 207
    data = MULTISTATUS


class FakeClient:
    username = "openclaw"

    def request(self, method, path, **kwargs):
        self.last = (method, path, kwargs)
        return FakeResponse()


class DiscoveryParsingTests(unittest.TestCase):
    def test_distinguishes_event_calendars_and_task_lists(self) -> None:
        discovery = NextcloudDiscovery(FakeClient())
        collections = discovery.calendar_collections()
        self.assertEqual(len(collections), 3)

        personal = next(item for item in collections if item.name == "Personal")
        self.assertEqual(personal.components, ("VEVENT", "VTODO"))
        self.assertTrue(personal.can_read)
        self.assertTrue(personal.can_create)

        calendars = discovery.calendars()
        self.assertEqual({item.name for item in calendars}, {"Personal", "Termine"})
        task_lists = discovery.task_lists()
        self.assertEqual({item.name for item in task_lists}, {"Personal", "Aufgaben"})
        tasks = next(item for item in task_lists if item.name == "Aufgaben")
        self.assertTrue(tasks.can_read)
        self.assertFalse(tasks.can_create)

    def test_resource_ids_are_stable(self) -> None:
        discovery = NextcloudDiscovery(FakeClient())
        first = {item.href: item.resource_id for item in discovery.calendar_collections()}
        second = {item.href: item.resource_id for item in discovery.calendar_collections()}
        self.assertEqual(first, second)


class FakeRegistry:
    def __init__(self):
        self.resources = {}
        self.upserts = []
        self.writes = []

    def upsert(self, resource):
        self.resources[resource.id] = resource
        self.upserts.append(resource)
        return None

    def write(self, resources):
        self.writes.append(resources)

    def get(self, resource_id):
        return self.resources[resource_id]


class FakeDiscovery:
    def __init__(self, collections):
        self._collections = collections

    def root_health(self):
        return {"ok": True}

    def calendar_collections(self):
        return list(self._collections)

    def calendars(self):
        return [item for item in self._collections if item.supports("VEVENT")]

    def task_lists(self):
        return [item for item in self._collections if item.supports("VTODO")]

    def addressbooks(self):
        return []


class DiscoveryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.personal = DiscoveredCollection(
            kind="calendar",
            href="/remote.php/dav/calendars/openclaw/personal/",
            name="Personal",
            resource_id="nextcloud-calendar-personal",
            components=("VEVENT", "VTODO"),
            privileges=("{DAV:}read", "{DAV:}bind"),
            can_read=True,
            can_create=True,
        )
        self.tasks_readonly = DiscoveredCollection(
            kind="calendar",
            href="/remote.php/dav/calendars/openclaw/tasks/",
            name="Aufgaben",
            resource_id="nextcloud-calendar-tasks",
            components=("VTODO",),
            privileges=("{DAV:}read",),
            can_read=True,
            can_create=False,
        )

    def assistant(self) -> PersonalAssistant:
        value = PersonalAssistant.__new__(PersonalAssistant)
        value.nextcloud_discovery = FakeDiscovery([self.personal, self.tasks_readonly])
        value.registry = FakeRegistry()
        value.tool_settings = ToolSettings(path=Path("/tmp/tools.toml"))
        value.config = SimpleNamespace(
            nextcloud=SimpleNamespace(
                resource_id="nextcloud-main",
                base_url_env="NEXTCLOUD_URL",
                allowed_file_roots=("Assistent",),
            )
        )
        return value

    def test_read_only_discovery_does_not_persist_or_configure(self) -> None:
        assistant = self.assistant()
        calendars = assistant.calendar_discover()
        tasks = assistant.tasks_discover()
        self.assertTrue(calendars["read_only"])
        self.assertTrue(tasks["read_only"])
        self.assertEqual(calendars["count"], 1)
        self.assertEqual(tasks["count"], 2)
        self.assertEqual(assistant.registry.upserts, [])
        self.assertEqual(assistant.registry.writes, [])

    def test_calendar_configuration_requires_event_and_write_capability(self) -> None:
        assistant = self.assistant()
        with patch("personal_assistant.tool_setup.configure_calendar_tools") as configure:
            configure.return_value = {"ok": True}
            result = assistant.calendar_configure(resource_id=self.personal.resource_id)
        self.assertTrue(result["ok"])
        self.assertTrue(result["explicit_user_selection"])
        resource = assistant.registry.upserts[-1]
        self.assertEqual(resource.permissions, ("read", "create"))
        self.assertEqual(resource.metadata["components"], ["VEVENT", "VTODO"])

        with self.assertRaises(ValueError):
            assistant.calendar_configure(resource_id=self.tasks_readonly.resource_id)

    def test_task_configuration_can_be_read_only(self) -> None:
        assistant = self.assistant()
        with patch("personal_assistant.tool_setup.configure_tasks_tools") as configure:
            configure.return_value = {"ok": True}
            result = assistant.tasks_configure(
                resource_id=self.tasks_readonly.resource_id,
                allow_create=False,
                allow_list=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(assistant.registry.upserts[-1].permissions, ("read",))

        with self.assertRaises(PermissionError):
            assistant.tasks_configure(
                resource_id=self.tasks_readonly.resource_id,
                allow_create=True,
                allow_list=True,
            )

    def test_full_nextcloud_discovery_persists_vtodo_only_collection(self) -> None:
        assistant = self.assistant()
        result = assistant.discover_nextcloud(persist=True)
        self.assertEqual(result["task_lists"][1]["name"], "Aufgaben")
        written = assistant.registry.writes[-1]
        task_resource = next(item for item in written if item.id == self.tasks_readonly.resource_id)
        self.assertEqual(task_resource.metadata["components"], ["VTODO"])
        self.assertEqual(task_resource.permissions, ("read",))


class AgentContractTests(unittest.TestCase):
    def test_cli_and_tool_registry_expose_both_discovery_flows(self) -> None:
        root = parser()
        calendar = root.parse_args(["calendar", "discover"])
        tasks = root.parse_args(["tasks", "discover"])
        self.assertEqual(calendar.calendar_command, "discover")
        self.assertEqual(tasks.tasks_command, "discover")

        ids = {item.id for item in build_tool_registry(ToolSettings(path=Path("/tmp/tools.toml")))}
        self.assertIn("nextcloud.calendar.discover", ids)
        self.assertIn("nextcloud.calendar.configure", ids)
        self.assertIn("nextcloud.tasks.discover", ids)
        self.assertIn("nextcloud.tasks.configure", ids)

    def test_configure_commands_have_explicit_yes_gate(self) -> None:
        root = parser()
        calendar = root.parse_args([
            "calendar", "configure", "--resource", "nextcloud-calendar-personal"
        ])
        tasks = root.parse_args([
            "tasks", "configure", "--resource", "nextcloud-calendar-tasks"
        ])
        self.assertFalse(calendar.yes)
        self.assertFalse(tasks.yes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
