"""Crew goal under D10 consent, privacy between crew members and canonical results (CONTRACTS 8; E22 3.3, 3.7;
T-E22-056, 057, 063 to 066, INV-E22-05, 06, 07, 09)."""

from __future__ import annotations

import random
from dataclasses import fields, replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.participation.domain.canonical import result_sha256
from crew_health.participation.domain.crew_goal import CrewConsent, derive_crew_goal, set_consent
from crew_health.participation.domain.derivation import award_views, derive_streak
from crew_health.participation.domain.errors import AggregateOnly, InvalidConsent, UnauthorizedScope
from crew_health.participation.domain.privacy import reject_breakdown, require_no_selector, require_owner
from crew_health.participation.domain.types import CrewGoalView

from .harness import PROP, example_value, facts_for, rules, validator

pytestmark = pytest.mark.E22
AS_OF = datetime(2026, 11, 13, 9, 10, 16, tzinfo=UTC)
CREW = ("mara", "tomas", "ines", "oleg")
ON = CrewConsent(True, 1, 1)


def goal(
    today: int = 19,
    *,
    consents: dict[str, CrewConsent] | None = None,
    checked: dict[str, set[int]] | None = None,
    origin: str = "SIM",
    roster: dict[int, frozenset[str]] | None = None,
) -> CrewGoalView:
    roster = roster if roster is not None else {d: frozenset(CREW) for d in range(-5, 60)}
    return derive_crew_goal(
        mission_id="demo-mission", origin_mode=origin,  # type: ignore[arg-type]
        today=today, roster_by_day=roster,
        consents={m: ON for m in CREW} if consents is None else consents,
        checked_days={m: frozenset(d) for m, d in (checked or {}).items()},
        as_of=AS_OF, rules=rules(), clock_version="clock-1",
    )  # fmt: skip


@pytest.mark.trace("T-E22-003", "D03", "R32")
def test_four_people_over_seven_days_have_28_due_and_the_example_shape() -> None:
    expected = example_value("participation/crew-goal.json")
    checked = {
        "mara": set(range(13, 20)),
        "tomas": set(range(13, 20)),
        "ines": set(range(13, 20)),
        "oleg": {13, 14},
    }
    view = goal(checked=checked)
    assert (view.window_first_mission_day, view.window_last_mission_day) == (13, 19)
    assert (view.completed_total, view.due_total, view.reached) == (23, 28, False)
    assert (view.derivation_version, view.mission_clock_version) == (
        expected["derivation_version"],
        expected["mission_clock_version"],
    )
    assert goal(checked={m: set(range(13, 20)) for m in CREW}).reached is True


@pytest.mark.trace("T-E22-056", "T-E22-057", "INV-E22-06", "R32", "V45")
def test_view_fields_equal_the_schema_and_hold_no_person_or_per_day_value() -> None:
    schema = cast(dict[str, Any], validator("crew-goal-view.schema.json").schema)
    schema_fields = set(schema["properties"])
    assert {f.name for f in fields(CrewGoalView)} == schema_fields
    forbidden = ("patient", "person", "member", "name", "day_count", "per_")
    assert not [n for n in schema_fields if any(w in n for w in forbidden)]


@pytest.mark.trace("T-E22-064", "D10", "Q15")
def test_nobody_counts_until_they_opt_in_and_zero_due_is_never_reached() -> None:
    none = goal(consents={}, checked={m: set(range(1, 30)) for m in CREW})
    assert (none.completed_total, none.due_total, none.reached) == (0, 0, False)
    assert CrewConsent() == CrewConsent(False, None, 0)
    off = CrewConsent(False, None, 3)
    assert goal(consents={m: off for m in CREW}).reached is False


@pytest.mark.trace("T-E22-064", "D10")
def test_enabling_counts_prospectively_and_revocation_excludes_at_once() -> None:
    current = set_consent(CrewConsent(), enabled=True, today=15, expected_revision=0)
    assert current == CrewConsent(True, 16, 1)
    view = goal(today=19, consents={"mara": current}, checked={"mara": {13, 14, 15, 16, 17, 18, 19}})
    assert (view.due_total, view.completed_total) == (4, 4)  # days 16 to 19 only, no partial or earlier day
    withdrawn = set_consent(current, enabled=False, today=18, expected_revision=1)
    assert withdrawn == CrewConsent(False, None, 2)
    gone = goal(today=19, consents={"mara": withdrawn}, checked={"mara": {13, 14, 15, 16, 17, 18, 19}})
    assert (gone.due_total, gone.completed_total, gone.reached) == (0, 0, False)
    again = set_consent(withdrawn, enabled=True, today=19, expected_revision=2)
    assert again == CrewConsent(True, 20, 3)  # earlier days never come back


@pytest.mark.trace("T-E22-064", "D10")
def test_consent_is_idempotent_and_a_stale_revision_is_refused() -> None:
    on = CrewConsent(True, 6, 1)
    assert set_consent(on, enabled=True, today=30, expected_revision=1) is on
    off = CrewConsent()
    assert set_consent(off, enabled=False, today=30, expected_revision=0) is off
    with pytest.raises(InvalidConsent):
        set_consent(on, enabled=False, today=30, expected_revision=0)
    with pytest.raises(InvalidConsent):
        CrewConsent(True, None, 1)
    with pytest.raises(InvalidConsent):
        CrewConsent(False, 3, 1)
    with pytest.raises(InvalidConsent):
        CrewConsent(False, None, -1)


