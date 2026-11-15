"""Hold, attention, Act routing during sleep, headline and list order (E24 3.12, C14).

T-E24-042, 043, 080, 081, 099 (pure part), 121 (pure part); Q9 and D2.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain import attention, hold
from crew_health.rules.domain.definitions import HeadlineRule, RulesDocument
from crew_health.rules.domain.headline import headline
from crew_health.rules.domain.hold import OverlayFact, route_act
from crew_health.rules.domain.ordering import after, order_key, ordered
from crew_health.rules.domain.vocabulary import ClearReason, Level, OriginMode, WarningState
from crew_health.rules.domain.warning import HeadlineFields, PersonActor, Warning, acknowledge

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "V74", "H4", "H16")]

SUPPRESSED = frozenset({"sleep", "eva"})  # the routine-rules `suppressed_overlays` of the example release


def suppressed_overlays() -> frozenset[str]:
    return frozenset(h.example_value("routine-rules")["suppressed_overlays"])  # type: ignore[arg-type]


def base_warning(world: h.World) -> Warning:
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (w,) = world.by_definition("co2_above_limit")
    return w


def variant(
    w: Warning,
    wid: str,
    level: Level,
    minutes: int,
    *,
    rank: int = 20,
    solo: bool = False,
    fragment: str = "x",
) -> Warning:
    return replace(
        w,
        warning_id=wid,
        level=level,
        raised_at=h.T0 + timedelta(minutes=minutes),
        headline=HeadlineFields(fragment, rank, solo),
    )


def at(minutes: int) -> datetime:
    return h.T0 + timedelta(minutes=minutes)


def test_the_suppressed_overlays_come_from_configuration() -> None:
    assert suppressed_overlays() == SUPPRESSED


@pytest.mark.trace("T-E24-080")
def test_t080_hold_spans_merge_sleep_then_walk_and_touching_overlays() -> None:
    spans = hold.spans(
        [
            OverlayFact("sleep", at(0), at(60)),
            OverlayFact("eva", at(60), at(120)),  # touching: merges
            OverlayFact("exercise", at(10), at(20)),  # not suppressed
            OverlayFact("sleep", at(300), None),  # still active
        ],
        suppressed_overlays(),
    )
    assert [(s.start, s.end) for s in spans] == [(at(0), at(120)), (at(300), None)]
    assert spans[0].overlays == {"sleep", "eva"}


@pytest.mark.trace("T-E24-080", "T-E24-081", "H4")
def test_t080_emergency_is_never_held_and_a_station_session_is_never_held() -> None:
    spans = hold.spans([OverlayFact("sleep", at(0), at(600))], suppressed_overlays())
    inside = at(100)
    assert hold.held_state(Level.EMERGENCY, inside, spans, person_session=True) is None
    assert hold.held_state(Level.WATCH, inside, spans, person_session=False) is None
    state = hold.held_state(Level.WATCH, inside, spans, person_session=True)
    assert state is not None and state.since == at(0) and state.until == at(600)
    assert hold.held_state(Level.ACT, inside, spans, person_session=True) is not None


@pytest.mark.trace("T-E24-081")
def test_t081_a_warning_raised_before_the_span_is_not_re_presented_during_it() -> None:
    spans = hold.spans([OverlayFact("sleep", at(60), at(600))], suppressed_overlays())
    assert hold.held_state(Level.WATCH, at(30), spans, person_session=True) is None
    assert hold.held_state(Level.WATCH, at(600), spans, person_session=True) is None  # the end is exclusive


@pytest.mark.trace("T-E24-081")
def test_a_held_summary_keeps_cleared_items_and_drops_acknowledged_ones(world: h.World) -> None:
    w = base_warning(world)
    span = hold.spans([OverlayFact("sleep", at(-10), at(600))], SUPPRESSED)[0]
    a = variant(w, "wrn_a", Level.WATCH, 5)
    b = acknowledge(variant(w, "wrn_b", Level.WATCH, 6), PersonActor("p1"), at(7))
    c = replace(
        variant(w, "wrn_c", Level.WATCH, 8),
        state=WarningState.CLEARED,
        cleared_at=at(9),
        clear_reason=ClearReason.CONDITION_FALSE,
    )
    e = variant(w, "wrn_e", Level.EMERGENCY, 9)
    items = hold.held_items([a, b, c, e], span, person_id="p1")
    assert [x.warning_id for x in items] == ["wrn_a", "wrn_c"]


@pytest.mark.trace("T-E24-121", "D2", "Q9")
def test_t121_the_storm_is_emergency_so_it_is_never_held_in_sleep_or_on_a_walk(
    deployment_doc: RulesDocument,
) -> None:
    storm = deployment_doc.by_id()["radiation_storm_shelter"]
    spans = hold.spans(
        [OverlayFact("sleep", at(0), at(480)), OverlayFact("eva", at(600), at(700))], suppressed_overlays()
    )
    for minute in (100, 650):
        assert hold.held_state(storm.level, at(minute), spans, person_session=True) is None


# --- Q9: Act now while asleep ------------------------------------------------------------------------


@pytest.mark.trace("Q9")
def test_q9_act_goes_at_once_to_someone_awake_and_the_sleeper_waits_for_the_configured_interval() -> None:
    routing = route_act(
        sleepers=["p3"],
        awake_responders=["p2", "p1"],
        acknowledged_by=[],
        elapsed_seconds=10,
        wake_after_seconds=300,
        responders_may_receive=True,
    )
    assert routing.notify_now == ("p1", "p2") and routing.wake_now == () and routing.wake_after_seconds == 300


@pytest.mark.trace("Q9")
def test_q9_an_awake_acknowledgement_prevents_the_wake_and_expiry_wakes() -> None:
    kw = {
        "sleepers": ["p3"],
        "awake_responders": ["p1"],
        "wake_after_seconds": 300,
        "responders_may_receive": True,
    }
    acked = route_act(acknowledged_by=["p1"], elapsed_seconds=999, **kw)  # type: ignore[arg-type]
    assert acked.wake_now == ()
    expired = route_act(acknowledged_by=["p9"], elapsed_seconds=300, **kw)  # type: ignore[arg-type]
    assert expired.wake_now == ("p3",)  # an acknowledgement by someone not awake does not count


@pytest.mark.trace("Q9")
@pytest.mark.parametrize(
    "case",
    [
        {"awake_responders": [], "wake_after_seconds": 300, "responders_may_receive": True},  # nobody awake
        {
            "awake_responders": ["p1"],
            "wake_after_seconds": None,
            "responders_may_receive": True,
        },  # not configured
        {
            "awake_responders": ["p1"],
            "wake_after_seconds": 300,
            "responders_may_receive": False,
        },  # no disclosure basis
    ],
)
def test_q9_the_sleeper_is_woken_at_once_when_nobody_can_act_or_the_time_is_not_configured(
    case: dict[str, object],
) -> None:
    routing = route_act(sleepers=["p3"], acknowledged_by=[], elapsed_seconds=0, **case)  # type: ignore[arg-type]
    assert routing.wake_now == ("p3",) and routing.wake_after_seconds is None


@pytest.mark.trace("Q9")
def test_q9_a_woken_act_is_not_held_but_a_walk_still_holds_it() -> None:
    sleep = hold.spans([OverlayFact("sleep", at(0), at(600))], SUPPRESSED)
    walk = hold.spans([OverlayFact("eva", at(0), at(600))], SUPPRESSED)
    assert hold.held_state(Level.ACT, at(5), sleep, person_session=True, act_woken=True) is None
    assert hold.held_state(Level.ACT, at(5), sleep, person_session=True, act_woken=False) is not None
    assert (
        hold.held_state(Level.ACT, at(5), walk, person_session=True, act_woken=True) is not None
    )  # EVA suppression stays
    assert (
        hold.held_state(Level.WATCH, at(5), sleep, person_session=True, act_woken=True) is not None
    )  # lower levels wait


# --- attention ---------------------------------------------------------------------------------------


@pytest.mark.trace("T-E24-134", "H16")
def test_attention_shows_every_unacknowledged_act_most_recent_first_and_counts_the_overflow(
    world: h.World,
) -> None:
    w = base_warning(world)
    watch = variant(w, "wrn_w", Level.WATCH, 50)
    acts = [variant(w, f"wrn_a{i}", Level.ACT, i) for i in range(1, 8)]
    emergencies = [variant(w, "wrn_e1", Level.EMERGENCY, 20), variant(w, "wrn_e2", Level.EMERGENCY, 30)]
    plan = attention.plan([watch, *acts, *emergencies], person_id="p1", held_ids=set(), max_banner_items=5)
    assert plan.emergencies == ("wrn_e2", "wrn_e1")  # all emergencies together, most recent first
    assert plan.banner == ("wrn_a7", "wrn_a6", "wrn_a5", "wrn_a4", "wrn_a3") and plan.more == 2
    assert plan.per_warning["wrn_w"].interrupt is False  # a watch item never interrupts
    assert plan.per_warning["wrn_e2"].order == 0 and plan.per_warning["wrn_a7"].order == 2
    assert all(
        plan.per_warning[f"wrn_a{i}"].interrupt for i in range(1, 8)
    )  # none concealed behind a lower level


def test_attention_acknowledging_removes_only_from_this_persons_banner_and_held_acts_wait(
    world: h.World,
) -> None:
    w = base_warning(world)
    a1 = acknowledge(variant(w, "wrn_1", Level.ACT, 1), PersonActor("p1"), at(2))
    a2 = variant(w, "wrn_2", Level.ACT, 3)
    a3 = variant(w, "wrn_3", Level.ACT, 4)
    mine = attention.plan([a1, a2, a3], person_id="p1", held_ids={"wrn_3"}, max_banner_items=5)
    assert mine.banner == ("wrn_2",) and not mine.per_warning["wrn_3"].interrupt
    other = attention.plan([a1, a2], person_id="p2", held_ids=set(), max_banner_items=5)
    assert other.banner == ("wrn_2", "wrn_1")  # the warning itself stays open for others


@given(
    st.lists(st.tuples(st.sampled_from(list(Level)), st.integers(0, 500), st.booleans()), max_size=12),
    st.integers(0, 6),
)
@h.PROP
@pytest.mark.trace("T-E24-134", "H16")
def test_attention_never_hides_an_unheld_unacknowledged_act_or_emergency(
    specs: list[tuple[Level, int, bool]], bound: int
) -> None:
    from crew_health.rules.domain.definitions import parse_rules_document

    world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
    base = base_warning(world)
    items = [variant(base, f"wrn_{i:02d}", lv, m) for i, (lv, m, _) in enumerate(specs)]
    held = {
        w.warning_id
        for w, (lv, _, held_flag) in zip(items, specs, strict=True)
        if held_flag and lv is Level.ACT
    }
    plan = attention.plan(items, person_id="p1", held_ids=held, max_banner_items=bound)
    expected = {
        w.warning_id
        for w in items
        if w.level is Level.EMERGENCY or (w.level is Level.ACT and w.warning_id not in held)
    }
    interrupting = {wid for wid, a in plan.per_warning.items() if a.interrupt}
    assert interrupting == expected
    assert len(plan.banner) + plan.more == len(
        [w for w in items if w.level is Level.ACT and w.warning_id not in held]
    )
    assert len(plan.banner) <= bound


# --- headline and list order -------------------------------------------------------------------------


def example_list(name: str) -> list[dict[str, object]]:
    path = h.ROOT / "docs" / "product" / "api" / "examples" / "warnings" / f"list-{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["value"]["items"]  # type: ignore[no-any-return]


@pytest.mark.trace("T-E24-042", "C14")
@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        # CONTRACTS section 14 "Headline": "With the example documents this gives ..." (independent expected text).
        ("station", "Short sleep, CO2 above the limit"),
        ("transit", "Solar storm: go to the shelter"),
        ("lunar", "Dust after the surface walk"),
        ("mars", "Sol 3: getting used to gravity"),
    ],
)
def test_t042_headline_vectors_for_the_four_environments(
    world: h.World, example_doc: RulesDocument, environment: str, expected: str
) -> None:
    base = base_warning(world)
    items = example_list(environment)
    warnings = [
        replace(
            base,
            warning_id=f"wrn_{environment}_{i}",
            headline=HeadlineFields(
                i_["headline"]["fragment"], i_["headline"]["rank"], i_["headline"]["solo"]
            ),  # type: ignore[index]
            raised_at=datetime.fromisoformat(str(i_["raised_at"])),
        )
        for i, i_ in enumerate(items)
    ]
    assert headline(warnings, example_doc.headline).text == expected


def test_headline_none_text_and_the_fragment_bound(world: h.World, example_doc: RulesDocument) -> None:
    base = base_warning(world)
    assert headline([], example_doc.headline).text == example_doc.headline.none_text
    three = [variant(base, f"wrn_{i}", Level.WATCH, i, rank=i, fragment=f"frag {i}") for i in range(3)]
    rule = HeadlineRule(2, example_doc.headline.separator, "none")
    result = headline(three, rule)
    assert result.warning_ids == ("wrn_0", "wrn_1") and result.text == "Frag 0, frag 1"
    ended = replace(
        three[0], state=WarningState.CLEARED, cleared_at=at(1), clear_reason=ClearReason.CONDITION_FALSE
    )
    assert headline([ended, *three[1:]], rule).warning_ids == ("wrn_1", "wrn_2")  # only active warnings count


@pytest.mark.trace("T-E24-043")
def test_t043_list_order_is_headline_rank_then_raised_at_then_id(world: h.World) -> None:
    base = base_warning(world)
    items = [
        variant(base, "wrn_c", Level.ACT, 5, rank=20),
        variant(base, "wrn_b", Level.ACT, 1, rank=20),
        variant(base, "wrn_a", Level.ACT, 1, rank=20),
        variant(base, "wrn_z", Level.WATCH, 9, rank=10),
    ]
    assert [w.warning_id for w in ordered(items)] == ["wrn_z", "wrn_a", "wrn_b", "wrn_c"]
    page = after(items, order_key(ordered(items)[1]), 2)
    assert [w.warning_id for w in page] == ["wrn_b", "wrn_c"]
    assert after(items, None, 0) == ()


def test_the_example_list_order_defect_is_recorded_r_e24_6(example_doc: RulesDocument) -> None:
    """R-E24-6: the example list puts rank 20 before rank 10 against the operation's description."""
    station = example_list("station")
    ranks = [i["headline"]["rank"] for i in station]  # type: ignore[index]
    assert ranks == [20, 10]
    assert OriginMode.LIVE.value == "LIVE" and example_doc.headline.max_fragments >= 1
