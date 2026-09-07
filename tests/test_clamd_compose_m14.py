from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rendered_compose() -> dict[str, object]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "docker/deployment.env.example",
            "--profile",
            "tools",
            "--profile",
            "maintenance",
            "-f",
            "compose.yaml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def mounts(service: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(item["target"]): item
        for item in service.get("volumes", [])  # type: ignore[union-attr]
    }


def test_clamd_is_isolated_and_signature_writer_is_separate() -> None:
    services = rendered_compose()["services"]
    clamd = services["clamd"]
    updater = services["clamav-update"]
    assert clamd["user"] == "100:101"
    assert clamd["network_mode"] == "none"
    assert clamd["read_only"] is True
    assert clamd["cap_drop"] == ["ALL"]
    assert clamd["security_opt"] == ["no-new-privileges:true"]
    assert not clamd.get("ports")
    assert not clamd.get("secrets")
    assert mounts(clamd)["/var/lib/clamav"]["read_only"] is True
    assert mounts(updater)["/var/lib/clamav"].get("read_only", False) is False
    assert "/run/clamav" not in mounts(updater)


def test_only_scan_roles_receive_read_only_socket_and_group() -> None:
    services = rendered_compose()["services"]
    allowed = {"gateway", "mail-worker", "agent-cli"}
    for name, service in services.items():
        service_mounts = mounts(service)
        if name in allowed:
            assert service_mounts["/run/clamav"]["read_only"] is True
            assert service["group_add"] == ["101"]
        elif name != "clamd" and name != "clamav-socket-init":
            assert "/run/clamav" not in service_mounts


def test_socket_initializer_has_only_narrow_root_exception() -> None:
    service = rendered_compose()["services"]["clamav-socket-init"]
    assert service["user"] == "0:0"
    assert service["network_mode"] == "none"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert sorted(service["cap_add"]) == ["CHOWN", "DAC_OVERRIDE"]
    assert mounts(service)["/run/clamav"].get("read_only", False) is False
    assert list(mounts(service)) == ["/run/clamav"]


def test_index_runtime_receives_daemon_readiness_configuration() -> None:
    services = rendered_compose()["services"]
    for name in ("gateway", "mail-worker", "agent-cli"):
        environment = services[name]["environment"]
        assert environment["OPENCLAW_CLAMD_SOCKET"] == "/run/clamav/clamd.sock"
        assert environment["CLAMAV_SIGNATURE_MAX_AGE_SECONDS"] == "172800"


def test_compose_matches_machine_readable_operating_budget() -> None:
    budget = json.loads(
        (ROOT / "docs/architecture/clamd-operating-budget-m14.json").read_text(
            encoding="utf-8"
        )
    )
    clamd = rendered_compose()["services"]["clamd"]
    container = budget["container"]
    scanner = budget["scanner"]
    assert clamd["network_mode"] == container["network_mode"]
    assert clamd["user"] == container["user"]
    assert clamd["read_only"] == container["read_only_root"]
    assert clamd["cap_drop"] == container["cap_drop"]
    assert clamd["cpus"] == container["cpus"]
    assert int(clamd["mem_limit"]) == container["memory_bytes"]
    assert int(clamd["pids_limit"]) == container["pids"]
    environment = clamd["environment"]
    assert int(environment["CLAMAV_STREAM_MAX_BYTES"]) == scanner["max_stream_bytes"]
    assert int(environment["CLAMAV_MAX_THREADS"]) == scanner["max_threads"]
    assert int(environment["CLAMAV_MAX_QUEUE"]) == scanner["max_queue"]
    assert int(environment["CLAMAV_SCAN_TIMEOUT_SECONDS"]) == scanner["scan_timeout_seconds"]
    assert int(environment["CLAMAV_SELF_CHECK_SECONDS"]) == scanner["self_check_seconds"]
    assert int(environment["CLAMAV_SIGNATURE_MAX_AGE_SECONDS"]) == scanner[
        "signature_max_age_seconds"
    ]
