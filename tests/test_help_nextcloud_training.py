from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import main as cli_main
from mail_agent.command import CommandResult
from mail_agent.config import load_config
from mail_agent.envfile import load_env_file, update_env_file
from mail_agent.models import Envelope
from mail_agent.nextcloud import NextcloudSkillClient
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.setup_assistant import configuration_fingerprint, extended_help
from mail_agent.storage import Storage
from mail_agent.training import TrainingManager

WORKSPACE = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[list[str]] = []

    def run(self, args, **kwargs):
        command = [str(item) for item in args]
        self.calls.append(command)
        if self.results:
            return self.results.pop(0)
        return CommandResult(command, 0, "", "")


class HelpNextcloudTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config_text = (WORKSPACE / "mail_agent/config.example.toml").read_text(encoding="utf-8")
        config_text = config_text.replace("mail_agent/data/", str(self.root / "data") + "/")
        config_text = config_text.replace(
            'rules_file = "mail_agent/rules.toml"',
            f'rules_file = "{self.root / "rules.toml"}"',
        )
        self.rules_path = self.root / "rules.toml"
        self.rules_path.write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=[]\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(config_text, encoding="utf-8")
        self.config = load_config(self.config_path)
        self.storage = Storage(self.config.runtime.database)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def test_help_topics_explain_files_training_and_nextcloud(self) -> None:
        overview = extended_help("overview", self.config)
        training = extended_help("training", self.config)
        nextcloud = extended_help("nextcloud", self.config)
        self.assertIn("help training", overview)
        self.assertIn("mail_agent/rules.toml", extended_help("files", self.config))
        self.assertIn("training rule-add", training)
        self.assertIn("Korrektur-Kein-Spam", training)
        self.assertIn("nextcloud setup", nextcloud)
        self.assertIn("skill-card", nextcloud)
        self.assertIn("~/.config/mail-agent.env", nextcloud)


    def test_force_is_rejected_for_noninteractive_automation(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch.dict(os.environ, {"MAIL_AGENT_ALLOW_FORCE": "YES"}), \
             patch("mail_agent.cli.sys.stdin.isatty", return_value=False), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main([
                "--config", str(self.config_path),
                "run", "--limit", "1", "--no-digest", "--force",
            ])
        self.assertEqual(code, 4)
        self.assertIn("Automationen duerfen", stderr.getvalue())

    def test_help_still_works_with_invalid_config(self) -> None:
        broken = self.root / "broken.toml"
        broken.write_text("[ollama\nmodel = \"broken\"\n", encoding="utf-8")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main(["--config", str(broken), "help", "files"])
        self.assertEqual(code, 0)
        self.assertIn("WORKSPACE-DATEIEN", stdout.getvalue())
        self.assertIn("config.toml konnte nicht geladen werden", stderr.getvalue())

    def test_env_file_roundtrip_does_not_execute_shell_syntax(self) -> None:
        env_path = self.root / "mail-agent.env"
        marker = self.root / "must-not-exist"
        update_env_file(env_path, {
            "NEXTCLOUD_URL": "https://cloud.example.test",
            "NEXTCLOUD_USER": "jan",
            "NEXTCLOUD_TOKEN": f"$(touch {marker})",
        })
        backup = update_env_file(env_path, {"NEXTCLOUD_USER": "jan-updated"})
        self.assertIsNotNone(backup)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        old_values = {name: os.environ.get(name) for name in ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_TOKEN")}
        try:
            for name in old_values:
                os.environ.pop(name, None)
            loaded = load_env_file(env_path, override=True)
            self.assertEqual(set(loaded), {"NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_TOKEN"})
            self.assertEqual(os.environ["NEXTCLOUD_TOKEN"], f"$(touch {marker})")
            self.assertEqual(os.environ["NEXTCLOUD_USER"], "jan-updated")
            self.assertFalse(marker.exists())
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        finally:
            for name, value in old_values.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_training_rule_add_remove_creates_backups(self) -> None:
        manager = TrainingManager(self.rules_path, self.storage)
        added = manager.add_rule("spam", "domain", "newsletter.example")
        self.assertTrue(added.changed)
        self.assertTrue(added.backup and added.backup.exists())
        self.assertEqual(added.backup.stat().st_mode & 0o777, 0o600)
        self.assertIn("newsletter.example", manager.rules()["spam"]["domains"])
        removed = manager.remove_rule("spam", "domain", "newsletter.example")
        self.assertTrue(removed.changed)
        self.assertNotIn("newsletter.example", manager.rules()["spam"]["domains"])

    def test_reply_invoice_is_not_forced_to_routine(self) -> None:
        raw = b"""From: Person <person@example.test>\r
To: Jan <jan@example.test>\r
Subject: AW: Rechnung-Nr. 2026-17\r
Message-ID: <reply-invoice@example.test>\r
Content-Type: text/plain; charset=utf-8\r
\r
Bitte pruefen Sie meine Rueckfrage zur Rechnung und antworten Sie mir.\r
"""
        message = parse_eml(raw, Envelope("1"), "INBOX")
        result = RuleEngine(self.rules_path, self.storage).evaluate(message)
        self.assertIsNone(result.forced)

    def test_carddav_contact_blocks_model_only_spam_but_not_hard_rule(self) -> None:
        raw = b"""From: Known Person <known@example.test>\r
To: Jan <jan@example.test>\r
Subject: Kurze Rueckfrage\r
Message-ID: <known-contact@example.test>\r
Content-Type: text/plain; charset=utf-8\r
\r
Kannst du mich bitte zurueckrufen?\r
"""
        message = parse_eml(raw, Envelope("2"), "INBOX")
        rules = RuleEngine(
            self.rules_path,
            self.storage,
            contact_lookup=lambda address: address == "known@example.test",
            contacts_prevent_spam=True,
            trust_contacts_for_calendar=False,
            contact_importance_boost=1,
        )
        context = rules.evaluate(message)
        self.assertTrue(context.known_contact)
        self.assertTrue(context.prevent_spam)
        self.assertFalse(context.important_sender)
        self.assertFalse(rules.is_trusted_sender(message))

        manager = TrainingManager(self.rules_path, self.storage)
        manager.add_rule("spam", "address", "known@example.test")
        hard_context = RuleEngine(
            self.rules_path,
            self.storage,
            contact_lookup=lambda _address: True,
            contacts_prevent_spam=True,
        ).evaluate(message)
        self.assertIsNotNone(hard_context.forced)
        self.assertEqual(hard_context.forced.category, "spam")

    def test_skill_review_requires_explicit_approval(self) -> None:
        payload = '{"schema":"clawhub.skill.verify.v1","ok":true,"decision":"review"}'
        runner = FakeRunner([
            CommandResult(["openclaw"], 0, payload, ""),
            CommandResult(["openclaw"], 0, payload, ""),
        ])
        client = NextcloudSkillClient(self.config, runner)  # type: ignore[arg-type]
        with patch("mail_agent.nextcloud.shutil.which", return_value="/usr/bin/openclaw"):
            denied = client.verify_skill()
            approved = client.verify_skill(allow_review=True)
        self.assertFalse(denied.ok)
        self.assertEqual(denied.status, "nextcloud-skill-review-required")
        self.assertTrue(approved.ok)
        self.assertEqual(approved.status, "nextcloud-skill-review-approved")



    def test_review_install_uses_official_openclaw_risk_acknowledgement(self) -> None:
        skill_dir = self.root / "review-nextcloud-skill"
        self.config.nextcloud.skill_dir = skill_dir
        review_payload = '{"schema":"clawhub.skill.verify.v1","ok":true,"decision":"review"}'

        class InstallingRunner(FakeRunner):
            def run(inner_self, args, **kwargs):
                command = [str(item) for item in args]
                inner_self.calls.append(command)
                if command[1:3] == ["skills", "verify"]:
                    return CommandResult(command, 0, review_payload, "")
                if command[1:3] == ["skills", "install"]:
                    script = skill_dir / "scripts" / "nextcloud.js"
                    script.parent.mkdir(parents=True, exist_ok=True)
                    script.write_text("// installed", encoding="utf-8")
                    return CommandResult(command, 0, "installed", "")
                if command[1:3] == ["skills", "check"]:
                    return CommandResult(command, 0, "ok", "")
                return CommandResult(command, 1, "", "unexpected")

        runner = InstallingRunner()
        client = NextcloudSkillClient(self.config, runner)  # type: ignore[arg-type]
        with patch("mail_agent.nextcloud.shutil.which", return_value="/usr/bin/openclaw"):
            result = client.install_skill(allow_review=True)
        self.assertTrue(result.ok)
        install_call = next(call for call in runner.calls if call[1:3] == ["skills", "install"])
        self.assertIn("--acknowledge-clawhub-risk", install_call)

    def test_addressbooks_are_requested_only_once(self) -> None:
        client = NextcloudSkillClient(self.config, FakeRunner())  # type: ignore[arg-type]
        with patch.object(client, "_run", return_value=[]) as call:
            self.assertEqual(client.list_addressbooks(), [])
        call.assert_called_once_with(["addressbooks", "list"])

    def test_workspace_root_does_not_follow_alternate_config_location(self) -> None:
        client = NextcloudSkillClient(self.config, FakeRunner())  # type: ignore[arg-type]
        self.assertEqual(client.workspace_root, WORKSPACE)
        self.assertTrue(self.config.nextcloud.contacts_prevent_spam)


    def test_resource_selection_accepts_display_name_or_slug(self) -> None:
        calendars = [
            {"displayName": "Chief of Staff", "href": "/remote.php/dav/calendars/jan/chief-of-staff/"}
        ]
        addressbooks = [
            {"displayName": "Kontakte", "href": "/remote.php/dav/addressbooks/users/jan/contacts/"}
        ]
        self.assertTrue(
            NextcloudSkillClient._resource_selected(calendars, "Chief of Staff", kind="calendar")
        )
        self.assertTrue(
            NextcloudSkillClient._resource_selected(calendars, "chief-of-staff", kind="calendar")
        )
        self.assertTrue(
            NextcloudSkillClient._resource_selected(addressbooks, "contacts", kind="addressbook")
        )
        self.assertFalse(
            NextcloudSkillClient._resource_selected(calendars, "Privat", kind="calendar")
        )

    def test_default_nextcloud_contact_guard_is_enabled(self) -> None:
        self.assertTrue(self.config.nextcloud.contacts_enabled)
        self.assertTrue(self.config.nextcloud.contacts_prevent_spam)
        self.assertFalse(self.config.nextcloud.trust_contacts_for_calendar)

    def test_enabled_nextcloud_skill_changes_invalidate_dry_run_fingerprint(self) -> None:
        skill_dir = self.root / "fingerprinted-nextcloud-skill"
        script = skill_dir / "scripts" / "nextcloud.js"
        script.parent.mkdir(parents=True)
        script.write_text("// version one", encoding="utf-8")
        self.config.nextcloud.enabled = True
        self.config.nextcloud.skill_dir = skill_dir
        first = configuration_fingerprint(self.config)
        script.write_text("// version two", encoding="utf-8")
        second = configuration_fingerprint(self.config)
        self.assertNotEqual(first, second)

    def test_existing_skill_is_still_verified_before_use(self) -> None:
        skill_dir = self.root / "existing-nextcloud-skill"
        script = skill_dir / "scripts" / "nextcloud.js"
        script.parent.mkdir(parents=True)
        script.write_text("// test", encoding="utf-8")
        self.config.nextcloud.skill_dir = skill_dir
        payload = '{"schema":"clawhub.skill.verify.v1","ok":true,"decision":"pass"}'
        runner = FakeRunner([CommandResult(["openclaw"], 0, payload, "")])
        client = NextcloudSkillClient(self.config, runner)  # type: ignore[arg-type]
        with patch("mail_agent.nextcloud.shutil.which", return_value="/usr/bin/openclaw"):
            result = client.install_skill()
        self.assertTrue(result.ok)
        self.assertIn("aktuell verifiziert", result.detail)
        self.assertEqual(runner.calls[0][1:3], ["skills", "verify"])

    def test_calendar_bridge_caps_untrusted_text_arguments(self) -> None:
        client = NextcloudSkillClient(self.config, FakeRunner())  # type: ignore[arg-type]
        event = SimpleNamespace(
            title="T" * 1000,
            notes="N" * 9000,
            location="L" * 1000,
        )
        start = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        normalized = SimpleNamespace(event=event, start=start, end=start + timedelta(hours=1))
        with patch.object(client, "_run", return_value={"uid": "test-uid"}) as call:
            result = client.create_event(normalized)
        self.assertTrue(result.ok)
        args = call.call_args.args[0]
        self.assertEqual(len(args[args.index("--summary") + 1]), 300)
        self.assertEqual(len(args[args.index("--description") + 1]), 4000)
        self.assertEqual(len(args[args.index("--location") + 1]), 500)

    def test_contact_email_extraction_is_normalized(self) -> None:
        contact = {
            "displayName": "Example",
            "emails": ["Person@Example.ORG", {"value": "other@example.org"}],
            "phone": "+49 123",
        }
        self.assertEqual(
            NextcloudSkillClient.contact_emails(contact),
            {"person@example.org", "other@example.org"},
        )


if __name__ == "__main__":
    unittest.main()
