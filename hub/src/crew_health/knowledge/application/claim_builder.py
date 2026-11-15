from __future__ import annotations

from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.application.ports import EvidenceQueryPort
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.export import ClaimReview, ClaimRevision, CurrentSpan, DiscrepancyInput, ReviewExport, SpanRef
from crew_health.knowledge.domain.manifest import MemberSnapshot
from crew_health.knowledge.domain.results import Eligible


def _heads(export: ReviewExport, settings: HubSettings) -> dict[str, tuple[ClaimReview, ...]]:
    require_bound(len(export.claim_reviews), settings.integer("knowledge.review.max_records"))
    claims = {claim.claim_rev_sha256: claim for claim in export.claims}
    records = {review.claim_review_id: review for review in export.claim_reviews}
    if len(claims) != len(export.claims) or len(records) != len(export.claim_reviews):
        raise EvidenceInvalid("duplicate_claim")
    superseded = set()
    for review in export.claim_reviews:
        claim = claims.get(review.claim_rev_sha256)
        if claim is None or review.reviewer == claim.author or review.audit_receipt not in export.audit_receipts:
            raise EvidenceInvalid("claim_review")
        current = review
        for _ in range(settings.integer("knowledge.review.max_chain_depth")):
            if current.supersedes_claim_review_id is None:
                break
            previous = records.get(current.supersedes_claim_review_id)
            if previous is None or previous.claim_rev_sha256 != review.claim_rev_sha256:
                raise EvidenceInvalid("claim_review_chain")
            current = previous
        else:
            raise EvidenceInvalid("claim_review_chain")
        if review.supersedes_claim_review_id is not None:
            superseded.add(review.supersedes_claim_review_id)
    head_ids = frozenset(records.keys() - superseded)
    if head_ids != frozenset(export.claim_review_heads):
        raise EvidenceInvalid("claim_review_heads")
    results = {}
    for digest in claims:
        heads = tuple(records[key] for key in sorted(head_ids) if records[key].claim_rev_sha256 == digest)
        if not heads or any(review.verdict not in ("supported", "partially_supported") for review in heads):
            raise EvidenceInvalid("claim_not_supported")
        results[digest] = heads
    return results


def _support(claim: ClaimRevision, export: ReviewExport, members: tuple[MemberSnapshot, ...],
             previous: EvidenceQueryPort | None, settings: HubSettings) -> None:
    require_bound(len(claim.source_spans), settings.integer("knowledge.query.max_source_ids"))
    if not claim.source_spans:
        raise EvidenceInvalid("claim_support")
    current = {CurrentSpan(member.passage.extraction_id, member.passage.char_start, member.passage.char_end, member.section): member for member in members}
    resolved = set()
    for span in claim.source_spans:
        if isinstance(span, SpanRef):
            if claim.context != "discrepancy" or span.release_id != export.previous_release_id or previous is None:
                raise EvidenceInvalid("claim_previous_pin")
            read = previous.read_approved(span.release_id, span.passage_id, claim.use)
            if not isinstance(read, Eligible):
                raise EvidenceInvalid("claim_previous_support")
            resolved.update(read.membership.source_ids)
        else:
            member = current.get(span)
            status = next((status for status in member.uses if status.use == claim.use), None) if member is not None else None
            if member is None or member.withdrawn or status is None or status.status != "approved":
                raise EvidenceInvalid("claim_current_support")
            resolved.update(member.source_ids)
    if not set(claim.source_ids) <= resolved:
        raise EvidenceInvalid("claim_source")


def validate_claims(export: ReviewExport, members: tuple[MemberSnapshot, ...], previous: EvidenceQueryPort | None,
                    settings: HubSettings) -> tuple[DiscrepancyInput, ...]:
    require_bound(len(export.claims), settings.integer("knowledge.review.max_records"))
    require_bound(len(export.discrepancies), settings.integer("knowledge.review.max_records"))
    heads = _heads(export, settings)
    claims = {claim.claim_rev_sha256: claim for claim in export.claims}
    for claim in export.claims:
        _support(claim, export, members, previous, settings)
    for relation in export.discrepancies:
        claim = claims.get(relation.claim_rev_sha256)
        if claim is None or claim.claim_id != relation.claim_id or claim.context != "discrepancy":
            raise EvidenceInvalid("discrepancy_claim")
        if relation.superseded not in claim.source_spans or relation.current not in claim.source_spans:
            raise EvidenceInvalid("discrepancy_spans")
        reviews = heads[relation.claim_rev_sha256]
        if relation.recorded_by not in {review.reviewer for review in reviews} or any(review.relation != relation.relation for review in reviews):
            raise EvidenceInvalid("discrepancy_review")
    if {claim.claim_id for claim in export.claims if claim.context == "discrepancy"} != {relation.claim_id for relation in export.discrepancies}:
        raise EvidenceInvalid("discrepancy_missing")
    return tuple(sorted(export.discrepancies, key=lambda relation: (relation.claim_id, relation.claim_rev_sha256)))
