from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal

from crew_health.kernel.artifacts import ArtifactReference as ArtifactReference
from crew_health.kernel.clock import require_utc
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import require_id
from crew_health.knowledge.domain.catalog import DocumentVersion
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_hash
from crew_health.knowledge.domain.extraction import Extraction
from crew_health.knowledge.domain.license import LicenseRecord
from crew_health.knowledge.domain.policy import AllowedUse
from crew_health.knowledge.domain.registry import require_source_id
from crew_health.knowledge.domain.release import PathwayCard
from crew_health.knowledge.domain.review import PathwayCardTarget, ReviewTarget, SourceReview


@dataclass(frozen=True, slots=True)
class Span:
    char_start: int
    char_end: int
    section: str

    def __post_init__(self) -> None:
        if type(self.char_start) is not int or type(self.char_end) is not int:
            raise EvidenceInvalid("span")
        if not 0 <= self.char_start < self.char_end or not self.section.strip():
            raise EvidenceInvalid("span")


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    sequence: int
    state: Literal["active", "superseded", "withdrawn"]
    successor_ref: str | None
    reason: str | None
    at: datetime
    audit_receipt: str

    def __post_init__(self) -> None:
        require_utc(self.at)
        require_id(self.audit_receipt)
        if type(self.sequence) is not int or self.sequence < 1:
            raise EvidenceInvalid("approval_sequence")
        if self.state not in ("active", "superseded", "withdrawn"):
            raise EvidenceInvalid("approval_state")
        if self.state != "active" and (not self.reason or not self.reason.strip()):
            raise EvidenceInvalid("approval_reason")
        if self.state == "superseded" and self.successor_ref is None:
            raise EvidenceInvalid("approval_successor")
        if self.successor_ref is not None:
            require_id(self.successor_ref)


@dataclass(frozen=True, slots=True)
class UseApproval:
    approval_id: str
    target: ReviewTarget | PathwayCardTarget
    use: AllowedUse
    spans: tuple[Span, ...]
    partial_acknowledged: bool
    assessment_id: str | None
    technical_check_id: str | None
    clinical_review_id: str | None
    license_id: str | None
    policy_sha256: str
    approved_by: str
    approved_at: datetime
    revision: int
    events: tuple[ApprovalEvent, ...]

    def __post_init__(self) -> None:
        for value in (self.approval_id, self.approved_by):
            require_id(value)
        for value in (self.assessment_id, self.technical_check_id, self.license_id):
            if value is not None:
                require_id(value)
        if self.clinical_review_id is not None:
            require_id(self.clinical_review_id)
        require_utc(self.approved_at)
        require_hash(self.policy_sha256)
        if self.use not in ("risk_context", "patient_instruction", "routine_basis", "limit_basis"):
            raise EvidenceInvalid("allowed_use")
        if (isinstance(self.target, ReviewTarget) and not self.spans) or len(set(self.spans)) != len(
            self.spans
        ):
            raise EvidenceInvalid("approval_spans")
        if isinstance(self.target, PathwayCardTarget) and self.spans:
            raise EvidenceInvalid("approval_spans")
        if type(self.partial_acknowledged) is not bool:
            raise EvidenceInvalid("partial_acknowledged")
        if not self.events or type(self.revision) is not int or self.revision != len(self.events):
            raise EvidenceInvalid("approval_revision")
        if tuple(event.sequence for event in self.events) != tuple(range(1, self.revision + 1)):
            raise EvidenceInvalid("approval_sequence")
        if self.events[0].state != "active" or any(event.state == "active" for event in self.events[1:]):
            raise EvidenceInvalid("approval_transition")


