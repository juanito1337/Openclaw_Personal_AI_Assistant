"""Real adapter entry points with ephemeral managed state and synthetic DAV data."""

import json
import time
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from test_nextcloud_access import scope, update_policy

from mail_agent.assistant_bridge import PersonalAssistantActionBridge
from mail_agent.config import NextcloudConfig
from mail_agent.invoice_reprocess import ReadOnlyInvoicePdfReader
from mail_agent.nextcloud import NextcloudSkillClient
from personal_assistant.nextcloud_onboarding import HttpResponse

pytest_plugins = ("test_nextcloud_file_access", "test_nextcloud_foreground_access")


def contact_bridge(account, tmp_path):
    book = account.pa.tool_settings.nextcloud.contacts.resource_id
    config = SimpleNamespace(
        nextcloud=NextcloudConfig(enabled=True, addressbook=book, contact_cache_file=tmp_path / "cache"),
        runtime=SimpleNamespace(command_timeout_seconds=5),
    )
    adapter = NextcloudSkillClient(config, None)
    href = account.pa.registry.get(book).remote_id + "one.vcf"
    account.replies["REPORT"] = HttpResponse(
        207,
        {},
        f"""<d:multistatus xmlns:d="DAV:" xmlns:a="urn:ietf:params:xml:ns:carddav">
<d:response><d:href>{href}</d:href>
<d:propstat><d:status>HTTP/1.1 200 OK</d:status>
<d:prop><d:getetag>"1"</d:getetag><a:address-data>BEGIN:VCARD
VERSION:3.0
UID:one
FN:Synthetic
EMAIL:one@example.invalid
END:VCARD</a:address-data></d:prop></d:propstat></d:response></d:multistatus>""".encode(),
    )
    return adapter


