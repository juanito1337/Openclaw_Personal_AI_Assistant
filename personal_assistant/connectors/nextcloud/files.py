from __future__ import annotations

import hashlib
import json
import posixpath
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...config import AssistantConfig
from ...incremental_sync import RemoteObject, StageTelemetry, plan_batch
from ...knowledge import KnowledgeIndexer
from ...storage import AssistantStorage
from .client import NextcloudClient, NextcloudError
from .xmlutil import DAV, parse_multistatus, q


@dataclass(slots=True, frozen=True)
class RemoteFile:
    href: str
    path: str
    name: str
    is_collection: bool
    content_type: str
    size: int
    etag: str
    modified_at: str


class NextcloudFiles:
    _MANAGED_REGISTER = re.compile(r"(?:^|/)(20\d{2}|21\d{2})/Rechnungen_\1\.csv$")
    _MANAGED_REGISTER_HEADER = (
        "Status;Rechnungsdatum;Eingangsdatum;Rechnungsnummer;Rechnungssteller;Kategorie;"
        "Nettobetrag;USt-Betrag;Bruttobetrag;Währung;Fälligkeitsdatum;Erkennung;Konfidenz;"
        "Nextcloud-Pfad;Originaldatei;SHA256"
    )

    def __init__(self, config: AssistantConfig, client: NextcloudClient) -> None:
        self.config = config
        self.client = client

    @staticmethod
    def clean_path(value: str) -> str:
        path = PurePosixPath("/" + str(value or "").replace("\\", "/").lstrip("/"))
        parts = [part for part in path.parts if part not in {"", "/", "."}]
        if any(part == ".." for part in parts):
            raise ValueError("Ungueltiger Nextcloud-Pfad")
        return "/".join(parts)

    def files_root(self) -> str:
        username = urllib.parse.quote(self.client.username, safe="")
        return f"/remote.php/dav/files/{username}/"

    def list_folder(self, path: str) -> list[RemoteFile]:
        clean = self.clean_path(path)
        target = self.files_root() + self._quote(clean)
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<d:propfind xmlns:d='DAV:'><d:prop><d:displayname/><d:resourcetype/>
<d:getcontenttype/><d:getcontentlength/><d:getetag/><d:getlastmodified/>
</d:prop></d:propfind>"""
        response = self.client.request("PROPFIND", target, data=body, headers={"Depth": "1"}, expected={207})
        items: list[RemoteFile] = []
        normalized_target = urllib.parse.urlparse(response.url).path.rstrip("/") + "/"
        for item in parse_multistatus(response.data):
            item_path = urllib.parse.unquote(urllib.parse.urlparse(item.href).path)
            if item_path.rstrip("/") == normalized_target.rstrip("/"):
                continue
            raw_type = item.raw_properties.get(q(DAV, "resourcetype"))
            is_collection = raw_type is not None and raw_type.find(q(DAV, "collection")) is not None
            relative = self._relative_from_href(item.href)
            name = item.properties.get(q(DAV, "displayname")) or posixpath.basename(relative.rstrip("/"))
            try:
                size = int(item.properties.get(q(DAV, "getcontentlength"), "0") or 0)
            except ValueError:
                size = 0
            items.append(
                RemoteFile(
                    href=item.href,
                    path=relative,
                    name=name,
                    is_collection=is_collection,
                    content_type=item.properties.get(q(DAV, "getcontenttype"), ""),
                    size=size,
                    etag=item.properties.get(q(DAV, "getetag"), "").strip('"'),
                    modified_at=item.properties.get(q(DAV, "getlastmodified"), ""),
                )
            )
        return items

    def download(self, path: str, *, expected_etag: str = "") -> bytes:
        clean = self.clean_path(path)
        etag = str(expected_etag or "").strip()
        if "\r" in etag or "\n" in etag:
            raise ValueError("Ungueltiger Nextcloud-ETag")
        if etag and not (etag.startswith('"') or etag.startswith('W/"')):
            etag = f'"{etag}"'
        response = self.client.request(
            "GET",
            self.files_root() + self._quote(clean),
            headers={"If-Match": etag} if etag else None,
            expected={200, 412} if etag else {200},
        )
        if response.status == 412:
            raise NextcloudError("Nextcloud-Datei wurde seit der Auswahl geaendert; bitte erneut auflisten")
        return response.data

    def exists(self, path: str) -> bool:
        """Return whether a WebDAV resource exists without modifying it."""
        clean = self.clean_path(path)
        if not clean:
            return True
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<d:propfind xmlns:d='DAV:'><d:prop><d:resourcetype/></d:prop></d:propfind>"""
        response = self.client.request(
            "PROPFIND",
            self.files_root() + self._quote(clean),
            data=body,
            headers={"Depth": "0"},
            expected={207, 404},
        )
        return response.status == 207

    def ensure_folder(self, path: str) -> None:
        clean = self.clean_path(path)
        current: list[str] = []
        for part in clean.split("/") if clean else []:
            current.append(part)
            target = self.files_root() + self._quote("/".join(current))
            response = self.client.request("MKCOL", target)
            if response.status not in {201, 405}:
                raise NextcloudError(f"Ordner konnte nicht angelegt werden: HTTP {response.status}")

    def upload_new(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        clean = self.clean_path(path)
        response = self.client.request(
            "PUT",
            self.files_root() + self._quote(clean),
            data=data,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(data)),
                "If-None-Match": "*",
            },
        )
        if response.status not in {200, 201, 204}:
            if response.status == 412:
                raise NextcloudError("Datei existiert bereits; Ueberschreiben ist verboten")
            raise NextcloudError(f"Datei-Upload fehlgeschlagen: HTTP {response.status} {response.reason}")

    def _etag(self, path: str) -> str:
        clean = self.clean_path(path)
        body = b"""<?xml version='1.0' encoding='utf-8'?>
<d:propfind xmlns:d='DAV:'><d:prop><d:getetag/></d:prop></d:propfind>"""
        response = self.client.request(
            "PROPFIND",
            self.files_root() + self._quote(clean),
            data=body,
            headers={"Depth": "0"},
            expected={207, 404},
        )
        if response.status == 404:
            return ""
        for item in parse_multistatus(response.data):
            value = item.properties.get(q(DAV, "getetag"), "").strip()
            if value:
                return value
        return ""

    def replace_managed_invoice_register(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str,
        expected_sha256: str,
    ) -> None:
        """Create or conditionally replace only the fixed yearly invoice CSV.

        This is the sole controlled overwrite path in the assistant. It uses an
        ETag precondition so concurrent edits are never silently lost.
        """
        clean = self.clean_path(path)
        if not self._MANAGED_REGISTER.search(clean):
            raise ValueError("Verwaltetes Rechnungsregister besitzt keinen erlaubten Jahrespfad")
        if len(data) > 50_000_000:
            raise ValueError("Rechnungsregister ist unerwartet gross")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("SHA-256 des Rechnungsregisters stimmt nicht")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Rechnungsregister ist kein UTF-8-CSV") from exc
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if first_line != self._MANAGED_REGISTER_HEADER:
            raise ValueError("Rechnungsregister besitzt nicht das kontrollierte CSV-Schema")

        parent = str(PurePosixPath(clean).parent)
        if parent not in {"", "."}:
            self.ensure_folder(parent)
        etag = self._etag(clean)
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
        }
        if etag:
            headers["If-Match"] = etag
        else:
            headers["If-None-Match"] = "*"
        response = self.client.request(
            "PUT",
            self.files_root() + self._quote(clean),
            data=data,
            headers=headers,
        )
        if response.status not in {200, 201, 204}:
            if response.status == 412:
                raise NextcloudError("Rechnungsregister wurde parallel geaendert; Aktualisierung abgebrochen")
            raise NextcloudError(
                f"Rechnungsregister-Upload fehlgeschlagen: HTTP {response.status} {response.reason}"
            )

    def move_new(self, source: str, destination: str) -> None:
        source_clean = self.clean_path(source)
        destination_clean = self.clean_path(destination)
        if not source_clean or not destination_clean:
            raise ValueError("Quelle und Ziel duerfen nicht leer sein")
        if source_clean == destination_clean:
            return
        parent = str(PurePosixPath(destination_clean).parent)
        if parent not in {"", "."}:
            self.ensure_folder(parent)
        destination_url = self.client.validate_url() + self.files_root() + self._quote(destination_clean)
        response = self.client.request(
            "MOVE",
            self.files_root() + self._quote(source_clean),
            headers={"Destination": destination_url, "Overwrite": "F"},
        )
        if response.status not in {201, 204}:
            if response.status == 412:
                raise NextcloudError("Ziel existiert bereits; Ueberschreiben ist verboten")
            raise NextcloudError(f"Verschieben fehlgeschlagen: HTTP {response.status} {response.reason}")

    def sync_index(
        self,
        storage: AssistantStorage,
        indexer: KnowledgeIndexer,
        *,
        resource_id: str,
        roots: tuple[str, ...],
        max_items: int,
        max_depth: int,
        batch_size: int = 100,
    ) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "folders": 0,
            "files": 0,
            "indexed": 0,
            "unchanged": 0,
            "moved": 0,
            "removed": 0,
            "skipped_large": 0,
            "errors": 0,
            "resume_required": False,
            "cursor_reset": False,
            "authoritative": False,
        }
        telemetry = StageTelemetry()
        telemetry.start("discovery")
        queue: list[tuple[str, int]] = [(self.clean_path(root), 0) for root in roots]
        seen: set[str] = set()
        remote_files: list[RemoteFile] = []
        while queue and len(remote_files) < max_items:
            folder, depth = queue.pop(0)
            if folder in seen or depth > max_depth:
                continue
            seen.add(folder)
            try:
                entries = self.list_folder(folder)
            except Exception as exc:
                stats["errors"] = int(stats["errors"]) + 1
                if not storage.core_read_only:
                    storage.audit(
                        "nextcloud.files.list_failed",
                        {"path": folder, "error": str(exc)},
                        resource_id=resource_id,
                    )
                continue
            stats["folders"] = int(stats["folders"]) + 1
            for entry in entries:
                if entry.is_collection:
                    if depth < max_depth:
                        queue.append((entry.path, depth + 1))
                    continue
                remote_files.append(entry)
                stats["files"] = int(stats["files"]) + 1
                if entry.size > self.config.search.max_file_bytes:
                    stats["skipped_large"] = int(stats["skipped_large"]) + 1
                if len(remote_files) >= max_items:
                    break
        telemetry.stop("discovery")
        authoritative = not queue and int(stats["errors"]) == 0
        stats["authoritative"] = authoritative
        state = storage.get_sync_state(resource_id, "files")
        plan = plan_batch(
            [RemoteObject(item.path, item.etag, item.modified_at) for item in remote_files],
            cursor=str(state["cursor"] or "") if state is not None else "",
            batch_size=batch_size,
        )
        stats["resume_required"] = plan.resume_required
        stats["cursor_reset"] = plan.cursor_reset
        by_path = {item.path: item for item in remote_files}
        batch_errors = 0
        for remote in plan.objects:
            entry = by_path[remote.remote_id]
            if entry.size > self.config.search.max_file_bytes:
                continue
            telemetry.start("metadata_compare")
            inventory = storage.get_sync_inventory(resource_id, "files", entry.path)
            existing = storage.get_document(resource_id, entry.path)
            if entry.etag and (
                inventory is not None
                and str(inventory["etag"] or "") == entry.etag
                or existing is not None
                and str(existing["etag"] or "") == entry.etag
            ):
                if inventory is None:
                    storage.upsert_sync_inventory(
                        resource_id=resource_id,
                        scope="files",
                        remote_id=entry.path,
                        source_id=entry.path,
                        source_type="nextcloud-file",
                        etag=entry.etag,
                        modified_at=entry.modified_at,
                    )
                stats["unchanged"] = int(stats["unchanged"]) + 1
                telemetry.add("metadata_hits")
                telemetry.stop("metadata_compare")
                continue
            moved = storage.find_sync_inventory_by_etag(resource_id, "files", entry.etag)
            if moved is not None and str(moved["remote_id"]) != entry.path:
                previous_source = str(moved["source_id"])
                relocated = storage.update_document_locator(
                    resource_id=resource_id,
                    source_id=previous_source,
                    new_source_id=entry.path,
                    uri=self.client.validate_url() + entry.href,
                    title=entry.name,
                    modified_at=entry.modified_at,
                    etag=entry.etag,
                    metadata={"path": entry.path, "size": entry.size},
                )
                if relocated:
                    storage.move_sync_inventory(
                        resource_id=resource_id,
                        scope="files",
                        previous_remote_id=str(moved["remote_id"]),
                        remote_id=entry.path,
                        source_id=entry.path,
                        source_type="nextcloud-file",
                        etag=entry.etag,
                        modified_at=entry.modified_at,
                    )
                    stats["moved"] = int(stats["moved"]) + 1
                    telemetry.add("moves")
                    telemetry.stop("metadata_compare")
                    continue
            telemetry.add("metadata_misses")
            telemetry.stop("metadata_compare")
            try:
                telemetry.start("download")
                data = self.download(entry.path)
                telemetry.add("download_bytes", len(data))
                telemetry.stop("download")
                telemetry.start("parse")
                changed = indexer.index_binary_document(
                    resource_id=resource_id,
                    source_type="nextcloud-file",
                    source_id=entry.path,
                    uri=self.client.validate_url() + entry.href,
                    title=entry.name,
                    filename=entry.name,
                    data=data,
                    mime_type=entry.content_type,
                    modified_at=entry.modified_at,
                    etag=entry.etag,
                    metadata={"path": entry.path, "size": entry.size},
                )
                telemetry.stop("parse")
                telemetry.start("index_update")
                telemetry.stop("index_update")
                telemetry.start("commit")
                storage.upsert_sync_inventory(
                    resource_id=resource_id,
                    scope="files",
                    remote_id=entry.path,
                    source_id=entry.path,
                    source_type="nextcloud-file",
                    etag=entry.etag,
                    modified_at=entry.modified_at,
                    sha256=hashlib.sha256(data).hexdigest(),
                )
                telemetry.stop("commit")
                key = "indexed" if changed else "unchanged"
                stats[key] = int(stats[key]) + 1
            except Exception as exc:
                telemetry.stop_running()
                batch_errors += 1
                stats["errors"] = int(stats["errors"]) + 1
                if not storage.core_read_only:
                    storage.audit(
                        "nextcloud.file.index_failed",
                        {"path": entry.path, "error": str(exc)},
                        resource_id=resource_id,
                    )
        complete = plan.complete and authoritative and batch_errors == 0
        if complete:
            telemetry.start("commit")
            removed = storage.reconcile_sync_inventory(
                resource_id=resource_id,
                scope="files",
                remote_ids={item.path for item in remote_files},
            )
            telemetry.stop("commit")
            stats["removed"] = removed
        stats["telemetry"] = telemetry.to_dict()
        storage.set_sync_state(
            resource_id,
            "files",
            cursor=plan.cursor()
            if batch_errors == 0
            else (str(state["cursor"] or "") if state is not None else ""),
            status="ok" if complete else ("partial" if batch_errors or not authoritative else "in-progress"),
            detail=json.dumps(stats, ensure_ascii=False, sort_keys=True),
            data_changed=bool(int(stats["indexed"]) or int(stats["moved"]) or int(stats["removed"])),
        )
        return stats

    def _relative_from_href(self, href: str) -> str:
        path = urllib.parse.unquote(urllib.parse.urlparse(href).path)
        root = urllib.parse.unquote(self.files_root())
        marker = root.rstrip("/") + "/"
        if marker not in path:
            return path.strip("/")
        return path.split(marker, 1)[1].strip("/")

    @staticmethod
    def _quote(value: str) -> str:
        return "/".join(urllib.parse.quote(part, safe="") for part in value.split("/") if part)
