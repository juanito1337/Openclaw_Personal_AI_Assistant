from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from personal_assistant.cli import parser
from personal_assistant.connectors.nextcloud.contacts import Contact, NextcloudContacts
from personal_assistant.connectors.nextcloud.discovery import DiscoveredCollection
from personal_assistant.contact_tools import normalize_contact_update, update_vcard
from personal_assistant.models import ActionPlan, Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.service import PersonalAssistant
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import DirectContactsToolSettings, NextcloudToolSettings, ToolSettings


class Response:
    def __init__(self, status: int, data: bytes = b"", headers=None) -> None:
        self.status = status
        self.data = data
        self.headers = dict(headers or {})
        self.reason = ""
        self.url = "https://nextcloud.invalid/contact.vcf"


class UpdateClient:
    def __init__(self, *, conflict: bool = False) -> None:
        self.calls = []
        self.conflict = conflict
        self.vcard = (
            b"BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-1\r\n"
            b"FN:Max Alt\r\nN:Alt;Max;;;\r\nEMAIL:max@old.example\r\n"
            b"TEL:+49123\r\nADR:;;Musterweg 1;Hamburg;;;Deutschland\r\n"
            b"BDAY:19800101\r\nEND:VCARD\r\n"
        )
        self.etag = '"etag-1"'

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == "PUT":
            if self.conflict:
                return Response(412)
            self.vcard = kwargs["data"]
            self.etag = '"etag-2"'
            return Response(204)
        if method == "GET":
            return Response(200, self.vcard, {"ETag": self.etag})
        raise AssertionError(method)


class FakePolicy:
    def decide(self, resource_id, action, payload):
        return SimpleNamespace(allowed=True, requires_approval=action == "contacts.update", reason="ok")


class FakeRegistry:
    def __init__(self, resource):
        self.resource = resource

    def get(self, resource_id):
        assert resource_id == self.resource.id
        return self.resource


class FakeContacts:
    def __init__(self, contacts):
        self.contacts = list(contacts)
        self.refreshed = None

    def list_contacts(self, collection):
        return list(self.contacts)

    def read_contact(self, href, fallback_uid=""):
        return self.refreshed or self.contacts[0]


class FakeActions:
    def __init__(self, contacts):
        self.contacts = contacts
        self.plan_value = None
        self.approved = False

    def plan(self, action_type, resource_id, payload, idempotency_key=""):
        self.plan_value = ActionPlan(
            id="update-1",
            idempotency_key=idempotency_key,
            action_type=action_type,
            resource_id=resource_id,
            payload=payload,
            status="proposed",
            requires_approval=True,
            created_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:00:00+00:00",
            error="",
        )
        return self.plan_value

    def approve_configured_contacts_update(self, action_id, evidence):
        self.approved = True
        self.plan_value = replace(self.plan_value, status="approved")
        return self.plan_value

    def execute_contact_update(self, action_id):
        changes = self.plan_value.payload["changes"]
        old = self.contacts.contacts[0]
        self.contacts.refreshed = Contact(
            uid=old.uid,
            name=changes.get("name", old.name),
            emails=tuple(changes.get("emails", old.emails)),
            phones=tuple(changes.get("phones", old.phones)),
            organization=changes.get("organization", old.organization),
            note=changes.get("note", old.note),
            raw=self.plan_value.payload["vcard"],
            href=old.href,
            etag='"etag-2"',
        )
        self.plan_value = replace(self.plan_value, status="completed")
        return self.plan_value, False


