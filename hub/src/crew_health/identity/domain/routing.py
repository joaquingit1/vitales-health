"""E02 3.4 subject binding: who may enter a manual observation for whom.

Self capture, or an authenticated acting medical officer entering for a verified named roster member.
No face, display name, proposed ID or model output names a subject; officer status grants no chart read.
"""

from __future__ import annotations

from crew_health.kernel.uow import Role, SessionContext


def is_crew_person(session: SessionContext) -> bool:
    return (
        session.kind == "crew"
        and session.person_id is not None
        and session.actor.kind == "person"
        and session.actor.id == session.person_id
        and Role.CREW in session.roles
    )


def may_record_for(
    session: SessionContext, *, patient_id: str, operator_id: str, patient_on_roster: bool, officer_now: bool
) -> bool:
    """`officer_now` is the current roster rank read in the caller's snapshot, not the session's roles."""
    if not is_crew_person(session) or operator_id != session.person_id or not patient_on_roster:
        return False
    if patient_id == session.person_id:
        return True
    return officer_now and Role.CREW_MEDICAL_OFFICER in session.roles
