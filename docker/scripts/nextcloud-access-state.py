#!/usr/bin/env python3
"""Offline verifier/initializer shared by release backup, restore and deployment."""

import sys
from pathlib import Path

scripts = Path(__file__).resolve().parent
# The deployed bundle has only the release's three stdlib-only policy modules.
package_root = scripts if (scripts / "personal_assistant").is_dir() else scripts.parents[1]
sys.path.insert(0, str(package_root))
from personal_assistant.nextcloud_connection import ConnectionError, initialize_access_store  # noqa: E402

if __name__ == "__main__":
    try:
        transition, state, directory = sys.argv[1:]
        initialize_access_store(Path(state), Path(directory), transition=transition)
    except (OSError, ValueError, ConnectionError):
        sys.exit("policy-integrity-error")
