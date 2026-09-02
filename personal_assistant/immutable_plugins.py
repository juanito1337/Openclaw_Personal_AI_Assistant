"""Immutable external OpenClaw plugin contract and generated-index migration."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
TOOL_LOOP_DETECTION_POLICY: dict[str, object] = {
    "enabled": True,
    "historySize": 30,
    "warningThreshold": 2,
    "unknownToolThreshold": 2,
    "criticalThreshold": 3,
    "globalCircuitBreakerThreshold": 4,
}
TOOL_LOOP_DETECTORS = {
    "genericRepeat": True,
    "knownPollNoProgress": True,
    "pingPong": True,
}


def ensure_tool_loop_detection_config(tools: dict[str, object]) -> bool:
    """Enforce the bounded OpenClaw tool-loop circuit breaker without dropping extensions."""

    current = tools.get("loopDetection")
    if current is None:
        current = {}
        tools["loopDetection"] = current
    if not isinstance(current, dict):
        raise ValueError("openclaw.json tools.loopDetection muss ein JSON-Objekt sein")
    detectors = current.get("detectors")
    if detectors is None:
        detectors = {}
        current["detectors"] = detectors
    if not isinstance(detectors, dict):
        raise ValueError("openclaw.json tools.loopDetection.detectors muss ein JSON-Objekt sein")

    before = json.dumps(current, ensure_ascii=False, sort_keys=True)
    current.update(TOOL_LOOP_DETECTION_POLICY)
    detectors.update(TOOL_LOOP_DETECTORS)
    return before != json.dumps(current, ensure_ascii=False, sort_keys=True)


def load_contract(path: Path) -> dict[str, dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"Unbekannter immutable Pluginvertrag: {path}")
    raw_plugins = payload.get("plugins")
    if not isinstance(raw_plugins, dict) or not raw_plugins:
        raise RuntimeError(f"Immutable Pluginvertrag enthaelt keine Plugins: {path}")
    plugins: dict[str, dict[str, str]] = {}
    required = {"package", "version", "integrity", "shasum", "path"}
    for plugin_id, raw_contract in raw_plugins.items():
        if not isinstance(plugin_id, str) or not isinstance(raw_contract, dict):
            raise RuntimeError("Ungueltiger Plugin-Datensatz im immutable Vertrag")
        contract = {key: str(raw_contract.get(key) or "") for key in required}
        if any(not value for value in contract.values()):
            raise RuntimeError(f"Immutable Pluginvertrag fuer {plugin_id!r} ist unvollstaendig")
        if not contract["integrity"].startswith("sha512-"):
            raise RuntimeError(f"Immutable Pluginintegritaet fuer {plugin_id!r} ist ungueltig")
        if not SHA1_RE.fullmatch(contract["shasum"]):
            raise RuntimeError(f"Immutable Pluginpruefsumme fuer {plugin_id!r} ist ungueltig")
        if not contract["path"].startswith("/opt/openclaw-plugins/"):
            raise RuntimeError(f"Immutable Pluginpfad fuer {plugin_id!r} liegt nicht im Image")
        plugins[plugin_id] = contract
    return plugins


def synchronized_install_record(
    plugin_id: str,
    record: object,
    contract: dict[str, str],
) -> tuple[dict[str, object], bool]:
    if not isinstance(record, dict):
        raise RuntimeError(f"Plugin-Installationsdatensatz {plugin_id!r} ist kein JSON-Objekt")
    package = str(record.get("resolvedName") or "")
    if package != contract["package"]:
        raise RuntimeError(f"Managed Plugin {plugin_id!r} hat ein unerwartetes npm-Paket")
    updated = dict(record)
    exact_spec = f"{contract['package']}@{contract['version']}"
    expected: dict[str, object] = {
        "source": "npm",
        "spec": exact_spec,
        "installPath": contract["path"],
        "version": contract["version"],
        "resolvedName": contract["package"],
        "resolvedVersion": contract["version"],
        "resolvedSpec": exact_spec,
        "integrity": contract["integrity"],
        "shasum": contract["shasum"],
    }
    updated.update(expected)
    return updated, updated != record


def synchronize_installed_plugin_index(
    database: Path,
    contracts: dict[str, dict[str, str]],
) -> dict[str, object]:
    report: dict[str, object] = {
        "database_present": database.is_file(),
        "table_present": False,
        "registry_rows_changed": 0,
        "managed_records_checked": 0,
        "managed_records_changed": 0,
    }
    if not database.is_file():
        return report

    connection = sqlite3.connect(database, timeout=30)
    try:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("OpenClaw-State-Datenbank ist vor der Plugin-Migration defekt")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'installed_plugin_index'"
        ).fetchone()
        if table is None:
            return report
        report["table_present"] = True
        columns = {str(row[1]) for row in connection.execute('PRAGMA table_info("installed_plugin_index")')}
        if "install_records_json" not in columns:
            raise RuntimeError(
                "installed_plugin_index hat ein unbekanntes Schema; install_records_json fehlt"
            )

        updates: list[tuple[str, int, str]] = []
        records_checked = 0
        records_changed = 0
        rows = connection.execute(
            'SELECT rowid, "install_records_json" FROM "installed_plugin_index"'
        ).fetchall()
        for rowid, raw_json in rows:
            try:
                records = json.loads(raw_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "installed_plugin_index.install_records_json enthaelt ungueltiges JSON"
                ) from exc
            if not isinstance(records, dict):
                raise RuntimeError("installed_plugin_index.install_records_json muss ein JSON-Objekt sein")
            synchronized: dict[str, object] = {}
            row_changed = False
            for plugin_id, record in records.items():
                contract = contracts.get(str(plugin_id))
                if contract is None:
                    raise RuntimeError(
                        f"Managed Plugin {plugin_id!r} ist nicht im immutable Imagevertrag enthalten"
                    )
                updated, changed = synchronized_install_record(
                    str(plugin_id),
                    record,
                    contract,
                )
                synchronized[str(plugin_id)] = updated
                row_changed = row_changed or changed
                records_checked += 1
                records_changed += int(changed)
            if row_changed:
                updates.append(
                    (
                        json.dumps(synchronized, ensure_ascii=False, separators=(",", ":")),
                        int(rowid),
                        str(raw_json),
                    )
                )

        report["registry_rows_changed"] = len(updates)
        report["managed_records_checked"] = records_checked
        report["managed_records_changed"] = records_changed
        if not updates:
            return report

        connection.execute("BEGIN IMMEDIATE")
        for raw_json, rowid, previous_json in updates:
            cursor = connection.execute(
                'UPDATE "installed_plugin_index" SET "install_records_json" = ? '
                'WHERE rowid = ? AND "install_records_json" = ?',
                (raw_json, rowid, previous_json),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("OpenClaw-Pluginindex wurde waehrend der Migration geaendert")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("OpenClaw-State-Datenbank ist nach der Plugin-Migration defekt")
        connection.commit()
        return report
    except sqlite3.Error as exc:
        connection.rollback()
        raise RuntimeError("OpenClaw-Pluginindex konnte nicht sicher migriert werden") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
