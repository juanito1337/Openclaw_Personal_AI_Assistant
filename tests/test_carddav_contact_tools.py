from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.models import ParsedMessage
from personal_assistant.antivirus import AntivirusResult
from personal_assistant.cli import parser
from personal_assistant.connectors.nextcloud.contacts import Contact, NextcloudContacts
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection
from personal_assistant.contact_tools import build_vcard, candidate_from_mail, candidate_manual
from personal_assistant.models import ActionPlan, Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import (
    DirectContactsToolSettings,
    NextcloudToolSettings,
    ToolSettings,
)


class FakeResponse:
    def __init__(self, status: int, data: bytes = b"") -> None:
        self.status = status
        self.data = data
        self.headers = {}
        self.reason = ""
        self.url = "https://nextcloud.invalid/test"


class ContactClient:
    def __init__(self) -> None:
        self.calls = []
        self.vcard = b""

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "PUT":
            self.vcard = kwargs["data"]
            return FakeResponse(201)
        if method == "GET":
            return FakeResponse(200, self.vcard)
        raise AssertionError(method)


class FakeRegistry:
    def __init__(self, resources=None):
        self.resources = {item.id: item for item in (resources or [])}
        self.upserts = []
        self.writes = []

    def get(self, resource_id):
        return self.resources[resource_id]

    def upsert(self, resource):
        self.resources[resource.id] = resource
        self.upserts.append(resource)
        return None

    def write(self, resources):
        self.writes.append(resources)


class FakeDiscovery:
    def __init__(self, addressbooks):
        self._addressbooks = addressbooks

    def root_health(self):
        return {"ok": True}

    def addressbooks(self):
        return list(self._addressbooks)


class FakePolicy:
    def decide(self, resource_id, action, payload):
        return SimpleNamespace(allowed=True, requires_approval=False, reason="ok")


class FakeActions:
    def __init__(self):
        self.plans = []

    def plan(self, action_type, resource_id, payload, idempotency_key=""):
        plan = ActionPlan(
            id="action-1",
            idempotency_key=idempotency_key,
            action_type=action_type,
            resource_id=resource_id,
            payload=payload,
            status="approved",
            requires_approval=False,
            created_at="2026-07-25T00:00:00+00:00",
            updated_at="2026-07-25T00:00:00+00:00",
            error="",
        )
        self.plans.append(plan)
        return plan

    def approve_configured_contacts_tool(self, action_id, evidence):
        return self.plans[-1]

    def execute_contact_create(self, action_id):
        plan = self.plans[-1]
        completed = ActionPlan(
            id=plan.id,
            idempotency_key=plan.idempotency_key,
            action_type=plan.action_type,
            resource_id=plan.resource_id,
            payload=plan.payload,
            status="completed",
            requires_approval=False,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            error="",
        )
        return completed, False


class FakeContacts:
    def __init__(self, contacts=None):
        self.contacts = list(contacts or [])

    def list_contacts(self, collection):
        return list(self.contacts)


class CandidateTests(unittest.TestCase):
    def test_extracts_sender_company_and_labeled_phone(self) -> None:
        message = ParsedMessage(
            stable_key="mail-1",
            mailbox_id="42",
            source_folder="INBOX",
            raw=b"mail",
            subject="Anfrage",
            sender_name="Max Mustermann",
            sender_addr="max@example.com",
            received_at="2026-07-25T10:00:00+02:00",
            body_text=(
                "Hallo,\n\nMit freundlichen Gruessen\nMax Mustermann\n"
                "Muster Lieferant GmbH\nTelefon: +49 123 456789\n"
            ),
        )
        candidate = candidate_from_mail(message)
        self.assertEqual(candidate.name, "Max Mustermann")
        self.assertEqual(candidate.emails, ("max@example.com",))
        self.assertEqual(candidate.organization, "Muster Lieferant GmbH")
        self.assertEqual(candidate.phones, ("+49123456789",))
        self.assertFalse(candidate.automated_sender)

    def test_no_reply_is_flagged(self) -> None:
        message = ParsedMessage(
            stable_key="mail-2",
            mailbox_id="1",
            source_folder="INBOX",
            raw=b"mail",
            sender_name="System",
            sender_addr="no-reply@example.com",
        )
        self.assertTrue(candidate_from_mail(message).automated_sender)

    def test_vcard_is_create_only_payload_and_escapes_values(self) -> None:
        candidate = candidate_manual(
            name="Max Mustermann",
            emails=("max@example.com",),
            phones=("+49 123 456789",),
            organization="Muster, GmbH",
            source="manual",
        )
        vcard = build_vcard(candidate, "uid-1", note="Test; Notiz")
        self.assertIn("BEGIN:VCARD", vcard)
        self.assertIn("UID:uid-1", vcard)
        self.assertIn("EMAIL;TYPE=INTERNET:max@example.com", vcard)
        self.assertIn("ORG:Muster\\, GmbH", vcard)
        self.assertIn("NOTE:Test\\; Notiz", vcard)


