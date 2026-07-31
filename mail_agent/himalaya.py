from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import tomllib
from pathlib import Path

from .command import CommandResult, CommandRunner
from .config import Config
from .models import Envelope, OperationResult
from .utils import atomic_write_bytes, decode_header_value


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class HimalayaClient:
    def __init__(self, config: Config, runner: CommandRunner, dry_run: bool = False) -> None:
        self.config = config
        self.runner = runner
        self.dry_run = dry_run
        self.log = logging.getLogger(__name__)

    def _prefix(self) -> list[str]:
        command = [self.config.mailbox.himalaya_binary]
        if self.config.mailbox.account:
            command += ["--account", self.config.mailbox.account]
        return command

    def _run_variants(self, variants: list[list[str]]) -> CommandResult:
        last = CommandResult([], 1, "", "Kein Kommando ausgefuehrt")
        for args in variants:
            last = self.runner.run(self._prefix() + args)
            if last.ok:
                return last
        return last

    def list_folders(self) -> tuple[list[str], str]:
        result = self._run_variants([
            ["folder", "list", "--output", "json"],
            ["folder", "list", "-o", "json"],
            ["folder", "list"],
        ])
        if not result.ok:
            return [], result.combined
        text = result.stdout.strip()
        try:
            payload = json.loads(text)
            folders: list[str] = []
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, str):
                        folders.append(item)
                    elif isinstance(item, dict):
                        name = item.get("name") or item.get("folder") or item.get("path")
                        if name:
                            folders.append(str(name))
            elif isinstance(payload, dict):
                values = payload.get("folders") or payload.get("data") or []
                for item in values:
                    if isinstance(item, str):
                        folders.append(item)
                    elif isinstance(item, dict) and item.get("name"):
                        folders.append(str(item["name"]))
            if folders:
                return folders, ""
        except json.JSONDecodeError:
            pass
        folders = []
        for line in text.splitlines():
            value = line.strip().lstrip("* ")
            if value and not value.lower().startswith(("name", "folder")):
                folders.append(value)
        return folders, ""

    def ensure_folders(self, folders: list[str]) -> list[OperationResult]:
        existing, error = self.list_folders()
        if error:
            return [OperationResult(False, "folder-list-error", error)]
        existing_folded = {name.casefold() for name in existing}
        expanded: list[str] = []
        for folder in folders:
            parts = [part for part in folder.split("/") if part]
            for index in range(1, len(parts) + 1):
                candidate = "/".join(parts[:index])
                if candidate not in expanded:
                    expanded.append(candidate)
        results: list[OperationResult] = []
        for folder in expanded:
            if folder.casefold() in existing_folded:
                results.append(OperationResult(True, "exists", destination=folder))
                continue
            if self.dry_run:
                results.append(OperationResult(True, "would-create", destination=folder))
                continue
            result = self._run_variants([
                ["folder", "add", folder],
                ["folder", "create", folder],
            ])
            results.append(OperationResult(result.ok, "created" if result.ok else "create-failed", result.combined, folder))
        return results

    def list_envelopes(self, folder: str, limit: int | None = None) -> tuple[list[Envelope], str]:
        page_size = min(limit or self.config.mailbox.page_size, self.config.mailbox.page_size)
        result = self._run_variants([
            ["envelope", "list", "--folder", folder, "--page-size", str(page_size), "--output", "json"],
            ["envelope", "list", "--folder", folder, "--page-size", str(page_size), "-o", "json"],
            ["envelope", "list", "--folder", folder, "--output", "json"],
            ["envelope", "list", "--folder", folder, "-o", "json"],
        ])
        return self._parse_envelopes(result, limit=limit, folder=folder)

    def search_envelopes(
        self,
        folder: str,
        terms: list[str],
        *,
        limit: int = 50,
    ) -> tuple[list[Envelope], str]:
        """Search a complete folder through Himalaya's backend query API."""
        clean_terms = [str(term).strip() for term in terms if str(term).strip()]
        if not clean_terms:
            return [], "Suchbegriff darf nicht leer sein"
        page_size = max(1, min(int(limit), 200))
        query: list[str] = []
        for index, term in enumerate(clean_terms):
            if index:
                query.append("and")
            query.extend([
                "(", "(", "from", term, "or", "subject", term, ")",
                "or", "body", term, ")",
            ])
        result = self._run_variants([
            [
                "envelope", "list", "--folder", folder, "--page-size", str(page_size),
                "--output", "json", *query, "order", "by", "date", "desc",
            ],
            [
                "envelope", "list", "--folder", folder, "--page-size", str(page_size),
                "-o", "json", *query, "order", "by", "date", "desc",
            ],
        ])
        return self._parse_envelopes(result, limit=page_size, folder=folder)

    @staticmethod
    def _parse_envelopes(
        result: CommandResult,
        *,
        limit: int | None,
        folder: str,
    ) -> tuple[list[Envelope], str]:
        if not result.ok:
            return [], result.combined
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return [], f"Ungueltige Himalaya-JSON-Ausgabe fuer {folder}: {exc}"
        if isinstance(payload, dict):
            payload = payload.get("envelopes") or payload.get("data") or []
        if not isinstance(payload, list):
            return [], f"Unerwartete Himalaya-Ausgabe fuer {folder}"
        envelopes: list[Envelope] = []
        for item in payload[:limit] if limit else payload:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            sender = item.get("from") or item.get("sender") or {}
            if isinstance(sender, list):
                sender = sender[0] if sender else {}
            if isinstance(sender, str):
                sender = {"name": sender}
            if not isinstance(sender, dict):
                sender = {}
            envelopes.append(Envelope(
                mailbox_id=str(item.get("id")),
                subject=decode_header_value(item.get("subject")),
                sender_name=decode_header_value(sender.get("name")),
                sender_addr=str(sender.get("addr") or sender.get("address") or ""),
                date=str(item.get("date") or ""),
                received_at=str(
                    item.get("internalDate")
                    or item.get("internal_date")
                    or item.get("receivedAt")
                    or item.get("received_at")
                    or item.get("date")
                    or ""
                ),
            ))
        return envelopes, ""

    def export_message(self, folder: str, message_id: str, destination: Path) -> OperationResult:
        variants = [
            ["message", "export", message_id, "--folder", folder, "--full"],
            ["message", "export", message_id, "--folder", folder],
            ["message", "export", "--folder", folder, message_id, "--full"],
            ["message", "export", "--folder", folder, message_id],
        ]
        if folder == self.config.mailbox.source_folder:
            variants += [
                ["message", "export", message_id, "--full"],
                ["message", "export", message_id],
            ]
        last_error = ""
        for variant in variants:
            result = self.runner.run_bytes(self._prefix() + variant)
            if result.ok and result.stdout:
                atomic_write_bytes(destination, result.stdout)
                return OperationResult(True, "exported", path=str(destination))
            last_error = result.error_text
        return OperationResult(False, "export-failed", last_error or "Leere Exportausgabe")

    def move_message(self, source_folder: str, destination_folder: str, message_id: str) -> OperationResult:
        if source_folder == destination_folder:
            return OperationResult(True, "already-there", destination=destination_folder)
        if self.dry_run:
            return OperationResult(True, "would-move", destination=destination_folder)
        result = self._run_variants([
            ["message", "move", message_id, destination_folder, "--folder", source_folder],
            ["message", "move", "--folder", source_folder, destination_folder, message_id],
            ["message", "move", destination_folder, message_id, "--folder", source_folder],
        ])
        return OperationResult(result.ok, "moved" if result.ok else "move-failed", result.combined, destination_folder)

    @staticmethod
    def _default_config_path() -> Path | None:
        xdg_root = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
        for candidate in (
            xdg_root / "himalaya/config.toml",
            Path("~/.config/himalaya/config.toml").expanduser(),
            Path("~/.himalayarc").expanduser(),
        ):
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _environment_config_paths() -> list[Path]:
        value = os.environ.get("HIMALAYA_CONFIG", "").strip()
        if not value:
            default = HimalayaClient._default_config_path()
            return [default] if default else []
        paths: list[Path] = []
        for item in value.split(os.pathsep):
            item = item.strip()
            if item:
                paths.append(Path(item).expanduser().resolve())
        return paths

    def _account_for_override(self, config_paths: list[Path]) -> str:
        configured = self.config.mailbox.account.strip()
        if configured:
            return configured

        accounts: dict[str, dict[str, object]] = {}
        for path in config_paths:
            if not path.is_file():
                continue
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                continue
            section = data.get("accounts")
            if not isinstance(section, dict):
                continue
            for name, settings in section.items():
                if isinstance(settings, dict):
                    accounts[str(name)] = settings

        defaults = [name for name, settings in accounts.items() if settings.get("default") is True]
        if len(defaults) == 1:
            return defaults[0]
        if len(accounts) == 1:
            return next(iter(accounts))
        raise RuntimeError(
            "Himalaya-Konto fuer den sicheren Versand ist nicht eindeutig. "
            "Bitte mailbox.account in mail_agent/config.toml setzen."
        )

    def forwarding_safety(self) -> tuple[bool, str]:
        try:
            config_paths = self._environment_config_paths()
            if not config_paths:
                raise RuntimeError("Himalaya-Konfiguration nicht gefunden")
            account = self._account_for_override(config_paths)
        except RuntimeError as exc:
            return False, str(exc)
        return (
            True,
            f"Sicherer ZIP-Versand ohne IMAP-Sent-Kopie ist fuer Konto {account} vorbereitet",
        )

    def _no_save_copy_environment(self) -> tuple[dict[str, str], Path]:
        config_paths = self._environment_config_paths()
        if not config_paths:
            raise RuntimeError(
                "Himalaya-Konfiguration nicht gefunden; sichere Versandueberlagerung kann nicht erstellt werden"
            )
        account = self._account_for_override(config_paths)
        override_dir = self.config.forwarding.payload_dir / ".himalaya-overrides"
        override_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix="no-save-copy-", suffix=".toml", dir=override_dir)
        override_path = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                # JSON quoting is compatible with TOML basic quoted keys here.
                handle.write(f"[accounts.{json.dumps(account, ensure_ascii=False)}]\n")
                handle.write("message.send.save-copy = false\n")
            override_path.chmod(0o600)
        except Exception:
            override_path.unlink(missing_ok=True)
            raise

        env = os.environ.copy()
        existing = os.environ.get("HIMALAYA_CONFIG", "").strip()
        base = existing if existing else str(config_paths[0])
        env["HIMALAYA_CONFIG"] = base + os.pathsep + str(override_path)
        return env, override_path

    @staticmethod
    def _clean_error(detail: str) -> str:
        return ANSI_ESCAPE_RE.sub("", detail).strip()

    @staticmethod
    def _looks_like_post_send_copy_failure(detail: str) -> bool:
        text = HimalayaClient._clean_error(detail).casefold()
        return (
            "cannot add imap message" in text
            or "header limit reached" in text
            or "failed to save a copy" in text
            or ("save-copy" in text and "imap" in text)
        )

    def send_template(self, template: str, *, save_copy: bool | None = None) -> OperationResult:
        if self.dry_run:
            return OperationResult(True, "would-send")

        env: dict[str, str] | None = None
        override_path: Path | None = None
        if save_copy is False:
            try:
                env, override_path = self._no_save_copy_environment()
            except RuntimeError as exc:
                return OperationResult(False, "send-config-error", str(exc))

        try:
            result = self.runner.run(
                self._prefix() + ["template", "send"],
                input_text=template,
                env=env,
            )
        finally:
            if override_path is not None:
                override_path.unlink(missing_ok=True)
                try:
                    override_path.parent.rmdir()
                except OSError:
                    pass

        if result.ok:
            return OperationResult(True, "sent")
        detail = self._clean_error(result.combined)
        if self._looks_like_post_send_copy_failure(detail):
            return OperationResult(
                False,
                "delivery-uncertain",
                (
                    "SMTP-Versand kann bereits erfolgt sein; nur das Speichern der Kopie per IMAP ist "
                    "fehlgeschlagen. Nicht automatisch erneut senden. Originalfehler: " + detail
                )[:4000],
            )
        return OperationResult(False, "send-failed", detail)
