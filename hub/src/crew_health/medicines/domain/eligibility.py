"""Confirmation and override eligibility (E32 3.7, SYSTEM 8.4 rule 3, Q14): two separate checks, evaluated in the
confirming transaction from trusted facts (E31's clinical authority, the product permission, the plan), never from
caller-supplied booleans. Configuration can only narrow them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crew_health.medicines.domain.model import Alert, ConfirmBy

type Authority = Literal["crew_medical_officer", "acting_medical_officer", "none"]


@dataclass(frozen=True, slots=True)
class Confirmer:
    person_id: str | None
    authority: Authority  # E31 ClinicalAuthorityQuery for this subject now (the subject excluded)
    role_assignment_id: str | None = None  # the accepted assignment of an acting officer


def may_confirm(
    who: Confirmer, subject_person_id: str, confirm_by: ConfirmBy, plan_names_medicine: bool
) -> bool:
    """Q14: the crew medical officer confirms any proposal; the acting officer confirms the recommended dose,
    compensated included, of a product marked `any_trained_crew` or a medicine the subject's active plan step names
    for this condition."""
    if who.person_id is None or who.person_id == subject_person_id:
        return False
    if who.authority == "crew_medical_officer":
        return True
    return who.authority == "acting_medical_officer" and (
        confirm_by == "any_trained_crew" or plan_names_medicine
    )


def may_override(who: Confirmer, subject_person_id: str, alert: Alert, reason_code: str) -> bool:
    """Never the acting officer (Q14: never an allergy or interaction override)."""
    if who.person_id is None or who.person_id == subject_person_id:
        return False
    if reason_code not in alert.reason_codes:
        return False
    return who.authority == "crew_medical_officer" and "crew_medical_officer" in alert.override_roles


def may_record_manual(who: Confirmer, subject_person_id: str) -> bool:
    """Manual administration and correction: the crew medical officer or the acting officer, never the subject."""
    if who.person_id is None or who.person_id == subject_person_id:
        return False
    return who.authority in ("crew_medical_officer", "acting_medical_officer")
