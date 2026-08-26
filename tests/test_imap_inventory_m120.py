from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mail_agent.imap_inventory import (
    FORBIDDEN_IMAP_COMMANDS,
    ImapConnectionSettings,
    ImapInventoryError,
    NativeImapInventoryBackend,
    ReadOnlyCommandPolicy,
    StdlibImapTransport,
    _decode_modified_utf7,
    _encode_modified_utf7,
    load_imap_settings,
    native_backend,
    parse_list_name,
)
from mail_agent.index_backend import backfill_backend, reconcile_backend
from mail_agent.search_backfill import BackfillFolder
from personal_assistant.contracts.mail_index_authority import (
    FolderIdentityAssurance,
    FolderSnapshotEvidence,
    MailSearchDecision,
    MailSearchEvidence,
)


class FakeTransport:
    def __init__(
        self,
        settings: ImapConnectionSettings,
        *,
        capabilities: set[str] | None = None,
        uidvalidity_end: str = "44",
        uidnext_end: str = "8",
        messages_end: str = "2",
    ) -> None:
        self.settings = settings
        self.commands: list[str] = []
        self._capabilities = capabilities or {"IMAP4REV1", "UIDPLUS", "CONDSTORE", "IDLE"}
        self.uidvalidity_end = uidvalidity_end
        self.uidnext_end = uidnext_end
        self.messages_end = messages_end
        self.examine_calls = 0
        self.connected = False
        self.logged_out = False

    def connect(self) -> None:
        self.connected = True

    def capabilities(self) -> set[str]:
        self.commands.append("CAPABILITY")
        return self._capabilities

    def list_folders(self) -> list[tuple[str, str]]:
        self.commands.append("LIST")
        return [("INBOX", "INBOX"), ("Projekte/Grüße", _encode_modified_utf7("Projekte/Grüße"))]

    def examine(self, encoded_name: str) -> dict[str, str]:
        del encoded_name
        self.commands.append("EXAMINE")
        self.examine_calls += 1
        start = self.examine_calls % 2 == 1
        return {
            "uidvalidity": "44" if start else self.uidvalidity_end,
            "uidnext": "8" if start else self.uidnext_end,
            "highestmodseq": "99",
            "messages": "2" if start else self.messages_end,
        }

    def uid_search_all(self) -> tuple[int, ...]:
        self.commands.append("UID SEARCH")
        return (2, 7)

    def uid_fetch_headers(self, uid: int) -> bytes:
        self.commands.append("UID FETCH")
        return (
            f"Message-ID: <fixture-{uid}@example.invalid>\r\n"
            "Subject: Belegter Test\r\n"
            "From: Test <sender@example.invalid>\r\n"
            "Date: Sun, 23 Aug 2026 10:00:00 +0000\r\n\r\n"
        ).encode()

    def uid_fetch_raw(self, uid: int) -> bytes:
        self.commands.append("UID FETCH")
        return self.uid_fetch_headers(uid) + b"fixture body"

    def logout(self) -> None:
        self.commands.append("LOGOUT")
        self.logged_out = True


def _settings(tmp_path: Path) -> ImapConnectionSettings:
    secret = tmp_path / "imap-password"
    secret.write_text("fixture-value\n", encoding="utf-8")
    return ImapConnectionSettings("gmx", "imap.example.invalid", 993, "user", "tls", secret)


