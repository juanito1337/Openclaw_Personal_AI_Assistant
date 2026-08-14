from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mail_agent.app import MailAgent, RunSummary
from mail_agent.calendar import CalendarManager, event_from_ics
from mail_agent.classifier import OLLAMA_FORMAT_SCHEMA
from mail_agent.command import CommandResult, CommandRunner
from mail_agent.config import load_config
from mail_agent.digest import DigestManager
from mail_agent.forwarding import Forwarder
from mail_agent.himalaya import HimalayaClient
from mail_agent.lock import ProcessLock, ProcessLockError
from mail_agent.models import Classification, Envelope, OperationResult
from mail_agent.parser import parse_eml
from mail_agent.rules import RuleEngine
from mail_agent.storage import Storage
from personal_assistant.antivirus import AntivirusResult

SAMPLE = b"""From: Example Shop <news@example-shop.test>\r
To: Jan <jan@example.test>\r
Subject: Newsletter: 30 Prozent Rabatt nur heute\r
Message-ID: <abc@example.test>\r
Date: Fri, 17 Jul 2026 10:00:00 +0200\r
Content-Type: text/plain; charset=utf-8\r
\r
Jetzt sichern. Newsletter abbestellen. Sonderangebot und Gutschein.\r
"""


class MailAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config_text = (Path(__file__).parents[1] / "mail_agent/config.example.toml").read_text(encoding="utf-8")
        config_text = config_text.replace("mail_agent/data/", str(self.root / "data") + "/")
        config_text = config_text.replace('rules_file = "mail_agent/rules.toml"', f'rules_file = "{self.root / "rules.toml"}"')
        config_text = config_text.replace('log_file = "mail_agent/data/mail_agent.log"', f'log_file = "{self.root / "mail_agent.log"}"')
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(config_text, encoding="utf-8")
        (self.root / "rules.toml").write_text("[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n[important]\naddresses=[]\ndomains=[]\n[routine]\naddresses=[]\ndomains=[]\n", encoding="utf-8")
        self.config = load_config(self.config_path)
        self.storage = Storage(self.config.runtime.database)

    def tearDown(self) -> None:
        self.storage.close()
        self.temp.cleanup()

    def test_database_schema_version_is_recorded(self) -> None:
        version = self.storage.connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 3)

    def test_parser_uses_message_id_as_stable_key(self) -> None:
        message = parse_eml(SAMPLE, Envelope("42"), "INBOX")
        self.assertEqual(message.stable_key, "mid:abc@example.test")
        self.assertIn("Newsletter", message.subject)

    def test_rule_engine_detects_obvious_newsletter(self) -> None:
        message = parse_eml(SAMPLE, Envelope("42"), "INBOX")
        rules = RuleEngine(self.config.runtime.rules_file, self.storage)
        result = rules.evaluate(message)
        self.assertIsNotNone(result.forced)
        self.assertEqual(result.forced.category, "spam")
        self.assertGreaterEqual(result.forced.confidence, 0.95)

    def test_feedback_prevents_repeat_spam(self) -> None:
        message = parse_eml(SAMPLE, Envelope("42"), "INBOX")
        self.storage.record_feedback(message, "not_spam", "Agent/Korrektur-Kein-Spam")
        rules = RuleEngine(self.config.runtime.rules_file, self.storage)
        result = rules.evaluate(message)
        self.assertTrue(result.prevent_spam)

    def test_feedback_limit_is_global_across_correction_folders(self) -> None:
        class FakeHimalaya:
            def __init__(inner_self) -> None:
                inner_self.calls: list[tuple[str, int]] = []

            def list_envelopes(inner_self, folder: str, limit: int):
                inner_self.calls.append((folder, limit))
                if folder == self.config.folders.feedback_spam:
                    return [Envelope(str(index)) for index in range(1, 6)], ""
                return [], ""

        agent = object.__new__(MailAgent)
        agent.config = self.config
        agent.storage = self.storage
        agent.dry_run = True
        agent.log = logging.getLogger(__name__)
        agent.himalaya = FakeHimalaya()

        def load_message(folder: str, envelope: Envelope, summary: RunSummary):
            raw = SAMPLE.replace(
                b"<abc@example.test>",
                f"<feedback-{envelope.mailbox_id}@example.test>".encode(),
            )
            return parse_eml(raw, envelope, folder)

        agent._load_message = load_message
        agent._route = lambda message, classification, folder, force=False: OperationResult(
            True, "spam", "moved", destination=self.config.folders.spam
        )

        summary = RunSummary()
        agent._process_feedback(summary, limit=3)

        self.assertEqual(summary.processed, 3)
        self.assertEqual(len(summary.actions), 3)
        self.assertEqual(
            agent.himalaya.calls,
            [(self.config.folders.feedback_spam, 3)],
        )

    def test_feedback_recording_is_idempotent(self) -> None:
        message = parse_eml(SAMPLE, Envelope("42"), "INBOX")
        self.storage.record_feedback(message, "not_spam", "Agent/Korrektur-Kein-Spam")
        self.storage.record_feedback(message, "not_spam", "Agent/Korrektur-Kein-Spam")
        count = self.storage.connection.execute(
            "SELECT COUNT(*) FROM feedback WHERE stable_key = ? AND verdict = ?",
            (message.stable_key, "not_spam"),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_latest_feedback_replaces_older_verdict_for_same_message(self) -> None:
        message = parse_eml(SAMPLE, Envelope("42"), "INBOX")
        self.storage.record_feedback(message, "not_spam", "Agent/Korrektur-Kein-Spam")
        self.storage.record_feedback(message, "spam", "Agent/Korrektur-Spam")
        self.assertEqual(self.storage.exact_feedback_verdict(message), "spam")
        self.storage.record_feedback(message, "not_spam", "Agent/Korrektur-Kein-Spam")
        self.assertEqual(self.storage.exact_feedback_verdict(message), "not_spam")
        count = self.storage.connection.execute(
            "SELECT COUNT(*) FROM feedback WHERE stable_key = ?", (message.stable_key,)
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_mailer_daemon_is_relevant(self) -> None:
        raw = SAMPLE.replace(b"Example Shop <news@example-shop.test>", b"Mailer-Daemon <mailer-daemon@gmx.net>")
        raw = raw.replace(b"Newsletter: 30 Prozent Rabatt nur heute", b"Mail delivery failed")
        message = parse_eml(raw, Envelope("43"), "INBOX")
        rules = RuleEngine(self.config.runtime.rules_file, self.storage)
        result = rules.evaluate(message)
        self.assertEqual(result.forced.category, "relevant")

    def test_ics_invite_parser(self) -> None:
        event = event_from_ics(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test-1\nSUMMARY:Besprechung\nDTSTART;TZID=Europe/Berlin:20270722T100000\nDTEND;TZID=Europe/Berlin:20270722T110000\nSTATUS:CONFIRMED\nEND:VEVENT\nEND:VCALENDAR\n",
            "Europe/Berlin",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.status, "confirmed")
        self.assertTrue(event.start.startswith("2027-07-22T10:00:00"))

    def test_ics_invite_parser_accepts_quoted_tzid(self) -> None:
        event = event_from_ics(
            'BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test-quoted-tz\nSUMMARY:Besprechung\n'
            'DTSTART;TZID="Europe/Berlin":20270722T100000\n'
            'DTEND;TZID="Europe/Berlin":20270722T110000\n'
            'STATUS:CONFIRMED\nEND:VEVENT\nEND:VCALENDAR\n',
            "Europe/Berlin",
        )
        self.assertIsNotNone(event)
        self.assertTrue(event.start.startswith("2027-07-22T10:00:00"))

    def test_ics_invite_parser_rejects_unknown_tzid_safely(self) -> None:
        event = event_from_ics(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test-bad-tz\nSUMMARY:Besprechung\n"
            "DTSTART;TZID=Not/AZone:20260722T100000\n"
            "STATUS:CONFIRMED\nEND:VEVENT\nEND:VCALENDAR\n",
            "Europe/Berlin",
        )
        self.assertIsNone(event)


    def test_repeated_sender_feedback_becomes_routine_rule(self) -> None:
        first = parse_eml(
            SAMPLE.replace(b"<abc@example.test>", b"<one@example.test>").replace(
                b"Newsletter: 30 Prozent Rabatt nur heute", b"Automatische Info 1001"
            ),
            Envelope("51"),
            "INBOX",
        )
        second = parse_eml(
            SAMPLE.replace(b"<abc@example.test>", b"<two@example.test>").replace(
                b"Newsletter: 30 Prozent Rabatt nur heute", b"Automatische Info 1002"
            ),
            Envelope("52"),
            "INBOX",
        )
        candidate = parse_eml(
            SAMPLE.replace(b"<abc@example.test>", b"<three@example.test>").replace(
                b"Newsletter: 30 Prozent Rabatt nur heute", b"Automatische Info 1003"
            ),
            Envelope("53"),
            "INBOX",
        )
        self.storage.record_feedback(first, "routine", "Agent/Korrektur-Unwichtig")
        self.storage.record_feedback(second, "routine", "Agent/Korrektur-Unwichtig")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(candidate)
        self.assertIsNotNone(result.forced)
        self.assertEqual(result.forced.category, "routine")

    def test_calendar_queue_for_confirmed_invite(self) -> None:
        raw = b"""From: Person <person@example.test>\r
To: Jan <jan@example.test>\r
Subject: Einladung\r
Message-ID: <calendar@example.test>\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary=x\r
\r
--x\r
Content-Type: text/plain; charset=utf-8\r
\r
Termin anbei.\r
--x\r
Content-Type: text/calendar; charset=utf-8; method=REQUEST\r
Content-Disposition: attachment; filename=invite.ics\r
\r
BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:invite-1\r
SUMMARY:Projektbesprechung\r
DTSTART;TZID=Europe/Berlin:20270722T100000\r
DTEND;TZID=Europe/Berlin:20270722T110000\r
STATUS:CONFIRMED\r
END:VEVENT\r
END:VCALENDAR\r
--x--\r
"""
        message = parse_eml(raw, Envelope("60"), "INBOX")
        self.config.calendar.backend = "queue"
        manager = CalendarManager(self.config, self.storage, CommandRunner(), dry_run=False)
        classification = Classification("appointment", 0.99, 8, True, "Einladung")
        result = manager.process(message, classification)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "pending-review")
        self.assertIn("nicht vertrauenswuerdigen Absender", result.detail)
        self.assertTrue(Path(result.path).exists())
        repeated = manager.process(message, classification)
        self.assertEqual(repeated.status, "pending-review")
        self.assertNotEqual(repeated.status, "duplicate")

    def test_low_mail_confidence_never_creates_high_confidence_event(self) -> None:
        raw = b"""From: Person <person@example.test>\r
To: Jan <jan@example.test>\r
Subject: Einladung\r
Message-ID: <calendar-low-mail-confidence@example.test>\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary=x\r
\r
--x\r
Content-Type: text/plain; charset=utf-8\r
\r
Termin anbei.\r
--x\r
Content-Type: text/calendar; charset=utf-8; method=REQUEST\r
Content-Disposition: attachment; filename=invite.ics\r
\r
BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:invite-low-mail-confidence\r
SUMMARY:Projektbesprechung\r
DTSTART;TZID=Europe/Berlin:20270722T100000\r
DTEND;TZID=Europe/Berlin:20270722T110000\r
STATUS:CONFIRMED\r
END:VEVENT\r
END:VCALENDAR\r
--x--\r
"""
        message = parse_eml(raw, Envelope("601"), "INBOX")
        self.config.calendar.require_trusted_sender = False
        self.config.calendar.backend = "queue"
        manager = CalendarManager(self.config, self.storage, CommandRunner(), dry_run=False)
        result = manager.process(
            message,
            Classification("appointment", 0.70, 8, True, "Unsichere Zuordnung"),
            trusted_sender=True,
        )
        self.assertEqual(result.status, "pending-review")
        self.assertIn("Mail-/Terminkonfidenz", result.detail)

    def test_unknown_calendar_invite_is_not_hard_forced(self) -> None:
        raw = b"""From: Person <person@example.test>
To: Jan <jan@example.test>
Subject: Einladung
Message-ID: <calendar-unknown@example.test>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=x

--x
Content-Type: text/plain; charset=utf-8

Termin anbei.
--x
Content-Type: text/calendar; charset=utf-8; method=REQUEST
Content-Disposition: attachment; filename=invite.ics

BEGIN:VCALENDAR
BEGIN:VEVENT
UID:invite-unknown
SUMMARY:Unbekannte Einladung
DTSTART;TZID=Europe/Berlin:20270722T100000
DTEND;TZID=Europe/Berlin:20270722T110000
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
--x--
"""
        message = parse_eml(raw, Envelope("61"), "INBOX")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(message)
        self.assertIsNone(result.forced)
        self.assertTrue(any("Kalenderdatei" in note for note in (result.notes or [])))

    def test_calendar_attachment_is_not_downgraded_by_routine_footer(self) -> None:
        raw = b"""From: Person <person@example.test>
To: Jan <jan@example.test>
Subject: Einladung und Versandbestaetigung
Message-ID: <calendar-routine-footer@example.test>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=x

--x
Content-Type: text/plain; charset=utf-8

Ihre Versandbestaetigung. Termin anbei.
--x
Content-Type: text/calendar; charset=utf-8; method=REQUEST
Content-Disposition: attachment; filename=invite.ics

BEGIN:VCALENDAR
BEGIN:VEVENT
UID:invite-routine-footer
SUMMARY:Besprechung
DTSTART;TZID=Europe/Berlin:20270722T100000
DTEND;TZID=Europe/Berlin:20270722T110000
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
--x--
"""
        message = parse_eml(raw, Envelope("611"), "INBOX")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(message)
        self.assertIsNone(result.forced)
        self.assertTrue(any("Kalenderdatei" in note for note in (result.notes or [])))

    def test_explicitly_important_calendar_sender_is_trusted(self) -> None:
        self.config.runtime.rules_file.write_text(
            "[spam]\naddresses=[]\ndomains=[]\nsender_names=[]\nsubject_phrases=[]\n"
            "[important]\naddresses=['person@example.test']\ndomains=[]\n"
            "[routine]\naddresses=[]\ndomains=[]\n",
            encoding="utf-8",
        )
        raw = b"""From: Person <person@example.test>
To: Jan <jan@example.test>
Subject: Einladung
Message-ID: <calendar-trusted@example.test>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=x

--x
Content-Type: text/plain; charset=utf-8

Termin anbei.
--x
Content-Type: text/calendar; charset=utf-8; method=REQUEST
Content-Disposition: attachment; filename=invite.ics

BEGIN:VCALENDAR
BEGIN:VEVENT
UID:invite-trusted
SUMMARY:Vertrauenswuerdige Einladung
DTSTART;TZID=Europe/Berlin:20270722T100000
DTEND;TZID=Europe/Berlin:20270722T110000
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
--x--
"""
        message = parse_eml(raw, Envelope("62"), "INBOX")
        rules = RuleEngine(self.config.runtime.rules_file, self.storage)
        result = rules.evaluate(message)
        self.assertIsNotNone(result.forced)
        self.assertEqual(result.forced.category, "appointment")
        self.assertTrue(rules.is_trusted_sender(message, feedback_count=2))

    def test_two_important_corrections_do_not_trust_all_mail_from_sender_for_calendar(self) -> None:
        first = parse_eml(
            SAMPLE.replace(b"<abc@example.test>", b"<trust-one@example.test>").replace(
                b"Newsletter: 30 Prozent Rabatt nur heute", b"Persoenliche Nachricht eins"
            ),
            Envelope("63"),
            "INBOX",
        )
        second = parse_eml(
            SAMPLE.replace(b"<abc@example.test>", b"<trust-two@example.test>").replace(
                b"Newsletter: 30 Prozent Rabatt nur heute", b"Persoenliche Nachricht zwei"
            ),
            Envelope("64"),
            "INBOX",
        )
        candidate = parse_eml(
            SAMPLE.replace(b"<abc@example.test>", b"<trust-three@example.test>").replace(
                b"Newsletter: 30 Prozent Rabatt nur heute", b"Besprechung"
            ),
            Envelope("65"),
            "INBOX",
        )
        self.storage.record_feedback(first, "relevant", "Agent/Korrektur-Wichtig")
        self.storage.record_feedback(second, "relevant", "Agent/Korrektur-Wichtig")
        rules = RuleEngine(self.config.runtime.rules_file, self.storage)
        self.assertFalse(rules.is_trusted_sender(candidate, feedback_count=2))

    def test_payment_warning_is_model_hint_not_unconditional_forward(self) -> None:
        raw = SAMPLE.replace(b"Newsletter: 30 Prozent Rabatt nur heute", b"Payment failed for account")
        raw = raw.replace(b"Jetzt sichern. Newsletter abbestellen. Sonderangebot und Gutschein.", b"Your payment failed. Please review the account status.")
        message = parse_eml(raw, Envelope("66"), "INBOX")
        result = RuleEngine(self.config.runtime.rules_file, self.storage).evaluate(message)
        self.assertIsNone(result.forced)
        self.assertTrue(any("Zahlungs" in note for note in (result.notes or [])))

    def test_restored_spam_in_inbox_becomes_not_spam_feedback(self) -> None:
        message = parse_eml(SAMPLE, Envelope("72"), "INBOX")
        agent = MailAgent(self.config, dry_run=False)

        class FakeHimalaya:
            def __init__(self) -> None:
                self.moves: list[str] = []

            def list_envelopes(self, folder: str, limit: int | None = None):
                return [Envelope("72")], ""

            def export_message(self, folder: str, message_id: str, destination: Path) -> OperationResult:
                destination.write_bytes(SAMPLE)
                return OperationResult(True, "exported", path=str(destination))

            def move_message(self, source: str, destination: str, message_id: str) -> OperationResult:
                self.moves.append(destination)
                return OperationResult(True, "moved", destination=destination)

        class FakeClassifier:
            def classify(self, parsed, force_not_spam: bool = False):
                self_force = force_not_spam
                if not self_force:
                    raise AssertionError("INBOX-Restore muss force_not_spam verwenden")
                return Classification("uncertain", 0.60, 5, False, "Nach Korrektur pruefen")

        fake_himalaya = FakeHimalaya()
        agent.himalaya = fake_himalaya  # type: ignore[assignment]
        agent.classifier = FakeClassifier()  # type: ignore[assignment]
        agent.antivirus.scan_bytes = lambda *args, **kwargs: AntivirusResult(  # type: ignore[method-assign]
            "clean", "sha256", 1, "raw-mail", "72.eml", "test", "test"
        )
        agent.storage.upsert_message(message, Classification("spam", 0.99, 1, False, "Spam"), status="spam")
        summary = RunSummary()
        try:
            agent._process_folder("INBOX", summary, 10)
            self.assertEqual(agent.storage.exact_feedback_verdict(message), "not_spam")
            not_spam = agent.storage.list_not_spam_feedback(limit=10)
            self.assertEqual(not_spam[0]["origin"], "inbox-restore")
            self.assertEqual(not_spam[0]["previous_status"], "spam")
            self.assertEqual(fake_himalaya.moves, [self.config.folders.review])
            self.assertEqual(summary.actions[0]["status"], "review")
        finally:
            agent.close()

    def test_invalid_threshold_is_rejected_during_config_load(self) -> None:
        invalid_path = self.root / "invalid-config.toml"
        text = self.config_path.read_text(encoding="utf-8").replace("spam = 0.95", "spam = 1.50")
        invalid_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "thresholds.spam"):
            load_config(invalid_path)

    def test_forward_uses_zip_and_disables_sent_copy(self) -> None:
        class FakeHimalaya:
            def __init__(self) -> None:
                self.templates: list[str] = []
                self.save_copy_values: list[bool | None] = []

            def send_template(
                self, template: str, *, save_copy: bool | None = None
            ) -> OperationResult:
                self.templates.append(template)
                self.save_copy_values.append(save_copy)
                return OperationResult(True, "sent")

        message = parse_eml(SAMPLE, Envelope("70"), "INBOX")
        fake = FakeHimalaya()
        forwarder = Forwarder(self.config, fake)  # type: ignore[arg-type]
        result = forwarder.forward(
            message,
            Classification("relevant", 0.99, 9, True, "Wichtig", summary="Test"),
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(fake.templates), 1)
        self.assertEqual(fake.save_copy_values, [False])
        self.assertIn("application/zip", fake.templates[0])
        self.assertIn("Message-ID: <mail-agent-forward-", fake.templates[0])
        self.assertIn("X-Mail-Agent-Source: sha256=", fake.templates[0])
        self.assertNotIn("message/rfc822", fake.templates[0])
        self.assertEqual(result.path, "")

    def test_delivery_uncertain_is_not_marked_for_retry(self) -> None:
        message = parse_eml(SAMPLE, Envelope("69"), "INBOX")
        classification = Classification("relevant", 0.99, 9, True, "Wichtig")
        self.storage.upsert_message(message, classification, status="classified")

        class FakeForwarder:
            def forward(self, parsed, classified):
                return OperationResult(
                    False,
                    "delivery-uncertain",
                    "SMTP kann bereits erfolgreich gewesen sein",
                )

        agent = object.__new__(MailAgent)
        agent.storage = self.storage
        agent.forwarder = FakeForwarder()
        agent.config = self.config
        result = agent._forward_once(message, classification)
        row = self.storage.get_message(message.stable_key)
        self.assertEqual(result.status, "delivery-uncertain")
        self.assertEqual(row["status"], "delivery-uncertain")
        self.assertEqual(row["retry_count"], 0)
        self.assertIsNone(row["forwarded_at"])

    def test_himalaya_forward_send_uses_temporary_no_save_copy_overlay(self) -> None:
        base_config = self.root / "himalaya.toml"
        base_config.write_text(
            '[accounts.agent]\ndefault = true\nemail = "agent@example.test"\n',
            encoding="utf-8",
        )
        self.config.mailbox.account = "agent"
        self.config.forwarding.payload_dir.mkdir(parents=True, exist_ok=True)

        class FakeRunner:
            def __init__(inner_self) -> None:
                inner_self.overlay_path: Path | None = None
                inner_self.environment = {}

            def run(
                inner_self, args, *, input_text=None, env=None, timeout=None, cwd=None
            ):
                inner_self.environment = dict(env or {})
                paths = inner_self.environment["HIMALAYA_CONFIG"].split(os.pathsep)
                self.assertEqual(paths[0], str(base_config))
                inner_self.overlay_path = Path(paths[-1])
                self.assertTrue(inner_self.overlay_path.is_file())
                override = inner_self.overlay_path.read_text(encoding="utf-8")
                self.assertIn('[accounts."agent"]', override)
                self.assertIn("message.send.save-copy = false", override)
                return CommandResult(list(args), 0, "sent-id", "")

        runner = FakeRunner()
        with mock.patch.dict(os.environ, {"HIMALAYA_CONFIG": str(base_config)}, clear=False):
            result = HimalayaClient(self.config, runner).send_template(  # type: ignore[arg-type]
                "From: agent@example.test\nTo: target@example.test\nSubject: Test\n\nBody\n",
                save_copy=False,
            )
        self.assertTrue(result.ok)
        self.assertIsNotNone(runner.overlay_path)
        self.assertFalse(runner.overlay_path.exists())

    def test_himalaya_marks_imap_copy_failure_as_delivery_uncertain(self) -> None:
        class FakeRunner:
            def run(self, args, *, input_text=None, env=None, timeout=None, cwd=None):
                return CommandResult(
                    list(args),
                    1,
                    "",
                    "\x1b[91mcannot add IMAP message\x1b[0m: unexpected NO response: header limit reached",
                )

        result = HimalayaClient(self.config, FakeRunner()).send_template(  # type: ignore[arg-type]
            "From: a@example.test\nTo: b@example.test\nSubject: Test\n\nBody\n"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "delivery-uncertain")
        self.assertIn("Nicht automatisch erneut senden", result.detail)
        self.assertNotIn("\x1b", result.detail)

    def test_ambiguous_delivery_failure_is_not_retried(self) -> None:
        class FakeHimalaya:
            def __init__(self) -> None:
                self.templates: list[str] = []

            def send_template(
                self, template: str, *, save_copy: bool | None = None
            ) -> OperationResult:
                self.templates.append(template)
                self.asserted_save_copy = save_copy
                return OperationResult(
                    False,
                    "delivery-uncertain",
                    "SMTP-Versand kann bereits erfolgt sein; IMAP-Kopie fehlgeschlagen",
                )

        message = parse_eml(SAMPLE, Envelope("71"), "INBOX")
        fake = FakeHimalaya()
        result = Forwarder(self.config, fake).forward(  # type: ignore[arg-type]
            message,
            Classification("relevant", 0.99, 9, True, "Wichtig"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "delivery-uncertain")
        self.assertEqual(fake.asserted_save_copy, False)
        self.assertEqual(len(fake.templates), 1)

    def test_digest_lists_calendar_events(self) -> None:
        rendered = DigestManager._render(
            "2026-07-17",
            [],
            [{"title": "Projektbesprechung", "starts_at": "2026-07-22T10:00:00+02:00", "status": "created"}],
        )
        self.assertIn("Termine (1)", rendered)
        self.assertIn("Projektbesprechung", rendered)

    def test_digest_lists_nonfinal_technical_statuses(self) -> None:
        rendered = DigestManager._render(
            "2026-07-17",
            [{
                "status": "move-failed",
                "sender_addr": "person@example.test",
                "subject": "Wichtige Nachricht",
                "last_error": "IMAP-Verbindung fehlgeschlagen",
            }],
        )
        self.assertIn("Technische Zwischen-/Fehlerzustaende (1)", rendered)
        self.assertIn("move-failed", rendered)
        self.assertIn("IMAP-Verbindung fehlgeschlagen", rendered)

    def test_ollama_format_is_a_json_schema(self) -> None:
        self.assertEqual(OLLAMA_FORMAT_SCHEMA["type"], "object")
        category = OLLAMA_FORMAT_SCHEMA["properties"]["category"]
        self.assertIn("appointment", category["enum"])

    def test_process_lock_blocks_parallel_runs(self) -> None:
        lock_path = self.root / "mail-agent.lock"
        with ProcessLock(lock_path), self.assertRaises(ProcessLockError), ProcessLock(lock_path):
            pass


if __name__ == "__main__":
    unittest.main()
