from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from mail_agent.command import CommandResult
from mail_agent.himalaya import HimalayaClient
from mail_agent.models import Envelope, OperationResult
from personal_assistant.mail_move import MailMoveService
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_registry import build_tool_registry
from personal_assistant.tool_settings import MailMoveToolSettings, ToolSettings


class FakeClient:
    def __init__(self):
        self.folders = ["INBOX", "Archiv", "Trash", "Agent/Pruefen", "Agent/Virusverdacht"]
        self.messages = {
            "Archiv": [Envelope("42", "Auftragsbestätigung 4711", "Müller", "m@example.de", "2026-07-22")],
            "Agent/Pruefen": [Envelope("77", "Treffen TA", "Dirk Jätzel", "dj@ib-jaetzel.de", "2026-07-28")],
            "INBOX": [],
        }
        self.search_results = {
            ("Agent/Pruefen", ("jätzel",)): [
                Envelope("77", "Treffen TA", "Dirk Jätzel", "dj@ib-jaetzel.de", "2026-07-28")
            ],
            ("Archiv", ("jörn", "arp")): [
                Envelope("12", "Alte Nachricht", "Jörn Arp", "joern@example.de", "2024-01-02")
            ],
        }
        self.search_calls = []
        self.search_errors = set()
        self.templates = []
        self.config = SimpleNamespace(
            mailbox=SimpleNamespace(from_header="Jan <jan@example.de>")
        )
    def list_folders(self): return self.folders, ""
    def list_envelopes(self, folder, limit=None): return list(self.messages.get(folder, []))[:limit], ""
    def search_envelopes(self, folder, terms, limit=50):
        self.search_calls.append((folder, tuple(terms), limit))
        if folder in self.search_errors:
            return [], f"search failed: {folder}"
        lookup = (folder, tuple(term.casefold() for term in terms))
        return list(self.search_results.get(lookup, []))[:limit], ""
    def move_message(self, source, destination, message_id):
        msg = next((x for x in self.messages[source] if x.mailbox_id == message_id), None)
        if not msg:
            return OperationResult(False, "move-failed", "missing")
        self.messages[source].remove(msg)
        self.messages.setdefault(destination, []).append(msg)
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


class FakeRunner:
    def __init__(self):
        self.commands = []

    def run(self, command):
        self.commands.append(command)
        return CommandResult(
            list(command),
            0,
            (
                '[{"id":"12","subject":"Alte Nachricht",'
                '"from":{"name":"Jörn Arp","addr":"joern@example.de"},'
                '"date":"2024-01-02"}]'
            ),
            "",
        )


class StaticRunner:
    def __init__(self, result):
        self.result = result

    def run(self, command):
        return CommandResult(list(command), *self.result)


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
        runner = FakeRunner()
        config = SimpleNamespace(mailbox=SimpleNamespace(
            himalaya_binary="himalaya", account="", page_size=2,
        ))
        himalaya = HimalayaClient(config, runner)
        searched, error = himalaya.search_envelopes("Archiv", ["Jörn", "Arp"], limit=50)
        assert not error and searched[0].mailbox_id == "12"
        command = runner.commands[0]
        assert command[:2] == ["himalaya", "envelope"]
        assert command[command.index("--page-size") + 1] == "50"
        assert "Jörn" in command and "Arp" in command and "body" in command
        assert command[-4:] == ["order", "by", "date", "desc"]

        for empty in ("", "  \n"):
            empty_client = HimalayaClient(config, StaticRunner((0, empty, "")))
            searched, error = empty_client.search_envelopes("Archiv", ["nichtvorhanden"])
            assert searched == [] and error == ""
        malformed_client = HimalayaClient(config, StaticRunner((0, "not-json", "")))
        searched, error = malformed_client.search_envelopes("Archiv", ["defekt"])
        assert searched == [] and "Ungueltige Himalaya-JSON-Ausgabe" in error

        service, storage = build(Path(td))
        listed = service.list_messages('Archiv', limit=10)
        assert listed['messages'][0]['mailbox_id'] == '42'
        found = service.search_messages('Jätzel', limit=10)
        assert found['messages'][0]['folder'] == 'Agent/Pruefen'
        old = service.search_messages('JÖRN Arp', limit=10)
        assert old['messages'][0]['mailbox_id'] == '12'
        assert old['messages'][0]['folder'] == 'Archiv'
        assert old['complete'] and old['searched_folders'] == len(service._client_override.folders)
        assert not old['results_may_be_truncated']
        assert ('Archiv', ('JÖRN', 'Arp'), 10) in service._client_override.search_calls
        service._client_override.search_errors.add('Trash')
        partial = service.search_messages('Jätzel', limit=10)
        assert not partial['complete'] and partial['failed_folders'] == 1
        assert partial['folder_errors'][0]['folder'] == 'Trash'
        service._client_override.search_errors.clear()
        service._client_override.search_results[('INBOX', ('limit',))] = [
            Envelope('90', 'Limit A', 'A', 'a@example.de', '2026-07-30'),
            Envelope('91', 'Limit B', 'B', 'b@example.de', '2026-07-29'),
        ]
        limited = service.search_messages('limit', limit=2)
        assert limited['results_may_be_truncated'] and limited['limited_folders'] == ['INBOX']
        try:
            service.search_messages('eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn')
            raise AssertionError('More than twelve search terms must be rejected')
        except ValueError:
            pass
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
        registry_tools = build_tool_registry(ts)
        ids={x.id for x in registry_tools}
        assert {
            'mail.move-status','mail.list','mail.search','mail.read',
            'mail.reply-draft','mail.reply-send',
            'mail.compose-draft','mail.compose-send','mail.move',
        } <= ids
        search_tool = next(x for x in registry_tools if x.id == 'mail.search')
        assert 'serverseitig' in search_tool.description
        assert 'Vollstaendigkeit' in search_tool.description
        assert any(x.action_type=='mail.move' and x.status=='completed' for x in storage.list_actions(limit=20))
        storage2.close()
        storage.close()
    print('test_mail_move_tool: OK')

if __name__ == '__main__':
    main()