@dataclass(frozen=True, slots=True)
class ReleaseTarget:
    document: DocumentVersion
    extraction: Extraction
    original: ArtifactReference
    license: LicenseRecord
    source_ids: tuple[str, ...]
    source_kind: str
    environments: frozenset[str] | None

    def __post_init__(self) -> None:
        self.document.require_extractable()
        if self.document.document_version_id != self.extraction.document_version_id:
            raise EvidenceInvalid("extraction_target")
        if (
            self.document.content_sha256 != self.original.sha256
            or self.license.content_sha256 != self.original.sha256
        ):
            raise EvidenceInvalid("license_target")
        if not self.source_ids or len(set(self.source_ids)) != len(self.source_ids):
            raise EvidenceInvalid("source_aliases")
        for source_id in self.source_ids:
            require_source_id(source_id)
        if not self.license.permits_text:
            raise EvidenceInvalid("text_license")
        if not self.source_kind.strip():
            raise EvidenceInvalid("source_kind")

    def review_target(self) -> ReviewTarget:
        return ReviewTarget(
            self.document.document_version_id,
            self.document.content_sha256,
            self.extraction.extraction_id,
            self.extraction.text_sha256,
            self.extraction.page_map_sha256,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingPin:
    model_id: str
    revision: str
    sha256: str
    dims: int

    def __post_init__(self) -> None:
        require_id(self.model_id)
        require_hash(self.sha256)
        if not self.revision or type(self.dims) is not int or self.dims < 1:
            raise EvidenceInvalid("embedding_pin")


@dataclass(frozen=True, slots=True)
class SpanRef:
    release_id: str
    passage_id: str

    def __post_init__(self) -> None:
        require_id(self.release_id)
        require_id(self.passage_id)


@dataclass(frozen=True, slots=True)
class CurrentSpan:
    extraction_id: str
    char_start: int
    char_end: int
    section: str

    def __post_init__(self) -> None:
        require_id(self.extraction_id)
        Span(self.char_start, self.char_end, self.section)


@dataclass(frozen=True, slots=True)
class ClaimRevision:
    claim_id: str
    context: Literal["documentation", "passage_support", "pathway_card", "discrepancy"]
    text: str
    source_ids: tuple[str, ...]
    source_spans: tuple[SpanRef | CurrentSpan, ...]
    limitation: str
    author: str
    claim_rev_sha256: str
    use: AllowedUse

    def __post_init__(self) -> None:
        require_id(self.claim_id)
        require_id(self.author)
        if not self.text.strip() or not self.limitation.strip() or not self.source_ids:
            raise EvidenceInvalid("claim_qualification")
        if self.context not in ("documentation", "passage_support", "pathway_card", "discrepancy"):
            raise EvidenceInvalid("claim_context")
        for source_id in self.source_ids:
            require_source_id(source_id)
        if self.use not in ("risk_context", "patient_instruction", "routine_basis", "limit_basis"):
            raise EvidenceInvalid("allowed_use")
        body = {
            "claim_id": self.claim_id,
            "context": self.context,
            "text": self.text,
            "source_ids": self.source_ids,
            "source_spans": [
                {"release_id": span.release_id, "passage_id": span.passage_id} if isinstance(span, SpanRef)
                else {"extraction_id": span.extraction_id, "char_start": span.char_start, "char_end": span.char_end, "section": span.section}
                for span in self.source_spans],
            "limitation": self.limitation,
            "author": self.author,
            "use": self.use,
        }
        if sha256(canonical_json(body)).hexdigest() != require_hash(self.claim_rev_sha256):
            raise EvidenceInvalid("claim_hash")


@dataclass(frozen=True, slots=True)
class ClaimReview:
    claim_review_id: str
    claim_rev_sha256: str
    reviewer: str
    verdict: Literal["supported", "partially_supported", "unsupported", "contradicted", "revoked"]
    qualification: str | None
    relation: Literal["contradicts", "revises"] | None
    at: datetime
    supersedes_claim_review_id: str | None
    audit_receipt: str

    def __post_init__(self) -> None:
        for value in (self.claim_review_id, self.reviewer, self.audit_receipt):
            require_id(value)
        require_hash(self.claim_rev_sha256)
        require_utc(self.at)
        if self.verdict not in ("supported", "partially_supported", "unsupported", "contradicted", "revoked"):
            raise EvidenceInvalid("claim_verdict")
        if self.verdict == "partially_supported" and (
            not self.qualification or not self.qualification.strip()
        ):
            raise EvidenceInvalid("claim_qualification")
        if self.relation not in (None, "contradicts", "revises"):
            raise EvidenceInvalid("claim_relation")


@dataclass(frozen=True, slots=True)
class DiscrepancyInput:
    claim_id: str
    claim_rev_sha256: str
    superseded: SpanRef
    current: CurrentSpan
    relation: Literal["contradicts", "revises"]
    recorded_by: str

    def __post_init__(self) -> None:
        require_id(self.claim_id)
        require_id(self.recorded_by)
        require_hash(self.claim_rev_sha256)
        if self.relation not in ("contradicts", "revises") or self.superseded == self.current:
            raise EvidenceInvalid("discrepancy_relation")


@dataclass(frozen=True, slots=True)
class ReviewExport:
    export_id: str
    format_version: str
    effective_contract_sha256: str
    decision_overlay_sha256: str | None
    registry_sha256: str
    policy_sha256: str
    previous_release_id: str | None
    build_at: datetime
    author: str
    embedding_pin: EmbeddingPin | None
    targets: tuple[ReleaseTarget, ...]
    source_reviews: tuple[SourceReview, ...]
    review_heads: tuple[str, ...]
    approvals: tuple[UseApproval, ...]
    audit_receipts: tuple[str, ...]
    cards: tuple[PathwayCard, ...]
    claims: tuple[ClaimRevision, ...]
    claim_reviews: tuple[ClaimReview, ...]
    claim_review_heads: tuple[str, ...]
    discrepancies: tuple[DiscrepancyInput, ...]

    def __post_init__(self) -> None:
        require_id(self.export_id)
        require_id(self.author)
        require_utc(self.build_at)
        for value in (self.effective_contract_sha256, self.registry_sha256, self.policy_sha256):
            require_hash(value)
        if self.decision_overlay_sha256 is not None:
            require_hash(self.decision_overlay_sha256)
        if self.previous_release_id is not None:
            require_id(self.previous_release_id)
        if not self.format_version or not self.targets or not self.approvals:
            raise EvidenceInvalid("review_export")
        if len({target.extraction.extraction_id for target in self.targets}) != len(self.targets):
            raise EvidenceInvalid("duplicate_target")
        if len({approval.approval_id for approval in self.approvals}) != len(self.approvals):
            raise EvidenceInvalid("duplicate_approval")
        for values in (self.review_heads, self.audit_receipts, self.claim_review_heads):
            if len(set(values)) != len(values):
                raise EvidenceInvalid("duplicate_review_reference")
            for value in values:
                require_id(value)


def export_digest(canonical_body: bytes) -> str:
    return sha256(canonical_body).hexdigest()


def review_checkpoint(export: ReviewExport) -> bytes:
    return canonical_json(
        {
            "export_id": export.export_id,
            "review_ids": sorted(review.review_id for review in export.source_reviews),
            "review_heads": sorted(export.review_heads),
            "approval_ids": sorted(
                (approval.approval_id, approval.revision) for approval in export.approvals
            ),
            "audit_receipts": sorted(export.audit_receipts),
            "claim_review_ids": sorted(review.claim_review_id for review in export.claim_reviews),
            "claim_review_heads": sorted(export.claim_review_heads),
        }
    )
