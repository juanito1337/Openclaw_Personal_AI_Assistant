#!/usr/bin/env python3
"""Initialize the installer policy contract inside an isolated container fixture."""

import sys
from pathlib import Path

from personal_assistant.nextcloud_connection import ConnectionStore, initialize_access_store

state, directory = (Path(value) for value in sys.argv[1:3])
transition = sys.argv[3] if len(sys.argv) == 4 else "fresh"
assert transition in {"fresh", "verified-pre-policy"}
state.mkdir(mode=0o700, parents=True, exist_ok=True)
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
initialize_access_store(state, directory, transition=transition)
snapshot = ConnectionStore(directory, require_mode=True).snapshot()
assert snapshot.mode == ("unconfigured" if transition == "fresh" else "legacy")
assert snapshot.connection is None
