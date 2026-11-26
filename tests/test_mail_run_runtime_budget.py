from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_agent.app import MailAgent, RunSummary
from mail_agent.classifier import RuntimeBudgetExceeded
from mail_agent.cli import main
from mail_agent.models import OperationResult
from mail_agent.setup_assistant import read_setup_state


class ClockClassifier:
    def __init__(self, clock, metrics):
        self.clock = clock
        self.metrics = metrics
        self.deadline = None

    def reset_metrics(self):
        pass

    def set_runtime_deadline(self, deadline):
        self.deadline = deadline

    def clear_runtime_deadline(self):
        self.deadline = None

    def metrics_snapshot(self):
        return dict(self.metrics)

    def finish_request(self, elapsed):
        self.clock['now'] += elapsed
        if self.deadline is not None and self.clock['now'] > self.deadline:
            raise RuntimeBudgetExceeded('Kontrolliertes Laufzeitbudget erreicht')


def run_fixture(
    tmp_path, monkeypatch, *, elapsed=100, metrics=None, extra_args=(), configure=None, digest=False,
    command='run',
):
    config = SimpleNamespace(
        path=tmp_path / 'config.toml',
        mailbox=SimpleNamespace(source_folder='INBOX'),
        nextcloud=SimpleNamespace(enabled=False),
        runtime=SimpleNamespace(
            database=tmp_path / 'mail.sqlite3',
            log_file=tmp_path / 'mail.log',
            lock_file=tmp_path / 'mail.lock',
            rules_file=tmp_path / 'rules.toml',
            learning_folders_file=tmp_path / 'learning.toml',
        ),
    )
    clock = {'now': 100.0}
    classifier = ClockClassifier(clock, metrics or {})
    agent = object.__new__(MailAgent)
    agent.config = config
    agent.dry_run = True
    agent.classifier = classifier
    agent.antivirus = SimpleNamespace(close=lambda: None)
    agent.storage = SimpleNamespace(close=lambda: None)
    agent._prepare_run = lambda summary: True
    observed = {}

    def process_batch(*, limit):
        observed['deadline'] = classifier.deadline
        classifier.finish_request(elapsed)
        return RunSummary(processed=limit, actions=[{'ok': True} for _ in range(limit)])

    agent._process_batch = process_batch
    agent.himalaya = SimpleNamespace(list_envelopes=lambda folder, limit: ([], ''))
    if configure is not None:
        configure(agent, clock)
    monkeypatch.setattr('mail_agent.app.time.monotonic', lambda: clock['now'])
    monkeypatch.setattr('mail_agent.cli.load_env_file', lambda *args: None)
    monkeypatch.setattr('mail_agent.cli.load_config', lambda *args: config)
    monkeypatch.setattr('mail_agent.cli._configure_logging', lambda *args: None)
    monkeypatch.setattr('mail_agent.cli.MailAgent', lambda *args, **kwargs: agent)
    args = [command, '--dry-run', '--limit', '5']
    if command == 'run':
        args.extend(['--max-runtime', '900', '--shutdown-reserve', '60'])
        if not digest:
            args.append('--no-digest')
    code = main([*args, *extra_args])
    return code, config, classifier, observed


def test_single_batch_cli_budget_reaches_model_and_completed_validation_is_recorded(
    tmp_path: Path, monkeypatch, capsys
):
    code, config, classifier, observed = run_fixture(tmp_path, monkeypatch)
    result = json.loads(capsys.readouterr().out)
    assert observed['deadline'] == 940.0
    assert code == 0
    assert result['processed'] == 5
    assert result['errors'] == []
    assert read_setup_state(config)['last_dry_run_ok'] is True
    assert classifier.deadline is None


def test_interrupted_single_batch_does_not_stamp_successful_dry_run(
    tmp_path: Path, monkeypatch, capsys
):
    code, config, classifier, _ = run_fixture(tmp_path, monkeypatch, elapsed=841)
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result['errors']
    assert result['processed'] == 0
    assert read_setup_state(config)['last_dry_run_ok'] is False
    assert classifier.deadline is None


@pytest.mark.parametrize('metric', ['queue_timeouts', 'upstream_timeouts', 'runtime_budget_stops'])
def test_model_timeout_fallback_does_not_approve_validation(
    tmp_path: Path, monkeypatch, capsys, metric
):
    code, config, classifier, _ = run_fixture(tmp_path, monkeypatch, metrics={metric: 1})
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result['errors']
    assert read_setup_state(config)['last_dry_run_ok'] is False
    assert classifier.deadline is None


