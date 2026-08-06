from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_ruff_baseline_is_bound_to_the_actual_offending_source_line(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/check-ruff.py"
    source = tmp_path / "legacy_line.py"
    baseline = tmp_path / "ruff-baseline.json"
    source.write_text(f"# {'a' * 120}\n", encoding="utf-8")

    written = subprocess.run(
        [
            sys.executable,
            str(script),
            "--baseline",
            str(baseline),
            "--write-baseline",
            str(source),
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    accepted = subprocess.run(
        [sys.executable, str(script), "--baseline", str(baseline), str(source)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    source.write_text(f"# {'b' * 120}\n", encoding="utf-8")
    rejected = subprocess.run(
        [sys.executable, str(script), "--baseline", str(baseline), str(source)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert written.returncode == 0
    assert accepted.returncode == 0
    assert rejected.returncode == 1
    assert "nicht baselinierte Ruff-Befunde" in rejected.stderr
