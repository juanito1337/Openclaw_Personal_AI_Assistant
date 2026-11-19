#!/usr/bin/env python3
"""Bound expiry of an already successful pinned-Cosign TUF initialization.

This parses Cosign's trusted-cache status, not a repository or caller trust root.
The caller must first require successful initialization from the embedded root.
RFC822 output omits seconds; using the start of that minute expires conservatively.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from datetime import UTC, datetime

MAX_STATUS_BYTES = 64 * 1024
REQUIRED_ROLES = {"root.json", "timestamp.json", "snapshot.json", "targets.json"}
MONTHS = dict(zip(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    range(1, 13), strict=True,
))
ERROR = "sigstore-tuf-deadline-invalid-or-expired"


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def invalid_constant(_value: str) -> None:
    raise ValueError


def expiry_epoch(value: object) -> int:
    # Only UTC is emitted by the pinned production repository. Reject ambiguous
    # abbreviations and offsets instead of interpreting them in the host zone.
    if not isinstance(value, str):
        raise ValueError
    match = re.fullmatch(r"([0-9]{2}) ([A-Z][a-z]{2}) ([0-9]{2}) ([0-9]{2}):([0-9]{2}) UTC", value)
    if match is None:
        raise ValueError
    day, month, short_year, hour, minute = match.groups()
    year = int(short_year)
    year += 1900 if year >= 69 else 2000  # The pinned Go RFC822 two-digit year rule.
    return int(datetime(year, MONTHS[month], int(day), int(hour), int(minute), tzinfo=UTC).timestamp())


def status_deadline(path: str) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATUS_BYTES:
            raise ValueError
        raw = handle.read(MAX_STATUS_BYTES + 1)
    if len(raw) > MAX_STATUS_BYTES or not raw.startswith(b"Root status: \n"):
        raise ValueError
    report = json.loads(
        raw[len(b"Root status: \n"):], object_pairs_hook=unique_object, parse_constant=invalid_constant,
    )
    if not isinstance(report, dict):
        raise ValueError
    metadata = report.get("metadata")
    if not isinstance(metadata, dict) or not metadata.keys() >= REQUIRED_ROLES:
        raise ValueError
    deadlines = []
    for role, entry in metadata.items():
        if not role or not isinstance(entry, dict) or entry.get("error", "") != "":
            raise ValueError
        if type(entry.get("version")) is not int or entry["version"] <= 0:
            raise ValueError
        if type(entry.get("len")) is not int or entry["len"] <= 0:
            raise ValueError
        deadlines.append(expiry_epoch(entry.get("expiration")))
    return min(deadlines)


def main(arguments: list[str]) -> int:
    try:
        if len(arguments) == 2 and arguments[0] == "--check":
            if re.fullmatch(r"[1-9][0-9]{0,11}", arguments[1]) is None:
                raise ValueError
            deadline = int(arguments[1])
            if deadline > 253402300799:  # End of the supported UTC datetime range.
                raise ValueError
        elif len(arguments) == 1 and not arguments[0].startswith("--"):
            deadline = status_deadline(arguments[0])
        else:
            raise ValueError
        if not time.time() < deadline:
            raise ValueError
        if len(arguments) == 1:
            print(deadline)
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        print(ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
