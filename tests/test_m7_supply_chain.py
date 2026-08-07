from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/m7_supply_chain.py"
DEPLOY_VERIFY = ROOT / "docker/scripts/verify-image-supply-chain.sh"
DEPLOY = ROOT / "docker/scripts/deploy.sh"


def load_module():
    spec = importlib.util.spec_from_file_location("m7_supply_chain", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class M7SupplyChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_lock_and_all_workflow_actions_are_immutable(self) -> None:
        report = self.module.verify_lock(ROOT)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["base_images"], 2)
        self.assertGreaterEqual(report["github_actions"], 8)

    def test_private_repository_uses_registry_native_attestations(self) -> None:
        workflow = (ROOT / ".github/workflows/container.yml").read_text(encoding="utf-8")
        self.assertNotIn("actions/attest-build-provenance", workflow)
        self.assertNotIn("actions/attest-sbom", workflow)
        self.assertNotIn("attestations: write", workflow)
        self.assertIn("./scripts/publish-image-attestations.sh", workflow)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            artifacts = base / "artifacts"
            binary = base / "bin"
            binary.mkdir()
            calls = base / "cosign-calls"
            fake_cosign = binary / "cosign"
            fake_cosign.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >> {calls}\n",
                encoding="utf-8",
            )
            fake_cosign.chmod(0o755)
            for role in ("runtime", "proxy", "maintenance"):
                directory = artifacts / role
                directory.mkdir(parents=True)
                (directory / "provenance.json").write_text(
                    json.dumps({"predicate": {"role": role}}), encoding="utf-8"
                )
                (directory / "image.spdx.json").write_text(
                    json.dumps({"spdxVersion": "SPDX-2.3"}), encoding="utf-8"
                )
            digests = ["sha256:" + value * 64 for value in ("a", "b", "c")]
            environment = os.environ.copy()
            environment.update(
                {
                    "M7_ATTESTATION_ROOT": str(artifacts),
                    "M7_COSIGN": str(fake_cosign),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/publish-image-attestations.sh"),
                    "ghcr.io/example/openclaw",
                    *digests,
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            invocations = calls.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(invocations), 6)
            for index, role in enumerate(("runtime", "proxy", "maintenance")):
                provenance = invocations[index * 2]
                sbom = invocations[index * 2 + 1]
                self.assertIn("attest --yes --type slsaprovenance1", provenance)
                self.assertIn(f"{artifacts}/{role}/provenance.predicate.json", provenance)
                self.assertTrue(provenance.endswith("ghcr.io/example/openclaw@" + digests[index]))
                self.assertIn("attest --yes --type spdxjson", sbom)
                self.assertTrue(sbom.endswith("ghcr.io/example/openclaw@" + digests[index]))

    def test_mutable_image_reference_is_rejected(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.split_reference("python:3.11-slim-bookworm")

    def test_malformed_digest_is_rejected(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.split_reference("python:3.11@sha256:1234")

    def test_vulnerability_policy_has_no_exception(self) -> None:
        lock = self.module.load_lock()
        self.assertEqual(
            lock["vulnerability_policy"],
            {
                "fail_severities": ["CRITICAL"],
                "ignore_unfixed": False,
                "exceptions": [],
            },
        )

    def test_source_secret_scan_has_a_writable_bounded_scratch_space(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base / "bin"
            binary.mkdir()
            calls = base / "docker-calls"
            (binary / "docker").write_text(
                f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> {calls}\nexit 0\n',
                encoding="utf-8",
            )
            fake_python = binary / "python"
            fake_python.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                "  *m7_supply_chain.py) printf '%s\\n' 'trivy:test' ;;\n"
                "esac\n"
                "exit 0\n",
                encoding="utf-8",
            )
            for path in binary.iterdir():
                path.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(binary) + os.pathsep + environment["PATH"],
                    "M7_PYTHON": str(fake_python),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/check-source-secrets.sh")],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            invocation = calls.read_text(encoding="utf-8")
            self.assertIn("--read-only", invocation)
            self.assertIn("--network none", invocation)
            self.assertIn(
                "--tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
                invocation,
            )

    def test_clean_vulnerability_report_is_accepted(self) -> None:
        report = self.module.verify_vulnerability_payload(
            {"Results": [{"Vulnerabilities": []}]},
            {"fail_severities": ["CRITICAL"]},
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["rejected"], 0)

    def test_unapproved_critical_vulnerability_is_rejected(self) -> None:
        payload = {
            "Results": [
                {
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2099-0001",
                            "PkgName": "fixture",
                            "Severity": "CRITICAL",
                        }
                    ]
                }
            ]
        }
        with self.assertRaisesRegex(self.module.ContractError, "CVE-2099-0001:fixture"):
            self.module.verify_vulnerability_payload(
                payload, {"fail_severities": ["CRITICAL"]}
            )

    def test_workflow_permission_parser_rejects_ambient_permissions(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.workflow_job_permissions(
                "name: unsafe\npermissions: write-all\njobs:\n  test:\n    runs-on: ubuntu\n"
            )

    def test_valid_spdx_sbom_is_accepted(self) -> None:
        self.module.verify_sbom_payload(
            {
                "spdxVersion": "SPDX-2.3",
                "creationInfo": {"created": "2026-08-06T00:00:00Z"},
                "packages": [{"name": "fixture"}],
            }
        )

    def test_empty_spdx_sbom_is_rejected(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.verify_sbom_payload(
                {
                    "spdxVersion": "SPDX-2.3",
                    "creationInfo": {"created": "2026-08-06T00:00:00Z"},
                    "packages": [],
                }
            )

    def provenance(self, digest: str = "a" * 64) -> dict[str, object]:
        return {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "fixture", "digest": {"sha256": digest}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "externalParameters": {
                        "role": "runtime",
                        "revision": "b" * 40,
                        "release": "3.4.0-r27.2.5",
                        "sbom_sha256": "c" * 64,
                    },
                    "resolvedDependencies": [{}, {}, {}],
                }
            },
        }

    def test_matching_provenance_is_accepted(self) -> None:
        self.module.verify_provenance_payload(
            self.provenance(),
            image_id="sha256:" + "a" * 64,
            role="runtime",
            revision="b" * 40,
            version="3.4.0-r27.2.5",
            sbom_sha256="c" * 64,
        )

    def test_provenance_for_other_digest_is_rejected(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.verify_provenance_payload(
                self.provenance("d" * 64),
                image_id="sha256:" + "a" * 64,
                role="runtime",
                revision="b" * 40,
                version="3.4.0-r27.2.5",
                sbom_sha256="c" * 64,
            )

    def test_provenance_for_other_revision_is_rejected(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.verify_provenance_payload(
                self.provenance(),
                image_id="sha256:" + "a" * 64,
                role="runtime",
                revision="e" * 40,
                version="3.4.0-r27.2.5",
                sbom_sha256="c" * 64,
            )

    def fake_tools(self, folder: Path) -> Path:
        binary = folder / "bin"
        binary.mkdir()
        (binary / "cosign").write_text(
            '#!/bin/sh\n[ "${FAKE_COSIGN_FAIL:-0}" = 0 ] || exit 1\nexit 0\n',
            encoding="utf-8",
        )
        (binary / "docker").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = pull ]; then exit 0; fi\n'
            "if [ \"$1 $2\" = 'image inspect' ]; then\n"
            '  case "$4" in\n'
            "    *revision*) printf '%s\\n' \"${FAKE_REVISION}\";;\n"
            "    *version*) printf '%s\\n' \"${FAKE_RELEASE}\";;\n"
            "    *openclaw.role*) printf '%s\\n' \"${FAKE_ROLE}\";;\n"
            "  esac\n"
            "  exit 0\n"
            "fi\n"
            "exit 2\n",
            encoding="utf-8",
        )
        for path in binary.iterdir():
            path.chmod(0o755)
        return binary

    def run_deploy_verifier(self, *, cosign_fail: bool = False, revision: str | None = None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        binary = self.fake_tools(Path(temporary.name))
        expected = "b" * 40
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": str(binary) + os.pathsep + environment["PATH"],
                "FAKE_COSIGN_FAIL": "1" if cosign_fail else "0",
                "FAKE_REVISION": revision or expected,
                "FAKE_RELEASE": "3.4.0-r27.2.5",
                "FAKE_ROLE": "runtime",
            }
        )
        image = "registry.example/openclaw@sha256:" + "a" * 64
        return subprocess.run(
            ["bash", str(DEPLOY_VERIFY), image, expected, "runtime"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_signed_matching_image_passes_deployment_gate(self) -> None:
        result = self.run_deploy_verifier()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsigned_image_fails_deployment_gate(self) -> None:
        result = self.run_deploy_verifier(cosign_fail=True)
        self.assertNotEqual(result.returncode, 0)

    def test_revision_mismatch_fails_deployment_gate(self) -> None:
        result = self.run_deploy_verifier(revision="d" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Revision stimmt nicht", result.stderr)

    def test_mutable_tag_fails_before_signature_check(self) -> None:
        result = subprocess.run(
            ["bash", str(DEPLOY_VERIFY), "registry.example/openclaw:latest", "b" * 40, "runtime"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256-Digest", result.stderr)

    def test_unsigned_release_is_rejected_before_any_stack_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base / "bin"
            binary.mkdir()
            calls = base / "docker-calls"
            (binary / "docker").write_text(
                f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> {calls}\n[ "$1" = info ] && exit 0\nexit 0\n',
                encoding="utf-8",
            )
            (binary / "cosign").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            for path in binary.iterdir():
                path.chmod(0o755)
            environment_file = base / "deployment.env"
            environment_file.write_text(
                "OPENCLAW_IMAGE=registry.example/old@sha256:" + "f" * 64 + "\n"
                "OPENCLAW_WRITE_TEST_ENABLED=false\n"
                "OPENCLAW_EXPECTED_SOURCE_REVISION=" + "b" * 40 + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(binary) + os.pathsep + environment["PATH"],
                    "OPENCLAW_DEPLOY_ENV": str(environment_file),
                    "OPENCLAW_COMPOSE_FILE": str(ROOT / "compose.yaml"),
                }
            )
            digest = "registry.example/openclaw@sha256:" + "a" * 64
            result = subprocess.run(
                ["bash", str(DEPLOY), digest, digest, digest],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            observed_calls = calls.read_text(encoding="utf-8")
            self.assertIn("info", observed_calls)
            self.assertNotIn("compose stop", observed_calls)

    def test_compose_assigns_minimal_role_images(self) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "docker/deployment.env.example",
                "-f",
                "compose.yaml",
                "--profile",
                "maintenance",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertTrue(services["ollama-proxy"]["image"].endswith("-proxy"))
        self.assertTrue(services["clamav-update"]["image"].endswith("-maintenance"))
        self.assertEqual(services["gateway"]["image"], services["mail-worker"]["image"])

    def test_image_label_contract_rejects_wrong_role(self) -> None:
        labels = {
            "org.opencontainers.image.version": "3.4.0-r27.2.5",
            "org.opencontainers.image.revision": "b" * 40,
            "org.opencontainers.image.openclaw.role": "proxy",
            "org.opencontainers.image.created": "2026-08-06T00:00:00Z",
            "org.opencontainers.image.source": "https://example.invalid/repo",
            "org.opencontainers.image.base.name": "python:3.11",
            "org.opencontainers.image.base.digest": "sha256:" + "a" * 64,
        }
        with self.assertRaises(self.module.ContractError):
            self.module.verify_labels(labels, role="runtime", revision="b" * 40, version="3.4.0-r27.2.5")


if __name__ == "__main__":
    unittest.main()
