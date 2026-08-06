from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from personal_assistant.config import AssistantConfig, NextcloudConfig, RuntimeConfig, SearchConfig
from personal_assistant.connectors.nextcloud.client import DavResponse
from personal_assistant.connectors.nextcloud.discovery import NextcloudDiscovery
from personal_assistant.env import load_env, update_env
from personal_assistant.extractors import chunks, extract_text
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.settings import SettingsService
from personal_assistant.storage import AssistantStorage


class FakeClient:
    username = "personal-agent"

    def status(self):
        return {"installed": True, "maintenance": False, "versionstring": "34.0.1"}

    def request(self, method, path, **kwargs):
        if "calendars" in path:
            data = b"""<?xml version='1.0'?><d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'><d:response><d:href>/remote.php/dav/calendars/personal-agent/assistent/</d:href><d:propstat><d:prop><d:displayname>Assistent</d:displayname><d:resourcetype><d:collection/><c:calendar/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"""
        elif "addressbooks" in path:
            data = b"""<?xml version='1.0'?><d:multistatus xmlns:d='DAV:' xmlns:card='urn:ietf:params:xml:ns:carddav'><d:response><d:href>/remote.php/dav/addressbooks/users/personal-agent/contacts/</d:href><d:propstat><d:prop><d:displayname>Kontakte</d:displayname><d:resourcetype><d:collection/><card:addressbook/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"""
        else:
            data = b"<?xml version='1.0'?><d:multistatus xmlns:d='DAV:'/>"
        return DavResponse(207, "Multi-Status", {}, data, "https://example.test" + path)


class PersonalAssistantCoreTests(unittest.TestCase):
    def test_registry_and_policy_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_path = root / "resources.toml"
            registry = ResourceRegistry(registry_path)
            registry.write([
                Resource(
                    id="files",
                    kind="file-root",
                    connector="nextcloud",
                    permissions=("read", "create"),
                    metadata={"allowed_roots": ["Assistent"]},
                ),
                Resource(
                    id="calendar",
                    kind="calendar",
                    connector="nextcloud",
                    permissions=("read", "create"),
                ),
            ])
            policies = root / "policies.toml"
            policies.write_text('[approval]\nactions=["calendar.create"]\n', encoding="utf-8")
            engine = PolicyEngine(policies, ResourceRegistry(registry_path))
            allowed = engine.decide("files", "files.create", {"path": "Assistent/Rechnungen/a.pdf"})
            self.assertTrue(allowed.allowed)
            outside = engine.decide("files", "files.create", {"path": "Privat/a.pdf"})
            self.assertFalse(outside.allowed)
            denied = engine.decide("files", "files.delete", {"path": "Assistent/a.pdf"})
            self.assertFalse(denied.allowed)
            calendar = engine.decide("calendar", "calendar.create", {})
            self.assertTrue(calendar.allowed)
            self.assertTrue(calendar.requires_approval)

    def test_fts_index_and_idempotent_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            storage = AssistantStorage(Path(temp) / "assistant.sqlite3")
            try:
                storage.index_document(
                    source_type="email",
                    resource_id="mail-agent",
                    source_id="mail-1",
                    uri="mail-agent://mail-1",
                    title="Tankreinigung Wattenbek",
                    metadata={"sender": "firma@example.org"},
                    chunks=["Angebot fuer die Tankreinigung der Immobilie Wattenbek liegt vor."],
                )
                result = storage.search("Tankreinigung", limit=10)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].source_id, "mail-1")
                first = storage.create_action(
                    idempotency_key="upload:abc",
                    action_type="files.create",
                    resource_id="files",
                    payload={"path": "Assistent/a.pdf"},
                    requires_approval=False,
                )
                second = storage.create_action(
                    idempotency_key="upload:abc",
                    action_type="files.create",
                    resource_id="files",
                    payload={"path": "Assistent/a.pdf"},
                    requires_approval=False,
                )
                self.assertEqual(first.id, second.id)
            finally:
                storage.close()

    def test_nextcloud_discovery_parses_collections(self) -> None:
        discovery = NextcloudDiscovery(FakeClient())  # type: ignore[arg-type]
        self.assertEqual(discovery.root_health()["dav_status"], 207)
        calendars = discovery.calendars()
        books = discovery.addressbooks()
        self.assertEqual(calendars[0].name, "Assistent")
        self.assertEqual(books[0].name, "Kontakte")

    def test_extractors_and_chunking(self) -> None:
        text = extract_text("notes.txt", b"A\nB\nC")
        self.assertIn("A", text)
        parts = chunks("eins zwei drei vier fuenf", size=10, overlap=2)
        self.assertGreaterEqual(len(parts), 2)

    def test_central_env_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secrets.env"
            update_env(path, {"NEXTCLOUD_URL": "https://cloud.example", "NEXTCLOUD_TOKEN": "a b"})
            old = dict(os.environ)
            try:
                os.environ.pop("NEXTCLOUD_URL", None)
                os.environ.pop("NEXTCLOUD_TOKEN", None)
                load_env(path)
                self.assertEqual(os.environ["NEXTCLOUD_URL"], "https://cloud.example")
                self.assertEqual(os.environ["NEXTCLOUD_TOKEN"], "a b")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_safe_settings_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "config.toml"
            config_path.write_text(
                "[search]\ndefault_limit=20\nnextcloud_max_depth=6\nnextcloud_max_items=2000\nmax_file_bytes=25000000\n\n[nextcloud]\nenabled=false\n\n[self_management]\nallow_resource_discovery=true\n",
                encoding="utf-8",
            )
            storage = AssistantStorage(root / "assistant.sqlite3")
            try:
                service = SettingsService(config_path, storage)
                service.set_safe("search.default_limit", "30")
                self.assertEqual(service.list_safe()["search.default_limit"], 30)
                with self.assertRaises(ValueError):
                    service.set_safe("self_management.allow_code_changes", "true")
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()

