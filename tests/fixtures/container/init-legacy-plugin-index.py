#!/usr/bin/env python3
"""Create a public legacy plugin-index fixture inside an isolated Docker volume."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

database = Path("/home/node/.openclaw/state/openclaw.sqlite")
database.parent.mkdir(parents=True, exist_ok=True)
connection = sqlite3.connect(database)
connection.execute(
    "CREATE TABLE installed_plugin_index ("
    "index_key TEXT NOT NULL PRIMARY KEY, version INTEGER NOT NULL, "
    "host_contract_version TEXT NOT NULL, compat_registry_version TEXT NOT NULL, "
    "migration_version INTEGER NOT NULL, policy_hash TEXT NOT NULL, "
    "generated_at_ms INTEGER NOT NULL, refresh_reason TEXT, "
    "install_records_json TEXT NOT NULL, plugins_json TEXT NOT NULL, "
    "diagnostics_json TEXT NOT NULL, warning TEXT, updated_at_ms INTEGER NOT NULL)"
)
records = {
    "brave": {
        "source": "npm",
        "spec": "@openclaw/brave-plugin@2026.6.11",
        "installPath": "/home/jan/.openclaw/npm/projects/brave/node_modules/@openclaw/brave-plugin",
        "version": "2026.6.11",
        "resolvedName": "@openclaw/brave-plugin",
        "resolvedVersion": "2026.6.11",
    },
    "signal": {
        "source": "npm",
        "spec": "@openclaw/signal@2026.6.11",
        "installPath": "/home/jan/.openclaw/npm/projects/signal/node_modules/@openclaw/signal",
        "version": "2026.6.11",
        "resolvedName": "@openclaw/signal",
        "resolvedVersion": "2026.6.11",
    },
}
connection.execute(
    "INSERT INTO installed_plugin_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
        "installed-plugin-index",
        1,
        "legacy-fixture",
        "legacy-fixture",
        1,
        "legacy-fixture",
        0,
        "legacy-fixture",
        json.dumps(records),
        "[]",
        "[]",
        None,
        0,
    ),
)
connection.commit()
connection.close()
