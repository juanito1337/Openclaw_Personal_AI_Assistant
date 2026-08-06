#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

LEGACY_LAYOUT = 1


def state_layout(state_dir: Path) -> int:
    marker = state_dir / ".container-layout.json"
    if not marker.exists():
        return LEGACY_LAYOUT
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"State-Layoutmarker ist unlesbar: {marker}: {exc}") from exc
    layout = payload.get("layout") if isinstance(payload, dict) else None
    if not isinstance(layout, int) or layout < LEGACY_LAYOUT:
        raise ValueError(f"State-Layoutmarker enthaelt keine gueltige Layoutversion: {marker}")
    return layout


def compatible(layout: int, minimum: int, maximum: int) -> bool:
    return minimum <= layout <= maximum


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prueft die State-Layoutkompatibilitaet eines Images")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--target-min", type=int, default=LEGACY_LAYOUT)
    parser.add_argument("--target-max", type=int, default=LEGACY_LAYOUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        layout = state_layout(args.state_dir)
    except ValueError as exc:
        print(f"Downgrade-/Upgrade-Pruefung abgebrochen: {exc}")
        return 1
    if args.target_min < LEGACY_LAYOUT or args.target_max < args.target_min:
        print(
            f"Zielimage {args.target_image} meldet ungueltige Layoutgrenzen: "
            f"{args.target_min}..{args.target_max}"
        )
        return 1
    if not compatible(layout, args.target_min, args.target_max):
        print(
            f"Zielimage {args.target_image} akzeptiert State-Layout "
            f"{args.target_min}..{args.target_max}, vorhanden ist Layout {layout}. "
            "Deployment wird vor dem Stoppen des laufenden Stacks abgebrochen."
        )
        return 1
    print(
        f"State-Layout {layout} ist mit Zielimage {args.target_image} "
        f"({args.target_min}..{args.target_max}) kompatibel."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
