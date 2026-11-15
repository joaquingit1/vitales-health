from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.knowledge.domain.catalog import ArtifactRef, Extraction
from crew_health.knowledge.domain.passage import PathwayCard, ReleaseMembership
from crew_health.knowledge.domain.review import Approval, Review


@dataclass(frozen=True, slots=True)
class ActivePointer:
    release_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_id: str
    manifest_sha256: str
    registry_sha256: str
    state: Literal["staged", "verified", "active", "superseded", "rejected"]
    revision: int
    file_state: str
    artifact: ArtifactRef
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewExport:
    export_id: str
    format_version: str
    registry_sha256: str
    policy_sha256: str
    effective_contract_sha256: str
    overlay_sha256: str | None
    previous_release_id: str | None
    built_at: datetime
    author: str
    extractions: tuple[Extraction, ...]
    approvals: tuple[Approval, ...]
    reviews: tuple[Review, ...]
    memberships: tuple[ReleaseMembership, ...]
    cards: tuple[PathwayCard, ...]
    discrepancies: tuple[Discrepancy, ...]
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]


@dataclass(frozen=True, slots=True)
class Discrepancy:
    discrepancy_id: str
    previous_passage_id: str
    current_passage_id: str
    relation: Literal["contradicts", "revises"]
    review_ids: tuple[str, ...]
    receipt_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseBuildResult:
    release_id: str | None
    artifact: ArtifactRef | None
    manifest: bytes | None
    findings: tuple[str, ...]
    discrepancies: tuple[Discrepancy, ...]


@dataclass(frozen=True, slots=True)
class PruneReport:
    pruned: tuple[str, ...]
    retained: tuple[str, ...]
