from __future__ import annotations

import hashlib
import logging
import zipfile
from contextlib import suppress
from email.utils import parseaddr
from pathlib import Path

from .config import Config
from .himalaya import HimalayaClient
from .models import Classification, OperationResult, ParsedMessage
from .utils import atomic_write_bytes, clean_single_line, safe_filename


class Forwarder:
    def __init__(self, config: Config, himalaya: HimalayaClient) -> None:
        self.config = config
        self.himalaya = himalaya
        self.log = logging.getLogger(__name__)

    def forward(self, message: ParsedMessage, classification: Classification) -> OperationResult:
        if not self.config.forwarding.enabled:
            return OperationResult(False, "forwarding-disabled", "Weiterleitung ist deaktiviert")

        subject, body = self._subject_and_body(message, classification)
        payload_path: Path | None = None
        zip_path: Path | None = None

        if self.config.forwarding.attach_original_eml:
            payload_name = safe_filename(message.stable_key.replace(":", "-"), "message") + ".eml"
            payload_path = self.config.forwarding.payload_dir / payload_name
            zip_path = payload_path.with_suffix(".eml.zip")
            atomic_write_bytes(payload_path, message.raw)
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(payload_path, arcname="original-message.eml")
            body += (
                "\n\nDie Originalmail ist unveraendert als ZIP angehaengt. "
                "Dadurch werden verschachtelte Mail-Header nicht vom IMAP-Server verarbeitet."
            )
            template = self._template(
                subject,
                body,
                message,
                zip_path,
                "application/zip",
                "original-message.eml.zip",
            )
        else:
            template = self._template(subject, body, message)

        # Agenten-Weiterleitungen werden bewusst ohne IMAP-Kopie im Gesendet-Ordner
        # verschickt. Der Mailserver kann grosse, verschachtelte Originalmails beim
        # anschliessenden IMAP APPEND ablehnen, obwohl SMTP bereits erfolgreich war.
        result = self.himalaya.send_template(template, save_copy=False)
        if zip_path is not None:
            result.path = str(zip_path)
        if result.ok:
            self._cleanup(*(path for path in (payload_path, zip_path) if path is not None))
            result.path = ""
        return result

    @staticmethod
    def _cleanup(*paths: Path) -> None:
        for path in paths:
            with suppress(OSError):
                path.unlink(missing_ok=True)

    def _subject_and_body(self, message: ParsedMessage, classification: Classification) -> tuple[str, str]:
        prefix = self.config.forwarding.subject_prefix.format(importance=classification.importance).strip()
        subject = clean_single_line(f"{prefix} Fwd: {message.subject}", 900)
        sender = clean_single_line(f"{message.sender_name} <{message.sender_addr}>" if message.sender_name else message.sender_addr)
        attachment_names = ", ".join(item.filename for item in message.attachments) or "keine"
        body_lines = [
            "Warum diese Mail weitergeleitet wurde:",
            classification.reason or "Als wichtig eingestuft.",
            "",
            "Zusammenfassung:",
            classification.summary or "Keine separate Zusammenfassung verfuegbar.",
            "",
            "Empfohlene Aktion:",
            classification.expected_action or "Mail lesen und bei Bedarf reagieren.",
            "",
            f"Absender: {sender}",
            f"Originalbetreff: {clean_single_line(message.subject, 900)}",
            f"Eingangsdatum: {clean_single_line(message.received_at or message.date, 300)}",
            f"Original-Anhaenge: {clean_single_line(attachment_names, 1500)}",
            "",
            "Die vollstaendige Originalmail inklusive ihrer MIME-Struktur und aller Original-Anhaenge ist beigefuegt.",
        ]
        return subject, "\n".join(body_lines).replace("<#", "< #")

    def _outgoing_identity(self, message: ParsedMessage) -> tuple[str, str]:
        digest = hashlib.sha256(message.stable_key.encode("utf-8", errors="replace")).hexdigest()
        from_addr = parseaddr(self.config.mailbox.from_header)[1]
        domain = from_addr.rsplit("@", 1)[-1].strip().lower() if "@" in from_addr else "mail-agent.local"
        if not domain or any(char.isspace() for char in domain):
            domain = "mail-agent.local"
        return f"<mail-agent-forward-{digest[:32]}@{domain}>", digest

    def _template(
        self,
        subject: str,
        body: str,
        message: ParsedMessage,
        path: Path | None = None,
        mime_type: str = "",
        attachment_name: str = "",
    ) -> str:
        outgoing_id, source_digest = self._outgoing_identity(message)
        template_lines = [
            f"From: {self.config.mailbox.from_header}",
            f"To: {self.config.mailbox.forward_to}",
            f"Subject: {subject}",
            f"Message-ID: {outgoing_id}",
            f"X-Mail-Agent-Source: sha256={source_digest}",
            "",
        ]
        if path is not None:
            template_lines += [
                "<#multipart type=mixed>",
                "<#part type=text/plain>",
                body,
                f"<#part type={mime_type} filename={path} name={attachment_name}><#/part>",
                "<#/multipart>",
            ]
        else:
            template_lines.append(body)
        return "\n".join(template_lines) + "\n"

    def send_plain(self, subject: str, body: str) -> OperationResult:
        return self.send_plain_to(subject, body)

    def send_plain_to(
        self,
        subject: str,
        body: str,
        *,
        recipient: str | None = None,
        reply_to: str | None = None,
    ) -> OperationResult:
        headers = [
            f"From: {self.config.mailbox.from_header}",
            f"To: {recipient or self.config.mailbox.forward_to}",
            f"Subject: {clean_single_line(subject, 900)}",
        ]
        if reply_to:
            headers.append(f"Reply-To: {clean_single_line(reply_to, 500)}")
        template = "\n".join(headers) + "\n\n" + body.replace("<#", "< #") + "\n"
        return self.himalaya.send_template(template)