def test_memory_contact_hit_after_revocation(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    assert adapter.is_known_contact("one@example.invalid")
    update_policy(account.store, [])
    assert not adapter.is_known_contact("one@example.invalid")
    assert len(account.calls) == 1


def test_active_disk_cache_requires_exact_binding(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    adapter.config.nextcloud.contact_cache_file.write_text(
        json.dumps({"emails": ["old@example.invalid"], "refreshed_at": time.time()})
    )
    assert not adapter.is_known_contact("old@example.invalid")
    assert adapter.is_known_contact("one@example.invalid")
    assert len(account.calls) == 1
    assert contact_bridge(account, tmp_path).is_known_contact("one@example.invalid")
    assert len(account.calls) == 1


def test_failed_refresh_cannot_use_previous_contact_cache(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    assert adapter.is_known_contact("one@example.invalid")
    account.replies["REPORT"] = HttpResponse(503, {}, b"")
    assert not adapter.refresh_contact_cache(force=True)[0]
    assert not adapter.is_known_contact("one@example.invalid")


def test_unselected_addressbook_cannot_supply_known_sender(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    adapter.config.nextcloud.addressbook = ""
    ok, detail = adapter.refresh_contact_cache()
    assert not ok and "scope-unbound" in detail
    assert not account.calls


def test_file_scope_does_not_enable_invoice_adapters(workspace, monkeypatch):
    pa = workspace.pa
    pa.close = lambda: None
    bridge = PersonalAssistantActionBridge()
    monkeypatch.setattr(bridge, "_open", lambda: pa)
    args = dict(resource_id=scope(workspace.conn, "FILES")["resource_id"], allowed_folder="Belege")
    workspace.server.nodes["Belege/invoice.pdf"] = ("42", "G", b"%PDF-synthetic")
    reader = ReadOnlyInvoicePdfReader.__new__(ReadOnlyInvoicePdfReader)
    reader._files, reader._registry = pa.nextcloud_files, pa.registry
    for method in (reader.read, bridge.read_invoice_pdf):
        with pytest.raises(PermissionError, match="managed-account-archive-selection-required"):
            method(remote_path="Belege/invoice.pdf", **args)
    for result in (
        bridge.archive_invoice(
            message=None,
            attachment_hash="0" * 64,
            data=b"%PDF-synthetic",
            remote_path="Belege/new.pdf",
            resource_id=args["resource_id"],
        ),
        bridge.sync_invoice_register(
            data=b"csv",
            year=2026,
            remote_path="Belege/2026/Rechnungen_2026.csv",
            resource_id=args["resource_id"],
        ),
    ):
        assert not result.ok and "managed-account-archive-selection-required" in (
            result.status + result.detail
        )
    assert not workspace.server.calls


def test_portfolio_rechecks_download_before_local_commit(workspace, tmp_path):
    from dataclasses import replace

    from test_portfolio_tool import CleanAntivirus, csv_fixture

    from personal_assistant.policy import PolicyEngine
    from personal_assistant.portfolio import PortfolioService
    from personal_assistant.storage import AssistantStorage

    pa = workspace.pa
    root = tmp_path / "inbox"
    pa.tool_settings.portfolio.enabled = True
    pa.tool_settings.portfolio.database = tmp_path / "positions.sqlite"
    pa.tool_settings.portfolio.import_root = root
    pa.tool_settings.portfolio.nextcloud_folder = "Belege"
    pa.policy = PolicyEngine(tmp_path / "policy.toml", pa.registry)
    pa.storage = AssistantStorage(tmp_path / "assistant.sqlite")
    pa.portfolio = PortfolioService(pa.tool_settings.portfolio, CleanAntivirus())
    data = csv_fixture()
    path = "Belege/depot-31.07.2026.csv"
    workspace.server.nodes[path] = ("90", "G", data)
    original_list = pa.nextcloud_files.list_folder
    pa.nextcloud_files.list_folder = lambda value: [
        replace(row, size=len(data)) for row in original_list(value)
    ]
    scan = pa.portfolio.antivirus.scan_bytes

    def revoke(*args, **kwargs):
        result = scan(*args, **kwargs)
        update_policy(workspace.store, [])
        return result

    try:
        assert pa.portfolio_import_csv(nextcloud_path=path)["as_of"] == "2026-07-31"
        pa.portfolio.antivirus.scan_bytes = revoke
        with pytest.raises(PermissionError, match="policy-changed"):
            pa.portfolio_import_csv(nextcloud_path=path, dry_run=False)
        assert pa.portfolio.holdings()["count"] == 0
    finally:
        pa.portfolio.close()
        pa.storage.close()


@pytest.mark.parametrize("domain", ["calendar", "contacts"])
def test_mail_preview_cannot_cross_reconnection(account, tmp_path, domain):
    from test_action_completion_m15 import _message
    from test_nextcloud_access import measured

    from personal_assistant.connectors.nextcloud.client import NextcloudClient
    from personal_assistant.nextcloud_connection import apply_tool_connection
    from personal_assistant.registry import ResourceRegistry
    from personal_assistant.tool_settings import ToolSettings

    pa = account.pa
    source = _message()
    pa.mail_move_service = SimpleNamespace(read_message=lambda *a, **k: source)
    scan = SimpleNamespace(clean=True, to_dict=lambda: {"status": "clean"})
    pa.antivirus = SimpleNamespace(scan_bytes=lambda *a, **k: scan)
    pa.nextcloud_calendar = SimpleNamespace(find_events_by_uid=lambda *a: [])
    pa.nextcloud_contacts = SimpleNamespace(list_contacts=lambda *a: [])
    pa.tool_settings.security.antivirus.enabled = True
    source_args = dict(folder="INBOX", message_id="42", expected_subject=source.subject)
    target = getattr(pa.tool_settings.nextcloud, domain).resource_id

    def preview():
        with pa.select_nextcloud_resource(domain, target):
            if domain == "contacts":
                return pa.contact_from_mail(name="Synthetic", **source_args)
            return pa.calendar_from_mail_preview(**source_args)

    before = preview()
    conn = account.store.read()
    grants = conn["access_policy"]["grants"]
    for record in (
        measured("second"),
        {k: v for k, v in conn.items() if k not in {"revision", "access_policy", "schema_version"}},
    ):
        previous = account.store.snapshot()
        account.store.save(
            {**record, "schema_version": 1},
            expected_revision=previous.connection["revision"],
            expected_policy_revision=previous.binding.policy_revision,
        )
    update_policy(account.store, grants)
    pa.registry = ResourceRegistry(tmp_path / "resources.toml")
    pa.nextcloud_client = NextcloudClient(pa.config)
    pa.tool_settings = apply_tool_connection(ToolSettings(path=tmp_path / "tools.toml"), account.store.read())
    pa.tool_settings.security.antivirus.enabled = True
    after = preview()
    assert before.get("preview_digest") and before["preview_digest"] != after.get("preview_digest")
    with pa.select_nextcloud_resource(domain, target), pytest.raises(PermissionError):
        if domain == "calendar":
            pa.calendar_from_mail_create(
                **source_args,
                preview_digest=before["preview_digest"],
                candidate_id=before["candidates"][0]["candidate_id"],
                approved=True,
            )
        else:
            pa.contact_from_mail(
                **source_args, name="Synthetic", dry_run=False, preview_digest=before["preview_digest"]
            )


def test_legacy_calendar_reply_cannot_reuse_old_sqlite_token(account, tmp_path):
    from mail_agent.calendar import CalendarManager
    from mail_agent.config import load_config
    from mail_agent.models import ParsedMessage
    from mail_agent.storage import Storage

    config = load_config("mail_agent/config.example.toml")
    config.calendar.backend = "nextcloud_skill"
    config.calendar.approval_reply_from = "owner@example.invalid"
    storage = Storage(tmp_path / "mail.sqlite")
    manager = CalendarManager(config, storage, None)
    token = "A" * 16
    try:
        storage.create_calendar_approval(
            token_hash=manager._token_hash(token),
            token_hint=token[-4:],
            event_key="pending",
            fingerprint="one",
            stable_key="mail",
            source_subject="source",
            event_json="{}",
            ics_path="",
            requester_email=config.calendar.approval_reply_from,
            status="pending",
            expires_at="2099-01-01T00:00:00Z",
        )
        result = manager.handle_approval_reply(
            ParsedMessage(
                stable_key="reply",
                mailbox_id="2",
                source_folder="INBOX",
                raw=b"yes",
                sender_addr=config.calendar.approval_reply_from,
                subject=f"[MAIL-AGENT TERMIN {token}]",
                body_text="JA",
            )
        )
        assert not result.ok and "scope-unbound" in result.detail
        assert storage.get_calendar_approval(manager._token_hash(token))["status"] == "pending"
    finally:
        storage.close()


def test_mail_calendar_automation_cannot_adopt_current_foreground_calendar(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    adapter.fallback_calendar_resource_id = account.id
    result = adapter.create_event(SimpleNamespace(ics="unused", uid="one"))
    assert not result.ok and "scope-unbound" in result.detail
    assert not account.calls


def test_invoice_activation_during_scan_blocks_local_metadata_apply(tmp_path, monkeypatch):
    from test_invoice_reprocess_apply_m106 import InvoiceReprocessApplyM106Tests, _Extractor, _metadata
    from test_nextcloud_access import managed, measured

    from mail_agent.invoice_reprocess_apply import run_reprocess_apply

    store = managed(tmp_path, initial="verified-pre-policy")
    monkeypatch.setenv("OPENCLAW_NEXTCLOUD_CONNECTION_DIR", str(store.directory))
    fixture = InvoiceReprocessApplyM106Tests()
    fixture.setUp()

    def activate(*_args):
        store.save(measured(), expected_revision="")
        return "clamav:synthetic"

    try:
        result = run_reprocess_apply(
            fixture.database,
            attachment_hash=fixture.digest,
            expected_preview_sha256=fixture._preview_sha256(),
            extractor=_Extractor(_metadata()),
            read_pdf=lambda _: fixture.pdf,
            scan_pdf=activate,
            sync_register=lambda *a: pytest.fail("New policy disabled the invoice register"),
        )
        assert not result["ok"] and not result["local_applied"]
        assert fixture._invoice()["extraction_status"] == "review"
        assert not fixture._audit()
    finally:
        fixture.tearDown()


def test_contact_cache_does_not_leave_fresh_disk_trust_after_failed_forced_refresh(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    assert adapter.is_known_contact("one@example.invalid")
    account.replies["REPORT"] = HttpResponse(503, {}, b"")
    assert not adapter.refresh_contact_cache(force=True)[0]
    other = NextcloudSkillClient(adapter.config, None)
    assert not other.is_known_contact("one@example.invalid")


def test_native_binding_checked_even_for_contact_cache_hit(account, tmp_path, monkeypatch):
    adapter = contact_bridge(account, tmp_path)
    assert adapter.is_known_contact("one@example.invalid")
    monkeypatch.setenv("OPENCLAW_NC_NATIVE_CALL", "1")
    monkeypatch.setenv("OPENCLAW_NC_EXPECTED_ACCESS", json.dumps({"mode": "legacy", "binding": None}))
    assert not adapter.is_known_contact("one@example.invalid")
    ok, detail = adapter.refresh_contact_cache()
    assert not ok and "policy-changed" in detail
    assert len(account.calls) == 1


def test_active_contact_confirmation_rechecks_exact_candidate_and_duplicates(account, tmp_path, monkeypatch):
    from mail_agent.models import ParsedMessage
    from personal_assistant.actions import ActionService
    from personal_assistant.nextcloud_onboarding import SafeNextcloudTransport
    from personal_assistant.policy import PolicyEngine
    from personal_assistant.storage import AssistantStorage

    pa = account.pa
    adapter = contact_bridge(account, tmp_path)
    pa.nextcloud_contacts = adapter.contacts
    mime = EmailMessage()
    mime.set_content("hello")
    mime.add_attachment(b"sample", maintype="application", subtype="octet-stream", filename="sample.bin")
    source = ParsedMessage(
        stable_key="checked",
        mailbox_id="1",
        source_folder="INBOX",
        raw=mime.as_bytes(),
        subject="Selected",
        sender_name="Example",
        sender_addr="one@example.invalid",
    )
    pa.mail_move_service = SimpleNamespace(read_message=lambda *a, **kw: source)
    scanned = []

    def scan(data, *, name, source_type):
        scanned.append((name, data))
        return SimpleNamespace(clean=True, to_dict=lambda: {})

    pa.antivirus = SimpleNamespace(scan_bytes=scan)
    args = dict(folder=source.source_folder, message_id=source.mailbox_id, expected_subject=source.subject)
    preview = pa.contact_from_mail(**args)
    with pytest.raises(PermissionError, match="scope-unbound"):
        pa.contact_from_mail(**args, dry_run=False)
    with pytest.raises(PermissionError, match="Vorschau|vorschau"):
        pa.contact_from_mail(
            **args, note="Another note", dry_run=False, preview_digest=preview["preview_digest"]
        )
    confirmed = pa.contact_from_mail(**args, dry_run=False, preview_digest=preview["preview_digest"])
    assert preview["antivirus"]["attachment_count"] == 1
    assert scanned == [("selected-contact-mail.eml", source.raw), ("sample.bin", b"sample")] * 3
    assert confirmed["ok"] and confirmed["duplicate"] and not confirmed["created"]
    assert confirmed["duplicates"]["exact_email_matches"][0]["uid"] == "one"
    assert {method for method, _, _ in account.calls} == {"REPORT"}

    account.replies["REPORT"] = HttpResponse(207, {}, b'<d:multistatus xmlns:d="DAV:"/>')
    account.replies["PUT"] = HttpResponse(201, {}, b"")
    send = SafeNextcloudTransport.request

    def receive(self, method, path, **kwargs):
        if method == "PUT":
            assert scanned[-2:] == [("selected-contact-mail.eml", source.raw), ("sample.bin", b"sample")]
            assert kwargs["headers"]["If-None-Match"] == "*"
            account.replies["GET"] = HttpResponse(200, {"etag": '"created"'}, kwargs["data"])
        return send(self, method, path, **kwargs)

    monkeypatch.setattr(SafeNextcloudTransport, "request", receive)
    storage = AssistantStorage(tmp_path / "new-contact.sqlite")
    try:
        pa.policy = PolicyEngine(tmp_path / "contact-policy", pa.registry)
        pa.actions = ActionService(
            storage, pa.registry, pa.policy, pa.nextcloud_client,
            None, pa.nextcloud_calendar, pa.nextcloud_tasks, pa.nextcloud_contacts,
        )
        fresh = pa.contact_from_mail(**args)
        created = pa.contact_from_mail(**args, dry_run=False, preview_digest=fresh["preview_digest"])
        assert created["created"] and created["action"]["status"] == "completed"
        assert [method for method, _, _ in account.calls[-2:]] == ["PUT", "GET"]
    finally:
        storage.close()


def test_mail_diagnostics_do_not_publish_stale_account_or_claim_automation(account, tmp_path):
    adapter = contact_bridge(account, tmp_path)
    report = adapter.health(live=False)
    assert (
        report["ok"] is False and report["error_code"] == "managed-account-calendar-mail-selection-required"
    )
    update_policy(account.store, [])
    report = adapter.health(live=False)
    assert not report["ok"] and report["error_code"] == "policy-changed"
    assert "user" not in report and "base_url" not in report


@pytest.mark.parametrize("failure", ["disabled", "permissive", "scanner-error", "infected-attachment"])
def test_contact_preview_requires_clean_raw_and_physical_attachments(account, tmp_path, failure):
    from mail_agent.models import ParsedMessage

    pa = account.pa
    pa.nextcloud_contacts = contact_bridge(account, tmp_path).contacts
    mime = EmailMessage()
    mime.set_content("hello")
    mime.add_attachment(
        b"synthetic payload", maintype="application", subtype="octet-stream", filename="sample.bin"
    )
    msg = ParsedMessage(
        stable_key="contact",
        mailbox_id="1",
        source_folder="INBOX",
        raw=mime.as_bytes(),
        sender_name="Example",
        sender_addr="one@example.invalid",
    )
    pa.mail_move_service = SimpleNamespace(read_message=lambda *a, **kw: msg)
    pa.tool_settings.security.antivirus.enabled = failure != "disabled"
    pa.tool_settings.security.antivirus.fail_closed = failure != "permissive"
    scanned = []

    def scan(data, *, name, source_type):
        scanned.append(name)
        clean = failure != "scanner-error" and name != "sample.bin"
        return SimpleNamespace(
            clean=clean,
            signature="",
            detail="",
            status="error" if failure == "scanner-error" else "infected",
            to_dict=lambda: {},
        )

    pa.antivirus = SimpleNamespace(scan_bytes=scan)
    with pytest.raises(PermissionError):
        pa.contact_from_mail(folder="INBOX", message_id="1")
    assert not account.calls
    if failure in {"disabled", "permissive"}:
        assert not scanned
    if failure == "infected-attachment":
        assert "sample.bin" in scanned


@pytest.mark.parametrize("collision", ["exact_email_matches", "same_name_matches"])
def test_confirmed_duplicate_set_must_match_before_contact_create(account, tmp_path, collision):
    from mail_agent.models import ParsedMessage

    pa = account.pa
    adapter = contact_bridge(account, tmp_path)
    pa.nextcloud_contacts = adapter.contacts
    source = ParsedMessage(
        stable_key="contact",
        mailbox_id="1",
        source_folder="INBOX",
        raw=b"test",
        sender_name="Synthetic",
        sender_addr="one@example.invalid" if collision == "exact_email_matches" else "two@example.invalid",
    )
    pa.mail_move_service = SimpleNamespace(read_message=lambda *a, **kw: source)
    pa.antivirus = SimpleNamespace(
        scan_bytes=lambda *a, **kw: SimpleNamespace(clean=True, to_dict=lambda: {})
    )
    args = dict(folder="INBOX", message_id="1", allow_name_collision=True)
    preview = pa.contact_from_mail(**args)
    assert preview["duplicates"][collision]
    live = pa.nextcloud_contacts.list_contacts

    def changed(collection):
        result = live(collection)
        account.replies["REPORT"] = HttpResponse(207, {}, b'<d:multistatus xmlns:d="DAV:"/>')
        return result

    pa.nextcloud_contacts.list_contacts = changed
    pa.actions = SimpleNamespace(
        plan=lambda *a, **kw: pytest.fail("Cannot create after a duplicate disappears")
    )
    with pytest.raises(PermissionError, match="vorschau|Vorschau"):
        pa.contact_from_mail(**args, preview_digest=preview["preview_digest"], dry_run=False)


def test_local_annual_render_survives_active_policy(account, tmp_path, capsys):
    from test_invoice_effect_contract_m101 import InvoiceEffectContractM101Tests

    from mail_agent.cli import _handle_invoices
    from mail_agent.storage import Storage

    fixture = InvoiceEffectContractM101Tests()
    fixture.setUp()
    try:
        args = SimpleNamespace(
            invoices_command="export", year=2026, filename="", nextcloud=False, dry_run=True, yes=False
        )
        seeded = Storage(fixture.config.runtime.database)
        try:
            seeded.connection.execute("UPDATE invoices SET register_year = 2026")
            seeded.connection.commit()
        finally:
            seeded.close()
        before = fixture._invoice_row()
        assert _handle_invoices(args, fixture.config) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["rows"] == 1 and payload["remote_state_verified"] is False
        assert fixture._invoice_row() == before and not account.calls
    finally:
        fixture.tearDown()
