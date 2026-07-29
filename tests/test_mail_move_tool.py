from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from mail_agent.models import Envelope, OperationResult
from personal_assistant.mail_move import MailMoveService
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_settings import MailMoveToolSettings, ToolSettings
from personal_assistant.tool_registry import build_tool_registry


class FakeClient:
    def __init__(self):
        self.folders = ["INBOX", "Archiv", "Trash", "Agent/Pruefen", "Agent/Virusverdacht"]
        self.messages = {
            "Archiv": [Envelope("42", "Auftragsbestätigung 4711", "Müller", "m@example.de", "2026-07-22")],
            "Agent/Pruefen": [Envelope("77", "Treffen TA", "Dirk Jätzel", "dj@ib-jaetzel.de", "2026-07-28")],
            "INBOX": [],
        }
        self.templates = []
        self.config = SimpleNamespace(
            mailbox=SimpleNamespace(from_header="Jan <jan@example.de>")
        )
    def list_folders(self): return self.folders, ""
    def list_envelopes(self, folder, limit=None): return list(self.messages.get(folder, []))[:limit], ""
    def move_message(self, source, destination, message_id):
        msg = next((x for x in self.messages[source] if x.mailbox_id == message_id), None)
        if not msg: return OperationResult(False, "move-failed", "missing")
        self.messages[source].remove(msg); self.messages.setdefault(destination, []).append(msg)
        return OperationResult(True, "moved", destination=destination)
    def export_message(self, folder, message_id, destination):
        destination.write_bytes(
            b"From: Dirk Jaetzel <dj@ib-jaetzel.de>\r\n"
            b"To: Jan <jan@example.de>\r\n"
            b"Subject: Treffen TA\r\n"
            b"Message-ID: <treffen-ta@example.de>\r\n\r\n"
            b"Hallo Jan,\r\npasst dir Donnerstag?\r\n"
        )
        return OperationResult(True, "exported", path=str(destination))
    def send_template(self, template, save_copy=None):
        self.templates.append(template)
        return OperationResult(True, "sent")


def build(tmp):
    registry = ResourceRegistry(tmp/'resources.toml')
    registry.resources['mail-agent'] = Resource(
        id='mail-agent', kind='tool', connector='local',
        permissions=('read','move','forward'),
    )
    storage = AssistantStorage(tmp/'assistant.sqlite3')
    policy = PolicyEngine(tmp/'policies.toml', registry)
    settings = MailMoveToolSettings(enabled=True)
    return MailMoveService(settings, registry, policy, storage, FakeClient()), storage


def main():
    with tempfile.TemporaryDirectory() as td:
        service, storage = build(Path(td))
        listed = service.list_messages('Archiv', limit=10)
        assert listed['messages'][0]['mailbox_id'] == '42'
        found = service.search_messages('Jätzel', limit=10)
        assert found['messages'][0]['folder'] == 'Agent/Pruefen'
        read = service.read('Agent/Pruefen', '77', expected_subject='Treffen TA')
        assert 'Donnerstag' in read['message']['body_text']
        draft = service.draft_reply(
            'Agent/Pruefen', '77', 'Ja, Donnerstag passt.', expected_subject='Treffen TA'
        )
        assert draft['status'] == 'proposed' and draft['requires_explicit_approval']
        try:
            service.send_reply(draft['draft_id'])
            raise AssertionError('Unapproved reply must not be sent')
        except PermissionError:
            pass
        sent = service.send_reply(draft['draft_id'], approved=True)
        assert sent['ok'] and 'In-Reply-To: <treffen-ta@example.de>' in service._client_override.templates[0]
        composed = service.draft_message(
            'Jonas <jonas@example.de>', 'Vorstellung', 'Hallo Jonas,\n\nich bin Jan.'
        )
        assert composed['status'] == 'proposed'
        assert composed['to'] == 'jonas@example.de'
        assert len(service._client_override.templates) == 1
        try:
            service.send_message(composed['draft_id'])
            raise AssertionError('Unapproved new message must not be sent')
        except PermissionError:
            pass
        try:
            service.send_reply(composed['draft_id'], approved=True)
            raise AssertionError('A compose draft must not be accepted by reply-send')
        except PermissionError:
            pass
        compose_sent = service.send_message(composed['draft_id'], approved=True)
        assert compose_sent['ok']
        assert 'To: jonas@example.de' in service._client_override.templates[1]
        assert 'Subject: Vorstellung' in service._client_override.templates[1]
        assert 'In-Reply-To:' not in service._client_override.templates[1]
        try:
            service.draft_message('jonas@example.de\nBcc: victim@example.de', 'Test', 'Text')
            raise AssertionError('Header injection must be denied')
        except ValueError:
            pass
        try:
            service.move(source='Agent/Pruefen', destination='INBOX', message_id='77')
            raise AssertionError('Review mail must not be moved')
        except PermissionError:
            pass
        dry = service.move(source='Archiv', destination='INBOX', message_id='42', expected_subject='Auftragsbestätigung 4711', dry_run=True)
        assert dry['dry_run'] is True
        result = service.move(source='Archiv', destination='INBOX', message_id='42', expected_subject='Auftragsbestätigung 4711')
        assert result['ok'] and not result['duplicate']
        again = service.move(source='Archiv', destination='INBOX', message_id='42', expected_subject='Auftragsbestätigung 4711')
        assert again['duplicate']
        try:
            service2, storage2 = build(Path(td)/'b')
            service2.move(source='Archiv', destination='Trash', message_id='42')
            raise AssertionError('Trash must be denied')
        except PermissionError:
            pass
        ts=ToolSettings(path=Path(td)/'tools.toml')
        ts.mail.move.enabled=True
        ids={x.id for x in build_tool_registry(ts)}
        assert {
            'mail.move-status','mail.list','mail.search','mail.read',
            'mail.reply-draft','mail.reply-send',
            'mail.compose-draft','mail.compose-send','mail.move',
        } <= ids
        assert any(x.action_type=='mail.move' and x.status=='completed' for x in storage.list_actions(limit=20))
        storage2.close()
        storage.close()
    print('test_mail_move_tool: OK')

if __name__ == '__main__': main()
