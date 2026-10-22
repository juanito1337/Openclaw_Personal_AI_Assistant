"""Opt-in real Caddy test: isolated Docker network, no host ports or real data.

Run after building proxy-runtime with OPENCLAW_TEST_TALK_PROXY_IMAGE=<local tag>.
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

IMAGE = os.environ.get("OPENCLAW_TEST_TALK_PROXY_IMAGE", "")

FIXTURE = r'''
import os, socket, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from personal_assistant import talk_proxy_runtime as runtime
class Backend(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        good = (raw == b'{"test":"synthetic"}'
                and self.headers.get('X-Nextcloud-Talk-Signature') == 'test-signature')
        body = b'{"test":true}' if good else b'{}'
        self.send_response(200 if good else 400)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
threading.Thread(target=HTTPServer(('127.0.0.1', 8788), Backend).serve_forever, daemon=True).start()
os.environ['OPENCLAW_TALK_NEXTCLOUD_IP'] = socket.gethostbyname(socket.gethostname())
raise SystemExit(runtime.main(['serve']))
'''

PROBE = r'''
import http.client, json, socket, ssl
from personal_assistant import talk_proxy_runtime as runtime
address = '192.168.2.38'
fingerprint = runtime.health(address)
context = ssl.create_default_context(cafile='/var/lib/openclaw-talk-public/ca.crt')
def request(destination, method, path, extra=''):
    body = b'{"test":"synthetic"}'
    with socket.create_connection((destination, 8443), timeout=3) as connection:
        with context.wrap_socket(connection, server_hostname=address) as secured:
            payload = (f'{method} {path} HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\n'
                       f'Content-Length: {len(body)}\r\nX-Nextcloud-Talk-Signature: test-signature\r\n'
                       f'Connection: close\r\n{extra}\r\n').encode() + body
            secured.sendall(payload)
            response = http.client.HTTPResponse(secured)
            response.begin()
            return response.status, response.read(1024)
private = socket.gethostbyname(socket.gethostname())
assert request(private, 'POST', '/nextcloud-talk-webhook') == (200, b'{"test":true}')
assert request('127.0.0.1', 'POST', '/nextcloud-talk-webhook', f'X-Forwarded-For: {private}\r\n')[0] == 404
assert request(private, 'GET', '/nextcloud-talk-webhook')[0] == 404
assert request(private, 'POST', '/nextcloud-talk-webhook?unexpected=1')[0] == 404
assert request(private, 'POST', '/nextcloud%2dtalk-webhook')[0] == 404
assert request(private, 'POST', '/NEXTCLOUD-TALK-WEBHOOK')[0] == 404
assert request(private, 'POST', '/')[0] == 404
assert request(private, 'GET', '/healthz')[0] == 404
for wrong_context, wrong_name in ((ssl.create_default_context(), address), (context, '192.168.2.39')):
    try:
        with socket.create_connection(('127.0.0.1', 8443), timeout=3) as connection:
            with wrong_context.wrap_socket(connection, server_hostname=wrong_name):
                raise AssertionError('unexpected TLS trust')
    except ssl.SSLCertVerificationError:
        pass
print(json.dumps({'ok': True, 'ca_sha256': fingerprint, 'scope': 'isolated-caddy-tls-acl-routing'}))
'''


def docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout.strip()


def ready(name: str) -> None:
    for _ in range(60):
        result = subprocess.run(
            ["docker", "exec", name, "python3", "-P", "-m",
             "personal_assistant.talk_proxy_runtime", "health"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            return
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        assert state["Running"], docker("logs", name)
        time.sleep(0.25)
    raise AssertionError("isolated proxy did not become TLS healthy")


def check_real_caddy_tls_acl_routing_and_persistent_ca(tmp_path: Path) -> None:
    name = "openclaw-talk-test-" + uuid.uuid4().hex[:12]
    network = name + "-net"
    state, public = tmp_path / "pki", tmp_path / "public"
    state.mkdir(mode=0o700)
    public.mkdir(mode=0o700)
    docker("network", "create", "--internal", network)
    started = False
    try:
        docker(
            "run", "-d", "--name", name, "--network", network, "--add-host", "gateway:127.0.0.1",
            "--user", f"{os.getuid()}:{os.getgid()}", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "128m", "--pids-limit", "128",
            "--cpus", "0.5", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
            "-v", f"{state}:/var/lib/openclaw-talk", "-v", f"{public}:/var/lib/openclaw-talk-public",
            "-e", "OPENCLAW_TALK_PROXY_ENABLED=true", "-e", "OPENCLAW_TALK_PROXY_IP=192.168.2.38",
            "-e", "OPENCLAW_TALK_NEXTCLOUD_IP=192.168.2.3", "-e", "GOMEMLIMIT=96MiB", "-e", "GOMAXPROCS=1",
            "--entrypoint", "/sbin/tini", IMAGE, "--", "python3", "-P", "-c", FIXTURE,
        )
        started = True
        ready(name)
        first = json.loads(docker("exec", name, "python3", "-P", "-c", PROBE))
        assert first["ok"]
        assert [item.name for item in public.iterdir()] == ["ca.crt"]
        assert "PRIVATE KEY" not in (public / "ca.crt").read_text()
        docker("stop", "--time", "10", name)
        stopped = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        assert stopped["ExitCode"] == 0
        assert not stopped["OOMKilled"]
        docker("start", name)
        ready(name)
        second = json.loads(docker("exec", name, "python3", "-P", "-c", PROBE))
        assert second["ca_sha256"] == first["ca_sha256"]
    finally:
        if started:
            docker("rm", "-f", name)
        docker("network", "rm", network)


@unittest.skipUnless(IMAGE, "explicit local proxy test image required")
class TalkProxyContainerTests(unittest.TestCase):
    def test_real_caddy_tls_acl_routing_and_persistent_ca(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openclaw-talk-test-") as folder:
            check_real_caddy_tls_acl_routing_and_persistent_ca(Path(folder))