class ConnectorTests(unittest.TestCase):
    def test_create_uses_if_none_match_and_verifies_uid(self) -> None:
        client = ContactClient()
        connector = NextcloudContacts(SimpleNamespace(), client)
        addressbook = DiscoveredCollection(
            kind="addressbook",
            href="/remote.php/dav/addressbooks/users/openclaw/contacts/",
            name="Contacts",
            resource_id="nextcloud-addressbook-1",
            can_read=True,
            can_create=True,
        )
        candidate = candidate_manual(name="Max Mustermann", emails=("max@example.com",))
        vcard = build_vcard(candidate, "uid-1")
        href = connector.create_contact(addressbook, vcard, "uid-1")
        self.assertTrue(href.endswith("/uid-1.vcf"))
        put = client.calls[0]
        self.assertEqual(put[0], "PUT")
        self.assertEqual(put[2]["headers"]["If-None-Match"], "*")
        self.assertEqual(client.calls[1][0], "GET")


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book = DiscoveredCollection(
            kind="addressbook",
            href="/remote.php/dav/addressbooks/users/openclaw/contacts/",
            name="Kontakte",
            resource_id="nextcloud-addressbook-contacts",
            privileges=("{DAV:}read", "{DAV:}bind"),
            can_read=True,
            can_create=True,
        )
        self.resource = Resource(
            id=self.book.resource_id,
            kind="addressbook",
            connector="nextcloud",
            enabled=True,
            remote_id=self.book.href,
            permissions=("read", "create"),
            metadata={
                "href": self.book.href,
                "name": self.book.name,
                "server_can_read": True,
                "server_can_create": True,
            },
        )

    def assistant(self, contacts=None) -> PersonalAssistant:
        value = PersonalAssistant.__new__(PersonalAssistant)
        value.nextcloud_discovery = FakeDiscovery([self.book])
        value.registry = FakeRegistry([self.resource])
        value.tool_settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                contacts=DirectContactsToolSettings(
                    enabled=True,
                    resource_id=self.resource.id,
                    allow_list=True,
                    allow_create=True,
                    max_results=500,
                )
            ),
        )
        value.nextcloud_contacts = FakeContacts(contacts)
        value.policy = FakePolicy()
        value.actions = FakeActions()
        return value

    def test_discovery_and_configuration_are_explicit(self) -> None:
        assistant = self.assistant()
        result = assistant.contacts_discover()
        self.assertTrue(result["read_only"])
        self.assertEqual(result["count"], 1)
        with patch("personal_assistant.tool_setup.configure_contacts_tools") as configure:
            configure.return_value = {"ok": True}
            configured = assistant.contacts_configure(resource_id=self.book.resource_id)
        self.assertTrue(configured["explicit_user_selection"])
        self.assertEqual(assistant.registry.upserts[-1].permissions, ("read", "create"))

    def test_list_search_and_duplicate_check_do_not_expose_raw_vcard(self) -> None:
        existing = Contact(
            uid="1",
            name="Max Mustermann",
            emails=("max@example.com",),
            phones=("+49123",),
            organization="Muster GmbH",
            raw="BEGIN:VCARD\nSECRET\nEND:VCARD",
        )
        assistant = self.assistant([existing])
        listed = assistant.contacts_list(limit=100)
        self.assertEqual(listed["contacts"][0]["name"], "Max Mustermann")
        self.assertNotIn("raw", listed["contacts"][0])
        searched = assistant.contacts_search("muster", limit=10)
        self.assertEqual(searched["count"], 1)
        duplicate = assistant.contact_create(name="Max", emails=("MAX@example.com",))
        self.assertTrue(duplicate["duplicate"])
        self.assertFalse(duplicate["created"])

    def test_same_name_requires_explicit_collision_override(self) -> None:
        existing = Contact(
            uid="1", name="Max Mustermann", emails=("old@example.com",),
            phones=(), organization="", raw="",
        )
        assistant = self.assistant([existing])
        blocked = assistant.contact_create(name="Max Mustermann", emails=("new@example.com",))
        self.assertFalse(blocked["ok"])
        created = assistant.contact_create(
            name="Max Mustermann",
            emails=("new@example.com",),
            allow_name_collision=True,
        )
        self.assertTrue(created["ok"])
        self.assertTrue(created["created"])

    def test_from_mail_preview_and_no_reply_block(self) -> None:
        assistant = self.assistant([])
        message = ParsedMessage(
            stable_key="mail-1",
            mailbox_id="42",
            source_folder="INBOX",
            raw=b"mail",
            subject="Anfrage",
            sender_name="Max Mustermann",
            sender_addr="max@example.com",
            received_at="2026-07-25T10:00:00+02:00",
            body_text="Telefon: +49 123 456789",
        )
        assistant.mail_move_service = SimpleNamespace(read_message=lambda *args, **kwargs: message)
        assistant.antivirus = SimpleNamespace(
            scan_bytes=lambda *args, **kwargs: AntivirusResult(
                status="clean", sha256="x", size_bytes=4, source_type="contact-from-mail",
                name="selected-mail.eml", scanner="test", scanner_identity="test",
            )
        )
        assistant.tool_settings.security.antivirus.enabled = True
        assistant.tool_settings.security.antivirus.fail_closed = True
        preview = assistant.contact_from_mail(folder="INBOX", message_id="42", dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["candidate"]["emails"], ["max@example.com"])

        no_reply = ParsedMessage(
            stable_key="mail-2", mailbox_id="43", source_folder="INBOX", raw=b"mail",
            sender_name="System", sender_addr="no-reply@example.com",
        )
        assistant.mail_move_service = SimpleNamespace(read_message=lambda *args, **kwargs: no_reply)
        blocked = assistant.contact_from_mail(folder="INBOX", message_id="43", dry_run=False)
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["automated_sender_blocked"])


