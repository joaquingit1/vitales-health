"""Identity corrections (E22 3.5): the chain of void and replacement under a lineage and its generations.

A correction applies only when the lineage's active generation equals the expected one. The same correction
reference again returns the first outcome. Authorization belongs to the calling workflow; the domain enforces the
chain."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from typing import Literal

from crew_health.participation.domain.errors import InvalidCorrection
from crew_health.participation.domain.identity import (
    correction_identity,
    intent_key,
    replacement_identity,
)
from crew_health.participation.domain.types import CompletionBasis, CompletionRecord


@dataclass(frozen=True, slots=True)
class CorrectionPlan:
    outcome: Literal["apply", "duplicate", "conflict"]
    void_completion_id: str | None = None
    replacement_basis: CompletionBasis | None = None
    replacement_key: str | None = None
    correction_key: str | None = None
    next_generation: int | None = None
    replaces_completion_id: str | None = None
    lineage_id: str | None = None


def plan_correction(
    head: CompletionRecord,
    *,
    to_patient_id: str,
    correction_ref: str,
    expected_generation: int,
    seen_correction_refs: Collection[str],
) -> CorrectionPlan:
    """Decide a correction against the active head of its lineage.

    `head` is the active (not voided) record of the lineage. A reassignment to the same person is refused: it would
    change nothing and hide a caller error."""
    if head.status != "accepted":
        raise InvalidCorrection("only an accepted completion can be corrected")
    if to_patient_id == head.basis.patient_id:
        raise InvalidCorrection("the completion already belongs to that person")
    if expected_generation < 0:
        raise InvalidCorrection("expected_generation cannot be negative")
    ckey = intent_key(correction_identity(head.lineage_id, correction_ref))
    if correction_ref in seen_correction_refs:
        return CorrectionPlan("duplicate", correction_key=ckey, lineage_id=head.lineage_id)
    if head.generation != expected_generation:
        return CorrectionPlan("conflict", correction_key=ckey, lineage_id=head.lineage_id)
    generation = head.generation + 1
    return CorrectionPlan(
        "apply",
        void_completion_id=head.completion_id,
        # Occurred at, offset, basis, basis_ref and cause fields carry over; only the patient changes.
        replacement_basis=replace(head.basis, patient_id=to_patient_id),
        replacement_key=intent_key(replacement_identity(head.lineage_id, generation)),
        correction_key=ckey,
        next_generation=generation,
        replaces_completion_id=head.completion_id,
        lineage_id=head.lineage_id,
    )
