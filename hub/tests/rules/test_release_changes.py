"""Release and environment changes under pinned definitions (E24 3.8, D1) and what a link or a clock changes.

T-E24-013 to 017, 034, 038, 087, 127 (pure parts); INV-E24-04, INV-E24-10, INV-E24-15.
"""

from __future__ import annotations

import copy
from dataclasses import fields
from datetime import timedelta

import pytest
import rules_harness as h

from crew_health.rules.domain.decisions import Clear, Refuse
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.fingerprint import ReleaseDocuments
from crew_health.rules.domain.inputs import ActivationInput, GroundLinkInput
from crew_health.rules.domain.vocabulary import ClearReason, EnvironmentId, Level, WarningState
from crew_health.rules.domain.warning import PersonActor, acknowledge

pytestmark = [pytest.mark.E24, pytest.mark.trace("INV-E24-15", "INV-E24-04", "D1")]

CROSSED = {"crossed": True, "clear_holds": False}
HOLDING = {"crossed": False, "clear_holds": True}


def edited_rules(definition_id: str, **changes: object) -> object:
    raw = copy.deepcopy(h.deployment_rules())
    for d in raw["definitions"]:
        if d["id"] == definition_id:
            d.update(changes)
    return parse_rules_document(raw)


def content_edited() -> object:
    raw = copy.deepcopy(h.deployment_rules())
    for d in raw["definitions"]:
        d["content"]["title"] += " (edited)"
        d["content"]["why"] += " Edited."
    return parse_rules_document(raw)


