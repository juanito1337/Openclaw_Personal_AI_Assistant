#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "SOURCE_MANIFEST.sha256"
IMPORTS = ("personal_assistant.cli", "mail_agent.cli")


def source_paths() -> list[Path]:
    result: list[Path] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        _digest, separator, raw_path = line.partition("  ./")
        if separator:
            result.append(ROOT / raw_path)
    return result


def source_metrics() -> dict[str, int]:
    paths = source_paths()
    python = [path for path in paths if path.suffix == ".py"]
    shell = [path for path in paths if path.suffix == ".sh"]
    return {
        "manifest_files": len(paths),
        "manifest_bytes": sum(path.stat().st_size for path in paths),
        "python_files": len(python),
        "python_bytes": sum(path.stat().st_size for path in python),
        "shell_files": len(shell),
        "shell_bytes": sum(path.stat().st_size for path in shell),
    }


def cold_import_milliseconds(module: str, samples: int) -> dict[str, float | int]:
    program = (
        "import importlib,sys,time; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "start=time.perf_counter(); "
        f"importlib.import_module({module!r}); "
        "print((time.perf_counter()-start)*1000)"
    )
    values: list[float] = []
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for _index in range(samples):
        result = subprocess.run(
            [sys.executable, "-I", "-c", program],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        values.append(float(result.stdout.strip()))
    return {
        "samples": samples,
        "median_ms": round(statistics.median(values), 3),
        "minimum_ms": round(min(values), 3),
    }


def image_size(image: str) -> int | None:
    if not image:
        return None
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Size}}", image],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".m6-benchmark.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduzierbare M6-Bereinigungsmetriken")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--image", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("--samples muss mindestens 3 sein")
    started = time.perf_counter()
    payload: dict[str, object] = {
        "schema_version": 1,
        "python": sys.version.split()[0],
        "source": source_metrics(),
        "cold_imports": {
            module: cold_import_milliseconds(module, args.samples)
            for module in IMPORTS
        },
        "image": args.image or None,
        "image_size_bytes": image_size(args.image),
    }
    payload["measurement_seconds"] = round(time.perf_counter() - started, 3)
    if args.output:
        atomic_write(args.output.resolve(), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
