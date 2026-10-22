"""Verify browser-held Ed25519 authority independently of the gateway RPC relay.

The fixed paired-device registry is a read-only setup-role mount. It contains
credentials: only the necessary public key, role and scope fields are used.
Node's pinned standard crypto library performs signature verification; there is
no handwritten cryptography, shell expansion or new package dependency.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .nextcloud_connection import connection_directory
from .nextcloud_onboarding import OnboardingError

_VERIFY_JS = """
const {readFileSync} = require('node:fs');
const {createPublicKey,verify} = require('node:crypto');
try {
 const v=JSON.parse(readFileSync(0,'utf8'));
 const raw=Buffer.from(v.publicKey,'base64url');
 const sig=Buffer.from(v.signature,'base64url');
 if(raw.length!==32 || sig.length!==64) process.exit(1);
 const prefix=Buffer.from('302a300506032b6570032100','hex');
 const key=createPublicKey({key:Buffer.concat([prefix,raw]),format:'der',type:'spki'});
 process.exit(verify(null,Buffer.from(v.payload,'utf8'),key,sig)?0:1);
} catch { process.exit(1); }
"""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-json-key")
        result[key] = value
    return result


class OperatorProofVerifier:
    def __init__(
        self,
        path: Path | None = None,
        *,
        replay_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = path or Path(
            os.environ.get("OPENCLAW_SETUP_DEVICES_FILE", "/run/openclaw-gateway-devices/paired.json")
        )
        self.clock = clock
        directory = connection_directory()
        self.replay_path = replay_path or (directory / "operator-nonces.sqlite3" if directory else None)
        self.lock = threading.Lock()

    def verify(self, request: dict[str, Any]) -> None:
        try:
            self._verify(request)
        except (
            ValueError, KeyError, TypeError, AttributeError, OSError,
            subprocess.SubprocessError, sqlite3.Error,
        ):
            raise OnboardingError(
                "operator-proof-invalid", "OpenClaw-Geräteanmeldung bitte erneut pruefen"
            ) from None

    def _verify(self, request: dict[str, Any]) -> None:
        proof = request.get("proof")
        if not isinstance(proof, dict) or set(proof) != {"payload", "signature"}:
            raise ValueError
        payload, signature = proof["payload"], proof["signature"]
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > 65536:
            raise ValueError
        if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
            raise ValueError
        signed = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(signed, dict) or set(signed) != {
            "operation",
            "parameters",
            "actor",
            "issued_at",
            "nonce",
        }:
            raise ValueError
        for field in ("operation", "parameters", "actor"):
            if signed[field] != request.get(field):
                raise ValueError
        actor, issued, nonce = signed["actor"], signed["issued_at"], signed["nonce"]
        if not isinstance(actor, str) or not re.fullmatch(r"device:[a-f0-9]{64}", actor):
            raise ValueError
        now = self.clock()
        if type(issued) is not int or not -30_000 <= now * 1000 - issued <= 120_000:
            raise ValueError
        if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise ValueError
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError
        registry = json.loads(raw, object_pairs_hook=_unique_object)
        device_id = actor.removeprefix("device:")
        device = registry.get(device_id) if isinstance(registry, dict) else None
        if not isinstance(device, dict) or device.get("deviceId") != device_id:
            raise ValueError
        roles = device.get("roles", [])
        if not isinstance(roles, list) or (device.get("role") != "operator" and "operator" not in roles):
            raise ValueError
        scopes = device.get("approvedScopes", device.get("scopes", []))
        token = device.get("tokens", {}).get("operator", {})
        if (
            not isinstance(scopes, list)
            or "operator.admin" not in scopes
            or not isinstance(token, dict)
            or not isinstance(token.get("token"), str) or not token["token"]
            or token.get("role") != "operator"
            or not isinstance(token.get("scopes"), list)
            or "operator.admin" not in token["scopes"]
            or token.get("revokedAtMs") is not None
        ):
            raise ValueError
        key = device.get("publicKey")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
            raise ValueError
        if hashlib.sha256(base64.urlsafe_b64decode(key + "=")).hexdigest() != device_id:
            raise ValueError
        node = shutil.which("node", path="/usr/local/bin:/usr/bin:/bin")
        if not node:
            raise ValueError
        with self.lock:
            completed = subprocess.run(
                [node, "-e", _VERIFY_JS],
                input=json.dumps({"publicKey": key, "signature": signature, "payload": payload}),
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"},
            )
            if completed.returncode != 0:
                raise ValueError
            self._claim_nonce(device_id, nonce, now)

    def _claim_nonce(self, device_id: str, nonce: str, now: float) -> None:
        if self.replay_path is None:
            raise ValueError
        self.replay_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.replay_path.is_symlink():
            raise ValueError
        fd = os.open(self.replay_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.replay_path, timeout=5)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise ValueError
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS nonces (device TEXT, nonce TEXT, expires REAL, "
                "PRIMARY KEY(device,nonce))"
            )
            db.execute("PRAGMA user_version=1")
            with db:
                db.execute("DELETE FROM nonces WHERE expires <= ?", (now,))
                if db.execute("SELECT COUNT(*) FROM nonces").fetchone()[0] >= 4096:
                    raise ValueError
                db.execute("INSERT INTO nonces VALUES(?,?,?)", (device_id, nonce, now + 151))
        finally:
            db.close()
