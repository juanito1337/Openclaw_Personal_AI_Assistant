from __future__ import annotations

import base64
import hashlib
import imaplib
import os
import re
import shlex
import ssl
import time
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Protocol

from personal_assistant.contracts.mail_index_authority import (
    FolderIdentityAssurance,
    FolderSnapshotEvidence,
)

from .config import Config
from .search_backfill import (
    BackfillBackendError,
    BackfillEnvelope,
    BackfillFolder,
    BackfillPage,
    ConnectorCapabilities,
)

FIXED_IMAP_PASSWORD_FILE = Path("/run/openclaw-secrets/himalaya-imap-password")
ALLOWED_IMAP_COMMANDS = frozenset(
    {"CAPABILITY", "LIST", "STATUS", "EXAMINE", "UID SEARCH", "UID FETCH", "LOGOUT"}
)
FORBIDDEN_IMAP_COMMANDS = frozenset(
    {
        "STORE",
        "COPY",
        "MOVE",
        "EXPUNGE",
        "APPEND",
        "CREATE",
        "DELETE",
        "RENAME",
        "SUBSCRIBE",
        "UNSUBSCRIBE",
        "CLOSE",
    }
)


class ImapInventoryError(RuntimeError):
    def __init__(self, category: str, detail: str = "") -> None:
        self.category = category
        self.safe_detail = detail[:300]
        super().__init__(self.safe_detail or category)


class ReadOnlyCommandPolicy:
    @staticmethod
    def validate(command: str) -> str:
        normalized = " ".join(str(command).strip().upper().split())
        if normalized in FORBIDDEN_IMAP_COMMANDS or normalized.split(" ", 1)[0] in FORBIDDEN_IMAP_COMMANDS:
            raise PermissionError(f"IMAP-Schreibkommando blockiert: {normalized.split(' ', 1)[0]}")
        if normalized not in ALLOWED_IMAP_COMMANDS:
            raise PermissionError(f"IMAP-Kommando nicht freigegeben: {normalized}")
        return normalized


@dataclass(frozen=True, slots=True)
class ImapConnectionSettings:
    account: str
    host: str
    port: int
    login: str
    encryption: str
    password_file: Path = FIXED_IMAP_PASSWORD_FILE
    connect_timeout_seconds: float = 15.0
    read_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class ImapFolderState:
    folder_id: str
    name: str
    encoded_name: str
    uidvalidity: str
    uidnext: str
    highest_modseq: str
    server_mailbox_id: str
    message_count: int
    uids: tuple[int, ...]
    folder_identity_assurance: str
    complete: bool = True


class InventoryTransport(Protocol):
    commands: list[str]

    def capabilities(self) -> set[str]: ...

    def list_folders(self) -> list[tuple[str, str]]: ...

    def examine(self, encoded_name: str) -> dict[str, str]: ...

    def uid_search_all(self) -> tuple[int, ...]: ...

    def uid_fetch_headers(self, uid: int) -> bytes: ...

    def uid_fetch_raw(self, uid: int) -> bytes: ...

    def logout(self) -> None: ...


