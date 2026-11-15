from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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


def eligible(approval: Approval, reviews: tuple[Review, ...]) -> Eligibility:
    reasons: list[str] = []
    if approval.purpose not in {"risk_context", "patient_instruction", "routine_basis", "limit_basis"}:
        reasons.append("unknown_purpose")
    if approval.license_kind not in {"nasa_public_domain", "permission_documented"}:
        reasons.append("full_text_rights_required")
    if approval.completeness != "full" and not (
        approval.completeness == "partial"
        and approval.purpose == "risk_context"
        and approval.partial_acknowledged
    ):
        reasons.append("completeness")
    if approval.purpose == "patient_instruction" and approval.source_kind not in {
        "clinical_guideline",
        "first_aid_guidance",
        "device_manual",
    }:
        reasons.append("source_kind")
    if approval.approver == approval.extractor or not approval.receipt_refs:
        reasons.append("approval_attribution")
    if len(reviews) > 200000:
        return Eligibility(False, ("too_many_reviews",), "not_clinically_reviewed")
    by_id = {r.review_id: r for r in reviews}
    if len(by_id) != len(reviews) or not set(approval.review_ids) <= by_id.keys():
        reasons.append("incomplete_reviews")
    superseded: set[str] = set()
    for review in reviews:
        if review.supersedes is not None:
            prior = by_id.get(review.supersedes)
            if prior is None or (prior.extraction_id, prior.text_sha256, prior.kind) != (
                review.extraction_id,
                review.text_sha256,
                review.kind,
            ):
                reasons.append("review_chain")
            superseded.add(review.supersedes)
        seen: set[str] = set()
        cursor: Review | None = review
        for _ in range(min(len(reviews) + 1, 1001)):
            if cursor is None:
                break
            if cursor.review_id in seen:
                reasons.append("review_cycle")
                break
            seen.add(cursor.review_id)
            cursor = by_id.get(cursor.supersedes) if cursor.supersedes else None
        else:
            reasons.append("review_depth")
    heads = tuple(r for r in reviews if r.review_id not in superseded)
    matching = tuple(
        r for r in heads if (r.extraction_id, r.text_sha256) == (approval.extraction_id, approval.text_sha256)
    )
    if not matching or any(r.review_id not in approval.review_ids for r in matching):
        reasons.append("omitted_review_head")
    if not any(r.kind == "technical_check" and r.verdict == "pass" for r in matching):
        reasons.append("technical_check_required")
    if any(r.verdict != "pass" or not r.receipt_refs or r.reviewer == approval.extractor for r in matching):
        reasons.append("review_refused")
    clinical = any(
        r.kind == "clinical_review" and r.verdict == "pass" and r.attestation == "licensed_clinician"
        for r in matching
    )
    return Eligibility(
        not reasons,
        tuple(sorted(set(reasons))),
        "clinically_reviewed" if clinical else "not_clinically_reviewed",
    )
