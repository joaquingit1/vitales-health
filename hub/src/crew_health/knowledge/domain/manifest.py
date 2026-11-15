from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash
from crew_health.knowledge.domain.export import DiscrepancyInput, EmbeddingPin
from crew_health.knowledge.domain.extraction import Extraction
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.release import PathwayCard, ReleaseMembership, UseStatus


@dataclass(frozen=True, slots=True)
class CardMembership:
    card: PathwayCard
    uses: tuple[UseStatus, ...]
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]


@dataclass(frozen=True, slots=True)
class MemberSnapshot:
    passage: PassageArtifact
    extraction: Extraction
    source_ids: tuple[str, ...]
    license: LicenseRecord
    uses: tuple[UseStatus, ...]
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]
    environments: frozenset[str] | None
    section: str
    withdrawn: bool
    simulation_only: bool

    def bind(self, release_id: str) -> ReleaseMembership:
        return ReleaseMembership(
            release_id,
            self.passage,
            self.extraction,
            self.source_ids,
            self.license,
            self.uses,
            self.clinical_review,
            self.environments,
            self.section,
            self.withdrawn,
            self.simulation_only,
        )


@dataclass(frozen=True, slots=True)
class ReleaseContent:
    memberships: tuple[MemberSnapshot, ...]
    cards: tuple[CardMembership, ...]
    discrepancies: tuple[DiscrepancyInput, ...]


@dataclass(frozen=True, slots=True)
class ManifestMember:
    name: str
    sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        if (
            not self.name
            or "\\" in self.name
            or ":" in self.name
            or any(p in ("", ".", "..") for p in self.name.split("/"))
        ):
            raise EvidenceInvalid("bundle_path")
        require_hash(self.sha256)
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise EvidenceInvalid("bundle_member_bytes")


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    release_id: str
    format_version: str
    registry_sha256: str
    policy_sha256: str
    effective_contract_sha256: str
    decision_overlay_sha256: str | None
    review_export_sha256: str
    previous_release_id: str | None
    build_at: datetime
    author: str
    embedding_pin: EmbeddingPin | None
    members: tuple[ManifestMember, ...]

    def __post_init__(self) -> None:
        require_id(self.release_id)
        require_id(self.author)
        require_utc(self.build_at)
        for value in (
            self.registry_sha256,
            self.policy_sha256,
            self.effective_contract_sha256,
            self.review_export_sha256,
        ):
            require_hash(value)
        if self.decision_overlay_sha256 is not None:
            require_hash(self.decision_overlay_sha256)
        if self.previous_release_id is not None:
            require_id(self.previous_release_id)
        if (
            not self.format_version
            or not self.members
            or len({m.name for m in self.members}) != len(self.members)
        ):
            raise EvidenceInvalid("manifest_members")
        if any(m.name in ("manifest.json", "signature.json") for m in self.members):
            raise EvidenceInvalid("manifest_self_reference")


@dataclass(frozen=True, slots=True)
class SignedManifest:
    key_id: str
    signature: str
    manifest_sha256: str

    def __post_init__(self) -> None:
        require_id(self.key_id)
        require_hash(self.manifest_sha256)
        if not self.signature:
            raise EvidenceInvalid("signature")
