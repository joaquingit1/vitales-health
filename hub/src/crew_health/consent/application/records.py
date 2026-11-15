"""E02 3.3 and 3.4 binding of E01's `RecordAuthorization` to identity and consent.

Reads: the subject, or a crew person holding a current explicit grant for that one observation, made for its
mission and origin, that releases every requested field for the `read` action. Entering a record is never a
read grant: an officer's capture is returned once by the capture command itself, and a later read of it needs
the subject's consent like any other (E02 3.4, no later chart read). Writes: self capture, or an acting medical
officer's manual capture for a current roster member.

Every decision also needs the caller's session to be live in the deciding view. A read evaluated in a snapshot
is decided again in a fresh snapshot just before it is released, so a revocation, sign-out, credential change
or roster removal committed while the read was in flight refuses it (finding 3, E02 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from crew_health.consent.application.ports import ConsentQueries, Partition, RecordSubject
from crew_health.consent.application.service import RECORD_PURPOSE, ConsentBindings
from crew_health.identity.application.ports import CaptureEligibility, SessionReader
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import ReadSnapshot, ReadSnapshotFactory, SessionContext, UnitOfWork

READ_ACTION = "read"
__all__ = ["READ_ACTION", "RECORD_PURPOSE", "ConsentRecordAuthorization"]


def _refuse() -> ProblemError:
    return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


def _is_person(session: SessionContext) -> bool:
    return (
        session.kind == "crew"
        and session.person_id is not None
        and session.actor.kind == "person"
        and session.actor.id == session.person_id
    )


def _is_write(view: ReadSnapshot | UnitOfWork) -> bool:
    """A unit of work holds the writer, so its view is already the latest committed state."""
    return callable(getattr(view, "replicate", None))


def _partition(mission_id: str, origin_mode: str) -> Partition | None:
    for origin in ("LIVE", "REPLAY", "SIM"):
        if origin_mode == origin:
            return (mission_id, origin)
    return None


@dataclass(frozen=True, slots=True)
class ConsentRecordAuthorization:
    consent: ConsentQueries
    capture: CaptureEligibility
    sessions: SessionReader
    snapshots: ReadSnapshotFactory
    bound: ConsentBindings

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
        self._decide(view, session, object_ref=object_ref, patient_id=patient_id, fields=fields)
        if not _is_write(view):
            with self.snapshots.snapshot() as latest:
                self._decide(latest, session, object_ref=object_ref, patient_id=patient_id, fields=fields)

    def _decide(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        object_ref: str,
        patient_id: str,
        fields: frozenset[str],
    ) -> None:
        if session.person_id is None or not self.sessions.alive(view, session):
            raise _refuse()
        if session.person_id == patient_id:
            return
        records = self.bound.observations
        facts = None if records is None else records.observation(view, object_ref)
        partition = None if facts is None else _partition(facts.mission_id, facts.origin_mode)
        if facts is None or partition is None or facts.patient_id != patient_id:
            raise _refuse()
        released = self.consent.fields(
            view,
            subject_person_id=patient_id,
            recipient=session.person_id,
            scope=(RECORD_PURPOSE, object_ref),
            action=READ_ACTION,
            requested=fields,
            partition=partition,
        )
        if released != fields:
            raise _refuse()

    def require_write(self, uow: UnitOfWork, session: SessionContext, context: RecordSubject) -> None:
        if (
            session.actor != uow.actor
            or not self.sessions.alive(uow, session)
            or not self.capture.may_record(
                uow, session, patient_id=context.patient_id, operator_id=context.operator_id
            )
        ):
            raise _refuse()