@pytest.mark.trace("T-E24-013", "INV-E24-15")
def test_t013_a_content_edit_while_acknowledged_changes_nothing_of_the_warning(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    acked = acknowledge(w, PersonActor("p1"), h.T0)
    world.warnings[w.warning_id] = acked
    world.reload(content_edited())
    decision = world.feed(ActivationInput("release", "rel_a", "rel_b", "station_leo"))
    assert decision == ()
    kept = world.warnings[w.warning_id]
    assert kept == acked and kept.title == w.title and kept.why == w.why and kept.revision == acked.revision
    assert kept.config_release_id == "rel_a"  # the words stay as raised under their pinned release


@pytest.mark.trace("T-E24-016", "T-E24-127", "INV-E24-15", "D1")
def test_t016_activation_and_environment_change_never_clear_lower_or_repin_an_active_warning(
    world: h.World,
) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    before = dict(world.warnings)
    gone = copy.deepcopy(h.deployment_rules())
    gone["definitions"] = [d for d in gone["definitions"] if d["id"] != "co2_above_limit"]
    for new_doc, env in (
        (parse_rules_document(gone), EnvironmentId.STATION_LEO),
        (parse_rules_document(h.deployment_rules()), EnvironmentId.LUNAR_SURFACE),
    ):
        world.reload(new_doc)
        world.environment = env
        for kind in ("release", "environment"):
            assert world.feed(ActivationInput(kind, "rel_a", "rel_b", env.value)) == ()
    assert world.warnings == before and world.warnings[w.warning_id].pin.definition.level is Level.ACT
    assert world.warnings[w.warning_id].config_release_id == "rel_a"


@pytest.mark.trace("T-E24-017", "DE24-7")
def test_t017_a_level_raised_by_a_release_supersedes_the_old_warning_when_the_condition_holds(
    world: h.World,
) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (old,) = world.by_definition("co2_above_limit")
    world.reload(edited_rules("co2_above_limit", level="emergency"))
    world.advance(10)
    world.feed(world.sensor(release="rel_b", results=[h.result("co2_1h_act", **CROSSED)], reading="rdg_new"))
    (new,) = world.by_definition("co2_above_limit")
    assert (
        new.level is Level.EMERGENCY
        and new.config_release_id == "rel_b"
        and new.previous_warning_id == old.warning_id
    )
    assert world.warnings[old.warning_id].clear_reason is ClearReason.SUPERSEDED
    assert world.warnings[old.warning_id].level is Level.ACT  # the old record never changed its level


@pytest.mark.trace("T-E24-017", "DE24-7", "D1")
def test_t017_a_level_lowered_by_a_release_keeps_the_old_until_it_clears_by_its_pinned_rule_then_raises_the_lower(
    world: h.World,
) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (old,) = world.by_definition("co2_above_limit")
    world.reload(edited_rules("co2_above_limit", level="watch"))
    world.advance(10)
    assert (
        world.feed(
            world.sensor(release="rel_b", results=[h.result("co2_1h_act", **CROSSED)], reading="rdg_2")
        )
        == ()
    )
    assert world.warnings[old.warning_id].level is Level.ACT and world.warnings[old.warning_id].active
    fingerprint = old.pin.limit.limit_fingerprint
    for offset in range(70, 740, 60):  # the pinned dwell, on results evaluated under the pinned limit
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(
            world.sensor(
                release="rel_b",
                results=[h.result("co2_1h_act", fingerprint=fingerprint, **HOLDING)],
                reading=f"rdg_{offset}",
            )
        )
        if world.warnings[old.warning_id].state is WarningState.CLEARED:
            break
    assert world.warnings[old.warning_id].clear_reason is ClearReason.CONDITION_FALSE
    world.advance(60)
    world.feed(
        world.sensor(release="rel_b", results=[h.result("co2_1h_act", **CROSSED)], reading="rdg_after")
    )
    (lower,) = world.by_definition("co2_above_limit")
    assert (
        lower.level is Level.WATCH and lower.config_release_id == "rel_b"
    )  # the new release raises the lower level
    assert world.warnings[old.warning_id].level is Level.ACT


@pytest.mark.trace("T-E24-014", "T-E24-015", "T-E24-126", "D1")
def test_a_threshold_edit_leaves_the_pinned_dwell_and_never_clears_on_the_new_threshold(
    world: h.World, release_docs: ReleaseDocuments
) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    pinned = w.pin.limit
    assert pinned is not None
    limits = copy.deepcopy(dict(release_docs.sensor_limits))
    for entry in limits["limits"]:  # type: ignore[attr-defined]
        if entry["limit_id"] == "co2_1h_act":
            entry["value"] = 4
            entry["clear"]["value"] = 4
    edited = ReleaseDocuments(
        limits, release_docs.sensor_catalog, release_docs.questionnaires, release_docs.assistant_policy
    )
    world.reload(parse_rules_document(h.deployment_rules()), docs=edited)
    new_fp = world.limits["co2_1h_act"].limit_fingerprint
    assert new_fp != pinned.limit_fingerprint
    for offset in range(60, 1500, 60):  # results under the NEW limit never clear the OLD warning
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(
            world.sensor(release="rel_b", results=[h.result("co2_1h_act", fingerprint=new_fp, **HOLDING)])
        )
    assert world.warnings[w.warning_id].active
    for offset in range(1500, 2200, 60):  # a result under the pinned limit does
        world.now = h.T0 + timedelta(seconds=offset)
        world.feed(
            world.sensor(
                release="rel_b",
                results=[h.result("co2_1h_act", fingerprint=pinned.limit_fingerprint, **HOLDING)],
            )
        )
    assert world.warnings[w.warning_id].clear_reason is ClearReason.CONDITION_FALSE


@pytest.mark.trace("T-E24-038", "INV-E24-08")
def test_t038_a_superseded_warning_leaves_its_trigger_reusable_and_a_resolved_one_does_not(
    world: h.World,
) -> None:
    kw = {"sensor_type": "o2", "channel": "ch_o2"}
    world.feed(world.sensor(results=[h.result("o2_low_watch", **CROSSED)], reading="rdg_a", **kw))
    (watch,) = world.by_definition("oxygen_low_watch")
    world.advance(5)
    world.feed(
        world.sensor(
            results=[h.result("o2_low_watch", **CROSSED), h.result("o2_low_emergency", **CROSSED)],
            reading="rdg_b",
            **kw,
        )
    )
    assert world.warnings[watch.warning_id].clear_reason is ClearReason.SUPERSEDED
    assert (watch.definition_id, watch.scope_key, watch.trigger) not in world.spent
    (top,) = world.by_definition("oxygen_low_emergency")
    world.finish(top.warning_id, WarningState.RESOLVED)
    assert (top.definition_id, top.scope_key, top.trigger) in world.spent
    again = world.feed(world.sensor(results=[h.result("o2_low_emergency", **CROSSED)], reading="rdg_b", **kw))
    assert [type(s) for s in again] == [Refuse]


@pytest.mark.trace("T-E24-034", "INV-E24-10")
def test_t034_the_link_state_changes_nothing_but_ground_silence(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    world.feed(world.event("evt_a", "space_weather_alert", h.T0))
    before = dict(world.warnings)
    for status in ("down", "delayed", "up"):
        world.advance(60)
        decision = world.feed(
            GroundLinkInput(status, h.T0 - timedelta(hours=1), "adapter", "station_leo", False, world.now)
        )
        assert not any(isinstance(s, Clear) for s in decision)
    assert world.warnings == before  # the shipped threshold is null: the link raises and clears nothing


@pytest.mark.trace("T-E24-087", "S5.3")
def test_t087_stored_words_and_time_labels_are_never_rewritten_by_any_transition(world: h.World) -> None:
    world.feed(world.sensor(results=[h.result("co2_1h_act", **CROSSED)]))
    (w,) = world.by_definition("co2_above_limit")
    acked = acknowledge(w, PersonActor("p1"), h.T0)
    stable = [
        f.name
        for f in fields(w)
        if f.name in {"evidence_time_label", "title", "what", "why", "steps", "citation", "headline"}
    ]
    assert stable
    for name in stable:
        assert getattr(acked, name) == getattr(w, name)
    world.reload(content_edited())
    world.feed(ActivationInput("release", "rel_a", "rel_b", "station_leo"))
    for name in stable:
        assert getattr(world.warnings[w.warning_id], name) == getattr(w, name)
