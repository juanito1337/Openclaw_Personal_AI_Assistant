"""Opt-in runtime-image smoke with synthetic pairing and no network/host ports.

OPENCLAW_TEST_SETUP_IMAGE=<unique local runtime tag> python3 -m unittest discover \
  -s tests -p test_nextcloud_setup_image.py
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

IMAGE = os.environ.get("OPENCLAW_TEST_SETUP_IMAGE", "")
LIMIT = 128 * 1024**2

SIGN = r"""
const c=require('node:crypto');
const v=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const key=c.createPrivateKey({key:Buffer.concat([Buffer.from('302e020100300506032b657004220420','hex'),
 Buffer.alloc(32,19)]),format:'der',type:'pkcs8'});
const raw=c.createPublicKey(key).export({format:'der',type:'spki'}).subarray(-32);
console.log(JSON.stringify({id:c.createHash('sha256').update(raw).digest('hex'),
 publicKey:raw.toString('base64url'),signature:c.sign(null,Buffer.from(v.payload||''),key).toString('base64url')}));
"""

REQUEST = r"""
import http.client,json,socket,sys
body=sys.stdin.buffer.read(65537)
assert len(body)<=65536
connection=http.client.HTTPConnection('localhost',timeout=8)
connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
connection.sock.settimeout(8)
connection.sock.connect('/run/openclaw-setup/control.sock')
connection.request('POST','/rpc',body,{'Content-Type':'application/json'})
response=connection.getresponse()
assert response.status==200
print(response.read(65536).decode())
connection.close()
"""

MEMORY = r"""
import json
from pathlib import Path
root=Path('/sys/fs/cgroup')
if (root/'memory.peak').is_file():
    result={'memory_peak_bytes':int((root/'memory.peak').read_text()),
            'memory_limit_bytes':int((root/'memory.max').read_text())}
else:
    result={'memory_peak_bytes':int((root/'memory/memory.max_usage_in_bytes').read_text()),
            'memory_limit_bytes':int((root/'memory/memory.limit_in_bytes').read_text())}