class VCardUpdateTests(unittest.TestCase):
    def test_partial_update_preserves_unrelated_properties(self) -> None:
        raw = (
            "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-1\r\n"
            "FN:Max Alt\r\nN:Alt;Max;;;\r\nEMAIL;TYPE=WORK:max@old.example\r\n"
            "TEL;TYPE=CELL:+49123\r\nADR:;;Musterweg 1;Hamburg;;;Deutschland\r\n"
            "PHOTO;VALUE=URI:https://example.invalid/photo.jpg\r\nBDAY:19800101\r\n"
            "X-NEXTCLOUD-CUSTOM:bleibt\r\nEND:VCARD\r\n"
        )
        changes = normalize_contact_update(
            name="Max Neu",
            emails=("max@new.example",),
            phones=(),
            organization="Neue Firma GmbH",
            note="Geprueft",
        )
        updated = update_vcard(raw, "uid-1", changes)
        self.assertIn("UID:uid-1", updated)
        self.assertIn("FN:Max Neu", updated)
        self.assertIn("EMAIL;TYPE=INTERNET:max@new.example", updated)
        self.assertNotIn("TEL;", updated)
        self.assertIn("ORG:Neue Firma GmbH", updated)
        self.assertIn("NOTE:Geprueft", updated)
        self.assertIn("ADR:;;Musterweg 1;Hamburg;;;Deutschland", updated)
        self.assertIn("PHOTO;VALUE=URI:https://example.invalid/photo.jpg", updated)
        self.assertIn("BDAY:19800101", updated)
        self.assertIn("X-NEXTCLOUD-CUSTOM:bleibt", updated)

    def test_empty_or_invalid_update_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_contact_update()
        with self.assertRaises(ValueError):
            normalize_contact_update(name="")


class ConnectorUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book = DiscoveredCollection(
            kind="addressbook",
            href="/remote.php/dav/addressbooks/users/openclaw/contacts/",
            name="Contacts",
            resource_id="book-1",
            can_read=True,
            can_create=True,
            can_update=True,
        )

    def test_update_uses_if_match_and_verifies(self) -> None:
        client = UpdateClient()
        connector = NextcloudContacts(SimpleNamespace(), client)
        current = connector.read_contact(
            "/remote.php/dav/addressbooks/users/openclaw/contacts/contact-1.vcf",
            fallback_uid="uid-1",
        )
        changes = normalize_contact_update(phones=("+49 999 1234567",))
        vcard = update_vcard(current.raw, current.uid, changes)
        verified = connector.update_contact(
            self.book,
            href=current.href,
            uid=current.uid,
            vcard=vcard,
            etag=current.etag,
        )
        put = next(call for call in client.calls if call[0] == "PUT")
        self.assertEqual(put[2]["headers"]["If-Match"], '"etag-1"')
        self.assertEqual(verified.phones, ("+499991234567",))
        self.assertEqual(verified.uid, "uid-1")

    def test_conflict_never_overwrites_silently(self) -> None:
        client = UpdateClient(conflict=True)
        connector = NextcloudContacts(SimpleNamespace(), client)
        with self.assertRaisesRegex(RuntimeError, "zwischenzeitlich"):
            connector.update_contact(
                self.book,
                href="/remote.php/dav/addressbooks/users/openclaw/contacts/contact-1.vcf",
                uid="uid-1",
                vcard=client.vcard.decode(),
                etag='"etag-1"',
            )

    def test_href_outside_addressbook_is_rejected(self) -> None:
        connector = NextcloudContacts(SimpleNamespace(), UpdateClient())
        with self.assertRaises(PermissionError):
            connector.update_contact(
                self.book,
                href="/remote.php/dav/addressbooks/users/other/contacts/x.vcf",
                uid="uid-1",
                vcard="BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-1\r\nEND:VCARD\r\n",
                etag='"etag-1"',
            )


