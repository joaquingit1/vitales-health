from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json

import pytest

from crew_health.kernel.commands import canonical_json
from crew_health.kernel.testing import MemoryClock, MemoryIds, MemorySettings
from crew_health.knowledge.application.claim_builder import validate_claims
from crew_health.knowledge.application.evidence_query import EvidenceQueryService
from crew_health.knowledge.domain.export import ClaimRevision, ClaimReview, CurrentSpan, DiscrepancyInput, ReviewExport, SpanRef
from crew_health.knowledge.domain.manifest import MemberSnapshot
from crew_health.knowledge.domain.passage import PassageArtifact
from crew_health.knowledge.domain.release import UseStatus
from crew_health.knowledge.domain.errors import EvidenceInvalid


@pytest.mark.trace("T-E10-10", "T-E10-35", "T-E10-57", "V24", "R21", "I3", "I5")
def test_discrepancy_binds_reviewed_claim_to_exact_previous_and_current_spans(review_export: ReviewExport, settings: MemorySettings) -> None:
    target = review_export.targets[0]
    span = review_export.approvals[0].spans[0]
    passage = PassageArtifact.create(ids=MemoryIds(identifiers=()), extraction=target.extraction,
        char_start=span.char_start, char_end=span.char_end, max_chars=settings.integer("knowledge.passage.max_chars"))
    member = MemberSnapshot(passage, target.extraction, target.source_ids, target.license,
        (UseStatus("risk_context", "approved", "approval_a", None),), "not_clinically_reviewed", None, span.section, False, False)
    previous = SpanRef("evr_previous", passage.passage_id)
    current = CurrentSpan(passage.extraction_id, passage.char_start, passage.char_end, span.section)
    limitation = next(row["limitations"] for row in json.loads(Path("knowledge/inputs/package/condition-assessments-119.json").read_bytes()) if row["condition_id"] == "ICL106")
    body = {"claim_id": "claim_a", "context": "discrepancy", "text": passage.text,
        "source_ids": target.source_ids, "source_spans": [{"release_id": previous.release_id, "passage_id": previous.passage_id},
            {"extraction_id": current.extraction_id, "char_start": current.char_start, "char_end": current.char_end, "section": current.section}],
        "limitation": limitation, "author": "editor_claim", "use": "risk_context"}
    digest = sha256(canonical_json(body)).hexdigest()
    claim = ClaimRevision("claim_a", "discrepancy", passage.text, target.source_ids, (previous, current), limitation, "editor_claim", digest, "risk_context")
    review = ClaimReview("claim_review_a", digest, "editor_checker", "supported", None, "revises", review_export.build_at, None, "receipt_claim")
    relation = DiscrepancyInput(claim.claim_id, digest, previous, current, "revises", review.reviewer)
    export = replace(review_export, previous_release_id=previous.release_id, claims=(claim,), claim_reviews=(review,), claim_review_heads=(review.claim_review_id,),
        audit_receipts=(*review_export.audit_receipts, review.audit_receipt), discrepancies=(relation,))
    reader = EvidenceQueryService((member.bind(previous.release_id),), settings=settings, clock=MemoryClock(export.build_at, 0, "epoch_a"))
    assert validate_claims(export, (member,), reader, settings) == (relation,)
    for invalid in (
        replace(export, claim_review_heads=()),
        replace(export, claim_reviews=(replace(review, verdict="revoked"),)),
        replace(export, claim_reviews=(replace(review, reviewer=claim.author),)),
        replace(export, previous_release_id="evr_other"),
    ):
        with pytest.raises(EvidenceInvalid):
            validate_claims(invalid, (member,), reader, settings)
