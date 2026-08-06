#!/usr/bin/env python3
from __future__ import annotations

import sys
import urllib.request


def main() -> int:
    expect_unreachable = "--expect-unreachable" in sys.argv
    try:
        urllib.request.urlopen("http://fake-services:8080/health", timeout=2).read()
    except Exception as exc:  # fixture deliberately injects every connection failure
        if expect_unreachable:
            print(f"network loss observed: {type(exc).__name__}")
            return 0
        raise
    if expect_unreachable:
        print("fake service unexpectedly reachable", file=sys.stderr)
        return 1
    print("network restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
