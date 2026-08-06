from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from mail_agent.cli import _handle_nextcloud
from mail_agent.config import load_config
from mail_agent.models import OperationResult
from mail_agent.setup_assistant import extended_help, job_information
from personal_assistant.job_control import CommandResult, JobController, JobSpec

ROOT = Path(__file__).resolve().parents[1]


def load_script(relative: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Skript kann nicht geladen werden: {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class M6LegacyCleanupTests(unittest.TestCase):
    def test_supported_upgrade_floor_has_loadable_r261_fixture(self) -> None:
        policy = json.loads(
            (ROOT / "docs/architecture/compatibility-policy.json").read_text(encoding="utf-8")
        )
        self.assertEqual(policy["minimum_direct_upgrade_version"], "3.4.0-r26.1")
        self.assertEqual(
            policy["legacy_systemd"]["minimum_repository_package_version"],
            "3.4.0-r27.2.5",
        )
        fixture = ROOT / policy["minimum_direct_upgrade_fixture"]
        self.assertEqual((fixture / "VERSION").read_text(encoding="utf-8").strip(), "3.4.0-r26.1")
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            config_path = temp / "config.fixture.toml"
            rules_path = temp / "rules.fixture.toml"
            config_path.write_text(
                (fixture / "mail_agent/config.r26.1.fixture.toml")
                .read_text(encoding="utf-8")
                .replace("mail_agent/rules.r26.1.fixture.toml", str(rules_path)),
                encoding="utf-8",
            )
            shutil.copy2(fixture / "mail_agent/rules.r26.1.fixture.toml", rules_path)
            with patch.dict(os.environ, {"OPENCLAW_MAIL_DATA_DIR": str(temp / "data")}, clear=False):
                config = load_config(config_path)
        self.assertEqual(config.ollama.batch_size, 3)
        self.assertEqual(config.ollama.batch_prefetch, 9)
        self.assertTrue(config.invoices.register_enabled)
        self.assertEqual(config.runtime.rules_file, rules_path)

    def test_removed_python_implementations_are_not_importable(self) -> None:
        for module in (
            "mail_agent.nextcloud_files",
            "mail_agent.config_migrate_r25",
            "mail_agent.config_migrate_r26",
            "mail_agent.config_migrate_r261",
        ):
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(module))

    def test_removed_commands_are_not_advertised_by_runtime_help(self) -> None:
        help_result = subprocess.run(
            [str(ROOT / "scripts/assistant.sh"), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        advertised = help_result.stdout + extended_help("overview") + extended_help("automation")
        for obsolete in ("mail-chief-of-staff", "nextcloud-list.sh", "set-mail-agent-interval.sh"):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, advertised)

    def test_mail_guide_reads_job_state_through_registered_controller(self) -> None:
        response = {
            "ok": True,
            "jobs": [{"desired": "on", "state": "on", "issues": []}],
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(response), "")
        with patch("mail_agent.setup_assistant.subprocess.run", return_value=completed) as run:
            result = job_information()
        command = run.call_args.args[0]
        self.assertEqual(command[-4:], ["jobs", "status", "--target", "mail"])
        self.assertEqual(result, {"desired": "on", "state": "on", "detail": ""})

    def test_nextcloud_status_uses_central_action_bridge_health(self) -> None:
        bridge = SimpleNamespace(health=lambda: OperationResult(True, "ok", "central-files"))
        client = SimpleNamespace(health=lambda **_kwargs: {"ok": True, "detail": "community-signals"})
        agent = SimpleNamespace(nextcloud=client, assistant_bridge=bridge, close=lambda: None)
        config = SimpleNamespace(
            invoices=SimpleNamespace(enabled=True, nextcloud_folder="Assistent/Rechnungen")
        )
        output = io.StringIO()
        with patch("mail_agent.cli.MailAgent", return_value=agent), redirect_stdout(output):
            returncode = _handle_nextcloud(Namespace(nextcloud_command="status"), config)
        payload = json.loads(output.getvalue())
        self.assertEqual(returncode, 0)
        self.assertEqual(payload["files"]["detail"], "central-files")
        self.assertEqual(payload["detail"], "community-signals")

    def test_frozen_legacy_package_manifest_is_exact(self) -> None:
        verifier = load_script("scripts/verify-legacy-package.py", "legacy_manifest_positive")
        self.assertEqual(verifier.verify(ROOT / "legacy/systemd"), [])
        manifest = json.loads(
            (ROOT / "legacy/systemd/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["frozen_from"], "3.4.0-r27.2.5")

    def test_legacy_manifest_detects_changed_missing_and_extra_files(self) -> None:
        verifier = load_script("scripts/verify-legacy-package.py", "legacy_manifest_negative")
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "systemd"
            shutil.copytree(ROOT / "legacy/systemd", package)
            unit = package / "units/mail-agent.timer"
            original = unit.read_text(encoding="utf-8")

            unit.write_text(original + "\n# drift\n", encoding="utf-8")
            self.assertTrue(any("Geaenderter Legacy-Inhalt" in item for item in verifier.verify(package)))
            unit.write_text(original, encoding="utf-8")

            unit.unlink()
            self.assertTrue(any("Zusaetzlicher oder fehlender" in item for item in verifier.verify(package)))
            unit.write_text(original, encoding="utf-8")

            (package / "unexpected.service").write_text("[Unit]\n", encoding="utf-8")
            self.assertTrue(any("Fehlender Legacy-Eintrag" in item for item in verifier.verify(package)))

            (package / "unexpected.service").unlink()
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["frozen_from"] = "3.4.0-r25"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(any("frozen_from" in item for item in verifier.verify(package)))

    def test_legacy_interval_helper_is_fail_closed(self) -> None:
        environment = os.environ.copy()
        environment.pop("OPENCLAW_ENABLE_LEGACY_SYSTEMD", None)
        result = subprocess.run(
            [str(ROOT / "legacy/systemd/set-mail-agent-interval.sh"), "status"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("eingefrorener Legacy-systemd-Helfer", result.stderr)

    def test_frozen_legacy_package_is_not_in_active_image_context(self) -> None:
        patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/legacy/", patterns)

    def test_registered_job_controller_cannot_activate_legacy_units_implicitly(self) -> None:
        spec = JobSpec(
            name="mail",
            description="legacy fixture",
            timer_unit="mail-agent.timer",
            service_unit="mail-agent.service",
            default_on=False,
            standard=True,
        )

        def runner(_command: list[str], _timeout: int) -> CommandResult:
            return CommandResult(1, "LoadState=not-found\n", "not found")

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {"OPENCLAW_RUNTIME": "", "OPENCLAW_ENABLE_LEGACY_SYSTEMD": ""},
            clear=False,
        ):
            root = Path(temporary)
            controller = JobController(
                state_path=root / "job-control.json",
                workspace_root=root,
                unit_dir=root / "units",
                runner=runner,
                specs=(spec,),
            )
            result = controller.on(target="mail")
        self.assertFalse(result["ok"])
        self.assertIn("Legacy-systemd-Aktivierung ist eingefroren", result["actions"][0]["detail"])
        self.assertFalse(controller.state["desired"]["mail"])

    def test_component_inventory_exactly_covers_declared_component_types(self) -> None:
        inventory = json.loads(
            (ROOT / "docs/architecture/component-inventory.json").read_text(encoding="utf-8")
        )
        manifest_paths = {
            line.partition("  ./")[2]
            for line in (ROOT / "SOURCE_MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
            if "  ./" in line
        }
        expected = {
            path
            for path in manifest_paths
            if path.endswith((".py", ".sh", ".md"))
            or path.startswith("skills/")
            or path.startswith("legacy/systemd/units/")
            or "migrat" in path.casefold()
        }
        actual = {item["path"] for item in inventory["components"]}
        self.assertEqual(actual, expected)
        self.assertEqual(inventory["component_count"], len(actual))

    def test_removed_inventory_records_have_complete_evidence(self) -> None:
        inventory = json.loads(
            (ROOT / "docs/architecture/component-inventory.json").read_text(encoding="utf-8")
        )
        manifest_paths = {
            line.partition("  ./")[2]
            for line in (ROOT / "SOURCE_MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
            if "  ./" in line
        }
        for decision in inventory["removed_components"]:
            with self.subTest(paths=decision["paths"]):
                self.assertIn(decision["classification"], {"deprecated", "unused", "migration-only"})
                self.assertTrue(decision["owner"])
                self.assertTrue(decision["deployment_evidence"])
                self.assertTrue(decision["replacement"])
                self.assertTrue(decision["rollback_relevance"])
                self.assertTrue(set(decision["paths"]).isdisjoint(manifest_paths))

    def test_archived_release_notes_are_outside_active_document_contract(self) -> None:
        checker = load_script("scripts/check-docs.py", "m6_check_docs")
        active = set(checker.active_markdown_files(ROOT))
        self.assertNotIn(Path("docs/archive/AUDIT_INITIAL_IMPORT.md"), active)
        self.assertNotIn(Path("docs/archive/releases/HOTFIX_3_4_0_R9.md"), active)

    def test_upgrade_fixture_contains_no_credentials_or_runtime_data(self) -> None:
        fixture = ROOT / "tests/fixtures/upgrade/r26.1"
        self.assertEqual(
            sorted(path.name for path in fixture.rglob("*") if path.is_file()),
            ["VERSION", "config.r26.1.fixture.toml", "rules.r26.1.fixture.toml"],
        )
        config = tomllib.loads(
            (fixture / "mail_agent/config.r26.1.fixture.toml").read_text(encoding="utf-8")
        )
        self.assertNotIn("mailbox", config)
        self.assertNotIn("nextcloud", config)


if __name__ == "__main__":
    unittest.main()
