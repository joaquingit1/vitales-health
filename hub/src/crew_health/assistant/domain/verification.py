"""Deterministic per-claim support rules from E13 section 3.7."""

from dataclasses import dataclass, replace
from typing import Literal

from crew_health.assistant.domain.errors import AssistantRefused


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: Literal["observation", "answer", "passage", "daily_check"]
    ref_id: str


@dataclass(frozen=True, slots=True)
class Support:
    ref: EvidenceRef
    person_id: str | None
    resolved: bool
    seen: bool
    usable: bool
    finding_only: bool = False
    presence: str | None = None
    replay_exposure: bool = False
    habitat: bool = False


@dataclass(frozen=True, slots=True)
class Claim:
    kind: Literal["finding", "hypothesis", "contradiction"]
    text: str
    refs: tuple[EvidenceRef, ...]
    body_region: str | None = None
    condition_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VerifiedClaims:
    claims: tuple[Claim, ...]
    dropped_refs: int
    withheld_claims: int


def verify_claims(
    claims: tuple[Claim, ...],
    support: tuple[Support, ...],
    *,
    subject_person_id: str,
    body_regions: frozenset[str],
    condition_ids: frozenset[str],
) -> VerifiedClaims:
    by_ref = {item.ref: item for item in support}
    if len(by_ref) != len(support):
        raise AssistantRefused("conflicting_support")
    verified: list[Claim] = []
    dropped = 0
    withheld = 0
    for claim in claims:
        if (claim.body_region is not None and claim.body_region not in body_regions) or not set(
            claim.condition_ids
        ) <= condition_ids:
            raise AssistantRefused("invalid_claim_identity")
        refs: list[EvidenceRef] = []
        for ref in dict.fromkeys(claim.refs):
            item = by_ref.get(ref)
            if item is not None and item.person_id not in (None, subject_person_id):
                raise AssistantRefused("foreign_evidence")
            if item is None or not _eligible(item, claim.kind, subject_person_id):
                dropped += 1
            else:
                refs.append(ref)
        minimum = 2 if claim.kind == "contradiction" else 1
        finding_support = any(ref.kind in ("observation", "answer") for ref in refs)
        if len(refs) < minimum or (claim.kind == "finding" and not finding_support):
            withheld += 1
        else:
            verified.append(replace(claim, refs=tuple(refs)))
    return VerifiedClaims(tuple(verified), dropped, withheld)


def _eligible(item: Support, claim_kind: str, subject_person_id: str) -> bool:
    return (
        item.resolved
        and item.seen
        and item.usable
        and not item.replay_exposure
        and not item.habitat
        and (
            (item.ref.kind == "passage" and item.person_id is None)
            or (item.ref.kind != "passage" and item.person_id == subject_person_id)
        )
        and (not item.finding_only or claim_kind == "finding")
        and (item.ref.kind != "answer" or item.presence == "answered")
    )