def test_invalid_single_batch_budget_stops_before_validation(tmp_path: Path, monkeypatch, capsys):
    code, config, _, observed = run_fixture(
        tmp_path, monkeypatch, extra_args=('--max-runtime', '30', '--shutdown-reserve', '60')
    )
    assert code == 2
    assert 'deadline' not in observed
    assert read_setup_state(config) == {}
    assert 'shutdown-reserve' in capsys.readouterr().err


@pytest.mark.parametrize('final_step', ['_append_digest', '_finish_telemetry'])
def test_validation_finishing_after_deadline_cannot_stamp_success(tmp_path, monkeypatch, capsys, final_step):
    def configure(agent, clock):
        agent._append_digest = lambda summary: None
        setattr(agent, final_step, lambda summary: clock.update(now=1001))

    code, config, classifier, _ = run_fixture(tmp_path, monkeypatch, configure=configure, digest=True)
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['processed'] == 5
    assert 'mail-runtime-budget-exhausted' in result['errors']
    assert read_setup_state(config)['last_dry_run_ok'] is False
    assert classifier.deadline is None


@pytest.mark.parametrize('phase', ['telemetry-start', 'metrics-snapshot'])
def test_validation_exception_always_clears_deadline(tmp_path, monkeypatch, phase):
    classifiers = []

    def fail(*args):
        raise OSError('synthetic telemetry failure')

    def configure(agent, clock):
        classifiers.append(agent.classifier)
        if phase == 'telemetry-start':
            agent._start_telemetry = fail
        else:
            agent.classifier.metrics_snapshot = fail

    with pytest.raises(OSError, match='synthetic telemetry failure'):
        run_fixture(tmp_path, monkeypatch, configure=configure)
    assert classifiers[0].deadline is None


def test_budget_abort_preserves_completed_feedback_action(tmp_path, monkeypatch, capsys):
    def configure(agent, clock):
        del agent._process_batch  # Use the real feedback and inbox processing order.
        agent.config.folders = SimpleNamespace(
            feedback_spam='SpamFeedback', feedback_unimportant='RoutineFeedback',
            feedback_important='ImportantFeedback', feedback_not_spam='OtherFeedback',
        )
        agent.himalaya.list_envelopes = lambda folder, limit: ([1] if folder == 'SpamFeedback' else [], '')
        agent._load_message = lambda *args: SimpleNamespace(stable_key='synthetic-feedback', subject='')
        agent.storage.get_message = lambda key: None
        agent._route = lambda *args, **kwargs: OperationResult(True, 'would-move', 'synthetic')
        agent._available_quarantine_folders = lambda: []
        agent._process_folder = lambda *args: agent.classifier.finish_request(841)

    code, config, _, _ = run_fixture(tmp_path, monkeypatch, configure=configure)
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and read_setup_state(config)['last_dry_run_ok'] is False
    assert result['processed'] == 1
    assert [(action['message'], action['status']) for action in result['actions']] == [
        ('synthetic-feedback', 'would-move'),
    ]


