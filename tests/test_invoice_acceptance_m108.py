from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from personal_assistant.tool_registry import tool_definitions
from scripts.check_artifact import inspect_files
from scripts.evaluate_invoice_quality import evaluate

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/invoices"


class InvoiceAcceptanceM108Tests(unittest.TestCase):
    maxDiff = None

    def test_all_sanitized_field_baselines_remain_exact_and_fail_closed(self) -> None:
        cases = (
            ("m10_sanitized_corpus.json", "m10_extractor_baseline.json"),
            ("m102_number_date_corpus.json", "m102_number_date_baseline.json"),
            ("m103_amount_corpus.json", "m103_amount_baseline.json"),
        )
        for corpus_name, baseline_name in cases:
            with self.subTest(corpus=corpus_name):
                report = evaluate(FIXTURES / corpus_name)
                expected = json.loads((FIXTURES / baseline_name).read_text(encoding="utf-8"))
                self.assertEqual(report, expected)
                self.assertEqual(report["outcomes"]["false_confirmed"], 0)
                self.assertEqual(report["overall_fields"]["precision"], 1.0)
                self.assertEqual(report["overall_fields"]["coverage"], 1.0)
                for item in report["cases"]:
                    if item["arithmetic_error"]:
                        self.assertEqual(item["status"], "review")

    def test_invoice_tool_effects_and_approvals_match_the_final_contract(self) -> None:
        tools = {tool.id: tool for tool in tool_definitions()}
        expected = {
            "assistant.invoices.status": ("read", False, "none"),
            "assistant.invoices.audit": ("read", False, "none"),
            "assistant.invoices.reprocess-preview": ("read", False, "none"),
            "assistant.invoices.reprocess-apply": (
                "write",
                True,
                "explicit-user-single-invoice-reprocess",
            ),
        }
        for tool_id, contract in expected.items():
            tool = tools[tool_id]
            self.assertEqual((tool.mode, tool.writes_external_data, tool.approval), contract)

    def test_wheel_and_image_artifact_guard_rejects_pdf_payloads(self) -> None:
        issues = inspect_files(
            [("opt/openclaw-agent/private/invoice.pdf", b"%PDF-1.4\nprivate")]
        )
        self.assertEqual(
            issues,
            ["opt/openclaw-agent/private/invoice.pdf: Dokumentinhalt"],
        )

    def test_git_tree_contains_no_pdf_mail_database_log_or_runtime_config(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        paths = [
            value.decode("utf-8")
            for value in result.stdout.split(b"\0")
            if value
        ]
        forbidden_suffixes = (
            ".pdf",
            ".eml",
            ".msg",
            ".sqlite",
            ".sqlite3",
            ".db",
            ".log",
        )
        self.assertEqual(
            [path for path in paths if path.casefold().endswith(forbidden_suffixes)],
            [],
        )
        self.assertNotIn("mail_agent/config.toml", paths)
        self.assertNotIn("personal_assistant/config.toml", paths)

    def test_ci_runs_wheel_all_role_images_scans_and_hermetic_integration(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        required = (
            "./scripts/check-repo.sh",
            "./scripts/check-wheel.sh",
            "build/wheel-baseline.json",
            "./docker/scripts/build-local.sh openclaw-agent:m7-ci "
            "openclaw-agent:m7-ci-proxy openclaw-agent:m7-ci-maintenance",
            "./scripts/check-role-images.sh",
            "./scripts/check-image-supply-chain.sh openclaw-agent:m7-ci runtime",
            "./scripts/check-image-supply-chain.sh openclaw-agent:m7-ci-proxy proxy",
            "./scripts/check-image-supply-chain.sh openclaw-agent:m7-ci-maintenance maintenance",
            "./scripts/check-m8-integration.sh",
        )
        for command in required:
            with self.subTest(command=command):
                self.assertIn(command, ci)

    def test_rollout_contract_keeps_acceptance_preview_apply_and_recovery_separate(self) -> None:
        text = (ROOT / "docs/INVOICE_M10_ROLLOUT.md").read_text(encoding="utf-8")
        headings = (
            "## 1. Freigabevoraussetzungen",
            "## 2. Gesicherte Installation",
            "## 3. Read-only Baseline",
            "## 4. Canary-Vorschau",
            "## 5. Ausdrueckliche Einzeluebernahme",
            "## 6. Nachmessung",
            "## 7. Teilfehler und Rollback",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Plan wurde in M10.8 nicht produktiv ausgefuehrt", text)
        self.assertIn("invoices reprocess-apply", text)
        self.assertIn("--yes", text)
        self.assertIn("Ein Image-Rollback allein", text)


if __name__ == "__main__":
    unittest.main()
