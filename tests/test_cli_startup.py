from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, sentinel

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["version"],
        ["tools", "list", "--catalog"],
        ["capabilities", "--schema"],
        ["agent-tools", "status"],
    ],
)
def test_static_cli_never_loads_domain_runtime_or_configuration(
    arguments: list[str], tmp_path: Path,
) -> None:
    # A fresh process matters: collection of other tests imports the domain
    # services, hiding eager imports in a same-process test.
    script = """
import contextlib
import importlib.abc
import io
import json
import sys
from pathlib import Path

blocked = {'personal_assistant.service', 'personal_assistant.adapters.mail',
           'mail_agent.imap_inventory', 'personal_assistant.portfolio'}
class NoDomainRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in blocked:
            raise AssertionError('Unexpected eager domain import: ' + fullname)
        return None
sys.meta_path.insert(0, NoDomainRuntime())
from personal_assistant import cli
if sys.argv[2] == 'installed':
    module_path = Path(cli.__file__).resolve()
    assert 'site-packages' in module_path.parts, str(module_path)
    assert Path(sys.argv[3]).resolve() not in module_path.parents, str(module_path)
def no_state(*args, **kwargs):
    raise AssertionError('Static command accessed configured state')
cli._load_secrets = cli.load_config = cli.create_personal_assistant = no_state
cli._record_interactive_activity = cli._load_container_role_environment = no_state
output = io.StringIO()
with contextlib.redirect_stdout(output):
    try:
        result = cli.main(json.loads(sys.argv[1]))
    except SystemExit as exc:
        result = exc.code
if result == 1 and sys.argv[2] == 'installed' and json.loads(sys.argv[1]) == ['agent-tools', 'status']:
    # The standalone Python wheel intentionally has no immutable Docker plugin.
    # Its typed missing-contract result is valid static introspection, not a
    # reason to import the checkout or relax the plugin integrity check.
    status = json.loads(output.getvalue())
    contract = (Path(cli.__file__).resolve().parents[1]
                / 'docker/openclaw-personal-assistant-plugin/generated-tools.json')
    assert status['ok'] is False and status['native_tool_count'] == 0, status
    assert status['errors'] == [f'Native Toolvertrag fehlt: {contract}'], status
else:
    assert result == 0, output.getvalue()
assert not blocked.intersection(sys.modules)
assert output.getvalue().strip()
print(json.dumps({'ok': True}))
"""
    installed = os.environ.get("OPENCLAW_TEST_INSTALLED") == "1"
    completed = subprocess.run(
        [sys.executable, *(["-P"] if installed else []), "-c", script,
         json.dumps(arguments), "installed" if installed else "source", str(ROOT)],
        cwd=tmp_path if installed else ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"ok": True}


def test_deferred_composition_keeps_the_same_service_and_mail_adapter() -> None:
    from personal_assistant.bootstrap import create_personal_assistant

    with (
        patch("personal_assistant.service.PersonalAssistant", return_value=sentinel.assistant) as factory,
        patch("personal_assistant.adapters.mail.MailMoveService", sentinel.mail_adapter),
    ):
        assert create_personal_assistant(sentinel.config) is sentinel.assistant
        factory.assert_called_once_with(sentinel.config, mail_operations_factory=sentinel.mail_adapter)