print(json.dumps(result))
"""


@unittest.skipUnless(IMAGE, "set OPENCLAW_TEST_SETUP_IMAGE for isolated runtime image smoke")
class NextcloudSetupImageTests(unittest.TestCase):
    def docker(
        self, *arguments: str, input: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *arguments], input=input, text=True, capture_output=True, check=check, timeout=45
        )

    def sign(self, payload: str = "") -> dict:
        # This deterministic key exists only for this synthetic fixture. The
        # runtime owner independently verifies it with its own pinned Node.
        result = subprocess.run(
            ["node", "-e", SIGN],
            input=json.dumps({"payload": payload}),
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        return json.loads(result.stdout)

    def request(self, container: str, payload: dict) -> dict:
        result = self.docker("exec", "-i", container, "python3", "-c", REQUEST, input=json.dumps(payload))
        return json.loads(result.stdout)

    def test_owner_start_health_signed_status_and_peak_fit_128m(self) -> None:
        self.docker("info", "--format", "{{.ServerVersion}}")
        label = self.docker(
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.openclaw.setup-owner"}}',
            IMAGE,
        ).stdout.strip()
        self.assertEqual(label, "1")
        suffix = uuid.uuid4().hex[:16]
        container = f"openclaw-setup-smoke-{suffix}"
        volume = f"openclaw-setup-smoke-control-{suffix}"
        identity = self.sign()
        with tempfile.TemporaryDirectory(prefix="openclaw-setup-image-fixture-") as folder:
            fixture = Path(folder)
            fixture.chmod(0o755)
            registry = fixture / "paired.json"
            registry.write_text(
                json.dumps(
                    {
                        identity["id"]: {
                            "deviceId": identity["id"],
                            "publicKey": identity["publicKey"],
                            "role": "operator",
                            "approvedScopes": ["operator.admin"],
                            "tokens": {
                                "operator": {
                                    "token": "synthetic-fixture-only",
                                    "role": "operator",
                                    "scopes": ["operator.admin"],
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            # Public synthetic fixture, portable across host/image UID choices.
            registry.chmod(0o444)
            self.docker("volume", "create", volume)
            try:
                self.docker(
                    "run",
                    "-d",
                    "--name",
                    container,
                    "--network",
                    "none",
                    "--read-only",
                    "--user",
                    "1000:1000",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--pids-limit",
                    "128",
                    "--memory",
                    "128m",
                    "--memory-swap",
                    "128m",
                    "--cpus",
                    "0.5",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
                    "--tmpfs",
                    "/run/openclaw-nextcloud:rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=1000,gid=1000",
                    "--mount",
                    f"type=volume,src={volume},dst=/run/openclaw-setup",
                    "--mount",
                    f"type=bind,src={fixture},dst=/run/openclaw-gateway-devices,readonly",
                    "--env",
                    "OPENCLAW_ROLE=setup-service",
                    "--env",
                    "OPENCLAW_NEXTCLOUD_CONNECTION_DIR=/run/openclaw-nextcloud",
                    "--env",
                    "OPENCLAW_SETUP_SOCKET=/run/openclaw-setup/control.sock",
                    "--env",
                    "OPENCLAW_SETUP_DEVICES_FILE=/run/openclaw-gateway-devices/paired.json",
                    IMAGE,
                    "python3",
                    "-P",
                    "-m",
                    "personal_assistant.nextcloud_setup",
                )
                ready = False
                for _ in range(30):
                    probe = self.docker(
                        "exec", container, "/opt/openclaw-agent/docker/healthcheck.sh", "setup", check=False
                    )
                    if probe.returncode == 0:
                        ready = True
                        break
                    state = self.docker("inspect", "--format", "{{.State.Running}}", container).stdout.strip()
                    if state != "true":
                        break
                    time.sleep(0.2)
                self.assertTrue(ready, "isolated setup owner did not become ready")
                # A separate client can connect with the same read-only socket
                # mount used by the gateway; no configuration write is needed.
                self.docker(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--user",
                    "1000:1000",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--memory",
                    "64m",
                    "--mount",
                    f"type=volume,src={volume},dst=/run/openclaw-setup,readonly",
                    "--entrypoint",
                    "python3",
                    IMAGE,
                    "-P",
                    "-m",
                    "personal_assistant.nextcloud_setup",
                    "--check",
                )
                plain = {"operation": "status", "parameters": {}, "actor": "device:" + identity["id"]}
                forged = self.request(container, {**plain, "proof": {}})
                self.assertFalse(forged["ok"])
                signed = {**plain, "issued_at": int(time.time() * 1000), "nonce": uuid.uuid4().hex}
                payload = json.dumps(signed)
                request = {
                    **plain,
                    "proof": {"payload": payload, "signature": self.sign(payload)["signature"]},
                }
                answer = self.request(container, request)
                self.assertTrue(answer["ok"], answer)
                self.assertFalse(answer["result"]["connected"])
                replay = self.request(container, request)
                self.assertFalse(replay["ok"])
                self.assertEqual(replay["error"]["code"], "operator-proof-invalid")
                self.docker("exec", container, "/opt/openclaw-agent/docker/healthcheck.sh", "setup")
                memory = json.loads(self.docker("exec", container, "python3", "-c", MEMORY).stdout)
                self.assertEqual(memory["memory_limit_bytes"], LIMIT)
                self.assertGreater(memory["memory_peak_bytes"], 0)
                self.assertLessEqual(memory["memory_peak_bytes"], LIMIT)
                state = self.docker(
                    "inspect", "--format", "{{.State.Running}} {{.State.OOMKilled}}", container
                ).stdout.strip()
                self.assertEqual(state, "true false")
                print(
                    json.dumps(
                        {
                            "owner_ready": True,
                            "runtime_node_ed25519": True,
                            "invalid_proof_rejected": True,
                            "replay_rejected": True,
                            **memory,
                        }
                    )
                )
            finally:
                self.docker("rm", "-f", container, check=False)
                self.docker("volume", "rm", volume, check=False)


if __name__ == "__main__":
    unittest.main()
