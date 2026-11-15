"""Who may touch a report. Owner only, fail closed (SYSTEM 8.3, AGENTS 6 privacy).

Roles alone, help sessions and station sessions grant nothing. The assistant, ground, station and adapter
sessions never read or change a private report here. Another crew member gets NOT_FOUND so existence does
not leak. Recipient grants and approved snapshots arrive with P5 and are not guessed."""

from __future__ import annotations

from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.uow import SessionContext
from crew_health.reports.domain.errors import ReportError, not_found

REPORT_ROLES = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER})


def require_owner_session(session: SessionContext) -> str:
    """The person a report command acts for. Anything else is refused before any report is loaded."""
    person = session.person_id
    if (
        session.kind != "crew"
        or session.actor.kind != "person"
        or person is None
        or session.actor.id != person
        or not session.roles & REPORT_ROLES
    ):
        raise ReportError(ErrorCode.UNAUTHORIZED_SCOPE, "not_owner_session")
    return person


def require_report_owner(session: SessionContext, patient_id: str) -> None:
    if require_owner_session(session) != patient_id:
        raise not_found()
