"""Assistant insights: bounds, raising at info or watch only, expiry and dismissal (E24 3.13, DE24-5).

T-E24-003, 060 to 064, 084, 085, 088 (pure parts); R40, V10, V65, D17; INV-E24-02, INV-E24-03, INV-E24-10.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain import insight_bounds
from crew_health.rules.domain.decisions import Clear, Raise, RejectInsight
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.inputs import (
    AssistantPolicy,
    EvidenceKey,
    InsightDraft,
    InsightExpiredInput,
    InsightInput,
    RecordFacts,
)
from crew_health.rules.domain.vocabulary import ASSISTANT_LEVELS, ClearReason, Level, OriginMode, WarningState
from crew_health.rules.domain.warning import ActionNotAllowed, PersonActor, dismiss

pytestmark = [pytest.mark.E24, pytest.mark.trace("R40", "V10", "V65", "D17", "INV-E24-02", "INV-E24-03")]

OBS = h.OBS_KEY
CHECK = EvidenceKey("daily_check", "chk_1")


policy_from_config = h.assistant_policy


def facts(*pairs: tuple[EvidenceKey, RecordFacts]) -> tuple[tuple[EvidenceKey, RecordFacts], ...]:
    return tuple(pairs)


good = h.good_record
draft = h.insight_draft


def validate(d: InsightDraft, *pairs: tuple[EvidenceKey, RecordFacts]) -> tuple[str, ...]:
    return insight_bounds.validate(d, dict(pairs), policy_from_config())


def test_a_valid_insight_has_no_violation() -> None:
    assert validate(draft(), (OBS, good())) == ()
    assert (
        validate(
            draft(evidence=(OBS, CHECK), routine_occurrence_ids=("occ_1",)),
            (OBS, good()),
            (CHECK, good("submitted")),
        )
        == ()
    )


@pytest.mark.trace("T-E24-060")
@pytest.mark.parametrize(
    ("mutation", "pairs", "code"),
    [
        ({"level": Level.ACT}, [(OBS, good())], "level_not_allowed"),
        ({"level": Level.EMERGENCY}, [(OBS, good())], "level_not_allowed"),
        ({"topic_id": "unlisted"}, [(OBS, good())], "topic_unknown"),
        ({"evidence": ()}, [], "evidence_empty"),
        ({"evidence": (EvidenceKey("reading", "r1"),)}, [], "evidence_kind"),  # type: ignore[arg-type]
        ({}, [], "evidence_not_found"),
        ({}, [(OBS, RecordFacts(False, None, None, False, None, None, None, None))], "evidence_not_found"),
        ({}, [(OBS, replace(good(), owner_person_id="p2"))], "evidence_other_person"),
        ({}, [(OBS, replace(good(), origin_mode=OriginMode.REPLAY))], "evidence_replay"),
        ({}, [(OBS, replace(good(), is_sensor=True))], "evidence_replay"),
        ({}, [(OBS, good("rejected"))], "evidence_not_accepted"),
        ({"evidence": (CHECK,)}, [(CHECK, good("draft"))], "evidence_not_accepted"),
        (
            {"evidence": (EvidenceKey("routine_occurrence", "occ"),)},
            [(EvidenceKey("routine_occurrence", "occ"), good("skipped"))],
            "evidence_not_accepted",
        ),
        ({}, [(OBS, replace(good(), origin_mode=OriginMode.SIM))], "evidence_origin_mixed"),
        ({"habitat_fact_text_present": True}, [(OBS, good())], "habitat_fact_text"),
        ({"routine_occurrence_ids": ("a", "b")}, [(OBS, good())], "action_count"),
    ],
)
def test_t060_each_violation_code_is_reported(
    mutation: dict[str, object], pairs: list[tuple[EvidenceKey, RecordFacts]], code: str
) -> None:
    assert code in validate(draft(**mutation), *pairs)


def test_the_assistant_never_reaches_act_even_when_the_policy_lists_it() -> None:
    wide = AssistantPolicy(frozenset(Level), policy_from_config().topics)
    found = insight_bounds.validate(draft(level=Level.ACT), {OBS: good()}, wide)
    assert found == ("level_not_allowed",)  # a hard invariant, whatever the policy lists


def insight_world() -> h.World:
    return h.World(parse_rules_document(h.deployment_rules()), h.release_documents())


@pytest.mark.trace("T-E24-061", "T-E24-063", "T-E24-064")
def test_t063_a_valid_insight_raises_at_watch_with_its_own_evidence_and_the_topic_region() -> None:
    world = insight_world()
    decision = world.feed(InsightInput(draft(), facts((OBS, good())), policy_from_config()))
    (r,) = [s for s in decision if isinstance(s, Raise)]
    assert r.pin.definition.level is Level.WATCH and r.pin.definition.raised_by.value == "assistant"
    (w,) = world.by_definition("assistant_pattern")
    assert w.level in ASSISTANT_LEVELS and w.person_id == "p1" and w.insight_id == "ins_1"
    assert [e.kind.value for e in w.evidence] == ["insight", "observation"]
    assert w.steps == ("Go to bed earlier.",) and w.body_regions == ("eyes",)
    assert w.what == "You slept less." and w.headline.fragment == "Short sleep"


@pytest.mark.trace("T-E24-061", "T-E24-062", "V65", "D17")
def test_t061_a_pinned_act_proposal_is_rejected_and_a_clear_attempt_changes_nothing(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (co2,) = world.by_definition("co2_above_limit")
    for level in (Level.ACT, Level.EMERGENCY):
        decision = world.feed(InsightInput(draft(level=level), facts((OBS, good())), policy_from_config()))
        assert [type(s) for s in decision] == [RejectInsight] and decision[0].codes == ("level_not_allowed",)  # type: ignore[union-attr]
    assert world.warnings[co2.warning_id] == co2
    assert not any(
        isinstance(s, Clear)
        for s in world.feed(InsightInput(draft(), facts((OBS, good())), policy_from_config()))
    )
    assert world.warnings[co2.warning_id] == co2  # CO2 unchanged: state and revision


@pytest.mark.trace("T-E24-064", "INV-E24-03")
def test_t064_dismissal_affects_only_the_assistant_warning_and_only_its_owner() -> None:
    world = insight_world()
    world.feed(InsightInput(draft(), facts((OBS, good())), policy_from_config()))
    (w,) = world.by_definition("assistant_pattern")
    with pytest.raises(ActionNotAllowed):
        dismiss(w, PersonActor("p2"), h.T0, None)
    done = dismiss(w, PersonActor("p1"), h.T0 + timedelta(minutes=1), "not useful")
    assert (
        done.state is WarningState.DISMISSED
        and done.dismissed is not None
        and done.dismissed.reason == "not useful"
    )
    assert done.level is Level.WATCH and done.revision == w.revision + 1


def test_an_insight_for_an_unlisted_definition_or_a_second_active_warning_raises_nothing() -> None:
    world = insight_world()
    world.feed(InsightInput(draft(), facts((OBS, good())), policy_from_config()))
    assert (
        world.feed(InsightInput(draft(insight_id="ins_2"), facts((OBS, good())), policy_from_config())) == ()
    )
    # gravity_adjustment names no definition: the insight is valid but no warning exists for it
    other = draft(insight_id="ins_3", topic_id="gravity_adjustment")
    assert world.feed(InsightInput(other, facts((OBS, good())), policy_from_config())) == ()


def test_an_insight_in_a_live_mission_never_raises_from_sim_or_replay_origin() -> None:
    world = insight_world()
    sim = draft(origin_mode=OriginMode.SIM)
    decision = world.feed(
        InsightInput(sim, facts((OBS, replace(good(), origin_mode=OriginMode.SIM))), policy_from_config())
    )
    assert [type(s).__name__ for s in decision] == ["Refuse"] and decision[0].code == "origin_mismatch"  # type: ignore[union-attr]
    assert not world.warnings


# --- expiry and the clock (pure part of 084, 085, 088) -----------------------------------------------


def raised_insight() -> h.World:
    world = insight_world()
    world.feed(InsightInput(draft(), facts((OBS, good())), policy_from_config()))
    return world


@pytest.mark.trace("T-E24-084", "T-E24-085", "T-E24-088")
def test_expiry_runs_on_elapsed_time_and_is_suspended_while_the_clock_is_regressed() -> None:
    world = raised_insight()
    (w,) = world.by_definition("assistant_pattern")
    expires = w.pin.definition.clear.expires_after_seconds
    assert expires == 86400
    assert world.feed(InsightExpiredInput("ins_1", expires - 1)) == ()  # a forward jump never expires early
    world.clock_regressed = True
    assert (
        world.feed(InsightExpiredInput("ins_1", expires + 10)) == ()
    )  # suspended until the clock is corrected
    world.clock_regressed = False
    world.frozen = True
    assert (
        world.feed(InsightExpiredInput("ins_1", expires + 10)) == ()
    )  # no clear while an evaluation is pending
    world.frozen = False
    decision = world.feed(InsightExpiredInput("ins_1", expires))
    assert [type(s) for s in decision] == [Clear] and decision[0].reason is ClearReason.EXPIRED  # type: ignore[union-attr]
    assert world.warnings[w.warning_id].state is WarningState.CLEARED


def test_expiry_of_an_unknown_insight_or_of_a_rule_warning_does_nothing(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    assert world.feed(InsightExpiredInput("ins_x", 10**9)) == ()
    assert world.by_definition("co2_above_limit")


# --- T-E24-003: no input stores an assistant act or emergency -----------------------------------------

LEVELS = st.sampled_from(list(Level))
KEYS = st.sampled_from([OBS, CHECK, EvidenceKey("answer", "ans_1")])
RECORDS = st.builds(
    RecordFacts,
    exists=st.booleans(),
    owner_person_id=st.sampled_from(["p1", "p2", None]),
    origin_mode=st.sampled_from([*OriginMode, None]),
    is_sensor=st.booleans(),
    quality_status=st.none(),
    state=st.sampled_from(["accepted", "submitted", "done", "draft", None]),
    observed_at=st.just(h.T0),
    summary=st.sampled_from(["a summary", None]),
)


@given(
    level=LEVELS,
    allowed=st.sets(LEVELS),
    topic=st.sampled_from(["sleep_reaction", "gravity_adjustment", "nonsense"]),
    keys=st.lists(KEYS, max_size=3, unique=True),
    records=st.lists(RECORDS, min_size=3, max_size=3),
    origin=st.sampled_from(list(OriginMode)),
    occurrences=st.integers(0, 3),
)
@h.PROP
@pytest.mark.trace("T-E24-003", "INV-E24-02")
def test_t003_no_generated_insight_stores_an_assistant_act_or_emergency(
    level: Level,
    allowed: set[Level],
    topic: str,
    keys: list[EvidenceKey],
    records: list[RecordFacts],
    origin: OriginMode,
    occurrences: int,
) -> None:
    world = insight_world()
    policy = AssistantPolicy(frozenset(allowed), policy_from_config().topics)
    d = draft(
        level=level,
        topic_id=topic,
        evidence=tuple(keys),
        origin_mode=origin,
        routine_occurrence_ids=tuple(f"o{i}" for i in range(occurrences)),
    )
    pairs = tuple(zip(keys, records, strict=False))
    world.feed(InsightInput(d, pairs, policy))
    for w in world.warnings.values():
        assert w.level in ASSISTANT_LEVELS
        assert w.level in allowed
