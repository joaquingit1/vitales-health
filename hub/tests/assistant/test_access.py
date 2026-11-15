from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.assistant.application.access import Access, View
from crew_health.contracts_gen.enums import Role
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.platform.testing.sqlite import database

OWNER = SessionContext(
    "session_own", "crew", Actor("person", "person_own"), "person_own", frozenset({Role.CREW})
)


class Sessions:
    live = True

    def is_live(self, view: View, session: SessionContext, *, listener: str) -> bool:
        assert listener == "assistant"
        return self.live

    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool:
        return self.live

    def access_generation(self, view: View) -> int:
        return 1


class Grants:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[SessionContext, str, str, frozenset[str]]] = []

    def require_read(
        self,
        view: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        *,
        object_ref: str,
        patient_id: str,
        fields: frozenset[str],
    ) -> None:
        self.calls.append((session, object_ref, patient_id, fields))
        if not self.allowed:
            raise ProblemError(
                ErrorCode.CONSENT_REQUIRED,
                "sensitive provider detail",
                current_state={"patient_id": patient_id},
            )


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3", "C17")
def test_current_owner_inference_passes_and_foreign_clinical_inference_never_calls_grant(
    tmp_path: Path,
) -> None:
    db = database(tmp_path)
    grants = Grants(True)
    access = Access(Sessions(), grants)
    try:
        with db.snapshot() as view:
            access.inference(view, OWNER, "person_own")
            officer = replace(OWNER, roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
            with pytest.raises(ProblemError) as error:
                access.inference(view, officer, "person_foreign")
            assert error.value.code == ErrorCode.NOT_FOUND and error.value.current_state is None
            assert grants.calls == []
    finally:
        db.close()


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3")
@pytest.mark.parametrize(
    "session",
    [
        replace(OWNER, person_id="person_foreign"),
        replace(OWNER, actor=Actor("station", "station_own"), kind="station"),
        replace(OWNER, actor=Actor("assistant", "assistant_own")),
        replace(OWNER, actor=Actor("operator", "operator_own"), kind="operator"),
    ],
)
def test_session_kind_or_person_field_cannot_substitute_for_authenticated_actor(
    tmp_path: Path, session: SessionContext
) -> None:
    db = database(tmp_path)
    try:
        with db.snapshot() as view:
            with pytest.raises(ProblemError):
                Access(Sessions(), Grants(True)).inference(view, session, "person_own")
    finally:
        db.close()


@pytest.mark.trace("T-E13-043", "T-E13-050", "V25", "SYSTEM.8.3")
def test_revoked_and_unknown_session_fail_closed_including_owner_replay(tmp_path: Path) -> None:
    db = database(tmp_path)
    sessions = Sessions()
    try:
        with db.snapshot() as view:
            access = Access(sessions, Grants(True))
            access.own(view, OWNER, "person_own")
            sessions.live = False
            with pytest.raises(ProblemError):
                access.own(view, OWNER, "person_own")
            with pytest.raises(ProblemError):
                Access(None, Grants(True)).own(view, OWNER, "person_own")
    finally:
        db.close()


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3", "C17")
def test_manual_officer_assessment_read_uses_exact_grant_and_masks_denial(tmp_path: Path) -> None:
    db = database(tmp_path)
    sessions = Sessions()
    grants = Grants(True)
    officer = replace(OWNER, roles=frozenset({Role.CREW_MEDICAL_OFFICER}))
    try:
        with db.snapshot() as view:
            access = Access(sessions, grants)
            access.assessment(view, officer, person_id="person_foreign", assessment_id="assessment_foreign")
            assert grants.calls[0][0:3] == (officer, "assessment_foreign", "person_foreign")
            assert {
                "findings",
                "hypotheses",
                "contradictions",
                "missing_evidence",
                "next_step",
            } <= grants.calls[0][3]
            grants.allowed = False
            with pytest.raises(ProblemError) as error:
                access.assessment(
                    view, officer, person_id="person_foreign", assessment_id="assessment_foreign"
                )
            assert error.value.code == ErrorCode.NOT_FOUND and error.value.current_state is None
            assert "sensitive provider detail" not in str(error.value)
    finally:
        db.close()


@pytest.mark.trace("T-E13-043", "V25", "SYSTEM.8.3", "SYSTEM.8.7")
def test_ground_read_cannot_project_live_assessment_without_delivered_bytes(tmp_path: Path) -> None:
    db = database(tmp_path)
    grants = Grants(True)
    ground = SessionContext(
        "session_ground", "ground", Actor("ground", "ground_own"), None, frozenset({Role.GROUND})
    )
    try:
        with db.snapshot() as view:
            with pytest.raises(ProblemError):
                Access(Sessions(), grants).assessment(
                    view, ground, person_id="person_own", assessment_id="assessment_own"
                )
            assert grants.calls == []
    finally:
        db.close()


@pytest.mark.trace("T-E13-075", "V25", "SYSTEM.8.3")
def test_runtime_commands_require_live_operator_actor_kind_and_role(tmp_path: Path) -> None:
    db = database(tmp_path)
    operator = SessionContext(
        "session_operator", "operator", Actor("operator", "operator_own"), None, frozenset({Role.OPERATOR})
    )
    try:
        with db.snapshot() as view:
            access = Access(Sessions(), None)
            access.operator(view, operator)
            for invalid in (
                OWNER,
                replace(operator, actor=Actor("person", "person_own")),
                replace(operator, roles=frozenset()),
                replace(operator, kind="crew"),
            ):
                with pytest.raises(ProblemError):
                    access.operator(view, invalid)
    finally:
        db.close()
