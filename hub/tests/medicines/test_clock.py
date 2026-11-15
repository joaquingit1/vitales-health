"""A forward wall clock jump never lets a repeat dose through early (review B3; E32 C1 to C4; SYSTEM 5.2).

Each medicines command compares wall time against monotonic time since this process's previous medicines command
(C3). A jump beyond the tolerance is stored as evidence (both deltas, the reading it is measured from, the clock
correction head) and, until a person records a clock correction after it, proposals refuse `clock_discontinuity`
and a confirmation supersedes (C4). Real elapsed time moves both clocks and passes."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from .harness import Station, build_station
from .test_flow import _confirm, _context, _diagnose, _propose


@pytest.fixture
def st(tmp_path: Path) -> Station:
    station = build_station(tmp_path)
    _context(station)
    return station


def _dosed(st: Station) -> None:
    administered = _confirm(st, _propose(st, _diagnose(st)))
    assert administered.status_code == 201, administered.text


def _code(proposal: dict[str, object]) -> str | None:
    refusal = cast(dict[str, Any] | None, proposal.get("refusal"))
    return None if refusal is None else str(refusal["code"])


@pytest.mark.trace("REVIEW-B3", "E32-C3", "E32-C4", "SYSTEM-5.2")
def test_a_forward_jump_refuses_the_next_proposal_until_a_correction_is_recorded(st: Station) -> None:
    _dosed(st)
    assert _code(_propose(st, _diagnose(st))) == "min_interval_not_passed"
    st.clock.jump(timedelta(hours=4))  # wall time moves 4 hours, no time elapsed
    assert _code(_propose(st, _diagnose(st))) == "clock_discontinuity"
    ((reference, wall, monotonic, head),) = st.rows(
        "SELECT reference_seq, wall_delta_ms, monotonic_delta_ms, correction_version FROM med_clock_check"
        " WHERE kind = 'discontinuity'"
    )
    assert reference is not None and cast(int, wall) - cast(int, monotonic) >= 4 * 3600 * 1000 - 1000
    assert head == 0
    st.clock.at += timedelta(hours=1)  # time passing does not clear it
    assert _code(_propose(st, _diagnose(st))) == "clock_discontinuity"
    st.corrections.record(st.clock.at)  # a person records the correction (the clock module's journal)
    assert _code(_propose(st, _diagnose(st))) in {None, "min_interval_not_passed"}


@pytest.mark.trace("REVIEW-B3", "E32-C3")
def test_real_elapsed_time_is_not_a_jump(st: Station) -> None:
    _dosed(st)
    st.clock.at += timedelta(hours=7)  # wall and monotonic move together
    proposal = _propose(st, _diagnose(st))
    assert proposal["outcome"] == "proposal", proposal
    assert st.rows("SELECT count(*) FROM med_clock_check WHERE kind = 'discontinuity'") == [(0,)]


@pytest.mark.trace("REVIEW-B3", "E32-C4")
def test_a_jump_between_proposal_and_confirmation_supersedes_it(st: Station) -> None:
    proposal = _propose(st, _diagnose(st))
    st.clock.jump(timedelta(minutes=5))
    response = _confirm(st, proposal, client_time="2028-03-01T08:04:00Z")
    assert response.status_code == 409 and response.json()["current_state"]["state"] == "superseded"
    assert st.rows("SELECT count(*) FROM med_movement") == [(0,)]


@pytest.mark.trace("REVIEW-B3", "E32-C3")
def test_a_jump_within_the_tolerance_or_across_a_restart_is_not_marked(tmp_path: Path) -> None:
    st = build_station(tmp_path)
    _context(st)
    st.clock.jump(timedelta(seconds=59))  # inside the 60 s team tolerance
    assert _propose(st, _diagnose(st))["outcome"] == "proposal"
    st.clock.epoch = "epoch_2"  # a new process: its monotonic clock has no common origin with the old one
    st.clock.jump(timedelta(hours=3))
    restarted = build_station(tmp_path, clock=st.clock)
    assert _propose(restarted, _diagnose(restarted))["outcome"] == "proposal"
    assert restarted.rows("SELECT count(*) FROM med_clock_check WHERE kind = 'discontinuity'") == [(0,)]


@pytest.mark.trace("REVIEW-B3", "E32-C4", "AGENTS-3")
def test_without_the_clock_module_a_jump_stays_marked(st: Station) -> None:
    st.rebind(corrections=None)
    _dosed(st)
    st.clock.jump(timedelta(hours=4))
    assert _code(_propose(st, _diagnose(st))) == "clock_discontinuity"
    st.clock.at += timedelta(hours=8)
    assert _code(_propose(st, _diagnose(st))) == "clock_discontinuity"
