"""E02 3.3 and 3.4 binding of E01's `RecordAuthorization` to identity and consent.

Reads: the subject, the crew person who entered that one observation, or a crew person holding a current
explicit grant that releases every requested field for the `read` action on that object. Writes: self capture,
or an acting medical officer's manual capture for a roster member. Officer status, help or station sessions
never grant a read, and entering one reading opens no other record of the subject.
"""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.consent.application.ports import AuthoredRecords, ConsentQueries, RecordSubject
from crew_health.identity.application.ports import CaptureEligibility
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

RECORD_PURPOSE = "records.read"
READ_ACTION = "read"


def _refuse() -> ProblemError:
    return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


def _is_person(session: SessionContext) -> bool:
    return (
        session.kind == "crew"
        and session.person_id is not None
        and session.actor.kind == "person"
        and session.actor.id == session.person_id
    )


@dataclass(frozen=True, slots=True)
class ConsentRecordAuthorization:
    consent: ConsentQueries
    capture: CaptureEligibility
    records: AuthoredRecords

    def require_read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        object_ref: str,
        patient_id: str,
        fields: frozenset[str],
    ) -> None:
        if not _is_person(session) or session.person_id is None or not fields:
            raise _refuse()
        if session.person_id == patient_id or self._entered(view, session.person_id, object_ref, patient_id):
            return
        released = self.consent.fields(
            view,
            subject_person_id=patient_id,
            recipient=session.person_id,
            scope=(RECORD_PURPOSE, object_ref),
            action=READ_ACTION,
            requested=fields,
        )
        if released != fields:
            raise _refuse()

    def _entered(
        self, view: ReadSnapshot | UnitOfWork, person_id: str, object_ref: str, patient_id: str
    ) -> bool:
        entry = self.records.observation(view, object_ref)
        return entry is not None and entry.operator_id == person_id and entry.patient_id == patient_id

    def require_write(self, uow: UnitOfWork, session: SessionContext, context: RecordSubject) -> None:
        if session.actor != uow.actor or not self.capture.may_record(
            uow, session, patient_id=context.patient_id, operator_id=context.operator_id
        ):
            raise _refuse()
