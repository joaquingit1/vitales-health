"""Warning entity, raiser matrix and transitions (E24 3.3, 3.4).

T-E24-001, 002, 004, 005, 008, 009, 021, 022 and the immutability half of 006; INV-E24-01 to 04, 06, 08.
"""

from __future__ import annotations

import copy
from dataclasses import fields, replace
from datetime import timedelta

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain.decisions import Clear
from crew_health.rules.domain.definitions import RulesDocument, parse_rules_document
from crew_health.rules.domain.errors import (
    ActionNotAllowed,
    DefinitionInvalid,
    InvalidTransition,
    InvariantViolation,
    LevelNotAllowed,
    OriginMismatch,
)
from crew_health.rules.domain.vocabulary import (
    ASSISTANT_LEVELS,
    BODY_REGIONS,
    ClearReason,
    Level,
    OriginMode,
    RaisedBy,
    WarningState,
)
from crew_health.rules.domain.warning import (
    AssistantActor,
    EngineActor,
    PersonActor,
    SessionActor,
    Warning,
    acknowledge,
    add_note,
    attach_overrides,
    clear,
    dismiss,
    engine_actor,
    resolve,
)

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "R40", "INV-E24-01", "INV-E24-02", "INV-E24-03")]

IMMUTABLE = (
    "level", "raised_by", "definition_id", "scope_key", "trigger", "evidence", "steps", "raised_at",
    "origin_mode", "origin_node_id", "pin", "rule_fingerprint", "content_hash", "title", "what", "why",
)  # fmt: skip


def co2_warning(world: h.World) -> Warning:
    """A real CO2 act warning, raised through the engine from the example limit and the deployment rules."""
    limit = world.limits["co2_1h_act"]
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    assert limit.level is Level.ACT
    (w,) = world.by_definition("co2_above_limit")
    return w


@pytest.mark.trace("T-E24-001")
def test_t001_a_raised_warning_has_level_raiser_steps_regions_citation_evidence(world: h.World) -> None:
    w = co2_warning(world)
    assert w.level is Level.ACT and w.raised_by is RaisedBy.SENSOR_LIMIT
    assert w.steps and all(w.steps)
    assert set(w.body_regions) <= BODY_REGIONS
    assert w.citation.text and w.citation.source_ids == ("EV01",)
    assert [e.kind.value for e in w.evidence] == ["reading"]
    assert w.state is WarningState.RAISED and w.revision == 1


@pytest.mark.trace("T-E24-001")
def test_t001_a_warning_without_steps_or_citation_cannot_exist(world: h.World) -> None:
    w = co2_warning(world)
    with pytest.raises(InvariantViolation):
        replace(w, steps=())
    with pytest.raises(InvariantViolation):
        replace(w, citation=replace(w.citation, text=""))
    with pytest.raises(InvariantViolation):
        replace(w, body_regions=("not_a_region",))


@pytest.mark.trace("T-E24-002")
def test_t002_an_assistant_definition_at_act_fails_construction(example_doc: RulesDocument) -> None:
    raw = copy.deepcopy(h.example_value("warning-rules"))
    for d in raw["definitions"]:  # type: ignore[attr-defined]
        if d["id"] == "assistant_pattern":
            d["level"] = "act"
    with pytest.raises(DefinitionInvalid):
        parse_rules_document(raw)
    assert example_doc.by_id()["assistant_pattern"].level in ASSISTANT_LEVELS


@pytest.mark.trace("T-E24-002")
@pytest.mark.parametrize("level", ["act", "emergency"])
def test_t002_an_assistant_warning_value_cannot_hold_act_or_emergency(world: h.World, level: str) -> None:
    w = co2_warning(world)
    with pytest.raises(LevelNotAllowed):
        replace(w, raised_by=RaisedBy.ASSISTANT, level=Level(level), insight_id="ins_1")


