#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path


def run(command: list[str]) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout.strip()


def image_size(image: str) -> int:
    return int(run(["docker", "image", "inspect", image, "--format", "{{.Size}}"]))


def sample(image: str, module: str, count: int) -> dict[str, float | int]:
    durations: list[float] = []
    rss_values: list[int] = []
    code = (
        "import importlib,resource;"
        f"importlib.import_module({module!r});"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    )
    for _ in range(count):
        started = time.monotonic_ns()
        output = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "python3",
                image,
                "-P",
                "-c",
                code,
            ]
        )
        durations.append((time.monotonic_ns() - started) / 1_000_000)
        rss_values.append(int(output.splitlines()[-1]))
    return {
        "cold_start_ms_median": round(statistics.median(durations), 3),
        "cold_start_ms_min": round(min(durations), 3),
        "import_peak_rss_kib_median": int(statistics.median(rss_values)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--maintenance", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    roles = {
        "runtime": (args.runtime, "personal_assistant.cli"),
        "proxy": (args.proxy, "personal_assistant.ollama_priority_proxy"),
        "maintenance": (args.maintenance, "personal_assistant.clamav_health"),
    }
    role_metrics: dict[str, object] = {}
    payload: dict[str, object] = {
        "schema_version": 1,
        "samples": args.samples,
        "roles": role_metrics,
    }
    for role, (image, module) in roles.items():
        role_metrics[role] = {
            "image": image,
            "image_bytes": image_size(image),
            **sample(image, module, args.samples),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
