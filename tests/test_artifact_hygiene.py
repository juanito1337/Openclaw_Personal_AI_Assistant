from __future__ import annotations

from pathlib import Path

from scripts.check_artifact import inspect_files, inspect_image_root


def test_artifact_hygiene_accepts_source_and_example_configuration() -> None:
    issues = inspect_files(
        [
            ("personal_assistant/policy.py", b"SAFE = True\n"),
            ("docker/deployment.env.example", b"PASSWORD=\n"),
        ]
    )

    assert issues == []


def test_artifact_hygiene_rejects_runtime_database_and_configuration() -> None:
    issues = inspect_files(
        [
            ("personal_assistant/data/state.sqlite3", b"SQLite format 3"),
            ("mail_agent/config.toml", b"password = 'secret'"),
        ]
    )

    assert any("Laufzeitdaten" in issue for issue in issues)
    assert any("produktive Konfiguration" in issue for issue in issues)


def test_artifact_hygiene_rejects_private_key_material() -> None:
    issues = inspect_files(
        [("notes.txt", b"-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n")]
    )

    assert any("private key" in issue for issue in issues)


def test_artifact_hygiene_rejects_config_variants_runtime_trees_and_secret_files() -> None:
    issues = inspect_files(
        [
            (".env.production", b"DATABASE_PASSWORD=correct-horse-battery-staple\n"),
            ("personal_assistant/data/session.json", b"{}\n"),
            ("secrets.toml", b"api_key = 'not-a-real-secret-value'\n"),
            ("client.pem", b"private material selected by its file type\n"),
            (".venv/bin/python", b"local interpreter\n"),
        ]
    )

    assert len(issues) >= 5
    assert any(".env.production: produktive Konfiguration" in issue for issue in issues)
    assert any("personal_assistant/data/session.json: Laufzeitdaten" in issue for issue in issues)
    assert any("secrets.toml: private Schluessel-/Zugangsdaten" in issue for issue in issues)
    assert any("client.pem: private Schluessel-/Zugangsdaten" in issue for issue in issues)
    assert any(".venv/bin/python: Laufzeitverzeichnis" in issue for issue in issues)


def test_image_root_checks_product_paths_outside_application_directory(tmp_path: Path) -> None:
    application = tmp_path / "opt/openclaw-agent"
    application.mkdir(parents=True)
    (application / "README.md").write_text("safe\n", encoding="utf-8")
    configuration = tmp_path / "etc/openclaw-agent/worker.env"
    configuration.parent.mkdir(parents=True)
    configuration.write_text("ACCESS_TOKEN=correct-horse-battery-staple\n", encoding="utf-8")
    role_secret = tmp_path / "run/openclaw-env/gateway.env"
    role_secret.parent.mkdir(parents=True)
    role_secret.write_text("OPENCLAW_GATEWAY_TOKEN=fixture-token-value\n", encoding="utf-8")

    issues = inspect_image_root(tmp_path)

    assert any("worker.env: moegliches Secret" in issue for issue in issues)
    assert any("gateway.env: produktive Konfiguration" in issue for issue in issues)