@pytest.mark.trace("T-E24-004", "INV-E24-03")
def test_t004_no_transition_accepts_an_assistant_actor(world: h.World) -> None:
    w = co2_warning(world)
    bot = AssistantActor("assistant_1")
    at = h.T0 + timedelta(seconds=1)
    with pytest.raises(ActionNotAllowed):
        acknowledge(w, bot, at)
    with pytest.raises(ActionNotAllowed):
        add_note(w, bot, at, "note", max_notes=10)
    with pytest.raises(ActionNotAllowed):
        resolve(w, bot, at, "reason", role=None)
    with pytest.raises(ActionNotAllowed):
        dismiss(w, bot, at, None)
    with pytest.raises(ActionNotAllowed):
        clear(w, bot, ClearReason.CONDITION_FALSE, at)  # type: ignore[arg-type]


@pytest.mark.trace("T-E24-004", "T-E24-005", "INV-E24-03")
def test_t005_a_rule_or_sensor_warning_can_never_be_dismissed(world: h.World) -> None:
    w = co2_warning(world)
    with pytest.raises(ActionNotAllowed):
        dismiss(w, PersonActor("p1"), h.T0, "reason")
    with pytest.raises(ActionNotAllowed):
        replace(w, state=WarningState.DISMISSED, dismissed=object())  # type: ignore[arg-type]


@pytest.mark.trace("T-E24-004")
def test_clear_needs_the_origin_nodes_engine_actor(world: h.World) -> None:
    w = co2_warning(world)
    for actor in (PersonActor("p1"), SessionActor("operator", "op1")):
        with pytest.raises(ActionNotAllowed):
            clear(w, actor, ClearReason.CONDITION_FALSE, h.T0)  # type: ignore[arg-type]
    with pytest.raises(OriginMismatch):
        engine_actor("node_b", origin_node_id=w.origin_node_id)
    with pytest.raises(ActionNotAllowed):
        EngineActor(w.origin_node_id, object())
    other = engine_actor("node_b", origin_node_id="node_b")
    with pytest.raises(ActionNotAllowed):
        clear(w, other, ClearReason.CONDITION_FALSE, h.T0)


@pytest.mark.trace("T-E24-008", "INV-E24-06")
def test_t008_acknowledge_keeps_it_open_and_changes_only_acknowledgements(world: h.World) -> None:
    w = co2_warning(world)
    acked = acknowledge(w, PersonActor("p1"), h.T0 + timedelta(seconds=5))
    assert acked.state is WarningState.ACKNOWLEDGED and acked.active
    assert acked.revision == w.revision + 1
    changed = {f.name for f in fields(w) if getattr(w, f.name) != getattr(acked, f.name)}
    assert changed == {"revision", "state", "acknowledgements"}
    with pytest.raises(InvalidTransition):
        acknowledge(acked, PersonActor("p1"), h.T0 + timedelta(seconds=6))
    second = acknowledge(acked, PersonActor("p2"), h.T0 + timedelta(seconds=7))
    assert [a.person_id for a in second.acknowledgements] == ["p1", "p2"]


@pytest.mark.trace("T-E24-009", "INV-E24-08")
def test_t009_final_states_reject_every_action(world: h.World) -> None:
    w = co2_warning(world)
    engine = engine_actor(h.NODE, origin_node_id=w.origin_node_id)
    cleared = clear(w, engine, ClearReason.CONDITION_FALSE, h.T0 + timedelta(minutes=20))
    assert cleared.state is WarningState.CLEARED and cleared.clear_reason is ClearReason.CONDITION_FALSE
    person = PersonActor("p1")
    at = h.T0 + timedelta(minutes=21)
    for call in (
        lambda: acknowledge(cleared, person, at),
        lambda: add_note(cleared, person, at, "x", max_notes=5),
        lambda: resolve(cleared, person, at, "why", role=None),
        lambda: clear(cleared, engine, ClearReason.SUPERSEDED, at),
        lambda: attach_overrides(cleared, ("ovr_1",)),
    ):
        with pytest.raises(InvalidTransition):
            call()


@pytest.mark.trace("T-E24-009")
def test_a_recurrence_is_a_new_warning_with_a_new_id(world: h.World) -> None:
    w1 = co2_warning(world)
    world.advance(5)
    world.apply((Clear(w1.warning_id, ClearReason.CONDITION_FALSE),))
    world.advance(5)
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (w2,) = world.by_definition("co2_above_limit")
    assert w2.warning_id != w1.warning_id