def _deep_merge(target: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _config_paths() -> list[Path]:
    configured = os.environ.get("HIMALAYA_CONFIG", "").strip()
    if configured:
        return [Path(item).expanduser().resolve() for item in configured.split(os.pathsep) if item.strip()]
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    candidates = (xdg / "himalaya/config.toml", Path("~/.config/himalaya/config.toml").expanduser())
    return [candidate.resolve() for candidate in candidates if candidate.is_file()][:1]


def load_imap_settings(
    config: Config,
    *,
    config_paths: list[Path] | None = None,
    password_file: Path = FIXED_IMAP_PASSWORD_FILE,
) -> ImapConnectionSettings:
    merged: dict[str, Any] = {}
    paths = config_paths if config_paths is not None else _config_paths()
    if not paths:
        raise ImapInventoryError("configuration", "Himalaya-Konfiguration nicht gefunden")
    for path in paths:
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ImapInventoryError("configuration", "Konfigurationsdatei fehlt") from None
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            raise ImapInventoryError("configuration", "Konfigurationsdatei ist nicht lesbar") from None
        merged = _deep_merge(merged, parsed)
    accounts = merged.get("accounts")
    if not isinstance(accounts, dict) or not accounts:
        raise ImapInventoryError("configuration", "Kein Himalaya-Konto konfiguriert")
    account_name = config.mailbox.account.strip()
    if not account_name:
        defaults = [
            name
            for name, value in accounts.items()
            if isinstance(value, dict) and value.get("default") is True
        ]
        if len(defaults) == 1:
            account_name = str(defaults[0])
        elif len(accounts) == 1:
            account_name = str(next(iter(accounts)))
        else:
            raise ImapInventoryError("configuration", "Himalaya-Konto ist nicht eindeutig")
    account = accounts.get(account_name)
    if not isinstance(account, dict):
        raise ImapInventoryError("configuration", "Konfiguriertes Himalaya-Konto fehlt")
    backend = account.get("backend")
    if not isinstance(backend, dict) or str(backend.get("type") or "").casefold() != "imap":
        raise ImapInventoryError("configuration", "Konto verwendet kein IMAP-Backend")
    encryption = backend.get("encryption")
    encryption_type = str(
        encryption.get("type") if isinstance(encryption, dict) else encryption or "tls"
    ).casefold()
    encryption_type = encryption_type.replace("_", "-")
    if encryption_type not in {"tls", "start-tls", "starttls"}:
        raise ImapInventoryError("tls", "Nur TLS oder STARTTLS ist erlaubt")
    auth = backend.get("auth")
    if not isinstance(auth, dict) or str(auth.get("type") or "password").casefold() != "password":
        raise ImapInventoryError(
            "authentication",
            "Nur Passwortauthentifizierung ueber festen Secret-Mount ist erlaubt",
        )
    if any(key in auth for key in ("password", "raw")):
        raise ImapInventoryError("configuration", "Klartextpasswort in Connector-Konfiguration ist verboten")
    command = str(auth.get("command") or auth.get("cmd") or "").strip()
    if command:
        try:
            tokens = shlex.split(command)
        except ValueError:
            raise ImapInventoryError("configuration", "Ungueltiges Auth-Kommando") from None
        if tokens != ["cat", str(password_file)]:
            raise ImapInventoryError("configuration", "Auth-Kommando ist nicht der feste Secret-Mount")
    host = str(backend.get("host") or "").strip()
    login = str(backend.get("login") or account.get("email") or "").strip()
    try:
        port = int(backend.get("port") or (993 if encryption_type == "tls" else 143))
    except (TypeError, ValueError):
        raise ImapInventoryError("configuration", "Ungueltiger IMAP-Port") from None
    if not host or not login or not 1 <= port <= 65535:
        raise ImapInventoryError("configuration", "IMAP-Host, Port oder Login fehlt")
    if not password_file.is_file():
        raise ImapInventoryError("authentication", "IMAP-Secret-Mount fehlt")
    return ImapConnectionSettings(
        account=account_name,
        host=host,
        port=port,
        login=login,
        encryption="start-tls" if encryption_type in {"start-tls", "starttls"} else "tls",
        password_file=password_file,
    )


def _decode_modified_utf7(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        amp = value.find("&", index)
        if amp < 0:
            result.append(value[index:])
            break
        result.append(value[index:amp])
        end = value.find("-", amp)
        if end < 0:
            result.append(value[amp:])
            break
        token = value[amp + 1 : end]
        if not token:
            result.append("&")
        else:
            padded = token.replace(",", "/") + "=" * ((4 - len(token) % 4) % 4)
            try:
                result.append(base64.b64decode(padded).decode("utf-16-be"))
            except (ValueError, UnicodeError):
                raise ImapInventoryError("protocol", "Ungueltiger modifizierter UTF-7-Ordnername") from None
        index = end + 1
    return "".join(result)


def _encode_modified_utf7(value: str) -> str:
    result: list[str] = []
    non_ascii: list[str] = []

    def flush() -> None:
        if not non_ascii:
            return
        encoded_bytes = base64.b64encode("".join(non_ascii).encode("utf-16-be"))
        encoded = encoded_bytes.decode().rstrip("=").replace("/", ",")
        result.append(f"&{encoded}-")
        non_ascii.clear()

    for char in value:
        if " " <= char <= "~" and char != "&":
            flush()
            result.append(char)
        elif char == "&":
            flush()
            result.append("&-")
        else:
            non_ascii.append(char)
    flush()
    return "".join(result)


_LIST_RE = re.compile(
    r'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$'
)


def parse_list_name(raw: str) -> tuple[str, str]:
    match = _LIST_RE.match(raw.strip())
    if not match:
        raise ImapInventoryError("protocol", "Ungueltige LIST-Antwort")
    token = match.group("name").strip()
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return _decode_modified_utf7(token), token


def _list_flags(raw: str) -> set[str]:
    match = _LIST_RE.match(raw.strip())
    if not match:
        raise ImapInventoryError("protocol", "Ungueltige LIST-Antwort")
    return {item.casefold() for item in match.group("flags").split()}


def _response_text(values: list[bytes | None] | tuple[bytes | None, ...] | None) -> str:
    if not values:
        return ""
    return " ".join(item.decode("ascii", errors="ignore") for item in values if isinstance(item, bytes))


class StdlibImapTransport:
    def __init__(self, settings: ImapConnectionSettings) -> None:
        self.settings = settings
        self.commands: list[str] = []
        self._client: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    def _record(self, command: str) -> None:
        self.commands.append(ReadOnlyCommandPolicy.validate(command))

    def connect(self) -> None:
        context = ssl.create_default_context()
        try:
            if self.settings.encryption == "tls":
                client: imaplib.IMAP4 | imaplib.IMAP4_SSL = imaplib.IMAP4_SSL(
                    self.settings.host,
                    self.settings.port,
                    ssl_context=context,
                    timeout=self.settings.connect_timeout_seconds,
                )
            else:
                client = imaplib.IMAP4(
                    self.settings.host,
                    self.settings.port,
                    timeout=self.settings.connect_timeout_seconds,
                )
                client.starttls(ssl_context=context)
            password = self.settings.password_file.read_text(encoding="utf-8").rstrip("\r\n")
            if not password:
                raise ImapInventoryError("authentication", "IMAP-Secret ist leer")
            client.login(self.settings.login, password)
            if client.sock is not None:
                client.sock.settimeout(self.settings.read_timeout_seconds)
            self._client = client
        except ssl.SSLError:
            raise ImapInventoryError("tls", "TLS-Verifikation fehlgeschlagen") from None
        except TimeoutError:
            raise ImapInventoryError("timeout", "IMAP-Verbindungszeitlimit") from None
        except imaplib.IMAP4.error:
            raise ImapInventoryError(
                "authentication", "IMAP-Anmeldung oder Protokollaufbau fehlgeschlagen"
            ) from None
        except OSError:
            raise ImapInventoryError("network", "IMAP-Verbindung fehlgeschlagen") from None

    def _require(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        if self._client is None:
            raise ImapInventoryError("protocol", "IMAP-Verbindung ist nicht geoeffnet")
        return self._client

    @staticmethod
    def _ok(status: str, category: str) -> None:
        if status != "OK":
            raise ImapInventoryError(category, f"IMAP-Antwort {status}")

    @staticmethod
    def _execute(category: str, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except TimeoutError:
            raise ImapInventoryError("timeout", "IMAP-Lesezeitlimit") from None
        except imaplib.IMAP4.abort:
            raise ImapInventoryError("server-bye", "IMAP-Server brach die Sitzung ab") from None
        except imaplib.IMAP4.error:
            raise ImapInventoryError(category, "IMAP-Protokolloperation fehlgeschlagen") from None
        except OSError:
            raise ImapInventoryError("network", "IMAP-Netzoperation fehlgeschlagen") from None

    def capabilities(self) -> set[str]:
        self._record("CAPABILITY")
        status, values = self._execute("protocol", self._require().capability)
        self._ok(status, "protocol")
        return {item.upper() for item in _response_text(values).split()}

    def list_folders(self) -> list[tuple[str, str]]:
        self._record("LIST")
        status, values = self._execute("folder-inventory", self._require().list)
        self._ok(status, "folder-inventory")
        result: list[tuple[str, str]] = []
        for value in values or []:
            if isinstance(value, bytes):
                decoded = value.decode("utf-8", errors="strict")
                if "\\noselect" in _list_flags(decoded):
                    continue
                result.append(parse_list_name(decoded))
        return result

    def examine(self, encoded_name: str) -> dict[str, str]:
        self._record("EXAMINE")
        status, values = self._execute(
            "folder-vanished", lambda: self._require().select(encoded_name, readonly=True)
        )
        self._ok(status, "folder-vanished")
        client = self._require()
        result = {"messages": _response_text(values).strip() or "0"}
        for key in ("UIDVALIDITY", "UIDNEXT", "HIGHESTMODSEQ", "MAILBOXID"):
            response = client.response(key)
            text = _response_text(response[1]).strip()
            if text:
                result[key.casefold()] = text.strip("() ")
        return result

    def uid_search_all(self) -> tuple[int, ...]:
        self._record("UID SEARCH")
        status, values = self._execute(
            "partial-scan", lambda: self._require().uid("SEARCH", "ALL")
        )
        self._ok(status, "partial-scan")
        text = _response_text(values).strip()
        if not text:
            return ()
        try:
            return tuple(int(item) for item in text.split())
        except ValueError:
            raise ImapInventoryError("protocol", "Ungueltige UID-SEARCH-Antwort") from None

    def _fetch(self, uid: int, selector: str) -> bytes:
        self._record("UID FETCH")
        status, values = self._execute(
            "raw-fetch", lambda: self._require().uid("FETCH", str(uid), selector)
        )
        self._ok(status, "raw-fetch")
        for value in values or []:
            if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[1], bytes):
                return value[1]
        raise ImapInventoryError("raw-fetch", "UID-FETCH lieferte keinen Payload")

    def uid_fetch_headers(self, uid: int) -> bytes:
        return self._fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])")

    def uid_fetch_raw(self, uid: int) -> bytes:
        return self._fetch(uid, "(BODY.PEEK[])")

    def logout(self) -> None:
        if self._client is None:
            return
        self._record("LOGOUT")
        with suppress(imaplib.IMAP4.error, OSError):
            self._client.logout()
        self._client = None


TransportFactory = Callable[[ImapConnectionSettings], InventoryTransport]


class NativeImapInventoryBackend:
    """Internal IMAP inventory backend. Its public surface cannot express writes."""

    def __init__(
        self,
        settings: ImapConnectionSettings,
        *,
        resource_id: str = "mail-agent",
        transport_factory: TransportFactory = StdlibImapTransport,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.resource_id = resource_id
        self.transport_factory = transport_factory
        self.monotonic = monotonic
        self._started_at: float | None = None
        self._transport: InventoryTransport | None = None
        self._capabilities: set[str] = set()
        self._folders: list[BackfillFolder] | None = None
        self._states: dict[str, ImapFolderState] = {}

    def close(self) -> None:
        if self._transport is not None:
            self._transport.logout()
            self._transport = None

    def _session(self) -> InventoryTransport:
        if self._started_at is not None and (
            self.monotonic() - self._started_at > self.settings.total_timeout_seconds
        ):
            raise ImapInventoryError("timeout", "IMAP-Gesamtlaufzeitlimit erreicht")
        if self._transport is None:
            self._started_at = self.monotonic()
            transport = self.transport_factory(self.settings)
            connect = getattr(transport, "connect", None)
            if callable(connect):
                connect()
            self._transport = transport
            self._capabilities = {item.upper() for item in transport.capabilities()}
        return self._transport

    def capabilities(self) -> ConnectorCapabilities:
        self._session()
        caps = self._capabilities
        mailbox_id = bool(caps & {"OBJECTID", "MAILBOXID"})
        return ConnectorCapabilities(
            paging=True,
            raw_fetch=True,
            uid=True,
            uidvalidity=True,
            uidnext=True,
            modseq="CONDSTORE" in caps or "QRESYNC" in caps,
            condstore="CONDSTORE" in caps,
            qresync="QRESYNC" in caps,
            idle="IDLE" in caps,
            folder_stable_id=mailbox_id,
            folder_identity_assurance=(
                str(FolderIdentityAssurance.SERVER_STABLE)
                if mailbox_id
                else str(FolderIdentityAssurance.SNAPSHOT_STABLE)
            ),
            cursor_contract="complete-uid-snapshot",
        )

    def inventory(self) -> list[BackfillFolder]:
        rows = self._session().list_folders()
        initial_inventory = tuple(sorted(rows, key=lambda item: (item[0].casefold(), item[1])))
        names = sorted({name for name, _encoded in rows if name.strip()}, key=str.casefold)
        folders: list[BackfillFolder] = []
        for name in names:
            digest = hashlib.sha256(f"{self.resource_id}\0{name}".encode()).hexdigest()[:32]
            folders.append(BackfillFolder(f"folder:{digest}", name))
        resolved: list[BackfillFolder] = []
        for folder in folders:
            state = self.snapshot(folder)
            folder_id = folder.folder_id
            if state.server_mailbox_id:
                stable = hashlib.sha256(
                    f"{self.resource_id}\0{state.server_mailbox_id}".encode()
                ).hexdigest()[:32]
                folder_id = f"folder:{stable}"
                state = ImapFolderState(
                    folder_id=folder_id,
                    name=state.name,
                    encoded_name=state.encoded_name,
                    uidvalidity=state.uidvalidity,
                    uidnext=state.uidnext,
                    highest_modseq=state.highest_modseq,
                    server_mailbox_id=state.server_mailbox_id,
                    message_count=state.message_count,
                    uids=state.uids,
                    folder_identity_assurance=state.folder_identity_assurance,
                    complete=state.complete,
                )
                self._states[folder_id] = state
            resolved.append(BackfillFolder(folder_id, folder.name, state.uidvalidity))
        final_inventory = tuple(
            sorted(
                self._session().list_folders(),
                key=lambda item: (item[0].casefold(), item[1]),
            )
        )
        if final_inventory != initial_inventory:
            raise ImapInventoryError(
                "folder-list-race", "IMAP-Ordnerliste wechselte waehrend des Inventars"
            )
        self._folders = resolved
        return resolved

    def _encoded_name(self, name: str) -> str:
        return _encode_modified_utf7(name)

    def snapshot(self, folder: BackfillFolder) -> ImapFolderState:
        transport = self._session()
        encoded = self._encoded_name(folder.name)
        start = transport.examine(encoded)
        uidvalidity = str(start.get("uidvalidity") or "").split()[-1].strip("[]")
        uidnext = str(start.get("uidnext") or "").split()[-1].strip("[]")
        if not uidvalidity or not uidnext:
            raise ImapInventoryError("protocol", "UIDVALIDITY oder UIDNEXT fehlt")
        uids = transport.uid_search_all()
        if uids != tuple(sorted(set(uids))) or any(uid <= 0 for uid in uids):
            raise ImapInventoryError("protocol", "UID-Snapshot ist unsortiert oder doppelt")
        end = transport.examine(encoded)
        end_validity = str(end.get("uidvalidity") or "").split()[-1].strip("[]")
        end_uidnext = str(end.get("uidnext") or "").split()[-1].strip("[]")
        start_messages = str(start.get("messages") or "").split()[-1].strip("[]")
        end_messages = str(end.get("messages") or "").split()[-1].strip("[]")
        if end_validity != uidvalidity:
            raise ImapInventoryError("uidvalidity-race", "UIDVALIDITY wechselte waehrend des Snapshots")
        if end_uidnext != uidnext or end_messages != start_messages:
            raise ImapInventoryError(
                "snapshot-race", "UIDNEXT oder Nachrichtenanzahl wechselte waehrend des Snapshots"
            )
        if not end_messages.isdigit() or int(end_messages) != len(uids):
            raise ImapInventoryError(
                "partial-scan", "UID-Menge stimmt nicht mit der Nachrichtenanzahl ueberein"
            )
        server_id = str(start.get("mailboxid") or "")
        evidence = FolderSnapshotEvidence(True, True, True, True, server_id)
        state = ImapFolderState(
            folder_id=folder.folder_id,
            name=folder.name,
            encoded_name=encoded,
            uidvalidity=uidvalidity,
            uidnext=uidnext,
            highest_modseq=str(start.get("highestmodseq") or ""),
            server_mailbox_id=server_id,
            message_count=len(uids),
            uids=uids,
            folder_identity_assurance=str(evidence.assurance()),
        )
        self._states[folder.folder_id] = state
        return state

    def _state(self, folder: BackfillFolder) -> ImapFolderState:
        return self._states.get(folder.folder_id) or self.snapshot(folder)

    def fetch_page(self, folder: BackfillFolder, *, page: int, page_size: int) -> BackfillPage:
        if page < 1 or page_size < 1:
            raise ValueError("page und page_size muessen positiv sein")
        state = self._state(folder)
        self._session().examine(state.encoded_name)
        start = (page - 1) * page_size
        selected = state.uids[start : start + page_size]
        items: list[BackfillEnvelope] = []
        for uid in selected:
            raw_headers = self._session().uid_fetch_headers(uid)
            message = BytesParser(policy=policy.default).parsebytes(raw_headers, headersonly=True)
            sender = str(message.get("From") or "")
            sender_name, sender_addr = parseaddr(sender)
            subject = str(make_header(decode_header(str(message.get("Subject") or ""))))
            items.append(
                BackfillEnvelope(
                    mailbox_id=str(uid),
                    uid=str(uid),
                    subject=subject,
                    sender_name=sender_name,
                    sender_addr=sender_addr,
                    date=str(message.get("Date") or ""),
                )
            )
        return BackfillPage(tuple(items), start + len(selected) < len(state.uids))

    def fetch_raw(self, folder: BackfillFolder, envelope: BackfillEnvelope) -> bytes:
        state = self._state(folder)
        uid = int(envelope.uid or envelope.mailbox_id)
        if uid not in state.uids:
            raise BackfillBackendError("locator-conflict", "UID ist nicht mehr im belegten Ordnersnapshot")
        self._session().examine(state.encoded_name)
        return self._session().uid_fetch_raw(uid)

    def revalidate_locator(
        self,
        folder: BackfillFolder,
        *,
        uidvalidity: str,
        uid: str,
        expected_subject: str = "",
        expected_message_id: str = "",
    ) -> dict[str, Any]:
        state = self.snapshot(folder)
        if state.uidvalidity != str(uidvalidity):
            return {
                "ok": False,
                "state": "conflict",
                "code": "uidvalidity-changed",
                "read_only": True,
            }
        try:
            numeric_uid = int(uid)
        except ValueError:
            return {"ok": False, "state": "conflict", "code": "invalid-uid", "read_only": True}
        if numeric_uid not in state.uids:
            return {"ok": False, "state": "missing", "code": "uid-missing", "read_only": True}
        raw_headers = self._session().uid_fetch_headers(numeric_uid)
        message = BytesParser(policy=policy.default).parsebytes(raw_headers, headersonly=True)
        subject = str(make_header(decode_header(str(message.get("Subject") or ""))))
        message_id = str(message.get("Message-ID") or "").strip()
        if expected_subject and subject != expected_subject:
            return {"ok": False, "state": "conflict", "code": "subject-mismatch", "read_only": True}
        if expected_message_id and message_id != expected_message_id:
            return {"ok": False, "state": "conflict", "code": "message-id-mismatch", "read_only": True}
        return {
            "ok": True,
            "state": "validated",
            "read_only": True,
            "locator": {
                "resource_id": self.resource_id,
                "folder_id": folder.folder_id,
                "folder": folder.name,
                "uidvalidity": state.uidvalidity,
                "uid": str(numeric_uid),
            },
        }

    def scan_folder(self, folder: BackfillFolder, *, previous_cursor: str, max_messages: int) -> Any:
        del previous_cursor
        state = self.snapshot(folder)
        if len(state.uids) > max_messages:
            from .search_reconcile import FolderReconcileScan

            return FolderReconcileScan((), "", False, False, "message-limit")
        from .search_reconcile import FolderReconcileScan, ReconcileObservation

        observations = tuple(
            ReconcileObservation(mailbox_id=str(uid), uid=str(uid)) for uid in state.uids
        )
        cursor = f"uidvalidity={state.uidvalidity};uidnext={state.uidnext};count={len(state.uids)}"
        return FolderReconcileScan(observations, cursor, True, True)

    def capability_probe(self, *, probe_raw_fetch: bool = True) -> dict[str, Any]:
        started = time.monotonic()
        try:
            capabilities = self.capabilities()
            folders = self.inventory()
            total = 0
            uid_min: int | None = None
            uid_max: int | None = None
            raw_fetch = False
            assurance: set[str] = set()
            states: list[ImapFolderState] = []
            for folder in folders:
                state = self._state(folder)
                states.append(state)
                total += len(state.uids)
                assurance.add(state.folder_identity_assurance)
                if state.uids:
                    uid_min = state.uids[0] if uid_min is None else min(uid_min, state.uids[0])
                    uid_max = state.uids[-1] if uid_max is None else max(uid_max, state.uids[-1])
                    if probe_raw_fetch and not raw_fetch:
                        payload = self.fetch_raw(
                            folder,
                            BackfillEnvelope(str(state.uids[0]), str(state.uids[0])),
                        )
                        raw_fetch = isinstance(payload, bytes)
            commands = list(self._session().commands)
            return {
                "ok": True,
                "connector": "native-imap-readonly",
                "read_only": True,
                "writes_imap": False,
                "sets_seen": False,
                "capabilities": capabilities.to_dict(),
                "evidence": {
                    "tls": "verified",
                    "uid": "verified-complete-snapshot" if states else "verified-empty-account",
                    "uidvalidity": "verified" if all(item.uidvalidity for item in states) else "unknown",
                    "uidnext": "verified" if all(item.uidnext for item in states) else "unknown",
                    "raw_fetch": (
                        "verified-body-peek" if raw_fetch else "unknown-not-probed"
                        if not probe_raw_fetch else "failed"
                    ),
                    "highest_modseq": (
                        "observed" if states and all(item.highest_modseq for item in states)
                        else "unknown"
                    ),
                    "condstore": "advertised" if capabilities.condstore else "not-advertised",
                    "qresync": "advertised-not-exercised" if capabilities.qresync else "not-advertised",
                    "idle": "advertised-not-exercised" if capabilities.idle else "not-advertised",
                    "objectid": (
                        "advertised-not-exercised" if capabilities.folder_stable_id else "not-advertised"
                    ),
                    "utf8_accept": (
                        "advertised-not-enabled" if "UTF8=ACCEPT" in self._capabilities else "not-advertised"
                    ),
                },
                "folder_identity_assurance": sorted(assurance) or [str(FolderIdentityAssurance.UNKNOWN)],
                "aggregates": {
                    "folder_count": len(folders),
                    "message_count": total,
                    "uid_min": uid_min,
                    "uid_max": uid_max,
                },
                "probe": {
                    "raw_fetch_verified": raw_fetch if probe_raw_fetch else None,
                    "commands": commands,
                    "forbidden_commands_sent": sorted(set(commands) & FORBIDDEN_IMAP_COMMANDS),
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                },
            }
        except ImapInventoryError as exc:
            return {
                "ok": False,
                "connector": "native-imap-readonly",
                "read_only": True,
                "writes_imap": False,
                "error": {"category": exc.category, "detail": exc.safe_detail},
            }
        except TimeoutError:
            return {
                "ok": False,
                "connector": "native-imap-readonly",
                "read_only": True,
                "writes_imap": False,
                "error": {"category": "timeout", "detail": "IMAP-Zeitlimit"},
            }
        except Exception:
            return {
                "ok": False,
                "connector": "native-imap-readonly",
                "read_only": True,
                "writes_imap": False,
                "error": {"category": "protocol-unexpected", "detail": "Unerwartete IMAP-Antwort"},
            }


@contextmanager
def native_backend(
    config: Config,
    *,
    config_paths: list[Path] | None = None,
    password_file: Path = FIXED_IMAP_PASSWORD_FILE,
    transport_factory: TransportFactory = StdlibImapTransport,
    total_timeout_seconds: float | None = None,
) -> Iterator[NativeImapInventoryBackend]:
    settings = load_imap_settings(
        config,
        config_paths=config_paths,
        password_file=password_file,
    )
    if total_timeout_seconds is not None:
        if not 0 < total_timeout_seconds <= 604_800:
            raise ValueError("total_timeout_seconds ist ungueltig")
        settings = replace(
            settings,
            total_timeout_seconds=float(total_timeout_seconds),
        )
    backend = NativeImapInventoryBackend(
        settings,
        transport_factory=transport_factory,
    )
    try:
        yield backend
    finally:
        backend.close()
