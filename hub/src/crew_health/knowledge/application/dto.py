from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class DeclaredSource:
    source_id: str
    title: str
    url: str
    date_text: str | None = None
    access_text: str | None = None
    saved_as: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source_id: str
    title: str
    url: str
    date_text: str | None
    access_text: str | None
    saved_as: str | None
    urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceUnknown:
    source_id: str


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    resolved: tuple[SourceSummary, ...]
    unknown: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Membership:
    inventory_id: str
    source_entry_id: str
    title: str
    source_url: str
    row_sha256: str
    published_mapping: str
    title_match_mapping: str


@dataclass(frozen=True, slots=True)
class Condition:
    condition_id: str
    title: str
    status: Literal["active", "removed_or_split"]
    nasa_url: str
    family: str | None
    workup_source_ids: tuple[str, ...]
    workup_review_required: bool
    assessment: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RegistryCounts:
    active: int
    memberships: int
    inventory_counts: tuple[tuple[str, int], ...]
    unique_diseases: None = None


@dataclass(frozen=True, slots=True)
class ConditionResolution:
    condition: Condition
    nasa_page: str
    form_url: str | None
    plan_id: str | None
    written_for: str | None
    kit_review_status: str | None
    plan_review_status: str | None
    card_review_status: str | None
    config_release_id: str | None
    evidence_release_id: str | None


@dataclass(frozen=True, slots=True)
class Active:
    condition: Condition


@dataclass(frozen=True, slots=True)
class RemovedOrSplit:
    condition: Condition


@dataclass(frozen=True, slots=True)
class UnknownCondition:
    condition_id: str


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    sha256: str
    sources: tuple[SourceSummary, ...]
    conditions: tuple[Condition, ...]
    memberships: tuple[Membership, ...]
    inventory_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class CatalogItem:
    item_id: str
    url: str
    normalized_url: str
    title: str
    revision: int
    source_ids: tuple[str, ...]
    published_date_text: str | None = None


@dataclass(frozen=True, slots=True)
class LicenseRecord:
    license_id: str
    document_version_id: str
    content_sha256: str
    kind: Literal["nasa_public_domain", "permission_documented", "metadata_only"]
    basis: str
    actor_id: str
    at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class CaptureFacts:
    method: str
    complete: bool
    truncated: bool
    byte_count: int
    media_type: str


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    document_version_id: str
    item_id: str
    content_sha256: str
    artifact: ArtifactRef
    facts: CaptureFacts
    captured_by: str
    retrieved_at: datetime
    completeness: str | None
    stage: str
    revision: int
    license: LicenseRecord | None = None


@dataclass(frozen=True, slots=True)
class PageMap:
    page: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Extraction:
    extraction_id: str
    document_version_id: str
    text: str
    text_sha256: str
    page_map: tuple[PageMap, ...]
    page_map_sha256: str
    format_version: str
    extractor: str
    extractor_version: str
    at: datetime


@dataclass(frozen=True, slots=True)
class TextBlock:
    extraction_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class FormPage:
    page: int
    text: str


@dataclass(frozen=True, slots=True)
class Review:
    review_id: str
    extraction_id: str
    text_sha256: str
    kind: str
    verdict: str
    reviewer: str
    supersedes: str | None
    receipt_refs: tuple[str, ...]
    attestation: str | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    extraction_id: str
    text_sha256: str
    purpose: str
    approver: str
    extractor: str
    completeness: str
    source_kind: str
    license_kind: str
    partial_acknowledged: bool
    review_ids: tuple[str, ...]
    receipt_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Eligibility:
    allowed: bool
    reasons: tuple[str, ...]
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]


type AllowedUse = Literal["risk_context", "patient_instruction", "routine_basis", "limit_basis"]

type SearchMode = Literal["exact", "stemmed"]


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
class ApplicabilityField:
    allowed: frozenset[str]
    allow_unknown: bool = False


