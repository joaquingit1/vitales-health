"""Projection rules, mission day assignment and day moves (CONTRACTS 8; E22 3.4; T-E22-012, 013, 014, 021 to 023,
030 to 033)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.participation.domain.derivation import derive_streak
from crew_health.participation.domain.errors import InvalidBasis
from crew_health.participation.domain.identity import completion_id, intent_key, logical_identity
from crew_health.participation.domain.projection import (
    assign_day,
    build_completion,
    decide,
    plan_day_moves,
)
from crew_health.participation.domain.types import CompletionRecord

from .harness import DAY, EPOCH, PROP, at_day, basis, clock, clock_version, rules

pytestmark = pytest.mark.E22


def record(b, key: str | None = None, cl=None) -> CompletionRecord:  # type: ignore[no-untyped-def]
    cl = cl or clock()
    k = key or intent_key(logical_identity(b))
    built = build_completion(
        b, decide(b, record_origin="SIM"), key=k, clock=cl, rule_version="participation-1"
    )
    assert built is not None
    return built


@pytest.mark.trace("T-E22-021", "INV-E22-07")
def test_replay_creates_nothing() -> None:
    b = basis(origin="REPLAY")
    assert decide(b, record_origin="SIM").kind == "nothing"
    assert decide(b, record_origin="LIVE").kind == "nothing"
    assert (
        build_completion(b, decide(b, record_origin="SIM"), key="k", clock=clock(), rule_version="v") is None
    )


@pytest.mark.trace("T-E22-022", "INV-E22-07")
def test_origin_differing_from_the_record_origin_is_rejected() -> None:
    d = decide(basis(origin="LIVE"), record_origin="SIM")
    assert (d.kind, d.reason) == ("rejected", "ORIGIN_MISMATCH")
    # the origin rule comes first, before the identity and answer rules
    both = decide(basis(origin="LIVE", actor="tomas", complete=False), record_origin="SIM")
    assert both.reason == "ORIGIN_MISMATCH"
    assert decide(basis(origin="SIM"), record_origin="SIM").kind == "accepted"
    assert decide(basis(origin="LIVE"), record_origin="LIVE").kind == "accepted"


@pytest.mark.trace("T-E22-023")
def test_actor_must_be_the_patient_except_for_an_urgent_signal() -> None:
    assert decide(basis(actor="tomas"), record_origin="SIM").reason == "IDENTITY_MISMATCH"
    assert decide(basis(actor=None), record_origin="SIM").reason == "IDENTITY_MISMATCH"
    assert decide(basis("concern_reported", actor="tomas"), record_origin="SIM").reason == "IDENTITY_MISMATCH"
    answer = basis("episode_updated", cause_kind="answer", cause_ref="a1", actor="tomas")
    assert decide(answer, record_origin="SIM").reason == "IDENTITY_MISMATCH"
    urgent = basis("episode_updated", cause_kind="urgent_event", cause_ref="u1", actor=None)
    assert decide(urgent, record_origin="SIM").kind == "accepted"
    own = basis("episode_updated", cause_kind="answer", cause_ref="a1", actor="mara")
    assert decide(own, record_origin="SIM").kind == "accepted"


@pytest.mark.trace("T-E22-013", "V42", "R31")
def test_required_answers_complete_decides_a_daily_check_and_nothing_else() -> None:
    assert decide(basis(complete=True), record_origin="SIM").kind == "accepted"
    d = decide(basis(complete=False), record_origin="SIM")
    assert (d.kind, d.reason) == ("rejected", "REQUIRED_ANSWER_MISSING")
    # the basis has only the boolean: "prefer not to say" and a value are indistinguishable by construction
    assert decide(basis("concern_reported"), record_origin="SIM").kind == "accepted"


@pytest.mark.trace("T-E22-014", "V43", "R33")
def test_concern_or_episode_update_checks_a_day_with_no_daily_check() -> None:
    concern = record(basis("concern_reported", ref="ep_1", when=at_day(4)))
    update = record(basis("episode_updated", ref="ep_1", when=at_day(5), cause_kind="answer", cause_ref="a2"))
    assert [concern.mission_day, update.mission_day] == [4, 5]
    assert {concern.status, update.status} == {"accepted"}
    view = derive_streak(
        patient_id="mara", mission_id="demo-mission", origin_mode="SIM",
        facts=[concern.fact(), update.fact()], today=5, roster_first_day=4,
        as_of=at_day(5), rules=rules(), clock_version="clock-1", cursor="c",
    )  # fmt: skip
    assert (view.current_streak_days, view.completed_days_total) == (2, 2)


@pytest.mark.trace("T-E22-022", "T-E22-023")
def test_rejected_and_accepted_records_carry_a_consistent_status() -> None:
    rejected = build_completion(
        basis(actor="tomas"),
        decide(basis(actor="tomas"), record_origin="SIM"),
        key="k",
        clock=clock(),
        rule_version="v",
    )
    assert rejected is not None and (rejected.status, rejected.status_reason) == (
        "rejected",
        "IDENTITY_MISMATCH",
    )
    good = record(basis())
    assert (good.status, good.status_reason) == ("accepted", None)
    assert good.lineage_id == good.completion_id == completion_id(good.intent_key)
    assert (good.generation, good.replaces_completion_id) == (0, None)
    with pytest.raises(InvalidBasis):
        replace(good, status_reason="ORIGIN_MISMATCH")
    with pytest.raises(InvalidBasis):
        replace(good, status="rejected")


@pytest.mark.trace("T-E22-012", "V46", "R33")
def test_2358_and_0003_mission_time_are_consecutive_days() -> None:
    late = assign_day(clock(), at_day(7, 23 + 58 / 60))
    early = assign_day(clock(), at_day(8, 0.05))
    assert (late.mission_day, early.mission_day) == (7, 8)
    assert assign_day(clock(), at_day(8, 0)).mission_day == 8
    assert assign_day(clock(), at_day(8, 0) - timedelta(microseconds=1)).mission_day == 7
    assert assign_day(clock(), EPOCH - timedelta(hours=1)).mission_day == 0  # pre-flight days are ordinary


@pytest.mark.trace("T-E22-012", "V46")
@PROP
@given(offset_s=st.integers(min_value=-86400 * 30, max_value=86400 * 400))
def test_day_is_the_floor_of_elapsed_days_from_the_epoch(offset_s: int) -> None:
    instant = EPOCH + timedelta(seconds=offset_s)
    expected = (instant - EPOCH) // DAY + 1
    assert assign_day(clock(), instant).mission_day == expected


@pytest.mark.trace("T-E22-030", "INV-E22-08", "R31")
def test_a_new_clock_version_after_the_events_moves_none() -> None:
    recs = [record(basis(ref=f"d{i}", when=at_day(i))) for i in range(1, 6)]
    later = clock_version(
        "clock-2", generation=2, effective_from=at_day(20), epoch=EPOCH + timedelta(hours=7)
    )
    assert plan_day_moves(recs, clock(clock_version(), later)) == ()
    assert [r.mission_day for r in recs] == [1, 2, 3, 4, 5]
    assert {r.mission_clock_version for r in recs} == {"clock-1"}


@pytest.mark.trace("T-E22-031", "V46", "INV-E22-08")
def test_a_correction_over_a_range_moves_days_and_double_counts_none() -> None:
    old = clock()
    recs = [record(basis(ref=f"d{i}", when=at_day(i, 3)), cl=old) for i in range(1, 6)]
    fix = clock_version(
        "clock-2", generation=2, epoch=EPOCH + timedelta(hours=5),
        effective_from=at_day(2, 0), effective_until=at_day(4, 0),
    )  # fmt: skip
    moves = plan_day_moves(recs, clock(clock_version(), fix))
    assert sorted((m.from_day, m.to_day, m.to_clock_version) for m in moves) == [
        (2, 1, "clock-2"),
        (3, 2, "clock-2"),
    ]
    assert [m.completion_id for m in moves] == sorted(m.completion_id for m in moves)
    moved = {m.completion_id: m.to_day for m in moves}
    days = [moved.get(r.completion_id, r.mission_day) for r in recs]
    assert sorted(days) == [1, 1, 2, 4, 5]  # one completion per basis; day 1 now holds two and counts once
    assert len(set(days)) == 4
    # a second plan over the already moved state is empty (idempotent)
    applied = [
        replace(r, mission_day=moved[r.completion_id], mission_clock_version="clock-2")
        if r.completion_id in moved else r
        for r in recs
    ]  # fmt: skip
    assert plan_day_moves(applied, clock(clock_version(), fix)) == ()


@pytest.mark.trace("T-E22-032", "CONTRACTS-8-rule-8", "R33")
def test_late_completion_keeps_its_day_and_never_counts_for_the_sync_day() -> None:
    late = record(basis(ref="late", when=at_day(12, 20)))
    assert late.mission_day == 12  # the sync happens on day 15; the day comes from occurred_at
    others = [record(basis(ref=f"d{i}", when=at_day(i))) for i in (10, 11, 13, 14, 15)]

    def view(extra: list[CompletionRecord]):  # type: ignore[no-untyped-def]
        return derive_streak(
            patient_id="mara", mission_id="demo-mission", origin_mode="SIM",
            facts=[r.fact() for r in [*others, *extra]], today=15, roster_first_day=10,
            as_of=at_day(15), rules=rules(), clock_version="clock-1", cursor="c",
        )  # fmt: skip

    before, after = view([]), view([late])
    assert {d.mission_day: d.status for d in before.days}[12] == "missed"
    assert {d.mission_day: d.status for d in after.days}[12] == "checked"
    assert before.current_streak_days == 3 and after.current_streak_days == 6
    assert before.completed_days_total + 1 == after.completed_days_total


@pytest.mark.trace("T-E22-033", "DE22-4")
def test_rebuild_with_no_change_has_zero_moves() -> None:
    recs = [record(basis(ref=f"d{i}", when=at_day(i))) for i in range(1, 8)]
    assert plan_day_moves(recs, clock()) == ()
    assert plan_day_moves([], clock()) == ()
