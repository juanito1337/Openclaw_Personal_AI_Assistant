from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import ssl
import stat
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from personal_assistant import talk_proxy_runtime as runtime


class TalkProxyRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = tempfile.TemporaryDirectory(prefix="talk-runtime-test-cert-")
        cls.certificates = []
        for name, ca in [("root-a", True), ("root-b", True), ("leaf", False)]:
            root = Path(cls.fixtures.name)
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "ec",
                    "-pkeyopt",
                    "ec_paramgen_curve:P-256",
                    "-nodes",
                    "-keyout",
                    str(root / f"{name}.key"),
                    "-out",
                    str(root / f"{name}.crt"),
                    "-subj",
                    f"/CN={name}",
                    "-days",
                    "1",
                    "-addext",
                    f"basicConstraints=critical,CA:{'TRUE' if ca else 'FALSE'}",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cls.certificates.append((root / f"{name}.crt").read_bytes())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixtures.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talk-runtime-test-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "private"
        self.public = self.root / "public"
        self.state.mkdir(mode=0o700)
        self.public.mkdir(mode=0o755)
        parent = self.state
        for component in runtime.CA_RELATIVE.parts[:-1]:
            parent /= component
            parent.mkdir(mode=0o700)
        self.source = self.state / runtime.CA_RELATIVE
        self.source.write_bytes(self.certificates[0])
        self.source.chmod(0o600)
        self.target = self.public / "ca.crt"
        self.environment = {
            "OPENCLAW_TALK_PROXY_ENABLED": "true",
            "OPENCLAW_TALK_PROXY_IP": "192.168.2.38",
            "OPENCLAW_TALK_NEXTCLOUD_IP": "192.168.2.3",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def export(self) -> str:
        return runtime.export_public_ca(self.state, self.public)

    def test_explicit_enable_is_required(self) -> None:
        for enabled in [None, "", "false", "TRUE", "True", "1", "yes", "true "]:
            environment = dict(self.environment)
            if enabled is None:
                environment.pop("OPENCLAW_TALK_PROXY_ENABLED")
            else:
                environment["OPENCLAW_TALK_PROXY_ENABLED"] = enabled
            with self.subTest(enabled=enabled), self.assertRaisesRegex(runtime.ProxyError, "not-enabled"):
                runtime.configuration(environment)
        self.assertEqual(runtime.configuration(self.environment), ("192.168.2.38", "192.168.2.3"))

    def test_private_ip_scope_and_canonicalization(self) -> None:
        for value in ["10.1.2.3", "172.16.1.1", "172.31.255.254", "192.168.2.38", "fd00::1", "fc00::1"]:
            self.assertEqual(runtime.private_ip(value), value)
        self.assertEqual(runtime.private_ip("FD00:0000::0001"), "fd00::1")
        for value in [
            "",
            "home-agent.home.arpa",
            "127.0.0.1",
            "0.0.0.0",
            "8.8.8.8",
            "100.64.1.1",
            "169.254.1.1",
            "172.32.0.1",
            "192.0.2.1",
            "224.0.0.1",
            "::1",
            "::",
            "fe80::1",
            "fd00::1%eth0",
            "::ffff:192.168.2.38",
            "192.168.2.38\nadmin :2019",
            "192.168.2.38:443",
        ]:
            with self.subTest(value=value), self.assertRaisesRegex(runtime.ProxyError, "private-ip-required"):
                runtime.private_ip(value)

    def test_same_nextcloud_and_agent_address_rejected(self) -> None:
        self.environment["OPENCLAW_TALK_NEXTCLOUD_IP"] = "192.168.2.38"
        with self.assertRaisesRegex(runtime.ProxyError, "distinct-hosts"):
            runtime.configuration(self.environment)

    def test_generated_config_is_scoped_and_hardened(self) -> None:
        content = runtime.caddyfile("192.168.2.38", "192.168.2.3")
        for directive in [
            "admin off",
            "persist_config off",
            "skip_install_trust",
            "auto_https disable_redirects",
            "default_sni 192.168.2.38",
            "protocols h1 h2",
            "max_header_size 16KB",
            "output discard",
            "https://192.168.2.38:8443",
            "tls internal",
            "remote_ip 192.168.2.3",
            "method POST",
            "path /nextcloud-talk-webhook",
            "max_size 1048576",
            "reverse_proxy 127.0.0.1:9080",
            "respond 404",
        ]:
            self.assertIn(directive, content)
        for forbidden in [
            "tls_insecure",
            "trusted_proxies",
            "file_server",
            ":18789",
            "http://",
            " h3",
            "on_demand",
        ]:
            self.assertNotIn(forbidden, content)
        self.assertIn("https://[fd00::38]:8443", runtime.caddyfile("fd00::38", "fd00::3"))
        with self.assertRaises(runtime.ProxyError):
            runtime.caddyfile("8.8.8.8", "192.168.2.3")

    def test_export_is_only_ca_certificate_and_is_idempotent(self) -> None:
        (self.source.parent / "root.key").write_text("PRIVATE KEY MUST NEVER BE EXPORTED")
        fingerprint = self.export()
        der = ssl.PEM_cert_to_DER_cert(self.certificates[0].decode())
        self.assertEqual(fingerprint, hashlib.sha256(der).hexdigest())
        self.assertEqual(self.target.read_bytes(), self.certificates[0])
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o644)
        metadata = self.target.stat()
        self.assertEqual(self.export(), fingerprint)
        self.assertEqual(self.target.stat().st_ino, metadata.st_ino)
        self.assertEqual(self.target.stat().st_mtime_ns, metadata.st_mtime_ns)
        self.assertEqual([path.name for path in self.public.iterdir()], ["ca.crt"])

    def test_rotation_is_not_an_implicit_overwrite(self) -> None:
        self.export()
        metadata = self.target.stat()
        self.source.write_bytes(self.certificates[1])
        with self.assertRaisesRegex(runtime.ProxyError, "rotation-requires-approval"):
            self.export()
        self.assertEqual(self.target.read_bytes(), self.certificates[0])
        self.assertEqual(self.target.stat().st_ino, metadata.st_ino)

    def test_leaf_and_appended_or_prefixed_data_are_not_public_ca(self) -> None:
        for value in [
            self.certificates[2],
            b"garbage",
            b"",
            b"x" * 32769,
            self.certificates[0] + b"SECRET_TOKEN=never-export-this\n",
            b"SECRET_TOKEN=never-export-this\n" + self.certificates[0],
            self.certificates[0] + self.certificates[1],
            self.certificates[0] + b"-----BEGIN PRIVATE KEY-----\nnot-a-fixture\n-----END PRIVATE KEY-----\n",
        ]:
            self.source.write_bytes(value)
            with (
                self.subTest(size=len(value)),
                self.assertRaisesRegex(runtime.ProxyError, "invalid-public-ca"),
            ):
                self.export()
            self.assertFalse(self.target.exists())

    def test_source_final_symlink_is_rejected(self) -> None:
        alternate = self.root / "outside.crt"
        alternate.write_bytes(self.certificates[0])
        self.source.unlink()
        self.source.symlink_to(alternate)
        with self.assertRaises(OSError):
            self.export()
        self.assertFalse(self.target.exists())

    def test_each_nested_ca_directory_symlink_is_rejected(self) -> None:
        parent = self.state
        for index, component in enumerate(runtime.CA_RELATIVE.parts[:-1]):
            path = parent / component
            alternate = self.root / f"outside-{index}"
            path.rename(alternate)
            path.symlink_to(alternate, target_is_directory=True)
            with self.subTest(component=component), self.assertRaises(OSError):
                self.export()
            path.unlink()
            alternate.rename(path)
            parent = path
        self.assertFalse(self.target.exists())

    def test_target_and_storage_root_symlinks_are_rejected(self) -> None:
        alternate = self.root / "outside.crt"
        alternate.write_bytes(self.certificates[0])
        self.target.symlink_to(alternate)
        with self.assertRaises(OSError):
            self.export()
        self.target.unlink()
        for path in [self.state, self.public]:
            linked = self.root / f"linked-{path.name}"
            linked.symlink_to(path, target_is_directory=True)
            with self.subTest(path=path.name), self.assertRaises(OSError):
                runtime.export_public_ca(
                    linked if path == self.state else self.state,
                    linked if path == self.public else self.public,
                )
        self.assertEqual(alternate.read_bytes(), self.certificates[0])

    def test_private_and_public_directory_and_file_modes_fail_closed(self) -> None:
        for path, mode in [
            (self.state, 0o750),
            (self.source.parent, 0o755),
            (self.public, 0o777),
            (self.source, 0o666),
        ]:
            previous = stat.S_IMODE(path.stat().st_mode)
            path.chmod(mode)
            with self.subTest(path=path.name), self.assertRaises(runtime.ProxyError):
                self.export()
            path.chmod(previous)
        self.export()
        self.target.chmod(0o666)
        with self.assertRaisesRegex(runtime.ProxyError, "invalid-public-ca"):
            self.export()

    def test_foreign_owned_storage_is_rejected(self) -> None:
        with (
            patch.object(runtime.os, "getuid", return_value=os.getuid() + 1),
            self.assertRaisesRegex(runtime.ProxyError, "unsafe-storage"),
        ):
            self.export()

    def test_fifo_cannot_block_public_certificate_reader(self) -> None:
        self.source.unlink()
        os.mkfifo(self.source, 0o600)
        with self.assertRaisesRegex(runtime.ProxyError, "invalid-public-ca"):
            self.export()

    def test_failed_export_never_publishes_partial_target(self) -> None:
        with (
            patch.object(runtime.os, "fsync", side_effect=OSError("fixture-error-not-secret")),
            self.assertRaises(OSError),
        ):
            self.export()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.public.iterdir()), [])
        self.export()
        self.assertEqual(self.target.read_bytes(), self.certificates[0])

    def test_atomic_export_race_is_idempotent_and_leaves_no_temps(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            fingerprints = list(pool.map(lambda _: self.export(), range(12)))
        self.assertEqual(len(set(fingerprints)), 1)
        self.assertEqual(self.target.read_bytes(), self.certificates[0])
        self.assertEqual([path.name for path in self.public.iterdir()], ["ca.crt"])

    def mocked_health(self, stack: ExitStack):
        context = stack.enter_context(patch.object(runtime.ssl, "create_default_context"))
        transport = stack.enter_context(patch.object(runtime.socket, "create_connection"))
        http = stack.enter_context(patch.object(runtime.http.client, "HTTPConnection"))
        response = http.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"ok":true,"scope":"guard-transport-only"}\n'
        return context, transport, http, response

    def test_health_verifies_trust_bytes_and_ip_and_guard_transport(self) -> None:
        fingerprint = self.export()
        with ExitStack() as stack:
            context, transport, http, _ = self.mocked_health(stack)
            self.assertEqual(runtime.health("192.168.2.38", self.state, self.public), fingerprint)
            context.assert_called_once_with(cadata=self.certificates[0].decode())
            transport.assert_called_once_with(("127.0.0.1", 8443), timeout=2)
            context.return_value.wrap_socket.assert_called_once_with(
                transport.return_value.__enter__.return_value, server_hostname="192.168.2.38"
            )
            http.assert_called_once_with("127.0.0.1", 9080, timeout=2)
            http.return_value.request.assert_called_once_with("GET", "/healthz")
            http.return_value.close.assert_called_once()

    def test_health_rejects_mismatch_without_connecting(self) -> None:
        self.export()
        self.target.write_bytes(self.certificates[1])
        with patch.object(runtime.socket, "create_connection") as transport:
            with self.assertRaisesRegex(runtime.ProxyError, "export-mismatch"):
                runtime.health("192.168.2.38", self.state, self.public)
            transport.assert_not_called()

    def test_health_uses_safe_nested_ca_traversal(self) -> None:
        self.export()
        directory = self.source.parent
        alternate = self.root / "escaped-ca"
        directory.rename(alternate)
        directory.symlink_to(alternate, target_is_directory=True)
        with patch.object(runtime.socket, "create_connection") as transport:
            with self.assertRaises(OSError):
                runtime.health("192.168.2.38", self.state, self.public)
            transport.assert_not_called()

    def test_health_rejects_guard_error_or_wrong_body_and_closes(self) -> None:
        self.export()
        for status, body in [(503, b""), (200, b"ok\n"), (200, b'{"ok":false}')]:
            with ExitStack() as stack:
                _, _, http, response = self.mocked_health(stack)
                response.status, response.read.return_value = status, body
                with self.assertRaisesRegex(runtime.ProxyError, "guard-unhealthy"):
                    runtime.health("192.168.2.38", self.state, self.public)
                http.return_value.close.assert_called_once()

    def test_health_propagates_tls_verification_failure(self) -> None:
        self.export()
        with ExitStack() as stack:
            context, _, http, _ = self.mocked_health(stack)
            context.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("fixture-tls-error")
            with self.assertRaises(ssl.SSLCertVerificationError):
                runtime.health("192.168.2.38", self.state, self.public)
            http.assert_not_called()

    def test_stop_children_terminates_and_escalates_only_when_needed(self) -> None:
        running, exited, stuck = MagicMock(), MagicMock(), MagicMock()
        running.poll.return_value = None
        exited.poll.return_value = 0
        stuck.poll.return_value = None
        stuck.wait.side_effect = [subprocess.TimeoutExpired("fixture", 1), 0]
        runtime.stop_children([running, exited, stuck])
        running.terminate.assert_called_once()
        running.kill.assert_not_called()
        exited.terminate.assert_not_called()
        stuck.terminate.assert_called_once()
        stuck.kill.assert_called_once()

    def test_supervisor_stops_sibling_and_restores_signals_on_child_exit(self) -> None:
        child_a, child_b = MagicMock(), MagicMock()
        child_a.poll.return_value = 1
        child_b.poll.return_value = None
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, "STATE", self.state))
            stack.enter_context(patch.object(runtime, "PUBLIC", self.public))
            stack.enter_context(patch.object(runtime, "TEMP", self.root / "runtime"))
            stack.enter_context(patch.object(runtime.os, "umask"))
            signals = stack.enter_context(patch.object(runtime.signal, "signal", return_value=signal.SIG_DFL))
            launch = stack.enter_context(
                patch.object(runtime.subprocess, "Popen", side_effect=[child_a, child_b])
            )
            with self.assertRaisesRegex(runtime.ProxyError, "child-exited"):
                runtime.serve("192.168.2.38", "192.168.2.3")
            self.assertEqual(launch.call_count, 2)
            for invocation in launch.call_args_list:
                self.assertEqual(invocation.kwargs["stdin"], subprocess.DEVNULL)
                self.assertEqual(invocation.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(invocation.kwargs["stderr"], subprocess.DEVNULL)
            child_b.terminate.assert_called_once()
            self.assertEqual(signals.call_count, 4)

    def test_supervisor_sigterm_stops_both_children(self) -> None:
        children = [MagicMock(), MagicMock()]
        for child in children:
            child.poll.return_value = None
        handlers = {}

        def signal_handler(signum, handler):
            handlers[signum] = handler
            return signal.SIG_DFL

        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, "STATE", self.state))
            stack.enter_context(patch.object(runtime, "PUBLIC", self.public))
            stack.enter_context(patch.object(runtime, "TEMP", self.root / "runtime"))
            stack.enter_context(patch.object(runtime.os, "umask"))
            stack.enter_context(patch.object(runtime.signal, "signal", side_effect=signal_handler))
            stack.enter_context(patch.object(runtime.subprocess, "Popen", side_effect=children))
            stack.enter_context(patch.object(runtime, "export_public_ca", return_value="a" * 64))
            stack.enter_context(
                patch.object(
                    runtime.time,
                    "sleep",
                    side_effect=lambda _: handlers[signal.SIGTERM](signal.SIGTERM, None),
                )
            )
            self.assertEqual(runtime.serve("192.168.2.38", "192.168.2.3"), 0)
        for child in children:
            child.terminate.assert_called_once()
            child.wait.assert_called_once()
        self.assertEqual(handlers, {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL})

    def test_second_child_launch_failure_stops_first(self) -> None:
        child = MagicMock()
        child.poll.return_value = None
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, "STATE", self.state))
            stack.enter_context(patch.object(runtime, "PUBLIC", self.public))
            stack.enter_context(patch.object(runtime, "TEMP", self.root / "runtime"))
            stack.enter_context(patch.object(runtime.os, "umask"))
            signals = stack.enter_context(patch.object(runtime.signal, "signal", return_value=signal.SIG_DFL))
            stack.enter_context(
                patch.object(
                    runtime.subprocess, "Popen", side_effect=[child, OSError("fixture-startup-failure")]
                )
            )
            with self.assertRaises(OSError):
                runtime.serve("192.168.2.38", "192.168.2.3")
            child.terminate.assert_called_once()
            self.assertEqual(signals.call_count, 4)

    def test_main_emits_only_stable_error_not_library_exception(self) -> None:
        errors = io.StringIO()
        with (
            patch.dict(os.environ, self.environment),
            patch.object(runtime, "health", side_effect=OSError("credential-content-must-not-leak")),
            redirect_stderr(errors),
        ):
            self.assertEqual(runtime.main(["health"]), 1)
        self.assertEqual(json.loads(errors.getvalue()), {"ok": False, "error": "talk-proxy-io-failure"})
        output = io.StringIO()
        with (
            patch.dict(os.environ, self.environment),
            patch.object(runtime, "health", return_value="a" * 64),
            redirect_stdout(output),
        ):
            self.assertEqual(runtime.main(["health"]), 0)
        self.assertEqual(json.loads(output.getvalue())["scope"], "proxy-tls-only")


if __name__ == "__main__":
    unittest.main()
