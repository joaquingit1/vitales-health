from crew_health.assistant.application.ports import RecordAuthorization, SessionReader
from crew_health.contracts_gen.enums import Role
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

type View = ReadSnapshot | UnitOfWork


class Access:
    def __init__(self, sessions: SessionReader | None, records: RecordAuthorization | None) -> None:
        self.sessions, self.records = sessions, records

    def current(self, view: View, session: SessionContext) -> None:
        if self.sessions is None:
            raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, "session state unavailable")
        if self.sessions.is_live(view, session, listener="assistant") is not True:
            raise ProblemError(ErrorCode.SESSION_CLOSED, "session is closed")

    def own(self, view: View, session: SessionContext, person_id: str) -> None:
        if not (
            session.kind == "crew"
            and session.actor.kind == "person"
            and session.actor.id == session.person_id == person_id
        ):
            raise ProblemError(ErrorCode.NOT_FOUND, "assistant object not found")
        self.current(view, session)

    def operator(self, view: View, session: SessionContext) -> None:
        if not (
            session.kind == "operator" and session.actor.kind == "operator" and Role.OPERATOR in session.roles
        ):
            raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, "operator required")
        self.current(view, session)

    def inference(self, view: View, session: SessionContext, person_id: str) -> None:
        self.own(view, session, person_id)

    def assessment(
        self,
        view: View,
        session: SessionContext,
        *,
        person_id: str,
        assessment_id: str,
        fields: frozenset[str] | None = None,
    ) -> None:
        if session.actor.kind == "person" and session.actor.id == person_id:
            self.own(view, session, person_id)
            return
        if not (
            session.kind == "crew"
            and session.actor.kind == "person"
            and session.actor.id == session.person_id
            and Role.CREW_MEDICAL_OFFICER in session.roles
            and self.records is not None
        ):
            raise ProblemError(ErrorCode.NOT_FOUND, "assessment not found")
        self.current(view, session)
        try:
            self.records.require_read(
                view,
                session,
                object_ref=assessment_id,
                patient_id=person_id,
                fields=fields
                if fields is not None
                else frozenset(
                    {
                        "assessment_id",
                        "episode_id",
                        "origin",
                        "state_snapshot_version",
                        "evidence_release_id",
                        "model_release_id",
                        "findings",
                        "hypotheses",
                        "contradictions",
                        "missing_evidence",
                        "next_step",
                        "validation_status",
                        "created_at",
                    }
                ),
            )
        except ProblemError as error:
            if error.code not in {
                ErrorCode.NOT_FOUND,
                ErrorCode.UNAUTHORIZED_SCOPE,
                ErrorCode.CONSENT_REQUIRED,
                ErrorCode.ACTION_NOT_ALLOWED,
            }:
                raise
            raise ProblemError(ErrorCode.NOT_FOUND, "assessment not found") from None
