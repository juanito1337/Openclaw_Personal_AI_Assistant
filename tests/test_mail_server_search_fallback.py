from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mail_agent.himalaya import HimalayaClient
from mail_agent.models import Envelope
from personal_assistant.adapters.mail import MailMoveService
from personal_assistant.mail_hybrid_search import MailHybridSearch
from personal_assistant.models import Resource
from personal_assistant.policy import PolicyEngine
from personal_assistant.registry import ResourceRegistry
from personal_assistant.storage import AssistantStorage
from personal_assistant.tool_settings import MailMoveToolSettings


class FalseEmptyHimalayaLikeConnector:
    def __init__(self, *, server_rows: list[Envelope] | None = None) -> None:
        self.folders = ["INBOX", "Agent/Weitergeleitet"]
        self.server_rows = list(server_rows or [])
        self.metadata = {
            "INBOX": [],
            "Agent/Weitergeleitet": [
                Envelope(
                    "91",
                    "Ihr Angebot 424242",
                    "Synthetischer Vertrieb",
                    "angebot@hass-hatje.example.invalid",
                    "2026-08-21T07:29:00+00:00",
                    "2026-08-21T07:29:00+00:00",
                )
            ],
        }
        self.search_calls: list[tuple[str, tuple[str, ...]]] = []
        self.list_calls: list[tuple[str, int | None]] = []

    @staticmethod
    def search_contract() -> dict[str, object]:
        return HimalayaClient.search_contract()

    def list_folders(self) -> tuple[list[str], str]:
        return list(self.folders), ""

    def search_envelopes(
        self,
        folder: str,
        terms: list[str],
        *,
        limit: int = 50,
    ) -> tuple[list[Envelope], str]:
        self.search_calls.append((folder, tuple(terms)))
        return list(self.server_rows)[:limit] if folder == "INBOX" else [], ""

    def list_envelopes(
        self,
        folder: str,
        limit: int | None = None,
    ) -> tuple[list[Envelope], str]:
        self.list_calls.append((folder, limit))
        return list(self.metadata.get(folder, []))[:limit], ""


def _service(
    root: Path,
    client: FalseEmptyHimalayaLikeConnector,
) -> tuple[MailMoveService, AssistantStorage]:
    registry = ResourceRegistry(root / "resources.toml")
    registry.resources["mail-agent"] = Resource(
        id="mail-agent",
        kind="tool",
        connector="local",
        permissions=("read", "move", "forward"),
    )
    storage = AssistantStorage(root / "assistant.sqlite3")
    return (
        MailMoveService(
            MailMoveToolSettings(enabled=True),
            registry,
            PolicyEngine(root / "policies.toml", registry),
            storage,
            client,  # type: ignore[arg-type]
        ),
        storage,
    )


def test_false_empty_server_query_finds_moved_mail_in_bounded_metadata_fallback(
    tmp_path: Path,
) -> None:
    client = FalseEmptyHimalayaLikeConnector()
    service, storage = _service(tmp_path, client)
    try:
        for query in (
            "Hass",
            "Hatje",
            "Hass Hatje",
            "Hass und Hatje",
            "hass-hatje.example.invalid",
            "angebot@hass-hatje.example.invalid",
            "Ihr Angebot 424242",
        ):
            result = service.search_messages(query, limit=50)
            assert result["count"] == 1
            assert result["messages"][0]["folder"] == "Agent/Weitergeleitet"
            assert result["messages"][0]["mailbox_id"] == "91"
            assert result["messages"][0]["match_source"] == "bounded-envelope-metadata"
            assert result["complete"] is False
            assert result["metadata_fallback"]["used"] is True
            assert result["metadata_fallback"]["scanned_folders"] == 2
    finally:
        storage.close()


def test_himalaya_zero_result_never_proves_absence_or_body_coverage(tmp_path: Path) -> None:
    client = FalseEmptyHimalayaLikeConnector()
    client.metadata["Agent/Weitergeleitet"] = []
    service, storage = _service(tmp_path, client)
    try:
        result = service.search_messages("nur-im-body", limit=50)
    finally:
        storage.close()

    assert result["ok"] is True
    assert result["count"] == 0
    assert result["complete"] is False
    assert result["search_scope"]["server_query_authoritative"] is False
    assert result["search_scope"]["body_search_verified"] is False
    assert result["filter_limitations"] == [
        "server-query-not-authoritative",
        "body-search-not-verified",
        "bounded-envelope-metadata-only",
    ]
    assert result["results_may_be_truncated"] is False


def test_unverified_provider_hit_is_positive_but_does_not_claim_a_body_match(
    tmp_path: Path,
) -> None:
    provider_hit = Envelope(
        "72",
        "Neutrale Nachricht",
        "Synthetic Sender",
        "sender@example.invalid",
        "2026-08-21T07:00:00+00:00",
    )
    client = FalseEmptyHimalayaLikeConnector(server_rows=[provider_hit])
    service, storage = _service(tmp_path, client)
    try:
        result = service.search_messages("body-only-provider-hit", limit=50)
    finally:
        storage.close()

    assert result["count"] == 1
    assert result["complete"] is False
    assert result["metadata_fallback"]["used"] is False
    assert all(item["body_match_verified"] is False for item in result["messages"])
    assert "body-search-not-verified" in result["filter_limitations"]


def test_hybrid_server_result_propagates_search_limits_and_metadata_evidence(
    tmp_path: Path,
) -> None:
    client = FalseEmptyHimalayaLikeConnector()
    service, storage = _service(tmp_path, client)
    try:
        result = MailHybridSearch(
            storage,
            service,
            SimpleNamespace(),
        ).search("Hass und Hatje", mode="server", limit=50)
    finally:
        storage.close()

    assert result["backend"] == "server"
    assert result["count"] == 1
    assert result["complete"] is False
    assert result["coverage"]["authoritative"] is False
    assert result["filter_limitations"] == [
        "server-query-not-authoritative",
        "body-search-not-verified",
        "bounded-envelope-metadata-only",
    ]
    assert result["metadata_fallback"]["used"] is True
    assert result["results"][0]["match"]["reasons"] == [
        "bounded-envelope-metadata"
    ]
    assert result["results"][0]["match"]["body_verified"] is False
