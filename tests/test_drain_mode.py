from __future__ import annotations

import unittest
from types import SimpleNamespace

from mail_agent.app import MailAgent, RunSummary
from mail_agent.cli import build_parser
from mail_agent.models import Envelope


class _Classifier:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset_metrics(self) -> None:
        self.reset_calls += 1

    def metrics_snapshot(self) -> dict[str, object]:
        return {"model_requests": 0}


class _Himalaya:
    def __init__(self, inbox_states: list[bool]) -> None:
        self.inbox_states = list(inbox_states)
        self.calls = 0

    def list_envelopes(self, folder: str, limit: int):
        self.calls += 1
        state = self.inbox_states.pop(0) if self.inbox_states else False
        return ([Envelope("1")] if state else []), ""


def _batch(processed: int) -> RunSummary:
    summary = RunSummary(processed=processed)
    for index in range(processed):
        summary.actions.append({
            "message": f"mid:{index}",
            "subject": "",
            "category": "routine",
            "status": "moved",
            "ok": True,
            "detail": "",
            "destination": "Agent/Routine",
            "path": "",
        })
    return summary


def _agent(*, dry_run: bool, batches: list[RunSummary], inbox_states: list[bool]) -> MailAgent:
    agent = object.__new__(MailAgent)
    agent.dry_run = dry_run
    agent.config = SimpleNamespace(mailbox=SimpleNamespace(source_folder="INBOX"))
    agent.classifier = _Classifier()
    agent.himalaya = _Himalaya(inbox_states)
    agent.prepare_calls = 0
    agent.digest_calls = 0

    def prepare(summary):
        agent.prepare_calls += 1
        return True

    def append_digest(summary):
        agent.digest_calls += 1

    agent._prepare_run = prepare
    queue = list(batches)
    agent._process_batch = lambda *, limit: queue.pop(0)
    agent._append_digest = append_digest
    return agent


class DrainModeTests(unittest.TestCase):
    def test_drain_repeats_until_inbox_is_empty(self) -> None:
        agent = _agent(
            dry_run=False,
            batches=[_batch(20), _batch(5)],
            inbox_states=[True, False],
        )

        summary = agent.drain(
            batch_size=20,
            max_messages=500,
            max_runtime_seconds=2700,
            max_batches=100,
            include_digest=True,
        )

        self.assertEqual(summary.processed, 25)
        self.assertEqual(summary.drain["batches"], 2)
        self.assertEqual(summary.drain["stop_reason"], "queue-empty")
        self.assertFalse(summary.drain["inbox_remaining"])
        self.assertEqual(summary.errors, [])
        self.assertEqual(agent.classifier.reset_calls, 1)
        self.assertEqual(agent.prepare_calls, 1)
        self.assertEqual(agent.digest_calls, 1)

    def test_dry_run_executes_only_one_batch(self) -> None:
        agent = _agent(
            dry_run=True,
            batches=[_batch(20)],
            inbox_states=[True],
        )

        summary = agent.drain(include_digest=False)

        self.assertEqual(summary.processed, 20)
        self.assertEqual(summary.drain["batches"], 1)
        self.assertEqual(summary.drain["stop_reason"], "dry-run-single-batch")
        self.assertTrue(summary.drain["inbox_remaining"])

    def test_drain_stops_with_error_when_no_progress_is_possible(self) -> None:
        agent = _agent(
            dry_run=False,
            batches=[_batch(0)],
            inbox_states=[True],
        )

        summary = agent.drain(include_digest=False)

        self.assertEqual(summary.drain["stop_reason"], "no-progress")
        self.assertTrue(summary.errors)

    def test_safety_limit_stops_a_long_backlog_without_failing(self) -> None:
        agent = _agent(
            dry_run=False,
            batches=[_batch(20)],
            inbox_states=[True],
        )

        summary = agent.drain(
            batch_size=20,
            max_messages=20,
            max_runtime_seconds=2700,
            max_batches=100,
            include_digest=False,
        )

        self.assertEqual(summary.processed, 20)
        self.assertEqual(summary.drain["stop_reason"], "max-messages")
        self.assertTrue(summary.drain["inbox_remaining"])
        self.assertEqual(summary.errors, [])

    def test_cli_exposes_bounded_drain_options(self) -> None:
        args = build_parser().parse_args([
            "run", "--drain", "--batch-size", "25", "--max-messages", "600",
            "--max-runtime", "1800", "--max-batches", "40", "--no-digest",
        ])
        self.assertTrue(args.drain)
        self.assertEqual(args.batch_size, 25)
        self.assertEqual(args.max_messages, 600)
        self.assertEqual(args.max_runtime, 1800)
        self.assertEqual(args.max_batches, 40)


if __name__ == "__main__":
    unittest.main()
