"""Supervise the opt-in Talk TLS edge and export only its public trust anchor."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

STATE = Path("/var/lib/openclaw-talk")
PUBLIC = Path("/var/lib/openclaw-talk-public")
TEMP = Path("/tmp/openclaw-talk")
CA_RELATIVE = Path("caddy/pki/authorities/local/root.crt")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)


class ProxyError(ValueError):
    """Content-free, stable diagnostic code."""


def private_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ProxyError("talk-proxy-private-ip-required") from None
    if "%" in value or not any(address in network for network in PRIVATE_NETWORKS):
        raise ProxyError("talk-proxy-private-ip-required")
    return str(address)


def configuration(environment: Mapping[str, str]) -> tuple[str, str]:
    if environment.get("OPENCLAW_TALK_PROXY_ENABLED", "false") != "true":
        raise ProxyError("talk-proxy-not-enabled")
    address = private_ip(environment.get("OPENCLAW_TALK_PROXY_IP", ""))
    nextcloud = private_ip(environment.get("OPENCLAW_TALK_NEXTCLOUD_IP", ""))
    if address == nextcloud:
        raise ProxyError("talk-proxy-distinct-hosts-required")
    return address, nextcloud


def caddyfile(address: str, nextcloud: str) -> str:
    address, nextcloud = private_ip(address), private_ip(nextcloud)
    host = f"[{address}]" if ":" in address else address
    # IP-literal clients do not send DNS SNI. The DNAT destination seen inside
    # Docker differs from the certificate IP, hence this explicit default SNI.
    return f"""{{
    admin off
    persist_config off
    skip_install_trust
    auto_https disable_redirects
    default_sni {address}
    log default {{
        output discard
    }}
    servers {{
        protocols h1 h2
        max_header_size 16KB
        timeouts {{
            read_header 5s
            read_body 10s
            write 30s
            idle 30s
        }}
    }}
}}
https://{host}:8443 {{
    tls internal
    @webhook {{
        remote_ip {nextcloud}
        method POST
        path /nextcloud-talk-webhook
    }}
    handle @webhook {{
        request_body {{
            max_size 1048576
        }}
        reverse_proxy 127.0.0.1:9080 {{
            transport http {{
                dial_timeout 2s
                response_header_timeout 20s
                read_timeout 25s
                write_timeout 12s
            }}
        }}
    }}
    handle {{
        respond 404
    }}
}}
"""


def _check_directory_metadata(metadata: os.stat_result, *, private: bool) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ProxyError("talk-proxy-unsafe-storage")
    if stat.S_IMODE(metadata.st_mode) & (0o077 if private else 0o022):
        raise ProxyError("talk-proxy-unsafe-storage-mode")


@contextmanager
def _directory(path: Path, *, private: bool, dir_fd: int | None = None):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        _check_directory_metadata(os.fstat(descriptor), private=private)
        yield descriptor
    finally:
        os.close(descriptor)


def check_directory(path: Path, *, private: bool) -> None:
    with _directory(path, private=private):
        pass


def certificate(path: Path, *, dir_fd: int | None = None) -> tuple[bytes, str]:
    # O_NOFOLLOW and a bounded read protect the only publicly exported artifact.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or not 0 < metadata.st_size <= 32768
        ):
            raise ProxyError("talk-proxy-invalid-public-ca")
        data = stream.read(32769)
    try:
        pem = data.decode("ascii").strip()
        if len(data) > 32768 or not re.fullmatch(
            r"-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----", pem
        ):
            raise ValueError
        der = ssl.PEM_cert_to_DER_cert(pem)
        # The SSL loader also checks ASN.1, not only the PEM framing/base64.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=pem)
        if der not in context.get_ca_certs(binary_form=True):
            raise ValueError
        decoded = context.get_ca_certs()
        if len(decoded) != 1 or decoded[0]["subject"] != decoded[0]["issuer"]:
            raise ValueError
    except (ValueError, UnicodeError, ssl.SSLError):
        raise ProxyError("talk-proxy-invalid-public-ca") from None
    return ssl.DER_cert_to_PEM_cert(der).encode("ascii"), hashlib.sha256(der).hexdigest()


def _state_certificate(state: Path) -> tuple[bytes, str]:
    # Each component is opened relative to the verified parent descriptor.
    # A symlink in caddy/pki/... cannot escape the private storage root, including
    # one swapped after a pathname-only lstat check.
    with _directory(state, private=True) as root:
        descriptors = []
        parent = root
        try:
            for component in CA_RELATIVE.parts[:-1]:
                descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(descriptor)
                _check_directory_metadata(os.fstat(descriptor), private=True)
                parent = descriptor
            return certificate(Path(CA_RELATIVE.name), dir_fd=parent)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def _existing_export(public_descriptor: int, source: bytes) -> bool:
    try:
        existing, _ = certificate(Path("ca.crt"), dir_fd=public_descriptor)
    except FileNotFoundError:
        return False
    if existing != source:
        raise ProxyError("talk-proxy-ca-rotation-requires-approval")
    return True


def export_public_ca(state: Path = STATE, public: Path = PUBLIC) -> str:
    source, fingerprint = _state_certificate(state)
    with _directory(public, private=False) as directory:
        if not _existing_export(directory, source):
            temporary = f".ca-export-{uuid.uuid4().hex}.tmp"
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(source)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o644)
                    os.fsync(stream.fileno())
                try:
                    # Atomic publication without replacing an existing trust
                    # anchor. Even a concurrent exporter cannot overwrite it.
                    os.link(
                        temporary,
                        "ca.crt",
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    if not _existing_export(directory, source):
                        raise ProxyError("talk-proxy-ca-export-conflict") from None
            finally:
                os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
    return fingerprint


def health(address: str, state: Path = STATE, public: Path = PUBLIC) -> str:
    source, fingerprint = _state_certificate(state)
    with _directory(public, private=False) as directory:
        exported, _ = certificate(Path("ca.crt"), dir_fd=directory)
    if source != exported:
        raise ProxyError("talk-proxy-ca-export-mismatch")
    # Trust exactly the already-validated bytes; do not reopen a mutable path.
    context = ssl.create_default_context(cadata=exported.decode("ascii"))
    with (
        socket.create_connection(("127.0.0.1", 8443), timeout=2) as connection,
        context.wrap_socket(connection, server_hostname=private_ip(address)),
    ):
        pass
    connection_http = http.client.HTTPConnection("127.0.0.1", 9080, timeout=2)
    try:
        connection_http.request("GET", "/healthz")
        response = connection_http.getresponse()
        if response.status != 200 or response.read(64) != b'{"ok":true,"scope":"guard-transport-only"}\n':
            raise ProxyError("talk-proxy-guard-unhealthy")
    finally:
        connection_http.close()
    return fingerprint


def stop_children(children: list[subprocess.Popen[bytes]]) -> None:
    for child in children:
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + 5
    for child in children:
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)


def serve(address: str, nextcloud: str) -> int:
    os.umask(0o077)
    check_directory(STATE, private=True)
    check_directory(PUBLIC, private=False)
    TEMP.mkdir(mode=0o700, exist_ok=True)
    check_directory(TEMP, private=True)
    config = TEMP / "Caddyfile"
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(caddyfile(address, nextcloud))
    environment = dict(os.environ, XDG_DATA_HOME=str(STATE), XDG_CONFIG_HOME=str(TEMP))
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    children: list[subprocess.Popen[bytes]] = []
    try:
        for command in (
            [sys.executable, "-P", "-m", "personal_assistant.talk_proxy_guard"],
            ["/usr/bin/caddy", "run", "--config", str(config), "--adapter", "caddyfile"],
        ):
            children.append(
                subprocess.Popen(
                    command,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        deadline = time.monotonic() + 30
        fingerprint = ""
        next_check = 0.0
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise ProxyError("talk-proxy-child-exited")
            if time.monotonic() >= next_check:
                try:
                    fingerprint = export_public_ca()
                    next_check = time.monotonic() + 30
                except FileNotFoundError:
                    if fingerprint or time.monotonic() >= deadline:
                        raise ProxyError("talk-proxy-ca-unavailable") from None
            time.sleep(0.2)
        return 0
    finally:
        stop_children(children)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "health", "config"))
    arguments = parser.parse_args(argv)
    try:
        address, nextcloud = configuration(os.environ)
        if arguments.command == "config":
            print(caddyfile(address, nextcloud), end="")
        elif arguments.command == "health":
            print(json.dumps({"ok": True, "ca_sha256": health(address), "scope": "proxy-tls-only"}))
        else:
            return serve(address, nextcloud)
        return 0
    except (ProxyError, OSError, http.client.HTTPException) as error:
        # Neither exception text from libraries nor environment/config is logged.
        code = str(error) if isinstance(error, ProxyError) else "talk-proxy-io-failure"
        print(json.dumps({"ok": False, "error": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
