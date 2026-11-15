"""Confirmation and override eligibility (E32 3.7): two separate checks, evaluated in the confirming transaction.
Configuration can only narrow them."""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.medicines.domain.model import Alert, ConfirmBy, Path


@dataclass(frozen=True, slots=True)
class Confirmer:
    person_id: str | None
    roles: frozenset[str]
    acting_medical_officer: bool  # acting MO for this subject now (E31 cascade, the subject excluded)


def may_confirm(who: Confirmer, subject_person_id: str, confirm_by: ConfirmBy, path: Path) -> bool:
    if who.person_id is None or who.person_id == subject_person_id:
        return False
    if "crew_medical_officer" in who.roles:
        return True
    return who.acting_medical_officer and confirm_by == "any_trained_crew" and path != "compensated"


def may_override(who: Confirmer, subject_person_id: str, alert: Alert, reason_code: str) -> bool:
    if who.person_id is None or who.person_id == subject_person_id:
        return False
    if reason_code not in alert.reason_codes:
        return False
    return "crew_medical_officer" in who.roles and "crew_medical_officer" in alert.override_roles


def may_record_manual(who: Confirmer, subject_person_id: str) -> bool:
    """Manual administration and correction: the crew medical officer or the acting officer, never the subject."""
    if who.person_id is None or who.person_id == subject_person_id:
        return False
    return "crew_medical_officer" in who.roles or who.acting_medical_officer
