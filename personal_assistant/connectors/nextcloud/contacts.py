from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from ...config import AssistantConfig
from ...extractors import chunks
from ...storage import AssistantStorage
from .client import NextcloudClient
from .discovery import DiscoveredCollection
from .xmlutil import CARDDAV, DAV, q, parse_multistatus


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

    def sync_index(self, storage: AssistantStorage, addressbooks: list[DiscoveredCollection]) -> dict[str, int]:
        stats = {"addressbooks": 0, "contacts": 0, "indexed": 0, "errors": 0}
        for book in addressbooks:
            try:
                contacts = self.list_contacts(book)
                stats["addressbooks"] += 1
            except Exception as exc:
                stats["errors"] += 1
                storage.audit("nextcloud.contacts.sync_failed", {"addressbook": book.name, "error": str(exc)}, resource_id=book.resource_id)
                continue
            for contact in contacts:
                text = "\n".join([
                    contact.name,
                    "E-Mail: " + ", ".join(contact.emails),
                    "Telefon: " + ", ".join(contact.phones),
                    "Organisation: " + contact.organization,
                ])
                storage.index_document(
                    source_type="contact",
                    resource_id=book.resource_id,
                    source_id=contact.uid,
                    uri=f"nextcloud-carddav://{urllib.parse.quote(book.name)}/{urllib.parse.quote(contact.uid)}",
                    title=contact.name or contact.uid,
                    mime_type="text/vcard",
                    metadata={
                        "emails": list(contact.emails),
                        "phones": list(contact.phones),
                        "organization": contact.organization,
                        "addressbook": book.name,
                    },
                    chunks=chunks(text, size=self.config.search.chunk_chars, overlap=self.config.search.chunk_overlap_chars),
                )
                stats["contacts"] += 1
                stats["indexed"] += 1
            storage.set_sync_state(book.resource_id, "contacts", status="ok", detail=f"{len(contacts)} Kontakte")
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
