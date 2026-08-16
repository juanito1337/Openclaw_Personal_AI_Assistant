from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath

from personal_assistant.antivirus import HostAntivirus
from personal_assistant.tool_settings import InvoiceToolSettings

from .assistant_bridge import PersonalAssistantActionBridge
from .attachments import ExtractedAttachment, extract_pdf_attachments
from .config import Config
from .invoice_extract import InvoiceExtractor, InvoiceMetadata, amount_to_cents
from .invoice_register import InvoiceRegister
from .models import Classification, OperationResult, ParsedMessage
from .storage import Storage
from .utils import safe_filename

_FILENAME_POSITIVE = (
    "rechnung", "invoice", "faktura", "facture", "tax-invoice", "tax_invoice",
    "invoice-", "invoice_", "rechnung-", "rechnung_",
)
_FILENAME_NEGATIVE = (
    "lieferschein", "delivery-note", "delivery_note", "agb", "terms", "widerruf",
    "bestellbestaetigung", "order-confirmation", "order_confirmation", "receipt", "beleg",
    "gutschrift", "credit-note", "credit_note", "storno", "manual", "produktinformation",
    "datenschutz", "privacy", "angebot", "quotation",
)
_SUBJECT_POSITIVE = (
    "rechnung", "invoice", "faktura", "tax invoice", "rechnungsnummer",
)
_BODY_POSITIVE = (
    "rechnungsnummer", "rechnung nr", "rechnung-nr", "invoice number", "invoice no",
    "zahlungsziel", "faellig am", "fällig am", "faelligkeit", "fälligkeit",
    "rechnungsbetrag", "invoice total", "nettobetrag", "bruttobetrag",
)


@dataclass(slots=True)
class InvoiceDecision:
    attachments: list[ExtractedAttachment]
    confidence: float
    reason: str
    ambiguous: bool = False


