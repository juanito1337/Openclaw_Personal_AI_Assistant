"""Run the plugin's browser/auth/Unix bridge contract tests without live services."""

import subprocess
from pathlib import Path


def test_nextcloud_setup_ui_contracts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/nextcloud_setup_ui.test.mjs"),
         str(root / "tests/nextcloud_setup_ui_browser.test.mjs")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
