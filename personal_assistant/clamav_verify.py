"""Bounded child entrypoint for native CVD/CLD signature authentication.

libclamav cl_cvdverify validates CVD archive signatures and CLD signed .info
manifests plus the contained file hashes. It does not rely on FreshClam's
already-up-to-date exit status. The parent owns the execution deadline.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from .clamav_vendor import GROUPS, VendorError, database_snapshot, disk_matches


def verify(database: Path) -> bool:
    snapshot = database_snapshot(database)
    library = ctypes.CDLL("libclamav.so.12")
    library.cl_init.argtypes = [ctypes.c_uint]
    library.cl_init.restype = ctypes.c_int
    library.cl_cvdverify.argtypes = [ctypes.c_char_p]
    library.cl_cvdverify.restype = ctypes.c_int
    if library.cl_init(0) != 0:
        return False
    for group in GROUPS:
        path = database / snapshot["files"][group]["name"]
        if library.cl_cvdverify(os.fsencode(path)) != 0:
            return False
    return disk_matches(database, snapshot)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    try:
        return 0 if verify(Path(sys.argv[1])) else 1
    except (OSError, VendorError, AttributeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