class InvoiceManager:
    def __init__(
        self,
        config: Config,
        storage: Storage,
        bridge: PersonalAssistantActionBridge,
        settings: InvoiceToolSettings,
        *,
        antivirus: HostAntivirus | None = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.storage = storage
        self.bridge = bridge
        self.settings = settings
        self.antivirus = antivirus
        self.dry_run = dry_run
        self.log = logging.getLogger(__name__)
        self.extractor = InvoiceExtractor(config.invoices)
        self.register = InvoiceRegister(storage, config.invoices)

    @staticmethod
    def _normalize_name(value: str) -> str:
        return (value or "").strip().casefold().replace("\\", "/").rsplit("/", 1)[-1]

    def detect(self, message: ParsedMessage, classification: Classification) -> InvoiceDecision:
        pdfs = extract_pdf_attachments(message)
        if not pdfs:
            return InvoiceDecision([], 0.0, "Kein PDF-Anhang vorhanden")

        model = classification.invoice
        model_confident = bool(
            model
            and model.is_invoice
            and model.confidence >= self.config.invoices.min_confidence
        )
        model_names = {
            self._normalize_name(name)
            for name in (model.pdf_filenames if model else [])
            if self._normalize_name(name)
        }
        subject = message.subject.casefold()
        body = message.body_text[:12000].casefold()
        subject_signal = any(marker in subject for marker in _SUBJECT_POSITIVE)
        body_signal_count = sum(1 for marker in _BODY_POSITIVE if marker in body)
        strong_mail_signal = subject_signal or body_signal_count >= 2

        selected: list[ExtractedAttachment] = []
        for attachment in pdfs:
            name = self._normalize_name(attachment.filename)
            negative = any(marker in name for marker in _FILENAME_NEGATIVE)
            positive = any(marker in name for marker in _FILENAME_POSITIVE)
            model_named = name in model_names
            if negative and not (model_named and model and model.confidence >= 0.98):
                continue
            if positive or (model_confident and model_named):
                selected.append(attachment)

        if selected:
            confidence = 0.99 if any(
                any(marker in self._normalize_name(item.filename) for marker in _FILENAME_POSITIVE)
                for item in selected
            ) else float(model.confidence if model else 0.95)
            return InvoiceDecision(
                selected,
                confidence,
                (model.reason if model and model.reason else "PDF-Dateiname oder Modellzuordnung weist eindeutig auf eine Rechnung hin"),
            )

        if len(pdfs) == 1 and strong_mail_signal and (
            model_confident or subject_signal or body_signal_count >= 2
        ):
            confidence = max(
                0.95 if subject_signal and body_signal_count >= 1 else 0.90,
                float(model.confidence if model else 0.0),
            )
            if confidence >= self.config.invoices.min_confidence:
                return InvoiceDecision(
                    [pdfs[0]],
                    confidence,
                    model.reason if model and model.reason else "Eine einzelne PDF liegt in einer eindeutig als Rechnung bezeichneten Mail",
                )

        if (model_confident or strong_mail_signal) and len(pdfs) > 1:
            return InvoiceDecision(
                [],
                float(model.confidence if model else 0.80),
                "Mehrere PDFs vorhanden, aber keine einzelne Rechnungsdatei ist eindeutig identifiziert",
                ambiguous=True,
            )
        return InvoiceDecision([], float(model.confidence if model else 0.0), "PDF-Anhang ist nicht sicher als Rechnung erkennbar")

    def process(self, message: ParsedMessage, classification: Classification) -> OperationResult:
        cfg = self.config.invoices
        if not self.settings.enabled:
            return OperationResult(True, "invoice-archive-disabled", "Rechnungsarchivierung ist im zentralen Tool-Setup deaktiviert")
        if not cfg.register_enabled:
            return OperationResult(False, "invoice-register-disabled", "Das verpflichtende Nextcloud-Jahresregister ist deaktiviert")
        if cfg.require_routine and classification.category != "routine":
            return OperationResult(True, "invoice-not-routine", "Nur Routine-Mails werden automatisch als Rechnung archiviert")
        if classification.confidence < self.config.thresholds.routine:
            return OperationResult(True, "invoice-low-mail-confidence", "Routine-Klassifizierung ist nicht sicher genug")

        decision = self.detect(message, classification)
        if decision.ambiguous:
            return OperationResult(True, "invoice-review-required", decision.reason)
        if not decision.attachments:
            return OperationResult(True, "invoice-not-detected", decision.reason)
        if decision.confidence < cfg.min_confidence:
            return OperationResult(True, "invoice-review-required", "Rechnungssicherheit liegt unter dem Schwellwert")

        too_large = [item.filename for item in decision.attachments if item.size > cfg.max_pdf_bytes]
        if too_large:
            return OperationResult(
                False,
                "invoice-too-large",
                "Rechnungs-PDF ueberschreitet die konfigurierte Maximalgroesse: " + ", ".join(too_large),
            )

        bridge_health = self.bridge.health(resource_id=self.settings.resource_id)
        if not bridge_health.ok:
            return OperationResult(False, "invoice-assistant-unavailable", bridge_health.detail)

        uploaded: list[str] = []
        duplicates: list[str] = []
        date_review_required: list[str] = []
        metadata_review_required: list[str] = []
        register_paths: set[str] = set()

        for attachment in decision.attachments:
            scan = (
                self.antivirus.scan_bytes(
                    attachment.data,
                    name=attachment.filename,
                    source_type="invoice-before-nextcloud",
                )
                if self.antivirus is not None
                else None
            )
            if scan is not None and scan.infected:
                return OperationResult(
                    False,
                    "invoice-malware-detected",
                    "Rechnungs-PDF wurde vor dem Nextcloud-Upload blockiert: "
                    + (scan.signature or scan.detail or scan.status),
                )
            if scan is not None and scan.error and self.antivirus is not None and self.antivirus.settings.fail_closed:
                return OperationResult(
                    False,
                    "invoice-antivirus-error",
                    "Rechnungs-PDF wird nicht hochgeladen, weil der Virenscan nicht erfolgreich war: "
                    + (scan.detail or scan.status),
                )

            existing = self.storage.get_invoice(attachment.sha256)
            if existing and str(existing["status"] or "") in {"uploaded", "duplicate"}:
                metadata: InvoiceMetadata | None = None
                if not str(existing["extraction_status"] or ""):
                    metadata = self.extractor.extract(
                        attachment.data,
                        message,
                        filename=attachment.filename,
                        scanner_identity=str(getattr(scan, "scanner_identity", "") or ""),
                    )
                    record = self._metadata_record(message, metadata)
                    if not self.dry_run:
                        self.storage.update_invoice_extraction(attachment.sha256, **record)
                    register_year = int(record["register_year"])
                else:
                    stored_year = existing["register_year"]
                    stored_date = str(existing["invoice_date"] or "")
                    if stored_year:
                        register_year = int(stored_year)
                    elif self._stored_date_confirmed(existing) and len(stored_date) >= 4 and stored_date[:4].isdigit():
                        register_year = int(stored_date[:4])
                    else:
                        register_year = self._message_date(message).year
                if not self.dry_run:
                    register_result = self._sync_register(register_year)
                    if not register_result.ok:
                        return register_result
                    register_paths.add(register_result.path)
                remote_path = str(existing["nextcloud_path"] or attachment.filename)
                duplicates.append(remote_path)
                if metadata is not None:
                    if not metadata.date_confirmed:
                        date_review_required.append(remote_path)
                    elif metadata.status != "confirmed":
                        metadata_review_required.append(remote_path)
                elif not self._stored_date_confirmed(existing):
                    date_review_required.append(remote_path)
                elif str(existing["extraction_status"] or "") not in {"confirmed", "confirmed-manual"}:
                    metadata_review_required.append(remote_path)
                continue

            metadata = self.extractor.extract(
                attachment.data,
                message,
                filename=attachment.filename,
                scanner_identity=str(getattr(scan, "scanner_identity", "") or ""),
            )
            remote_folder = self._target_folder(message, metadata)
            remote_path = f"{remote_folder}/{self._target_filename(message, attachment, metadata)}"
            result = self.bridge.archive_invoice(
                message=message,
                attachment_hash=attachment.sha256,
                data=attachment.data,
                remote_path=remote_path,
                content_type="application/pdf",
                resource_id=self.settings.resource_id,
            )
            if not result.ok:
                if not self.dry_run:
                    self.storage.record_invoice(
                        stable_key=message.stable_key,
                        attachment_hash=attachment.sha256,
                        original_filename=attachment.filename,
                        nextcloud_path=remote_path,
                        size_bytes=attachment.size,
                        status="error",
                        error=result.detail,
                        **self._metadata_record(message, metadata),
                    )
                return result
            if self.dry_run and result.status == "would-archive-invoice":
                uploaded.append(remote_path)
                if not metadata.date_confirmed:
                    date_review_required.append(remote_path)
                elif metadata.status != "confirmed":
                    metadata_review_required.append(remote_path)
                continue

            status = "duplicate" if result.status == "invoice-duplicate" else "uploaded"
            if not self.dry_run:
                self.storage.record_invoice(
                    stable_key=message.stable_key,
                    attachment_hash=attachment.sha256,
                    original_filename=attachment.filename,
                    nextcloud_path=remote_path,
                    size_bytes=attachment.size,
                    status=status,
                    **self._metadata_record(message, metadata),
                )
                register_result = self._sync_register(self._register_year(message, metadata))
                if not register_result.ok:
                    return register_result
                register_paths.add(register_result.path)
            (duplicates if status == "duplicate" else uploaded).append(remote_path)
            if not metadata.date_confirmed:
                date_review_required.append(remote_path)
            elif metadata.status != "confirmed":
                metadata_review_required.append(remote_path)

        paths = [*uploaded, *duplicates]
        destination = self.settings.folder
        register_detail = ""
        if register_paths:
            register_detail = "; Jahres-CSV aktualisiert: " + ", ".join(sorted(register_paths))

        if uploaded:
            if self.dry_run:
                detail = f"Wuerde {len(uploaded)} Rechnungs-PDF(s) ueber den Personal Assistant in Nextcloud archivieren"
                if duplicates:
                    detail += f"; {len(duplicates)} bereits vorhanden"
                if date_review_required:
                    detail += "; Rechnungsdatum muss geprueft werden"
                elif metadata_review_required:
                    detail += "; Zusatzdaten werden mit Status Pruefen registriert"
                return OperationResult(True, "would-archive-invoice", detail, destination=destination, path=", ".join(paths))
            detail = f"{len(uploaded)} Rechnungs-PDF(s) ueber ActionPlan in Nextcloud archiviert"
            if duplicates:
                detail += f"; {len(duplicates)} bereits vorhanden"
            detail += register_detail
            if date_review_required:
                detail += f"; bei {len(date_review_required)} Datei(en) ist das Rechnungsdatum nicht sicher"
                return OperationResult(True, "invoice-archived-review-required", detail, destination=destination, path=", ".join(paths))
            if metadata_review_required:
                detail += f"; {len(metadata_review_required)} CSV-Datensatz/-saetze enthalten noch unvollstaendige Zusatzdaten"
                return OperationResult(True, "invoice-archived-metadata-review", detail, destination=destination, path=", ".join(paths))
            return OperationResult(True, "invoice-archived", detail, destination=destination, path=", ".join(paths))

        detail = f"{len(duplicates)} Rechnungs-PDF(s) waren bereits archiviert" + register_detail
        if date_review_required:
            detail += "; Rechnungsdatum muss geprueft werden"
            return OperationResult(True, "invoice-duplicate-review-required", detail, destination=destination, path=", ".join(paths))
        if metadata_review_required:
            detail += "; Jahres-CSV enthaelt noch unvollstaendige Zusatzdaten"
            return OperationResult(True, "invoice-duplicate-metadata-review", detail, destination=destination, path=", ".join(paths))
        return OperationResult(True, "invoice-duplicate", detail, destination=destination, path=", ".join(paths))

    def _sync_register(self, year: int) -> OperationResult:
        try:
            rendered = self.register.render(year, invoice_folder=self.settings.folder)
            result = self.bridge.sync_invoice_register(
                data=rendered.data,
                year=year,
                remote_path=rendered.path,
                resource_id=self.settings.resource_id,
            )
            if not result.ok:
                self.log.error("Rechnungsregister konnte nicht aktualisiert werden: %s", result.detail)
                return OperationResult(False, "invoice-register-failed", result.detail, path=rendered.path)
            return OperationResult(True, result.status, result.detail, destination=result.destination, path=rendered.path)
        except Exception as exc:
            self.log.error("Rechnungsregister konnte nicht aktualisiert werden: %s", exc)
            return OperationResult(False, "invoice-register-failed", str(exc))

    @staticmethod
    def _stored_date_confirmed(row: object) -> bool:
        try:
            status = str(row["extraction_status"] or "")  # type: ignore[index]
            invoice_date = str(row["invoice_date"] or "")  # type: ignore[index]
            if status in {"confirmed", "confirmed-manual"} and invoice_date:
                return True
            payload = json.loads(str(row["extraction_json"] or "{}"))  # type: ignore[index]
            field = payload.get("invoice_date") or {}
            return bool(field.get("value") and float(field.get("confidence") or 0.0) >= 0.85)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _message_date(self, message: ParsedMessage) -> datetime:
        try:
            value = parsedate_to_datetime(message.received_at or message.date)
            if value is not None:
                return value
        except (TypeError, ValueError, OverflowError):
            pass
        return datetime.now().astimezone()

    def _register_year(self, message: ParsedMessage, metadata: InvoiceMetadata) -> int:
        if metadata.date_confirmed:
            try:
                return int(metadata.invoice_date.value[:4])
            except (TypeError, ValueError):
                pass
        return self._message_date(message).year

    def _metadata_record(self, message: ParsedMessage, metadata: InvoiceMetadata) -> dict[str, object]:
        received = self._message_date(message).date().isoformat()
        return {
            "invoice_date": metadata.invoice_date.value,
            "received_date": received,
            "invoice_number": metadata.invoice_number.value,
            "supplier": metadata.supplier.value,
            "category": metadata.category.value or "Ungeklärt",
            "gross_amount_cents": amount_to_cents(metadata.gross_amount.value),
            "net_amount_cents": amount_to_cents(metadata.net_amount.value),
            "tax_amount_cents": amount_to_cents(metadata.tax_amount.value),
            "currency": metadata.currency.value or "EUR",
            "due_date": metadata.due_date.value,
            "extraction_status": metadata.status,
            "extraction_confidence": metadata.confidence,
            "extraction_method": metadata.method,
            "extraction_json": metadata.to_json(),
            "register_year": self._register_year(message, metadata),
        }

    def _target_folder(self, message: ParsedMessage, metadata: InvoiceMetadata | None = None) -> str:
        base = str(PurePosixPath("/" + self.settings.folder.lstrip("/"))).lstrip("/")
        received = self._message_date(message)
        if metadata is not None and not metadata.date_confirmed:
            review = safe_filename(self.config.invoices.review_subfolder, "Pruefen")
            return f"{base}/{review}/{received:%Y}/{received:%m}"
        if not self.settings.organize_by_year_month:
            return base
        if metadata is not None and metadata.date_confirmed:
            try:
                invoice_date = datetime.strptime(metadata.invoice_date.value, "%Y-%m-%d")
                return f"{base}/{invoice_date:%Y}/{invoice_date:%m}"
            except ValueError:
                pass
        return f"{base}/{received:%Y}/{received:%m}"

    def _target_filename(
        self, message: ParsedMessage, attachment: ExtractedAttachment, metadata: InvoiceMetadata | None = None
    ) -> str:
        received = self._message_date(message)
        if metadata is not None and metadata.date_confirmed:
            date_prefix = metadata.invoice_date.value
            sender_value = metadata.supplier.value or message.sender_domain or message.sender_name or "unbekannt"
            number = safe_filename(metadata.invoice_number.value, "rechnung")[:60]
            sender = safe_filename(sender_value, "unbekannt")[:50]
            return f"{date_prefix}_{sender}_{number}_{attachment.sha256[:10]}.pdf"
        sender = safe_filename(message.sender_domain or message.sender_name or "unbekannt", "unbekannt")[:50]
        stem = attachment.safe_name[:-4] if attachment.safe_name.casefold().endswith(".pdf") else attachment.safe_name
        stem = safe_filename(stem, "rechnung")[:100]
        return f"PRUEFEN_{received:%Y-%m-%d}_{sender}_{stem}_{attachment.sha256[:10]}.pdf"
