#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold-seconds", type=float, default=0)
    args = parser.parse_args()
    path = Path("/lease/mail-writer.lock")
    with path.open("a+", encoding="utf-8") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("REJECTED second mail writer", flush=True)
            return 73
        print("ACQUIRED sole mail writer", flush=True)
        time.sleep(max(0, args.hold_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
