from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

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
        self.folders = ["INBOX", "Archiv", "Trash", "Agent/Virusverdacht"]
        self.messages = {"Archiv": [Envelope("42", "Auftragsbestätigung 4711", "Müller", "m@example.de", "2026-07-22")], "INBOX": []}
    def list_folders(self): return self.folders, ""
    def list_envelopes(self, folder, limit=None): return list(self.messages.get(folder, []))[:limit], ""
    def move_message(self, source, destination, message_id):
        msg = next((x for x in self.messages[source] if x.mailbox_id == message_id), None)
        if not msg: return OperationResult(False, "move-failed", "missing")
        self.messages[source].remove(msg); self.messages.setdefault(destination, []).append(msg)
        return OperationResult(True, "moved", destination=destination)


def build(tmp):
    registry = ResourceRegistry(tmp/'resources.toml')
    registry.write([Resource(id='mail-agent', kind='tool', connector='local', permissions=('read','move'))])
    storage = AssistantStorage(tmp/'assistant.sqlite3')
    policy = PolicyEngine(tmp/'policies.toml', registry)
    settings = MailMoveToolSettings(enabled=True)
    return MailMoveService(settings, registry, policy, storage, FakeClient()), storage


def main():
    with tempfile.TemporaryDirectory() as td:
        service, storage = build(Path(td))
        listed = service.list_messages('Archiv', limit=10)
        assert listed['messages'][0]['mailbox_id'] == '42'
        dry = service.move(source='Archiv', destination='INBOX', message_id='42', expected_subject='Auftragsbestätigung 4711', dry_run=True)
        assert dry['dry_run'] is True
        result = service.move(source='Archiv', destination='INBOX', message_id='42', expected_subject='Auftragsbestätigung 4711')
        assert result['ok'] and not result['duplicate']
        again = service.move(source='Archiv', destination='INBOX', message_id='42', expected_subject='Auftragsbestätigung 4711')
        assert again['duplicate']
        try:
            service2, _ = build(Path(td)/'b')
            service2.move(source='Archiv', destination='Trash', message_id='42')
            raise AssertionError('Trash must be denied')
        except PermissionError:
            pass
        ts=ToolSettings(path=Path(td)/'tools.toml')
        ts.mail.move.enabled=True
        ids={x.id for x in build_tool_registry(ts)}
        assert {'mail.move-status','mail.list','mail.move'} <= ids
        assert any(x.action_type=='mail.move' and x.status=='completed' for x in storage.list_actions(limit=20))
    print('test_mail_move_tool: OK')

if __name__ == '__main__': main()
