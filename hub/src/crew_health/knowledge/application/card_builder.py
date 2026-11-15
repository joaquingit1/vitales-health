from __future__ import annotations

from crew_health.kernel.settings import HubSettings
from crew_health.knowledge.domain.errors import EvidenceInvalid, require_bound
from crew_health.knowledge.domain.export import ReviewExport, UseApproval
from crew_health.knowledge.domain.manifest import CardMembership, MemberSnapshot
from crew_health.knowledge.domain.release import PathwayCard, UseStatus
from crew_health.knowledge.domain.review import PathwayCardTarget, clinical_review_label, effective_reviews


def _approve(export: ReviewExport, card: PathwayCard, approval: UseApproval,
             heads: frozenset[str], members: dict[str, MemberSnapshot], settings: HubSettings) -> UseStatus:
    target = PathwayCardTarget(card.condition_id, card.content_sha256)
    records = {review.review_id: review for review in export.source_reviews}
    technical = records.get(approval.technical_check_id) if approval.technical_check_id is not None else None
    if approval.approved_by == card.authored_by or approval.policy_sha256 != export.policy_sha256:
        raise EvidenceInvalid("card_approval")
    if any(event.audit_receipt not in export.audit_receipts for event in approval.events):
        raise EvidenceInvalid("approval_receipt")
    if technical is None or technical.review_id not in heads or technical.target != target or technical.kind != "technical_check" or technical.completeness_confirmed != "full":
        raise EvidenceInvalid("card_review")
    if effective_reviews(export.source_reviews, target, "technical_check", card.authored_by,
        max_records=settings.integer("knowledge.review.max_records"), max_chain_depth=settings.integer("knowledge.review.max_chain_depth")) != "pass":
        raise EvidenceInvalid("card_review")
    if approval.clinical_review_id is not None:
        clinical = records.get(approval.clinical_review_id)
        if clinical is None or clinical.review_id not in heads or clinical.target != target or clinical.kind != "clinical_review" or clinical.verdict != "pass":
            raise EvidenceInvalid("clinical_review_pin")
    require_bound(len(card.supporting_passage_ids), settings.integer("knowledge.query.max_source_ids"))
    supported_sources = set()
    for identifier in card.supporting_passage_ids:
        member = members.get(identifier)
        status = next((status for status in member.uses if status.use == approval.use), None) if member is not None else None
        if member is None or status is None or status.status != "approved" or member.withdrawn:
            raise EvidenceInvalid("card_support")
        if member.simulation_only and not card.simulation_only:
            raise EvidenceInvalid("card_origin")
        supported_sources.update(member.source_ids)
    if not set(card.workup_source_ids) <= supported_sources:
        raise EvidenceInvalid("card_sources")
    event = approval.events[-1]
    return UseStatus(approval.use, "approved" if event.state == "active" else event.state, approval.approval_id, event.successor_ref)


def build_cards(export: ReviewExport, heads: frozenset[str], members: tuple[MemberSnapshot, ...],
                settings: HubSettings) -> tuple[CardMembership, ...]:
    require_bound(len(export.cards), settings.integer("knowledge.registry.max_rows"))
    if len({card.condition_id for card in export.cards}) != len(export.cards):
        raise EvidenceInvalid("duplicate_card")
    passages = {member.passage.passage_id: member for member in members}
    results = []
    for card in export.cards:
        target = PathwayCardTarget(card.condition_id, card.content_sha256)
        uses = tuple(_approve(export, card, approval, heads, passages, settings)
            for approval in export.approvals if approval.target == target)
        if not uses or len({status.use for status in uses}) != len(uses):
            raise EvidenceInvalid("card_purposes")
        label = clinical_review_label(export.source_reviews, target, card.authored_by,
            max_records=settings.integer("knowledge.review.max_records"), max_chain_depth=settings.integer("knowledge.review.max_chain_depth"))
        results.append(CardMembership(card, uses, label))
    return tuple(results)
