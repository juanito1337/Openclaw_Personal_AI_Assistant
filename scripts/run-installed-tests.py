#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import mail_agent
import personal_assistant


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for package in (mail_agent, personal_assistant):
        package_path = Path(package.__file__).resolve()
        if root == package_path or root in package_path.parents or "site-packages" not in package_path.parts:
            print(
                f"Wheel-Test importiert {package.__name__} nicht aus site-packages: {package_path}",
                file=sys.stderr,
            )
            return 4
    os.chdir(root)
    return int(pytest.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
