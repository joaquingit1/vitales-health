"""E23 section 12 tests T-E23-011 to 014, 056, 067 (overlays, I-O1 to I-O5, on kernel fakes)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from e23m_helpers import START, Station, build_station, documents, edited, operator, person
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ConfigName
from crew_health.kernel.scheduler import AbsoluteDeadline
from crew_health.mission.application.deadlines import register_deadlines, sweep_overdue
from crew_health.mission.application.init import initialize_mission
from crew_health.mission.application.use_cases import (
    POST_EVA_HANDLER,
    Caller,
    EventTimeFacts,
    create_overlay,
    end_overlay,
    end_post_eva,
    record_event,
)
from crew_health.mission.domain import overlay as rules
from crew_health.mission.domain.errors import (
    InvalidTransition,
    InvalidValue,
    MissionDomainError,
    StaleVersion,
    UnauthorizedScope,
)

pytestmark = pytest.mark.e23
EXAMPLES = settings(max_examples=1000, deadline=None, suppress_health_check=list(HealthCheck))


def crew(person_id: str = "per_a") -> Caller:
    return person(person_id, "crew")


def init(station: Station) -> None:
    with station.run(operator().actor) as uow:
        initialize_mission(
            station.services,
            uow,
            operator(),
            mission_mode="SIM",
            planned_length_days=30,
            site_node_id="node",
            environment_id="lunar_surface",
        )


def make(
    station: Station,
    who: Caller,
    kind: str,
    *,
    started: datetime | None = None,
    planned: datetime | None = None,
):  # type: ignore[no-untyped-def]
    with station.run(who.actor) as uow:
        return create_overlay(
            station.services, uow, who, kind=kind, planned_start=planned, started_at=started, location=None
        )


def end(station: Station, who: Caller, record, at: datetime):  # type: ignore[no-untyped-def]
    with station.run(who.actor) as uow:
        return end_overlay(
            station.services,
            uow,
            who,
            overlay_id=record.overlay_id,
            ended_at=at,
            expected_revision=record.revision,
        )


def event(
    station: Station,
    event_type: str,
    persons: tuple[str, ...],
    at: datetime,
    *,
    who: Caller | None = None,
    state: str = "known",
):  # type: ignore[no-untyped-def]
    caller = who or crew()
    with station.run(caller.actor) as uow:
        return record_event(
            station.services,
            uow,
            caller,
            event_type=event_type,
            occurred_at=at,
            person_ids=persons,
            location=None,
            note=None,
            origin_mode="SIM",
            time=EventTimeFacts(state, at if state in ("known", "replay") else None),  # type: ignore[arg-type]
        )


@pytest.fixture
def ready(station: Station) -> Station:
    init(station)
    return station


@pytest.mark.trace("T-E23-011", "I-O1", "I-O2", "I-O5", "C12")
def test_overlay_invariants_and_the_source_field(ready: Station) -> None:
    mine = make(ready, crew(), "exercise", started=START)
    assert (mine.state, mine.source_kind, mine.source_ref, mine.person_id) == (
        "active",
        "person",
        None,
        "per_a",
    )
    with pytest.raises(InvalidTransition):  # I-O1: one active overlay of each kind per person
        make(ready, crew(), "exercise", started=START + timedelta(minutes=1))
    other = make(ready, crew("per_b"), "exercise", started=START)  # another person may have their own
    assert other.person_id == "per_b"
    planned = make(ready, crew(), "sleep", planned=START + timedelta(hours=1))
    assert planned.state == "planned" and planned.started_at is None
    ended_planned = end(ready, crew(), planned, START + timedelta(minutes=5))
    assert (
        ended_planned.state == "ended" and ended_planned.started_at is None
    )  # I-O2: null started_at preserved
    with pytest.raises(InvalidValue):
        end(ready, crew(), mine, START - timedelta(seconds=1))  # ended_at before started_at
    with pytest.raises(InvalidValue):
        make(ready, crew(), "sleep")  # neither planned_start nor started_at
    with pytest.raises(InvalidValue):
        make(ready, crew(), "sleep", started=START, planned=START)
    closed = end(ready, crew(), mine, START + timedelta(minutes=30))
    with pytest.raises(InvalidTransition):
        end(ready, crew(), closed, START + timedelta(minutes=31))
    assert (
        make(ready, crew(), "exercise", started=START + timedelta(hours=1)).state == "active"
    )  # the kind is free again


@pytest.mark.trace("T-E23-011", "I-O5", "R-E23-7")
def test_an_overlay_kind_absent_from_the_profile_is_refused_but_ending_uses_the_pinned_definition() -> None:
    station = build_station()
    init(station)
    live = make(station, crew(), "sleep", started=START)
    narrowed = edited(
        documents(),
        ConfigName.ENVIRONMENT_PROFILES,
        lambda d: [p["overlays"].remove("sleep") for p in d["profiles"]],
    )
    from e23m_helpers import make_release

    release = make_release("rel_test_0002", narrowed)
    with pytest.raises(InvalidValue):
        with station.run(crew().actor, release=release) as uow:
            create_overlay(
                station.services,
                uow,
                crew(),
                kind="sleep",
                planned_start=None,
                started_at=START,
                location=None,
            )
    with station.run(crew().actor, release=release) as uow:  # a release cannot prevent an authorized end
        ended = end_overlay(
            station.services,
            uow,
            crew(),
            overlay_id=live.overlay_id,
            ended_at=START + timedelta(hours=8),
            expected_revision=live.revision,
        )
    assert ended.state == "ended" and ended.pinned_release_id == "rel_test_0001"
    with pytest.raises(InvalidValue):  # no person on the session: direct operator creation is R-E23-7
        with station.run(operator().actor) as uow:
            create_overlay(
                station.services,
                uow,
                operator(),
                kind="exercise",
                planned_start=None,
                started_at=START,
                location=None,
            )


@pytest.mark.trace("T-E23-012", "E25 seam", "C12")
def test_intervals_are_exact_open_and_closed_and_over_the_bound_raises(ready: Station) -> None:
    closed = end(ready, crew(), make(ready, crew(), "exercise", started=START), START + timedelta(hours=1))
    open_one = make(ready, crew(), "sleep", started=START + timedelta(hours=2))
    make(ready, crew(), "eva", planned=START + timedelta(hours=3))  # planned: no interval until it starts
    port = ready.state
    got = port.intervals("per_a", {"exercise", "sleep", "eva"}, START, START + timedelta(hours=5), 10)
    assert [(f.overlay_id, f.started_at, f.ended_at) for f in got] == [
        (closed.overlay_id, START, START + timedelta(hours=1)),
        (open_one.overlay_id, START + timedelta(hours=2), None),
    ]
    assert port.intervals(
        "per_a", {"exercise"}, START + timedelta(hours=1), START + timedelta(hours=9), 10
    )  # touches
    assert not port.intervals(
        "per_a", {"exercise"}, START + timedelta(hours=1, seconds=1), START + timedelta(hours=9), 10
    )
    assert port.intervals("per_a", {"sleep"}, START, START + timedelta(hours=5), 1)
    with pytest.raises(Exception, match="overlay rows above the bound"):
        port.intervals(
            "per_a", {"exercise", "sleep"}, START, START + timedelta(hours=5), 1
        )  # never a truncated list
    assert [f.overlay_id for f in port.active(["per_a", "per_b"], START + timedelta(hours=2))["per_a"]] == [
        open_one.overlay_id
    ]
    assert port.active(["per_b"], START) == {"per_b": []}


@pytest.mark.trace("T-E23-013", "I-O4", "3.1")
def test_post_eva_ends_at_the_mission_day_end_survives_restart_and_clock_correction(ready: Station) -> None:
    make(ready, crew(), "eva", started=START)
    event(ready, "eva_ended", ("per_a",), START + timedelta(hours=1))
    post = next(r for r in ready.store.overlay_rows.values() if r.kind == "post_eva")
    day_end = ready.services.day_end(START)
    assert day_end == datetime(2026, 1, 21, tzinfo=START.tzinfo)
    job, _, _ = ready.factory.state.schedules["mission.post_eva." + post.overlay_id]
    assert job.handler == POST_EVA_HANDLER and job.deadline == AbsoluteDeadline(
        day_end
    )  # absolute, not elapsed
    ready.clock.at = START - timedelta(hours=3)  # a clock correction moves the clock, never the deadline
    assert job.deadline == AbsoluteDeadline(day_end)
    ready.clock.at = day_end + timedelta(minutes=1)
    registered: dict[str, object] = {}

    class Registry:
        def register(self, handler: str, fn: object) -> None:
            registered[handler] = fn

    register_deadlines(Registry(), ready.services)  # a restarted hub registers the handlers again
    with ready.run(operator().actor) as uow:
        registered[POST_EVA_HANDLER](uow, job)  # type: ignore[operator]
    ended = ready.store.overlay_rows[post.overlay_id]
    assert ended.state == "ended" and ended.ended_at == day_end
    with ready.run(operator().actor) as uow:
        end_post_eva(ready.services, uow, job)  # a repeated job changes nothing
    assert ready.store.overlay_rows[post.overlay_id] == ended


@pytest.mark.trace("T-E23-013", "3.1", "AGENTS6")
def test_a_forward_clock_jump_is_caught_up_by_the_sweep(ready: Station) -> None:
    make(ready, crew(), "post_eva", started=START)
    ready.clock.at = START + timedelta(days=3)
    with ready.run(operator().actor) as uow:
        assert sweep_overdue(ready.services, uow, people=("per_a",)) == 1
    only = next(iter(ready.store.overlay_rows.values()))
    assert only.state == "ended" and only.ended_at == ready.services.day_end(START)


@pytest.mark.trace("T-E23-014", "V59", "C12", "I-O3")
def test_eva_ended_ends_eva_and_starts_post_eva_atomically_an_operator_can_end_an_overlay(
    ready: Station,
) -> None:
    eva = make(ready, crew(), "eva", started=START)
    ready.store.fail_after = ready.store.writes + 1  # crash after the first write of the next command
    with pytest.raises(RuntimeError):
        event(ready, "eva_ended", ("per_a",), START + timedelta(hours=1))
    assert ready.store.overlay_rows[eva.overlay_id].state == "active" and len(ready.store.overlay_rows) == 1
    assert ready.store.event_rows == []  # nothing partial: the event was not recorded either
    ready.store.fail_after = None
    recorded = event(ready, "eva_ended", ("per_a",), START + timedelta(hours=1))
    rows = {r.kind: r for r in ready.store.overlay_rows.values()}
    assert rows["eva"].state == "ended" and rows["eva"].ended_at == START + timedelta(hours=1)
    assert rows["post_eva"].state == "active" and rows["post_eva"].source_kind == "mission_event"
    assert (
        rows["post_eva"].source_ref == recorded.event_id
        and rows["post_eva"].started_at == rows["eva"].ended_at
    )
    sleeper = make(ready, crew("per_b"), "sleep", started=START)
    with pytest.raises(UnauthorizedScope):
        end(ready, crew("per_c"), sleeper, START + timedelta(hours=1))  # a crewmate cannot end it
    assert end(ready, operator(), sleeper, START + timedelta(hours=1)).state == "ended"  # an operator can
    cmo = person("per_c", "crew_medical_officer")
    other = make(ready, crew("per_b"), "exercise", started=START)
    assert end(ready, cmo, other, START + timedelta(hours=1)).state == "ended"


@pytest.mark.trace("T-E23-056", "10.1", "S5.8")
def test_two_clients_ending_the_same_overlay_one_wins_one_stale_version(ready: Station) -> None:
    record = make(ready, crew(), "exercise", started=START)
    assert end(ready, crew(), record, START + timedelta(minutes=10)).state == "ended"
    with pytest.raises(StaleVersion):
        end(ready, operator(), record, START + timedelta(minutes=11))  # the second client held revision 1


@pytest.mark.trace("T-E23-067", "3.5", "3.6")
def test_a_delayed_event_older_than_the_last_effect_changes_nothing_a_conflicting_effect_is_skipped_and_audited(
    ready: Station,
) -> None:
    first = event(ready, "eva_started", ("per_a",), START + timedelta(hours=2))
    eva = next(r for r in ready.store.overlay_rows.values() if r.kind == "eva")
    assert eva.state == "active" and eva.last_event_id == first.event_id
    event(ready, "eva_started", ("per_a",), START + timedelta(hours=3))  # eva is already active: a conflict
    assert len([r for r in ready.store.overlay_rows.values() if r.kind == "eva"]) == 1
    skipped = [a for a in ready.factory.state.audit if a.event_type == "mission.overlay_effect_skipped"]
    assert len(skipped) == 1 and skipped[0].refs["overlay_id"] == eva.overlay_id
    assert len(ready.store.event_rows) == 2  # the real report is kept either way
    event(ready, "eva_ended", ("per_a",), START + timedelta(hours=4))
    event(ready, "eva_ended", ("per_a",), START + timedelta(hours=5))  # eva_ended with none active
    assert (
        len([a for a in ready.factory.state.audit if a.event_type == "mission.overlay_effect_skipped"]) == 2
    )
    late = event(
        ready, "eva_started", ("per_a",), START + timedelta(hours=1)
    )  # delayed: older than the last effect
    kinds = sorted((r.kind, r.state) for r in ready.store.overlay_rows.values())
    assert kinds == [("eva", "ended"), ("post_eva", "active")]  # nothing new started
    assert late.event_id in {e.event_id for e in ready.store.event_rows}
    for state in ("unknown", "future"):
        before = dict(ready.store.overlay_rows)
        event(ready, "eva_started", ("per_b",), START + timedelta(hours=6), state=state)
        assert ready.store.overlay_rows == before  # untrusted time is stored without effects


@pytest.mark.trace("T-E23-011", "T-E23-009", "I-O1", "I-O2", "I-O3", "I-O4")
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(["start", "plan", "begin", "end", "eva_started", "eva_ended"]),
            st.sampled_from(["eva", "post_eva", "exercise", "sleep"]),
            st.integers(0, 600),
        ),
        max_size=30,
    )
)
@EXAMPLES
def test_overlay_rules_hold_for_any_sequence_of_operations(steps: list[tuple[str, str, int]]) -> None:
    kinds = {"eva", "post_eva", "exercise", "sleep"}
    records: list[rules.OverlayRecord] = []
    for number, (name, kind, minute) in enumerate(steps):
        at = START + timedelta(minutes=minute)
        active = {r.kind for r in records if r.state == "active"}
        try:
            if name in ("start", "plan"):
                records.append(
                    rules.create(
                        overlay_id=f"o{number}",
                        person_id="p",
                        kind=kind,
                        kinds_in_profile=kinds,
                        planned_start=at if name == "plan" else None,
                        started_at=at if name == "start" else None,
                        location=None,
                        source_kind="person",
                        source_ref=None,
                        active_kinds=active,
                        release_id="r",
                    )
                )
            elif name == "begin":
                target = next((r for r in records if r.state == "planned"), None)
                if target is not None:
                    records[records.index(target)] = rules.start(target, at, active)
            elif name == "end":
                target = next(
                    (r for r in records if r.kind == kind and r.state in ("planned", "active")), None
                )
                if target is not None:
                    records[records.index(target)] = rules.end(target, at)
            else:
                effects, skipped = rules.plan_event_effects(
                    "eva" if name == "eva_started" else "post_eva",
                    None if name == "eva_started" else "eva",
                    active,
                )
                if not skipped:
                    for effect in effects:
                        current = next(
                            (r for r in records if r.kind == effect.kind and r.state == "active"), None
                        )
                        if effect.action == "end" and current is not None:
                            records[records.index(current)] = rules.end(current, at)
                        elif effect.action == "start":
                            records.append(
                                rules.create(
                                    overlay_id=f"o{number}{effect.kind}",
                                    person_id="p",
                                    kind=effect.kind,
                                    kinds_in_profile=kinds,
                                    planned_start=None,
                                    started_at=at,
                                    location=None,
                                    source_kind="mission_event",
                                    source_ref="e",
                                    active_kinds=(),
                                    release_id="r",
                                )
                            )
        except MissionDomainError:
            pass
        rules.assert_one_active_per_kind(records)  # I-O1
        for r in records:
            if r.started_at is not None and r.ended_at is not None:
                assert r.ended_at >= r.started_at  # I-O2
            if r.state == "planned":
                assert r.started_at is None