def test_resolve_needs_a_reason_and_notes_are_bounded(world: h.World) -> None:
    w = co2_warning(world)
    p = PersonActor("p1")
    with pytest.raises(InvalidTransition):
        resolve(w, p, h.T0, "   ", role=None)
    noted = add_note(w, p, h.T0, "first", max_notes=1)
    assert len(noted.notes) == 1 and noted.state is WarningState.RAISED
    with pytest.raises(InvalidTransition):
        add_note(noted, p, h.T0, "second", max_notes=1)
    with pytest.raises(InvalidTransition):
        add_note(w, p, h.T0, "", max_notes=5)


def test_expiry_never_ends_a_rule_or_sensor_warning(world: h.World) -> None:
    """INV-E24-15: `expired` is allowed for assistant warnings only."""
    w = co2_warning(world)
    engine = engine_actor(h.NODE, origin_node_id=w.origin_node_id)
    with pytest.raises(InvalidTransition):
        clear(w, engine, ClearReason.EXPIRED, h.T0)


@pytest.mark.parametrize("reason", [ClearReason.CONFIG_CHANGED, ClearReason.ENVIRONMENT_CHANGED])
def test_the_engine_never_clears_for_a_configuration_or_environment_change(
    world: h.World, reason: ClearReason
) -> None:
    w = co2_warning(world)
    engine = engine_actor(h.NODE, origin_node_id=w.origin_node_id)
    with pytest.raises(ActionNotAllowed):
        clear(w, engine, reason, h.T0)


@pytest.mark.trace("T-E24-006", "INV-E24-04")
@given(
    st.lists(
        st.sampled_from(["ack", "note", "override", "clear", "resolve"]),
        min_size=0,
        max_size=8,
    )
)
@h.PROP
def test_t006_no_transition_sequence_changes_an_immutable_field(steps: list[str]) -> None:
    from crew_health.rules.domain.fingerprint import ReleaseDocuments  # noqa: F401

    docs = h.release_documents()
    world = h.World(parse_rules_document(h.deployment_rules()), docs)
    w0 = co2_warning(world)
    w = w0
    engine = engine_actor(h.NODE, origin_node_id=w.origin_node_id)
    when = h.T0
    for i, step in enumerate(steps):
        when = when + timedelta(seconds=i + 1)
        try:
            if step == "ack":
                w = acknowledge(w, PersonActor(f"p{i}"), when)
            elif step == "note":
                w = add_note(w, PersonActor("p1"), when, "n", max_notes=3)
            elif step == "override":
                w = attach_overrides(w, (f"ovr_{i}",))
            elif step == "clear":
                w = clear(w, engine, ClearReason.CONDITION_FALSE, when)
            else:
                w = resolve(w, PersonActor("p1"), when, "because", role="owner")
        except InvalidTransition:
            continue
        assert w.revision >= w0.revision
    for name in IMMUTABLE:
        assert getattr(w, name) == getattr(w0, name)
    assert w.level is w0.level


@pytest.mark.trace("T-E24-021", "T-E24-022", "INV-E24-04")
def test_t022_a_level_cannot_be_changed_in_place(world: h.World) -> None:
    w = co2_warning(world)
    lowered = replace(w, level=Level.WATCH)  # a value can be built, but no transition produces it
    assert lowered.level is Level.WATCH
    for transition in (
        acknowledge(w, PersonActor("p1"), h.T0),
        add_note(w, PersonActor("p1"), h.T0, "n", max_notes=2),
        attach_overrides(w, ("ovr_x",)),
        clear(w, engine_actor(h.NODE, origin_node_id=w.origin_node_id), ClearReason.CONDITION_FALSE, h.T0),
    ):
        assert transition.level is w.level


def test_a_person_warning_is_never_replay_and_only_person_scope_names_a_person(world: h.World) -> None:
    w = co2_warning(world)
    with pytest.raises(InvariantViolation):
        replace(w, person_id="p1")  # habitat scope names nobody
    with pytest.raises(InvariantViolation):
        replace(w, state=WarningState.CLEARED)  # cleared needs its time and reason
    assert OriginMode.REPLAY.value == "REPLAY"
