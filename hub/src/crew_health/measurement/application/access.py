"""Object and field access for measurement requests (SYSTEM 8.3; E05 10).

The subject reads their own requests. Anyone else needs a current consent grant for this subject, recipient and
capability scope; an operator additionally reads only after accepting the task. Officer status, a help session
or a station session never grants a read. A missing consent provider is a missing answer: denied.
"""

from __future__ import annotations

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.uow import SessionContext
from crew_health.measurement.application.ports import ConsentQueries, View
from crew_health.measurement.domain.model import MeasurementRequest, Viewer, is_bound_operator, is_subject

_BEFORE_ACCEPTANCE = frozenset({"proposed", "awaiting_acceptance", "declined"})
MEASUREMENT_SCOPE = "measurement"


def viewer_of(session: SessionContext) -> Viewer:
    return Viewer(person_id=session.person_id, is_medical_officer=Role.CREW_MEDICAL_OFFICER in session.roles)


def is_crew_person(session: SessionContext) -> bool:
    return (
        session.kind == "crew"
        and session.person_id is not None
        and bool({Role.CREW, Role.CREW_MEDICAL_OFFICER} & session.roles)
    )


def consented(
    view: View, consent: ConsentQueries | None, *, subject: str, recipient: str, capability_code: str
) -> bool:
    if consent is None:
        return False
    return consent.allows(
        view, subject_person_id=subject, recipient=recipient, scope=(MEASUREMENT_SCOPE, capability_code)
    )


def can_read(
    view: View, consent: ConsentQueries | None, request: MeasurementRequest, session: SessionContext
) -> bool:
    if not is_crew_person(session) or session.person_id is None:
        return False
    viewer = viewer_of(session)
    if is_subject(request, viewer):
        return True
    if is_bound_operator(request, viewer) and request.state in _BEFORE_ACCEPTANCE:
        return False
    return consented(
        view,
        consent,
        subject=request.patient_id,
        recipient=session.person_id,
        capability_code=request.capability_code,
    )
