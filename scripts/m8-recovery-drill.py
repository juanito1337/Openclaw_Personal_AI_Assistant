#!/usr/bin/env python3
"""Run the M8 backup/restore drill exclusively against temporary fixture roots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "docker/scripts/backup.sh"
RESTORE = ROOT / "docker/scripts/restore-local-state.sh"
VERIFY = ROOT / "docker/scripts/verify-backup.sh"
COMPATIBILITY = ROOT / "docker/scripts/check-layout-compatibility.py"


def _version(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return (result.stdout or result.stderr).splitlines()[0].strip()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _fixture(openclaw: Path, version: str) -> None:
    for name in ("state", "config/himalaya", "secrets", "backups/releases"):
        (openclaw / name).mkdir(parents=True, exist_ok=True)
    (openclaw / "config/release.conf").write_text(f"release={version}\n", encoding="utf-8")
    (openclaw / "secrets/fixture.env").write_text(
        "M8_LOCAL_FIXTURE=non-production\n", encoding="utf-8"
    )
    if version == "3.4.0-r26.1":
        source = ROOT / "tests/fixtures/upgrade/r26.1"
        shutil.copytree(source / "mail_agent", openclaw / "state/workspace/mail_agent")
        database = openclaw / "state/workspace/mail_agent/data/mail_agent.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
    else:
        (openclaw / "state/.container-layout.json").write_text(
            json.dumps({"layout": 3, "release": version}) + "\n", encoding="utf-8"
        )
        database = openclaw / "state/v3/domains/mail/mail_agent.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO fixture(value) VALUES(?)", (f"snapshot-{version}",))
    connection.commit()
    connection.close()


def _environment(openclaw: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "OPENCLAW_ROOT": str(openclaw),
            "OPENCLAW_STATE_DIR": str(openclaw / "state"),
            "OPENCLAW_CONFIG_DIR": str(openclaw / "config"),
            "OPENCLAW_SECRETS_DIR": str(openclaw / "secrets"),
            "OPENCLAW_BACKUP_DIR": str(openclaw / "backups/releases"),
            "HIMALAYA_CONFIG_DIR": str(openclaw / "config/himalaya"),
            "OPENCLAW_RESTORE_OFFLINE": "YES",
            "PREVIOUS_IMAGE": "fixture.invalid/openclaw:previous",
            "TARGET_IMAGE": "fixture.invalid/openclaw:target",
            "PREVIOUS_RUNTIME": "docker",
            "BACKUP_RETENTION_RELEASES": "10",
        }
    )
    return environment


def _run_drill(label: str, version: str, *, failed_upgrade: bool = False) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="openclaw-m8-recovery-") as folder:
        openclaw = Path(folder) / "openclaw"
        _fixture(openclaw, version)
        environment = _environment(openclaw)
        expected = {
            name: _tree_digest(openclaw / name) for name in ("state", "config", "secrets")
        }

        started = time.perf_counter()
        backup_result = subprocess.run(
            [str(BACKUP), label],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        backup_seconds = time.perf_counter() - started
        backup_id = backup_result.stdout.strip().splitlines()[-1]
        backup_path = openclaw / "backups/releases" / backup_id
        subprocess.run(
            [str(VERIFY), str(backup_path)],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
        )
        manifest = json.loads((backup_path / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("verified"):
            raise RuntimeError(f"Drill-Backup ist nicht verifiziert: {label}")

        failure_gate: dict[str, Any] | None = None
        if failed_upgrade:
            marker = openclaw / "state/.container-layout.json"
            marker.write_text("{invalid-migration\n", encoding="utf-8")
            before_gate = _tree_digest(openclaw / "state")
            gate = subprocess.run(
                [
                    sys.executable,
                    str(COMPATIBILITY),
                    "--state-dir",
                    str(openclaw / "state"),
                    "--target-image",
                    "fixture.invalid/openclaw:target",
                    "--target-min",
                    "1",
                    "--target-max",
                    "3",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            failure_gate = {
                "exit_code": gate.returncode,
                "failed_closed": gate.returncode != 0,
                "state_unchanged": _tree_digest(openclaw / "state") == before_gate,
            }
            if not failure_gate["failed_closed"] or not failure_gate["state_unchanged"]:
                raise RuntimeError("Ungueltige Migration wurde nicht unveraendert abgewiesen")
        else:
            (openclaw / "state/release-after-snapshot.txt").write_text(
                "must disappear during restore\n", encoding="utf-8"
            )
        (openclaw / "config/release.conf").write_text("failed-upgrade\n", encoding="utf-8")

        started = time.perf_counter()
        subprocess.run(
            [str(RESTORE), str(backup_path)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        restore_seconds = time.perf_counter() - started
        actual = {name: _tree_digest(openclaw / name) for name in ("state", "config", "secrets")}
        verified = actual == expected
        if not verified:
            raise RuntimeError(f"Restore-Inhalt stimmt nicht mit Snapshot ueberein: {label}")
        with tarfile.open(backup_path / "payload.tar.gz", "r:gz") as archive:
            members = sorted(member.name for member in archive.getmembers())
        return {
            "scenario": label,
            "source_version": version,
            "backup_seconds": round(backup_seconds, 6),
            "restore_seconds": round(restore_seconds, 6),
            "backup_bytes": (backup_path / "payload.tar.gz").stat().st_size,
            "verified_exact_tree": verified,
            "archive_roots": sorted({name.split("/", 1)[0] for name in members}),
            "invalid_migration_gate": failure_gate,
            "accepted_operations_lost": 0,
        }


def run() -> dict[str, Any]:
    results = [
        _run_drill("minimum-r26.1", "3.4.0-r26.1"),
        _run_drill("current-r27.2.5", "3.4.0-r27.2.5"),
        _run_drill("failed-upgrade", "3.4.0-r27.2.5", failed_upgrade=True),
    ]
    return {
        "schema_version": 1,
        "milestone": "M8",
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scope": "temporary local fixture roots; no /srv/openclaw and no remote systems",
        "minimum_direct_upgrade_version": "3.4.0-r26.1",
        "results": results,
        "rto": {
            "measured_local_restore_max_seconds": max(item["restore_seconds"] for item in results),
            "excludes": ["image pull", "remote snapshot restore", "production health convergence"],
        },
        "rpo": {
            "accepted_local_operations_lost_in_drill": 0,
            "contract": "state after the verified snapshot is not recoverable from that snapshot",
            "remote_without_snapshot": "not recoverable by OpenClaw local rollback",
        },
        "tools": {
            "python": _version([sys.executable, "--version"]),
            "sqlite": _version(["sqlite3", "--version"]),
            "tar": _version(["tar", "--version"]),
            "rsync": _version(["rsync", "--version"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M8 local backup/restore rehearsal")
    parser.add_argument("--output", type=Path, default=ROOT / "build/m8-recovery.json")
    args = parser.parse_args()
    result = run()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
