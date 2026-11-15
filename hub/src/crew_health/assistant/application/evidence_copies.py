"""E10 frozen read-only contract, structural until its held provider is selected and bound.

The host adapter converts each owner value to these exact consumer variants; unknown values never admit support.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

type AllowedUse = Literal["risk_context", "patient_instruction", "routine_basis", "limit_basis"]


@dataclass(frozen=True, slots=True)
class PassageArtifact:
    passage_id: str
    extraction_id: str
    start: int
    end: int
    text_sha256: str
    text: str
    section: str
    page: int | None


@dataclass(frozen=True, slots=True)
class ReleaseMembership:
    release_id: str
    passage: PassageArtifact
    source_ids: tuple[str, ...]
    license_kind: str
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]
    use_status: tuple[tuple[AllowedUse, str], ...]
    applicability: tuple[tuple[str, tuple[str, ...] | None], ...]
    content_sha256: str
    simulation_only: bool = False


@dataclass(frozen=True, slots=True)
class Eligible:
    passage: ReleaseMembership
    release_id: str
    purpose: AllowedUse


@dataclass(frozen=True, slots=True)
class NotApprovedForUse:
    release_id: str
    passage_id: str
    purpose: AllowedUse


@dataclass(frozen=True, slots=True)
class InReleaseHistory:
    release_id: str
    passage_id: str


@dataclass(frozen=True, slots=True)
class NotInRelease:
    release_id: str
    passage_id: str


@dataclass(frozen=True, slots=True)
class ReleaseUnavailable:
    release_id: str
    reason: str


type ApprovedRead = Eligible | NotApprovedForUse | InReleaseHistory | NotInRelease | ReleaseUnavailable


class EvidenceQueryPort(Protocol):
    def read_approved(self, release_id: str, passage_id: str, use: AllowedUse) -> ApprovedRead: ...