@dataclass(frozen=True, slots=True)
class ApplicabilityFilter:
    fields: tuple[tuple[str, ApplicabilityField], ...] = ()

    def admits(self, values: tuple[tuple[str, tuple[str, ...] | None], ...]) -> bool:
        known = dict(values)
        return all(
            (rule.allow_unknown if known.get(name) is None else bool(set(known[name] or ()) & rule.allowed))
            for name, rule in self.fields
        )


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
class PathwayCard:
    condition_id: str
    content_sha256: str
    assessment: str
    source_ids: tuple[str, ...]
    passage_ids: tuple[str, ...]
    purposes: tuple[str, ...]
    environments: tuple[str, ...] | None
    authored: bool
    simulation_only: bool
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]


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
class NoActiveRelease:
    reason: str = "no_active_release"


@dataclass(frozen=True, slots=True)
class ReleaseUnavailable:
    release_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class IndexUnavailable:
    release_id: str
    method: str
    reason: str


@dataclass(frozen=True, slots=True)
class QueryRejected:
    reason: str


@dataclass(frozen=True, slots=True)
class RankedPassage:
    rank: int
    method: str
    passage: ReleaseMembership


@dataclass(frozen=True, slots=True)
class Hits:
    release_id: str
    items: tuple[RankedPassage, ...]


@dataclass(frozen=True, slots=True)
class NoMatch:
    release_id: str


@dataclass(frozen=True, slots=True)
class CardFound:
    release_id: str
    card: PathwayCard


type ApprovedRead = Eligible | NotApprovedForUse | InReleaseHistory | NotInRelease | ReleaseUnavailable

type SearchResult = Hits | NoMatch | NoActiveRelease | ReleaseUnavailable | IndexUnavailable | QueryRejected

type CardRead = CardFound | NotInRelease | ReleaseUnavailable

type HistoryRead = tuple[ReleaseMembership, ...] | ReleaseUnavailable


@dataclass(frozen=True, slots=True)
class ItemUpsert:
    url: str
    title: str
    expected_revision: int | None
    provenance_sha256: str
    published_date_text: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    source: DeclaredSource
    expected_revision: int | None
    provenance_sha256: str


@dataclass(frozen=True, slots=True)
class CaptureInput:
    item_id: str
    artifact: ArtifactRef
    facts: CaptureFacts
    retrieved_at: datetime
    expected_revision: int


@dataclass(frozen=True, slots=True)
class CompletenessInput:
    document_version_id: str
    content_sha256: str
    completeness: str
    facts: CaptureFacts
    expected_revision: int


@dataclass(frozen=True, slots=True)
class ExtractionInput:
    document_version_id: str
    text: str
    page_map: tuple[PageMap, ...]
    format_version: str
    extractor_version: str
    expected_revision: int


@dataclass(frozen=True, slots=True)
class StageInput:
    document_version_id: str
    expected_revision: int
    stage: str
    review: Review


@dataclass(frozen=True, slots=True)
class UseStatusInput:
    document_version_id: str
    expected_revision: int
    purpose: AllowedUse
    status: str
    approval: Approval
    reason: str


@dataclass(frozen=True, slots=True)
class WithdrawalInput:
    document_version_id: str
    expected_revision: int
    reason: str


@dataclass(frozen=True, slots=True)
class LicenseInput:
    document_version_id: str
    content_sha256: str
    expected_revision: int
    kind: Literal["nasa_public_domain", "permission_documented", "metadata_only"]
    basis: str


@dataclass(frozen=True, slots=True)
class InstallInput:
    artifact: ArtifactRef
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    signature: bytes | None
    key_id: str | None


@dataclass(frozen=True, slots=True)
class VerifyInput:
    release_id: str
    expected_revision: int


@dataclass(frozen=True, slots=True)
class ActivationInput:
    release_id: str
    expected_revision: int
    expected_active_generation: int
    reason: str


@dataclass(frozen=True, slots=True)
class PruneInput:
    expected_active_generation: int
    release_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscrepancyPage:
    items: tuple[Discrepancy, ...]
    next_cursor: str | None