class ContractTests(unittest.TestCase):
    def test_cli_and_registry_expose_contact_tools(self) -> None:
        root = parser()
        self.assertEqual(root.parse_args(["contacts", "discover"]).contacts_command, "discover")
        create = root.parse_args([
            "contacts", "create", "--name", "Max", "--email", "max@example.com"
        ])
        self.assertFalse(create.yes)
        settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                contacts=DirectContactsToolSettings(
                    enabled=True,
                    resource_id="nextcloud-addressbook-1",
                    allow_list=True,
                    allow_create=True,
                )
            ),
        )
        ids = {item.id for item in build_tool_registry(settings)}
        self.assertIn("nextcloud.contacts.discover", ids)
        self.assertIn("nextcloud.contacts.configure", ids)
        self.assertIn("nextcloud.contacts.list", ids)
        self.assertIn("nextcloud.contacts.search", ids)
        self.assertIn("nextcloud.contacts.create", ids)
        self.assertIn("nextcloud.contacts.from-mail-preview", ids)
        self.assertIn("nextcloud.contacts.from-mail-create", ids)

    def test_policy_allows_create_but_keeps_update_and_delete_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ResourceRegistry(root / "resources.toml")
            registry.write([
                Resource(
                    id="nextcloud-addressbook-1",
                    kind="addressbook",
                    connector="nextcloud",
                    enabled=True,
                    permissions=("read", "create"),
                )
            ])
            policy = PolicyEngine(root / "policies.toml", registry)
            self.assertTrue(policy.decide("nextcloud-addressbook-1", "contacts.create", {}).allowed)
            self.assertFalse(policy.decide("nextcloud-addressbook-1", "contacts.write", {}).allowed)
            self.assertFalse(policy.decide("nextcloud-addressbook-1", "contacts.delete", {}).allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
