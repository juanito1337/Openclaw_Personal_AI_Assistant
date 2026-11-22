"""Private boundary between the immutable native adapter and one CLI invocation."""

from __future__ import annotations

import json
import sys

from .nextcloud_connection import ConnectionError, ConnectionStore, connection_directory
from .nextcloud_native_envelope import EXPECTED_ACCESS as EXPECTED_ACCESS
from .nextcloud_native_envelope import MAX_ENVELOPE_BYTES, native_access_projection
from .nextcloud_native_envelope import NATIVE_CALL as NATIVE_CALL
from .nextcloud_native_envelope import verify_native_access as verify_native_access


def main() -> int:
    try:
        directory = connection_directory()
        if directory is None:
            raise ConnectionError("policy-integrity-error")
        snapshot = ConnectionStore(directory, require_mode=True).snapshot()
        wire = json.dumps(native_access_projection(snapshot), ensure_ascii=False, separators=(",", ":"))
        if len(wire.encode()) > MAX_ENVELOPE_BYTES:
            raise ConnectionError("policy-integrity-error")
        print(wire)
        return 0
    except (ConnectionError, OSError, ValueError):
        print("policy-integrity-error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