class FakeWriteClient:
    username = "agent"

    def __init__(self) -> None:
        self.calls = []

    def validate_url(self):
        return "https://cloud.example"

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return DavResponse(201, "Created", {}, b"", "https://cloud.example" + path)


class PersonalAssistantConnectorTests(unittest.TestCase):
    def _config(self, root: Path) -> AssistantConfig:
        return AssistantConfig(
            runtime=RuntimeConfig(
                database=root / "assistant.sqlite3",
                log_file=root / "assistant.log",
                resources_file=root / "resources.toml",
                policies_file=root / "policies.toml",
                secrets_file=root / "secrets.env",
            ),
            search=SearchConfig(mail_snapshot_dir=root / "snapshots"),
            nextcloud=NextcloudConfig(enabled=True),
            path=root / "config.toml",
        )

    def test_nextcloud_file_upload_is_create_only(self) -> None:
        from personal_assistant.connectors.nextcloud.files import NextcloudFiles
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = FakeWriteClient()
            files = NextcloudFiles(self._config(root), client)  # type: ignore[arg-type]
            files.upload_new("Assistent/Test/a.txt", b"hello", "text/plain")
            method, path, kwargs = client.calls[-1]
            self.assertEqual(method, "PUT")
            self.assertEqual(kwargs["headers"]["If-None-Match"], "*")
            self.assertNotIn("DELETE", [call[0] for call in client.calls])

    def test_vcard_and_ics_parsers(self) -> None:
        from personal_assistant.connectors.nextcloud.calendar import NextcloudCalendar
        from personal_assistant.connectors.nextcloud.contacts import NextcloudContacts
        contact = NextcloudContacts._parse_vcard(
            "BEGIN:VCARD\nUID:1\nFN:Max Mustermann\nEMAIL:max@example.org\nTEL:+49123\nORG:Firma\nEND:VCARD\n",
            "fallback",
        )
        self.assertEqual(contact.name, "Max Mustermann")
        self.assertEqual(contact.emails, ("max@example.org",))
        event = NextcloudCalendar._parse_ics(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-1\nSUMMARY:Besichtigung\nDTSTART:20260722T100000Z\nDTEND:20260722T110000Z\nLOCATION:Wattenbek\nEND:VEVENT\nEND:VCALENDAR\n",
            "/event.ics",
            "etag",
        )
        self.assertEqual(event.uid, "event-1")
        self.assertEqual(event.location, "Wattenbek")

    def test_search_falls_back_for_invalid_fts_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            storage = AssistantStorage(Path(temp) / "assistant.sqlite3")
            try:
                storage.index_document(
                    source_type="email",
                    resource_id="mail-agent",
                    source_id="1",
                    uri="mail-agent://1",
                    title="Rechnung (Test)",
                    chunks=["Rechnung mit Sonderzeichen"],
                )
                result = storage.search('"unclosed', limit=5)
                self.assertIsInstance(result, list)
            finally:
                storage.close()