class ServiceUpdateTests(unittest.TestCase):
    def assistant(self, contacts):
        resource = Resource(
            id="book-1",
            kind="addressbook",
            connector="nextcloud",
            enabled=True,
            remote_id="/remote.php/dav/addressbooks/users/openclaw/contacts/",
            permissions=("read", "create", "update"),
            metadata={
                "href": "/remote.php/dav/addressbooks/users/openclaw/contacts/",
                "name": "Contacts",
                "server_can_read": True,
                "server_can_create": True,
                "server_can_update": True,
            },
        )
        fake_contacts = FakeContacts(contacts)
        value = PersonalAssistant.__new__(PersonalAssistant)
        value.registry = FakeRegistry(resource)
        value.tool_settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                contacts=DirectContactsToolSettings(
                    enabled=True,
                    resource_id=resource.id,
                    allow_list=True,
                    allow_create=True,
                    allow_update=True,
                    max_results=500,
                )
            ),
        )
        value.nextcloud_contacts = fake_contacts
        value.policy = FakePolicy()
        value.actions = FakeActions(fake_contacts)
        return value

    def test_exact_uid_update_requires_explicit_action_and_returns_before_after(self) -> None:
        current = Contact(
            uid="uid-1",
            name="Max Alt",
            emails=("max@example.com",),
            phones=("+49123",),
            organization="Alt GmbH",
            note="",
            raw=(
                "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-1\r\nFN:Max Alt\r\n"
                "N:Alt;Max;;;\r\nEMAIL:max@example.com\r\nTEL:+49123\r\nEND:VCARD\r\n"
            ),
            href="/remote.php/dav/addressbooks/users/openclaw/contacts/contact-1.vcf",
            etag='"etag-1"',
        )
        assistant = self.assistant([current])
        result = assistant.contact_update(
            uid="uid-1",
            phones=("+49 999 1234567",),
            expected_name="Max Alt",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["before"]["phones"], ["+49123"])
        self.assertEqual(result["after"]["phones"], ["+499991234567"])
        self.assertTrue(assistant.actions.approved)
        self.assertEqual(assistant.actions.plan_value.action_type, "contacts.update")
        self.assertTrue(assistant.actions.plan_value.payload["optimistic_concurrency"])

    def test_email_collision_is_blocked_before_write(self) -> None:
        first = Contact(
            uid="uid-1", name="Max", emails=("max@example.com",), phones=(), organization="",
            raw="BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-1\r\nFN:Max\r\nEND:VCARD\r\n",
            href="/remote.php/dav/addressbooks/users/openclaw/contacts/1.vcf", etag='"1"',
        )
        second = Contact(
            uid="uid-2", name="Erika", emails=("erika@example.com",), phones=(), organization="",
            raw="BEGIN:VCARD\r\nVERSION:3.0\r\nUID:uid-2\r\nFN:Erika\r\nEND:VCARD\r\n",
            href="/remote.php/dav/addressbooks/users/openclaw/contacts/2.vcf", etag='"2"',
        )
        assistant = self.assistant([first, second])
        result = assistant.contact_update(uid="uid-1", emails=("erika@example.com",))
        self.assertFalse(result["ok"])
        self.assertFalse(result["updated"])
        self.assertIsNone(assistant.actions.plan_value)


class ContractUpdateTests(unittest.TestCase):
    def test_cli_registry_and_policy_expose_guarded_update(self) -> None:
        root = parser()
        args = root.parse_args([
            "contacts", "update", "--uid", "uid-1", "--phone", "+49 123 456789", "--yes"
        ])
        self.assertEqual(args.contacts_command, "update")
        self.assertTrue(args.yes)
        settings = ToolSettings(
            path=Path("/tmp/tools.toml"),
            nextcloud=NextcloudToolSettings(
                contacts=DirectContactsToolSettings(
                    enabled=True,
                    resource_id="book-1",
                    allow_list=True,
                    allow_create=True,
                    allow_update=True,
                )
            ),
        )
        ids = {item.id for item in build_tool_registry(settings)}
        self.assertIn("nextcloud.contacts.update", ids)

        with tempfile.TemporaryDirectory() as tmp:
            root_path = Path(tmp)
            registry = ResourceRegistry(root_path / "resources.toml")
            registry.write([
                Resource(
                    id="book-1",
                    kind="addressbook",
                    connector="nextcloud",
                    enabled=True,
                    permissions=("read", "update"),
                )
            ])
            policy = PolicyEngine(root_path / "missing-policies.toml", registry)
            decision = policy.decide("book-1", "contacts.update", {})
            self.assertTrue(decision.allowed)
            self.assertTrue(decision.requires_approval)
            self.assertFalse(policy.decide("book-1", "contacts.delete", {}).allowed)


if __name__ == "__main__":
    unittest.main()