@pytest.mark.parametrize('elapsed,metrics', [(841, {}), (100, {'upstream_timeouts': 1})])
def test_incomplete_dry_drain_cannot_stamp_success(tmp_path, monkeypatch, capsys, elapsed, metrics):
    code, config, classifier, _ = run_fixture(
        tmp_path, monkeypatch, elapsed=elapsed, metrics=metrics, extra_args=('--drain', '--batch-size', '5'),
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result['errors']
    assert read_setup_state(config)['last_dry_run_ok'] is False
    assert classifier.deadline is None


@pytest.mark.parametrize('envelopes,limit,expected_code', [([1, 2], 5, 1), ([1], 5, 1), ([1], 1, 0)])
def test_feedback_admission_stops_new_messages_and_folder_reads_in_reserve(
    tmp_path, monkeypatch, capsys, envelopes, limit, expected_code,
):
    listed, loaded = [], []

    def configure(agent, clock):
        del agent._process_batch
        agent.config.folders = SimpleNamespace(
            feedback_spam='SpamFeedback', feedback_unimportant='RoutineFeedback',
            feedback_important='ImportantFeedback', feedback_not_spam='OtherFeedback',
        )

        def list_envelopes(folder, limit):
            listed.append(folder)
            return (envelopes if folder == 'SpamFeedback' else []), ''

        def load(folder, envelope, summary):
            loaded.append(envelope)
            return SimpleNamespace(stable_key=str(envelope), subject='')

        def route(*args, **kwargs):
            clock['now'] = 941
            return OperationResult(True, 'would-move', 'synthetic')

        agent.himalaya.list_envelopes = list_envelopes
        agent.himalaya.list_folders = lambda: ([], '')
        agent.config.mailbox.quarantine_folders = []
        agent._load_message = load
        agent.storage.get_message = lambda key: None
        agent._route = route
        agent.config.ollama = SimpleNamespace(batch_enabled=False)

    code, config, _, _ = run_fixture(
        tmp_path, monkeypatch, configure=configure, extra_args=('--limit', str(limit)),
    )
    result = json.loads(capsys.readouterr().out)
    assert (code, loaded, listed) == (expected_code, [1], ['SpamFeedback'])
    assert [action['message'] for action in result['actions']] == ['1']
    assert result['processed'] == 1
    assert read_setup_state(config)['last_dry_run_ok'] is (expected_code == 0)


@pytest.mark.parametrize('quarantine', [False, True])
@pytest.mark.parametrize('cross_at', ['fetch', 'first-route'])
def test_pending_rules_batch_must_admit_classification_and_each_routed_message(
    tmp_path, monkeypatch, capsys, quarantine, cross_at,
):
    classified, routed = [], []

    def configure(agent, clock):
        del agent._process_batch
        agent._process_feedback = lambda *args, **kwargs: None
        agent._available_quarantine_folders = lambda: ['Junk'] if quarantine else []
        agent.config.mailbox.quarantine_max_per_run = 10
        agent.config.ollama = SimpleNamespace(batch_enabled=True, batch_prefetch=2)
        source = 'Junk' if quarantine else 'INBOX'
        agent.himalaya.list_envelopes = lambda folder, limit: ([1, 2] if folder == source else [], '')

        def load(folder, envelope, summary):
            if envelope == 2 and cross_at == 'fetch':
                clock['now'] = 941
            return SimpleNamespace(stable_key=str(envelope), subject='')

        def classify(messages):
            classified.extend(message.stable_key for message in messages)
            return [SimpleNamespace(category='routine') for _ in messages]

        def route(message, classification, folder):
            routed.append(message.stable_key)
            if cross_at == 'first-route':
                clock['now'] = 941
            return OperationResult(True, 'would-move', 'synthetic')

        agent._load_message = load
        agent._handle_calendar_command_mail = lambda *args: None
        agent._handle_calendar_approval_reply = lambda *args: None
        agent.storage.get_message = lambda key: None
        agent._retry_pending_move = lambda *args: None
        agent.classifier.classify_many = classify
        agent._route = agent._route_quarantine = route

    code, config, _, _ = run_fixture(tmp_path, monkeypatch, configure=configure, extra_args=('--limit', '10'))
    result = json.loads(capsys.readouterr().out)
    assert classified == ([] if cross_at == 'fetch' else ['1', '2'])
    assert routed == ([] if cross_at == 'fetch' else ['1'])
    assert result['processed'] == len(routed)
    assert code == 1 and read_setup_state(config)['last_dry_run_ok'] is False


@pytest.mark.parametrize('metric', ['queue_timeouts', 'upstream_timeouts', 'runtime_budget_stops', None])
def test_spam_review_real_cli_stamps_only_completed_model_validation(tmp_path, monkeypatch, capsys, metric):
    def configure(agent, clock):
        agent._available_quarantine_folders = lambda: ['Junk']

        def review(folder, summary, limit):
            summary.processed += 1

        agent._process_quarantine_folder = review

    code, config, _, _ = run_fixture(
        tmp_path, monkeypatch, command='spam-review', configure=configure,
        metrics={metric: 1} if metric else {},
    )
    result = json.loads(capsys.readouterr().out)
    assert result['processed'] == 1
    assert code == (1 if metric else 0)
    assert bool(result['errors']) == bool(metric)
    assert read_setup_state(config)['last_dry_run_ok'] is (metric is None)


@pytest.mark.parametrize('command,drain', [('run', False), ('run', True), ('spam-review', False)])
def test_all_validation_stamp_writers_check_the_returned_metrics(
    tmp_path, monkeypatch, capsys, command, drain,
):
    def configure(agent, clock):
        def return_summary(**kwargs):
            return RunSummary(processed=1, classifier={'queue_timeouts': 1})

        agent.review_quarantine = agent.drain = agent.run = return_summary

    code, config, _, _ = run_fixture(
        tmp_path, monkeypatch, command=command, configure=configure, extra_args=('--drain',) if drain else (),
    )
    assert code == 1
    assert 'dry-run-model-timeout' in json.loads(capsys.readouterr().out)['errors']
    assert read_setup_state(config)['last_dry_run_ok'] is False
