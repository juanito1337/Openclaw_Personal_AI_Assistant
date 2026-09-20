from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from ...config import AssistantConfig
from ...extractors import chunks
from ...incremental_sync import RemoteObject, StageTelemetry, plan_batch
from ...storage import AssistantStorage
from .client import NextcloudClient
from .discovery import DiscoveredCollection
from .xmlutil import CARDDAV, DAV, parse_multistatus, q


@dataclass(slots=True, frozen=True)
class Contact:
    uid: str
    name: str
    emails: tuple[str, ...]
    phones: tuple[str, ...]
    organization: str
    raw: str
    href: str = ""
    etag: str = ""
    note: str = ""


@dataclass(slots=True, frozen=True)
class ContactMetadata:
    href: str
    etag: str


class NextcloudContacts:
    def __init__(self, config: AssistantConfig, client: NextcloudClient) -> None:
        self.config = config
        self.client = client

    def list_contacts(self, addressbook: DiscoveredCollection) -> list[Contact]:
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<card:addressbook-query xmlns:d='DAV:' xmlns:card='urn:ietf:params:xml:ns:carddav'>
 <d:prop><d:getetag/><card:address-data/></d:prop>
</card:addressbook-query>"""
        response = self.client.request(
            "REPORT",
            addressbook.href,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        contacts: list[Contact] = []
        for item in parse_multistatus(response.data):
            raw = item.properties.get(q(CARDDAV, "address-data"), "")
            if not raw:
                continue
            contacts.append(
                self._parse_vcard(
                    raw,
                    item.href,
                    href=item.href,
                    etag=item.properties.get(q(DAV, "getetag"), ""),
                )
            )
        return contacts

    def list_contact_metadata(self, addressbook: DiscoveredCollection) -> list[ContactMetadata]:
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<card:addressbook-query xmlns:d='DAV:' xmlns:card='urn:ietf:params:xml:ns:carddav'>
 <d:prop><d:getetag/></d:prop>
</card:addressbook-query>"""
        response = self.client.request(
            "REPORT",
            addressbook.href,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            expected={207},
        )
        return [
            ContactMetadata(
                href=item.href,
                etag=item.properties.get(q(DAV, "getetag"), ""),
            )
            for item in parse_multistatus(response.data)
            if item.href and item.href.rstrip("/") != addressbook.href.rstrip("/")
        ]

    def read_contact(self, href: str, *, fallback_uid: str = "") -> Contact:
        response = self.client.request("GET", href, expected={200})
        return self._parse_vcard(
            response.data.decode("utf-8", errors="replace"),
            fallback_uid or href,
            href=href,
            etag=self._header(response.headers, "ETag"),
        )

    def contact_exists(self, addressbook: DiscoveredCollection, uid: str) -> bool:
        href = self.contact_href(addressbook, uid)
        response = self.client.request("GET", href, expected={200, 404})
        return response.status == 200

    def create_contact(self, addressbook: DiscoveredCollection, vcard: str, uid: str) -> str:
        """Create a vCard without overwriting an existing CardDAV object."""
        href = self.contact_href(addressbook, uid)
        response = self.client.request(
            "PUT",
            href,
            data=vcard.encode("utf-8"),
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                "If-None-Match": "*",
            },
            expected={201, 204, 412},
        )
        if response.status == 412:
            raise FileExistsError(f"CardDAV-Kontakt existiert bereits: {uid}")
        verify = self.read_contact(href, fallback_uid=uid)
        if verify.uid.strip() != uid.strip():
            raise RuntimeError("CardDAV-Kontakt wurde angelegt, UID konnte aber nicht verifiziert werden")
        return href

    def update_contact(
        self,
        addressbook: DiscoveredCollection,
        *,
        href: str,
        uid: str,
        vcard: str,
        etag: str,
    ) -> Contact:
        """Update exactly one existing vCard with optimistic concurrency.

        The CardDAV object must reside below the configured address book. The
        server ETag is sent through ``If-Match`` so a concurrent edit in
        Nextcloud is never overwritten silently.
        """
        safe_href = self._validated_contact_href(addressbook, href)
        current_etag = str(etag or "").strip()
        if not current_etag:
            current_etag = self.read_contact(safe_href, fallback_uid=uid).etag
        if not current_etag:
            raise RuntimeError("CardDAV-Server lieferte keinen ETag; sicheres Kontakt-Update abgebrochen")
        response = self.client.request(
            "PUT",
            safe_href,
            data=vcard.encode("utf-8"),
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                "If-Match": current_etag,
            },
            expected={201, 204, 412},
        )
        if response.status == 412:
            raise RuntimeError(
                "Kontakt wurde zwischenzeitlich geaendert; bitte erneut suchen und die Aenderung wiederholen"
            )
        verified = self.read_contact(safe_href, fallback_uid=uid)
        if verified.uid.strip() != uid.strip():
            raise RuntimeError("CardDAV-Kontakt wurde aktualisiert, UID-Verifikation ist fehlgeschlagen")
        return verified

    @staticmethod
    def contact_href(addressbook: DiscoveredCollection, uid: str) -> str:
        clean_uid = str(uid or "").strip()
        if not clean_uid:
            raise ValueError("Kontakt-UID fehlt")
        filename = urllib.parse.quote(clean_uid, safe="") + ".vcf"
        return addressbook.href.rstrip("/") + "/" + filename

    @staticmethod
    def _validated_contact_href(addressbook: DiscoveredCollection, href: str) -> str:
        clean = str(href or "").strip()
        root = addressbook.href.rstrip("/") + "/"
        if not clean or not clean.startswith(root) or clean == root:
            raise PermissionError("Kontaktobjekt liegt ausserhalb des konfigurierten Adressbuchs")
        return clean

    def sync_index(
        self,
        storage: AssistantStorage,
        addressbooks: list[DiscoveredCollection],
        *,
        batch_size: int = 100,
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "addressbooks": 0,
            "contacts": 0,
            "indexed": 0,
            "unchanged": 0,
            "moved": 0,
            "removed": 0,
            "errors": 0,
            "resume_required": False,
            "cursor_resets": 0,
        }
        telemetry = StageTelemetry()
        for book in addressbooks:
            telemetry.start("discovery")
            try:
                metadata = self.list_contact_metadata(book)
                stats["addressbooks"] = int(stats["addressbooks"]) + 1
            except Exception as exc:
                stats["errors"] = int(stats["errors"]) + 1
                if not storage.core_read_only:
                    storage.audit(
                        "nextcloud.contacts.sync_failed",
                        {"addressbook": book.name, "error": str(exc)},
                        resource_id=book.resource_id,
                    )
                telemetry.stop("discovery")
                continue
            telemetry.stop("discovery")
            state = storage.get_sync_state(book.resource_id, "contacts")
            plan = plan_batch(
                [RemoteObject(item.href, item.etag) for item in metadata],
                cursor=str(state["cursor"] or "") if state is not None else "",
                batch_size=batch_size,
            )
            stats["resume_required"] = bool(stats["resume_required"]) or plan.resume_required
            stats["cursor_resets"] = int(stats["cursor_resets"]) + int(plan.cursor_reset)
            by_href = {item.href: item for item in metadata}
            batch_errors = 0
            for remote in plan.objects:
                item = by_href[remote.remote_id]
                telemetry.start("metadata_compare")
                inventory = storage.get_sync_inventory(book.resource_id, "contacts", item.href)
                if inventory is not None and item.etag and str(inventory["etag"] or "") == item.etag:
                    stats["unchanged"] = int(stats["unchanged"]) + 1
                    telemetry.add("metadata_hits")
                    telemetry.stop("metadata_compare")
                    continue
                moved = storage.find_sync_inventory_by_etag(book.resource_id, "contacts", item.etag)
                if moved is not None and str(moved["remote_id"]) != item.href:
                    storage.move_sync_inventory(
                        resource_id=book.resource_id,
                        scope="contacts",
                        previous_remote_id=str(moved["remote_id"]),
                        remote_id=item.href,
                        source_id=str(moved["source_id"]),
                        source_type="contact",
                        etag=item.etag,
                    )
                    stats["moved"] = int(stats["moved"]) + 1
                    telemetry.add("moves")
                    telemetry.stop("metadata_compare")
                    continue
                telemetry.add("metadata_misses")
                telemetry.stop("metadata_compare")
                try:
                    telemetry.start("download")
                    contact = self.read_contact(
                        item.href,
                        fallback_uid=str(inventory["source_id"] or "") if inventory else "",
                    )
                    telemetry.stop("download")
                    telemetry.start("parse")
                    text = "\n".join(
                        [
                            contact.name,
                            "E-Mail: " + ", ".join(contact.emails),
                            "Telefon: " + ", ".join(contact.phones),
                            "Organisation: " + contact.organization,
                        ]
                    )
                    contact_chunks = chunks(
                        text,
                        size=self.config.search.chunk_chars,
                        overlap=self.config.search.chunk_overlap_chars,
                    )
                    telemetry.stop("parse")
                    telemetry.start("index_update")
                    storage.index_document(
                        source_type="contact",
                        resource_id=book.resource_id,
                        source_id=contact.uid,
                        uri=f"nextcloud-carddav://{urllib.parse.quote(book.name)}/{urllib.parse.quote(contact.uid)}",
                        title=contact.name or contact.uid,
                        mime_type="text/vcard",
                        etag=contact.etag or item.etag,
                        metadata={
                            "emails": list(contact.emails),
                            "phones": list(contact.phones),
                            "organization": contact.organization,
                            "addressbook": book.name,
                        },
                        chunks=contact_chunks,
                    )
                    telemetry.stop("index_update")
                    telemetry.start("commit")
                    storage.upsert_sync_inventory(
                        resource_id=book.resource_id,
                        scope="contacts",
                        remote_id=item.href,
                        source_id=contact.uid,
                        source_type="contact",
                        etag=contact.etag or item.etag,
                    )
                    telemetry.stop("commit")
                    stats["contacts"] = int(stats["contacts"]) + 1
                    stats["indexed"] = int(stats["indexed"]) + 1
                except Exception as exc:
                    telemetry.stop_running()
                    batch_errors += 1
                    stats["errors"] = int(stats["errors"]) + 1
                    if not storage.core_read_only:
                        storage.audit(
                            "nextcloud.contact.index_failed",
                            {"href": item.href, "error": str(exc)},
                            resource_id=book.resource_id,
                        )
            completed = plan.complete and batch_errors == 0
            if completed:
                telemetry.start("commit")
                removed = storage.reconcile_sync_inventory(
                    resource_id=book.resource_id,
                    scope="contacts",
                    remote_ids={item.href for item in metadata},
                )
                telemetry.stop("commit")
                stats["removed"] = int(stats["removed"]) + removed
            storage.set_sync_state(
                book.resource_id,
                "contacts",
                cursor=plan.cursor()
                if batch_errors == 0
                else (str(state["cursor"] or "") if state is not None else ""),
                status="ok" if completed else ("partial" if batch_errors else "in-progress"),
                detail=str({key: value for key, value in stats.items() if key != "telemetry"}),
                data_changed=bool(int(stats["indexed"]) or int(stats["moved"]) or int(stats["removed"])),
            )
        stats["telemetry"] = telemetry.to_dict()
        return stats

    @staticmethod
    def _header(headers, name: str) -> str:
        target = name.casefold()
        for key, value in dict(headers or {}).items():
            if str(key).casefold() == target:
                return str(value or "").strip()
        return ""

    @staticmethod
    def _unescape(value: str) -> str:
        return (
            str(value or "")
            .replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
        )

    @classmethod
    def _parse_vcard(
        cls,
        raw: str,
        fallback_uid: str,
        *,
        href: str = "",
        etag: str = "",
    ) -> Contact:
        unfolded = re.sub(r"\r?\n[ \t]", "", raw)
        values: dict[str, list[str]] = {}
        for line in unfolded.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            base = key.split(";", 1)[0].rsplit(".", 1)[-1].upper()
            values.setdefault(base, []).append(cls._unescape(value.strip()))
        uid = (values.get("UID") or [fallback_uid])[0]
        name = (values.get("FN") or values.get("N") or [uid])[0].replace(";", " ").strip()
        emails = tuple(sorted({value.casefold() for value in values.get("EMAIL", []) if "@" in value}))
        phones = tuple(sorted({value for value in values.get("TEL", []) if value}))
        organization = (values.get("ORG") or [""])[0].replace(";", " ").strip()
        note = (values.get("NOTE") or [""])[0].strip()
        return Contact(
            uid=uid,
            name=name,
            emails=emails,
            phones=phones,
            organization=organization,
            raw=raw,
            href=href,
            etag=etag,
            note=note,
        )
