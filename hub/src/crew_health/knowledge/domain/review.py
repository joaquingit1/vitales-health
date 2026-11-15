from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound, require_hash

type ReviewKind = Literal["technical_check", "clinical_review"]
type Verdict = Literal["pass", "fail", "revoked"]


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    document_version_id: str
    content_sha256: str
    extraction_id: str
    text_sha256: str
    page_map_sha256: str

    def __post_init__(self) -> None:
        require_id(self.document_version_id)
        require_id(self.extraction_id)
        for value in (self.content_sha256, self.text_sha256, self.page_map_sha256):
            require_hash(value)


@dataclass(frozen=True, slots=True)
class PathwayCardTarget:
    condition_id: str
    content_sha256: str

    def __post_init__(self) -> None:
        require_id(self.condition_id)
        require_hash(self.content_sha256)


@dataclass(frozen=True, slots=True)
class SourceReview:
    review_id: str
    target: ReviewTarget | PathwayCardTarget
    kind: ReviewKind
    reviewer: str
    verdict: Verdict
    attestation: Literal["licensed_clinician", "team_member_not_clinician"] | None
    audit_receipt: str
    supersedes_review_id: str | None
    at: datetime
    completeness_confirmed: str | None = None
    source_kind: str | None = None
    applicability: bool | None = None

    def __post_init__(self) -> None:
        require_utc(self.at)


def effective_reviews(
    reviews: tuple[SourceReview, ...],
    target: ReviewTarget | PathwayCardTarget,
    kind: ReviewKind,
    extractor: str,
    *,
    max_records: int,
    max_chain_depth: int,
) -> Literal["none", "fail", "pass"]:
    require_bound(len(reviews), max_records)
    require_bound(1, max_chain_depth)
    by_id = {r.review_id: r for r in reviews}
    if len(by_id) != len(reviews):
        raise EvidenceInvalid("duplicate_review")
    for review in reviews:
        _validate_review(review, by_id)
        if review.target == target and review.reviewer == extractor:
            raise EvidenceInvalid("reviewer_is_author")
    selected = tuple(r for r in reviews if r.target == target and r.kind == kind)
    superseded = {r.supersedes_review_id for r in selected}
    heads = tuple(r for r in selected if r.review_id not in superseded)
    if selected and not heads:
        raise EvidenceInvalid("review_cycle")
    for review in selected:
        current = review
        for _ in range(max_chain_depth):
            if current.supersedes_review_id is None:
                break
            current = by_id[current.supersedes_review_id]
        else:
            raise EvidenceInvalid("review_chain_bound")
    if not heads:
        return "none"
    return "fail" if any(r.verdict in ("fail", "revoked") for r in heads) else "pass"


def clinical_review_label(
    reviews: tuple[SourceReview, ...],
    target: ReviewTarget | PathwayCardTarget,
    extractor: str,
    *,
    max_records: int,
    max_chain_depth: int,
) -> Literal["clinically_reviewed", "not_clinically_reviewed"]:
    verdict = effective_reviews(
        reviews,
        target,
        "clinical_review",
        extractor,
        max_records=max_records,
        max_chain_depth=max_chain_depth,
    )
    if verdict != "pass":
        return "not_clinically_reviewed"
    selected = tuple(r for r in reviews if r.target == target and r.kind == "clinical_review")
    superseded = {r.supersedes_review_id for r in selected}
    if any(r.attestation == "licensed_clinician" and r.review_id not in superseded for r in selected):
        return "clinically_reviewed"
    return "not_clinically_reviewed"


def _validate_review(review: SourceReview, by_id: dict[str, SourceReview]) -> None:
    for value in (review.review_id, review.reviewer, review.audit_receipt):
        if not value:
            raise EvidenceInvalid("review_incomplete")
        require_id(value)
    if review.kind not in ("technical_check", "clinical_review") or review.verdict not in (
        "pass",
        "fail",
        "revoked",
    ):
        raise EvidenceInvalid("review_incomplete")
    if review.kind == "clinical_review" and review.attestation not in (
        "licensed_clinician",
        "team_member_not_clinician",
    ):
        raise EvidenceInvalid("attestation")
    if review.supersedes_review_id is not None:
        previous = by_id.get(review.supersedes_review_id)
        if previous is None or previous.target != review.target or previous.kind != review.kind:
            raise EvidenceInvalid("review_chain")