def test_native_backend_uses_explicit_operation_budget_without_changing_default(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with patch("mail_agent.imap_inventory.load_imap_settings", return_value=settings):
        with native_backend(SimpleNamespace(), transport_factory=FakeTransport) as backend:
            assert backend.settings.total_timeout_seconds == 120.0
        with native_backend(
            SimpleNamespace(),
            transport_factory=FakeTransport,
            total_timeout_seconds=600.0,
        ) as backend:
            assert backend.settings.total_timeout_seconds == 600.0


@pytest.mark.parametrize("factory", (backfill_backend, reconcile_backend))
def test_index_backend_forwards_validated_operation_budget(factory) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    @contextmanager
    def fake_native_backend(config, *, total_timeout_seconds=None):
        captured["config"] = config
        captured["total_timeout_seconds"] = total_timeout_seconds
        yield sentinel

    config = SimpleNamespace(
        mailbox=SimpleNamespace(index_connector="native-imap-readonly")
    )
    with patch("mail_agent.index_backend.native_backend", fake_native_backend), factory(
        config,
        SimpleNamespace(),
        total_timeout_seconds=600.0,
    ) as backend:
        assert backend is sentinel

    assert captured == {
        "config": config,
        "total_timeout_seconds": 600.0,
    }


def test_himalaya_config_is_deep_merged_but_auth_command_is_fixed(tmp_path: Path) -> None:
    secret = tmp_path / "imap-password"
    secret.write_text("fixture-value\n", encoding="utf-8")
    base = tmp_path / "base.toml"
    base.write_text(
        """
[accounts.gmx]
default = true
email = "user@example.invalid"
[accounts.gmx.backend]
type = "imap"
host = "imap.example.invalid"
port = 993
login = "user@example.invalid"
[accounts.gmx.backend.encryption]
type = "tls"
""",
        encoding="utf-8",
    )
    auth = tmp_path / "auth.toml"
    auth.write_text(
        f"""
[accounts.gmx.backend.auth]
type = "password"
command = "cat {secret}"
""",
        encoding="utf-8",
    )
    config = SimpleNamespace(mailbox=SimpleNamespace(account="gmx"))

    settings = load_imap_settings(  # type: ignore[arg-type]
        config, config_paths=[base, auth], password_file=secret
    )

    assert settings.host == "imap.example.invalid"
    assert settings.login == "user@example.invalid"
    assert settings.password_file == secret


@pytest.mark.parametrize(
    "command",
    sorted(FORBIDDEN_IMAP_COMMANDS),
)
def test_command_policy_blocks_every_write_before_transport(command: str) -> None:
    with pytest.raises(PermissionError, match="blockiert"):
        ReadOnlyCommandPolicy.validate(command)


def test_auth_command_cannot_execute_arbitrary_shell(tmp_path: Path) -> None:
    secret = tmp_path / "imap-password"
    secret.write_text("fixture-value", encoding="utf-8")
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[accounts.gmx]
default = true
[accounts.gmx.backend]
type = "imap"
host = "imap.example.invalid"
login = "user"
[accounts.gmx.backend.encryption]
type = "tls"
[accounts.gmx.backend.auth]
type = "password"
command = "sh -c arbitrary"
""",
        encoding="utf-8",
    )
    config = SimpleNamespace(mailbox=SimpleNamespace(account="gmx"))
    with pytest.raises(ImapInventoryError) as raised:
        load_imap_settings(config, config_paths=[config_file], password_file=secret)  # type: ignore[arg-type]
    assert raised.value.category == "configuration"


def test_modified_utf7_round_trip_and_list_parser() -> None:
    value = "Projekte/Grüße & Pläne"
    encoded = _encode_modified_utf7(value)
    assert _decode_modified_utf7(encoded) == value
    assert parse_list_name(f'(\\HasNoChildren) "/" "{encoded}"') == (value, encoded)


def test_noselect_parent_is_not_treated_as_readable_mailbox(tmp_path: Path) -> None:
    class ListClient:
        def list(self):
            return "OK", [
                b'(\\Noselect \\HasChildren) "/" "Parent"',
                b'(\\HasNoChildren) "/" "Parent/Child"',
            ]

    transport = StdlibImapTransport(_settings(tmp_path))
    transport._client = ListClient()  # type: ignore[assignment]
    assert transport.list_folders() == [("Parent/Child", "Parent/Child")]


def test_probe_reports_only_aggregates_and_readonly_commands(tmp_path: Path) -> None:
    created: list[FakeTransport] = []

    def factory(settings: ImapConnectionSettings) -> FakeTransport:
        transport = FakeTransport(settings)
        created.append(transport)
        return transport

    backend = NativeImapInventoryBackend(_settings(tmp_path), transport_factory=factory)
    result = backend.capability_probe()
    backend.close()

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["writes_imap"] is False
    assert result["sets_seen"] is False
    assert result["aggregates"] == {
        "folder_count": 2,
        "message_count": 4,
        "uid_min": 2,
        "uid_max": 7,
    }
    assert result["probe"]["forbidden_commands_sent"] == []
    allowed = {"CAPABILITY", "LIST", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"}
    assert all(command in allowed for command in created[0].commands)
    rendered = repr(result)
    assert "sender@example.invalid" not in rendered
    assert "fixture body" not in rendered
    assert created[0].logged_out is True


def test_stdlib_transport_uses_examine_body_peek_and_never_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript: list[tuple[object, ...]] = []

    class Socket:
        def settimeout(self, value: float) -> None:
            transcript.append(("timeout", value))

    class Client:
        sock = Socket()

        def __init__(self, host: str, port: int, *, ssl_context: object, timeout: float) -> None:
            del ssl_context
            transcript.append(("connect-tls", host, port, timeout))

        def login(self, login: str, password: str):
            transcript.append(("login", login, bool(password)))
            return "OK", [b""]

        def capability(self):
            transcript.append(("CAPABILITY",))
            return "OK", [b"IMAP4rev1 UIDPLUS"]

        def list(self):
            transcript.append(("LIST",))
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

        def select(self, mailbox: str, readonly: bool = False):
            transcript.append(("SELECT", mailbox, readonly))
            return "OK", [b"1"]

        def response(self, key: str):
            values = {"UIDVALIDITY": b"44", "UIDNEXT": b"8"}
            return key, [values[key]] if key in values else []

        def uid(self, command: str, *args: object):
            transcript.append(("UID", command, *args))
            if command == "SEARCH":
                return "OK", [b"7"]
            return "OK", [(b"7 FETCH", b"Subject: Test\r\n\r\nBody")]

        def logout(self):
            transcript.append(("LOGOUT",))
            return "BYE", [b""]

    monkeypatch.setattr("mail_agent.imap_inventory.imaplib.IMAP4_SSL", Client)
    transport = StdlibImapTransport(_settings(tmp_path))
    transport.connect()
    assert transport.capabilities() == {"IMAP4REV1", "UIDPLUS"}
    assert transport.list_folders() == [("INBOX", "INBOX")]
    assert transport.examine("INBOX")["uidvalidity"] == "44"
    assert transport.uid_search_all() == (7,)
    assert transport.uid_fetch_raw(7).endswith(b"Body")
    transport.logout()

    assert ("SELECT", "INBOX", True) in transcript
    assert ("UID", "FETCH", "7", "(BODY.PEEK[])") in transcript
    assert not any("STORE" in " ".join(map(str, row)).upper() for row in transcript)
    assert not any("BODY[]" in " ".join(map(str, row)).upper() for row in transcript)


def test_uidvalidity_race_is_fail_closed(tmp_path: Path) -> None:
    backend = NativeImapInventoryBackend(
        _settings(tmp_path),
        transport_factory=lambda settings: FakeTransport(settings, uidvalidity_end="45"),
    )
    with pytest.raises(ImapInventoryError) as raised:
        backend.snapshot(BackfillFolder("folder:one", "INBOX"))
    assert raised.value.category == "uidvalidity-race"


def test_uidnext_or_message_count_race_is_fail_closed(tmp_path: Path) -> None:
    backend = NativeImapInventoryBackend(
        _settings(tmp_path),
        transport_factory=lambda settings: FakeTransport(settings, uidnext_end="9"),
    )
    with pytest.raises(ImapInventoryError) as raised:
        backend.snapshot(BackfillFolder("folder:one", "INBOX"))
    assert raised.value.category == "snapshot-race"


def test_folder_list_race_is_fail_closed(tmp_path: Path) -> None:
    class ChangingFolders(FakeTransport):
        def __init__(self, settings: ImapConnectionSettings) -> None:
            super().__init__(settings)
            self.list_calls = 0

        def list_folders(self) -> list[tuple[str, str]]:
            self.list_calls += 1
            rows = super().list_folders()
            return rows if self.list_calls == 1 else rows + [("Neu", "Neu")]

    backend = NativeImapInventoryBackend(
        _settings(tmp_path), transport_factory=ChangingFolders
    )
    with pytest.raises(ImapInventoryError) as raised:
        backend.inventory()
    assert raised.value.category == "folder-list-race"


def test_paging_is_stable_and_uses_uid_headers(tmp_path: Path) -> None:
    backend = NativeImapInventoryBackend(
        _settings(tmp_path), transport_factory=FakeTransport
    )
    folder = backend.inventory()[0]
    first = backend.fetch_page(folder, page=1, page_size=1)
    second = backend.fetch_page(folder, page=2, page_size=1)
    third = backend.fetch_page(folder, page=3, page_size=1)
    assert [first.items[0].uid, second.items[0].uid] == ["2", "7"]
    assert first.has_more is True
    assert second.has_more is False
    assert third.items == ()


def test_live_locator_revalidation_never_falls_back_to_fuzzy_match(tmp_path: Path) -> None:
    backend = NativeImapInventoryBackend(_settings(tmp_path), transport_factory=FakeTransport)
    folder = backend.inventory()[0]
    valid = backend.revalidate_locator(
        folder,
        uidvalidity="44",
        uid="2",
        expected_subject="Belegter Test",
        expected_message_id="<fixture-2@example.invalid>",
    )
    conflict = backend.revalidate_locator(
        folder,
        uidvalidity="44",
        uid="2",
        expected_subject="Anderer Betreff",
    )
    missing = backend.revalidate_locator(folder, uidvalidity="44", uid="999")
    assert valid["state"] == "validated"
    assert conflict == {
        "ok": False,
        "state": "conflict",
        "code": "subject-mismatch",
        "read_only": True,
    }
    assert missing["code"] == "uid-missing"


def test_authority_and_search_decision_contract() -> None:
    stable = FolderSnapshotEvidence(True, True, True, True)
    assert stable.assurance() is FolderIdentityAssurance.SNAPSHOT_STABLE
    server_stable = FolderSnapshotEvidence(True, True, True, True, "server-id")
    assert server_stable.assurance() is FolderIdentityAssurance.SERVER_STABLE
    assert FolderSnapshotEvidence(True, False, True, True).assurance() is FolderIdentityAssurance.UNKNOWN

    assert MailSearchEvidence(1, False, False, False).decision() is MailSearchDecision.MATCHES
    proven = MailSearchEvidence(0, True, True, True).to_contract()
    assert proven["decision"] == "no-match"
    assert proven["negative_claim_allowed"] is True
    partial = MailSearchEvidence(0, False, True, True).to_contract()
    assert partial["decision"] == "inconclusive"
    assert partial["negative_claim_allowed"] is False
