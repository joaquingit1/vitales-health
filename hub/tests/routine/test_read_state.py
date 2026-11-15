from dataclasses import replace
from datetime import timedelta

import pytest

from crew_health.routine.domain.occurrence import allowed_actions, display_state

from .support import NOW, occurrence


@pytest.mark.trace("T-E25-093", "T-E25-017")
def test_read_does_not_regress_a_due_state_after_clock_correction() -> None:
    occ = replace(occurrence(), due_from=NOW + timedelta(hours=1), mission_day=2)
    assert display_state(occ, now=NOW, today=1, held_kind=None) == ("due", None)


@pytest.mark.trace("INV-E25-01")
def test_future_occurrence_does_not_offer_person_actions() -> None:
    occ = replace(occurrence(), state="not_due", mission_day=2)
    assert allowed_actions(occ, session_person_id=occ.person_id, held=False) == ()


@pytest.mark.trace("T-E25-014", "INV-E25-11")
def test_read_shows_missed_at_deadline_even_when_scheduler_has_not_dispatched() -> None:
    occ = occurrence()
    assert occ.due_until is not None
    assert display_state(occ, now=occ.due_until, today=2, held_kind=None) == ("missed", None)
    assert display_state(occ, now=occ.due_until, today=2, held_kind="sleep") == ("later", "sleep")
