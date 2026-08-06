from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-docs.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("check-docs.py konnte nicht geladen werden")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ArchitectureDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checker = load_checker()

    def test_repository_documentation_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(ROOT)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_broken_internal_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("[fehlt](docs/fehlt.md)\n", encoding="utf-8")
            errors = self.checker.validate_links(root)
        self.assertTrue(any("ungueltiger interner Link" in error for error in errors))

    def test_owner_contract_rejects_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            architecture = root / "docs/architecture"
            architecture.mkdir(parents=True)
            (architecture / "README.md").write_text("# Architektur\n", encoding="utf-8")
            (architecture / "owners.json").write_text(
                json.dumps({"schema_version": 1, "documents": []}),
                encoding="utf-8",
            )
            errors = self.checker.validate_owners(root)
        self.assertIn("owners.json: Owner fehlt fuer docs/architecture/README.md", errors)

    def test_owner_contract_rejects_duplicate_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            architecture = root / "docs/architecture"
            architecture.mkdir(parents=True)
            path = "docs/architecture/README.md"
            (architecture / "README.md").write_text("# Architektur\n", encoding="utf-8")
            (architecture / "owners.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "documents": [
                        {"path": path, "owner": "A"},
                        {"path": path, "owner": "B"},
                    ],
                }),
                encoding="utf-8",
            )
            errors = self.checker.validate_owners(root)
        self.assertTrue(any("doppelter Dokumentpfad" in error for error in errors))

    def test_readme_reachability_is_bounded_to_two_links(self) -> None:
        errors = self.checker.validate_readme_reachability(ROOT)
        self.assertEqual(errors, [])

    def test_role_and_data_matrices_cover_required_entries(self) -> None:
        errors = self.checker.validate_matrices(ROOT)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
