"""E02 3.4 subject binding: self capture, or an acting medical officer's manual capture for a roster member."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.identity.domain.routing import may_record_for
from crew_health.identity.domain.session import roles_for
from crew_health.kernel.uow import Actor, SessionContext


def crew(person_id: str, rank: int | None = None) -> SessionContext:
    return SessionContext("ses_1", "crew", Actor("person", person_id), person_id, roles_for("crew", rank))


@pytest.mark.trace("T-E02-004", "R29", "V08")
def test_self_capture_and_officer_capture_for_a_roster_member() -> None:
    assert may_record_for(
        crew("mara"), patient_id="mara", operator_id="mara", patient_on_roster=True, officer_now=False
    )
    officer = crew("ilya", 1)
    assert may_record_for(
        officer, patient_id="mara", operator_id="ilya", patient_on_roster=True, officer_now=True
    )


@pytest.mark.trace("T-E02-004", "T-E02-001")
def test_every_other_capture_is_refused() -> None:
    officer = crew("ilya", 1)
    station = SessionContext("ses_2", "station", Actor("station", "stn"), None, roles_for("station", None))
    cases = (
        (crew("tomas"), "mara", "tomas", True, False),
        (officer, "mara", "ilya", True, False),
        (crew("ilya"), "mara", "ilya", True, True),
        (officer, "ghost", "ilya", False, True),
        (officer, "mara", "sofia", True, True),
        (station, "mara", "mara", True, True),
        (crew("mara"), "mara", "mara", False, False),
    )
    for session, patient, operator, on_roster, officer_now in cases:
        assert not may_record_for(
            session,
            patient_id=patient,
            operator_id=operator,
            patient_on_roster=on_roster,
            officer_now=officer_now,
        )


@pytest.mark.trace("T-E02-004", "T-E02-018")
@given(
    rank=st.one_of(st.none(), st.integers(1, 3)),
    officer_now=st.booleans(),
    on_roster=st.booleans(),
    self_capture=st.booleans(),
)
def test_capture_for_another_needs_both_a_session_officer_role_and_a_current_rank(
    rank: int | None, officer_now: bool, on_roster: bool, self_capture: bool
) -> None:
    session = crew("ilya", rank)
    patient = "ilya" if self_capture else "mara"
    allowed = may_record_for(
        session, patient_id=patient, operator_id="ilya", patient_on_roster=on_roster, officer_now=officer_now
    )
    expected = on_roster and (self_capture or (officer_now and rank in (1, 2)))
    assert allowed == expected
