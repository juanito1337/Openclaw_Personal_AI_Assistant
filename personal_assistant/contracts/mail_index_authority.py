from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class FolderIdentityAssurance(StrEnum):
    SERVER_STABLE = "server-stable"
    SNAPSHOT_STABLE = "snapshot-stable"
    UNKNOWN = "unknown"


class MailSearchDecision(StrEnum):
    MATCHES = "matches"
    NO_MATCH = "no-match"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class MailSearchEvidence:
    match_count: int
    complete: bool
    authoritative: bool
    fresh: bool
    folder_errors: tuple[str, ...] = ()
    filter_limitations: tuple[str, ...] = ()
    results_may_be_truncated: bool = False

    def decision(self) -> MailSearchDecision:
        if self.match_count > 0:
            return MailSearchDecision.MATCHES
        if (
            self.complete
            and self.authoritative
            and self.fresh
            and not self.folder_errors
            and not self.filter_limitations
            and not self.results_may_be_truncated
        ):
            return MailSearchDecision.NO_MATCH
        return MailSearchDecision.INCONCLUSIVE

    def to_contract(self) -> dict[str, Any]:
        decision = self.decision()
        negative = decision is MailSearchDecision.NO_MATCH
        return {
            "decision": str(decision),
            "absence_proven": negative,
            "negative_claim_allowed": negative,
            "complete": self.complete,
            "freshness": "fresh" if self.fresh else "stale-or-unknown",
            "coverage": {
                "authoritative": self.authoritative,
                "complete": self.complete,
            },
            "folder_errors": list(self.folder_errors),
            "filter_limitations": list(self.filter_limitations),
            "results_may_be_truncated": self.results_may_be_truncated,
            "answer_contract": (
                "negative-claim-permitted"
                if negative
                else "report-matches" if decision is MailSearchDecision.MATCHES
                else "negative-claim-prohibited-report-inconclusive"
            ),
        }


@dataclass(frozen=True, slots=True)
class FolderSnapshotEvidence:
    list_complete: bool
    snapshot_complete: bool
    uidvalidity_stable: bool
    folder_list_stable: bool
    server_mailbox_id: str = ""

    def assurance(self) -> FolderIdentityAssurance:
        if not (
            self.list_complete
            and self.snapshot_complete
            and self.uidvalidity_stable
            and self.folder_list_stable
        ):
            return FolderIdentityAssurance.UNKNOWN
        if self.server_mailbox_id:
            return FolderIdentityAssurance.SERVER_STABLE
        return FolderIdentityAssurance.SNAPSHOT_STABLE

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["folder_identity_assurance"] = str(self.assurance())
        result["coverage_complete"] = self.assurance() is not FolderIdentityAssurance.UNKNOWN
        return result