@pytest.mark.trace("T-E22-063", "D10", "INV-E22-06")
def test_both_totals_use_consent_and_only_roster_days_count() -> None:
    roster = {d: frozenset({"mara", "tomas"} if d >= 16 else {"mara"}) for d in range(10, 25)}
    view = goal(
        today=19, roster=roster, consents={"mara": ON, "tomas": ON},
        checked={"mara": {13, 14}, "tomas": {13, 17, 18}},
    )  # fmt: skip
    # mara 13..19 = 7 due; tomas on the roster 16..19 = 4 due; tomas' day 13 is outside his roster days
    assert (view.due_total, view.completed_total) == (11, 4)
    with_one = goal(
        today=19, roster=roster, consents={"mara": ON}, checked={"mara": {13, 14}, "tomas": {17, 18}}
    )
    assert (with_one.due_total, with_one.completed_total) == (7, 2)


@pytest.mark.trace("T-E22-063", "INV-E22-06")
@PROP
@given(
    today=st.integers(min_value=-3, max_value=40),
    window_seed=st.integers(min_value=0, max_value=10**6),
    data=st.data(),
)
def test_totals_match_brute_force_and_never_exceed_due(
    today: int, window_seed: int, data: st.DataObject
) -> None:
    rng = random.Random(window_seed)
    consents = {
        m: CrewConsent(True, rng.randint(-5, 40), 1) if rng.random() < 0.7 else CrewConsent() for m in CREW
    }
    checked = {m: set(data.draw(st.sets(st.integers(min_value=-5, max_value=45), max_size=25))) for m in CREW}
    first = today - rules().crew_goal_window_days + 1
    view = goal(today=today, consents=consents, checked=checked)
    due = completed = 0
    for m in CREW:
        for d in range(first, today + 1):
            if consents[m].enabled and d >= (consents[m].effective_from_day or 0):
                due += 1
                completed += d in checked[m]
    assert (view.due_total, view.completed_total) == (due, completed)
    assert view.reached == (due > 0 and completed >= due)
    assert view.completed_total <= view.due_total
    assert view.window_last_mission_day - view.window_first_mission_day + 1 == rules().crew_goal_window_days


@pytest.mark.trace("T-E22-020", "V47", "D09", "INV-E22-07")
def test_sim_and_live_are_computed_from_their_own_inputs_and_never_mixed() -> None:
    sim = goal(origin="SIM", checked={m: set(range(13, 20)) for m in CREW})
    live = goal(
        origin="LIVE",
        consents={"guest": ON},
        roster={d: frozenset({"guest"}) for d in range(1, 30)},
        checked={"guest": {19}},
    )
    assert (sim.origin_mode, live.origin_mode) == ("SIM", "LIVE")
    assert (sim.completed_total, sim.due_total) == (28, 28)
    assert (live.completed_total, live.due_total) == (1, 7)
    assert sim == goal(origin="SIM", checked={m: set(range(13, 20)) for m in CREW})


@pytest.mark.trace("T-E22-050", "T-E22-051", "T-E22-056", "INV-E22-05", "R32", "V45")
def test_only_the_owner_reads_a_person_view_and_the_crew_goal_refuses_any_breakdown() -> None:
    require_owner("mara", "mara")
    for session in ("tomas", None, ""):
        with pytest.raises(UnauthorizedScope):
            require_owner(session, "mara")
    require_no_selector({})
    for query in ({"person_id": "tomas"}, {"day": "3"}, {"x": "y"}):
        with pytest.raises(UnauthorizedScope):
            require_no_selector(query)
    reject_breakdown({})
    for query in ({"by": "person"}, {"person_id": "tomas"}, {"day": "3"}):
        with pytest.raises(AggregateOnly):
            reject_breakdown(query)


@pytest.mark.trace("T-E22-034", "T-E22-036", "INV-E22-09", "INV-E22-05")
@PROP
@given(
    seed=st.integers(min_value=0, max_value=10**6),
    days=st.sets(st.integers(min_value=1, max_value=30), max_size=30),
)
def test_result_digest_ignores_order_and_as_of_and_tracks_content(seed: int, days: set[int]) -> None:
    def streak(patient: str, as_of: datetime):  # type: ignore[no-untyped-def]
        return derive_streak(
            patient_id=patient, mission_id="demo-mission", origin_mode="SIM", facts=facts_for(days, patient=patient),
            today=30, roster_first_day=1, as_of=as_of, rules=rules(), clock_version="clock-1", cursor="c",
        )  # fmt: skip

    views = [streak("mara", AS_OF), streak("tomas", AS_OF)]
    shuffled = list(reversed(views))
    random.Random(seed).shuffle(shuffled)
    later = [replace(v, as_of=datetime(2027, 1, 1, tzinfo=UTC)) for v in views]
    crew = goal(today=30, checked={"mara": days})
    assert result_sha256(views, (), crew) == result_sha256(shuffled, (), crew)
    assert result_sha256(views, (), crew) == result_sha256(
        later, (), replace(crew, as_of=datetime(2027, 1, 1, tzinfo=UTC))
    )
    assert result_sha256(views, (), None) != result_sha256(views, (), crew)
    assert result_sha256(views[:1], (), crew) != result_sha256(views, (), crew)
    awards = award_views("mara", "demo-mission", "SIM", [])
    assert result_sha256(views, awards, crew) == result_sha256(views, (), crew)
