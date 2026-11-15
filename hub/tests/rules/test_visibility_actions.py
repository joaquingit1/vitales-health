"""Visibility, field grants, allowed actions and resolution (E24 3.10, D1, D21, R-E24-21).

T-E24-004, 051 to 053, 058 (pure part), 059 (pure part), 125 (ownership, pure part), 128, 132 (pure part);
INV-E24-03, INV-E24-13, INV-E24-18.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from crew_health.rules.domain.actions import (
    ActionContext,
    Authority,
    Requester,
    allowed_actions,
    apply_action,
    resolve_role,
)
from crew_health.rules.domain.activation import definition_status, forbid_clears, withdrawn
from crew_health.rules.domain.decisions import Clear
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.errors import (
    ActionNotAllowed,
    InvalidTransition,
    InvariantViolation,
    NotVisible,
    OriginMismatch,
)
from crew_health.rules.domain.ownership import REPLICA_ACTIONS, replica_info, require_origin
from crew_health.rules.domain.visibility import WIRE_FIELDS, ReadGrant, Viewer, can_see, project_fields
from crew_health.rules.domain.vocabulary import (
    ClearReason,
    EnvironmentId,
    OriginMode,
    WarningAction,
    WarningState,
)
from crew_health.rules.domain.warning import PersonActor, SessionActor, Warning

from . import rules_harness as h

pytestmark = [pytest.mark.E24, pytest.mark.trace("R36", "INV-E24-13", "INV-E24-15", "D1", "D21")]

CTX = ActionContext(node_id=h.NODE, definition_status="current", max_notes=3, replica=False)


def person_warning(world: h.World) -> Warning:
    """A dizziness warning of person p1, raised through the engine."""
    from crew_health.rules.domain.inputs import AnswerInput

    w = world
    w.environment = EnvironmentId.MARS_SURFACE
    w.answer_events = (w.event("evt_land", "landing", h.T0 - timedelta(hours=1)),)
    w.feed(
        AnswerInput("ans_1", "p1", "dizzy_standing", "present", h.T0, h.T0, OriginMode.LIVE, "day 3", "09:10")
    )
    (found,) = w.by_definition("dizzy_after_landing")
    return found


def habitat_warning(world: h.World) -> Warning:
    world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
    (found,) = world.by_definition("co2_above_limit")
    return found


def crew(
    person: str, *, authority: Authority = "none", grant: ReadGrant | None = None, resolve_access: bool = True
) -> Requester:
    return Requester(Viewer("crew", person), PersonActor(person), authority, grant, resolve_access)


@pytest.mark.trace("T-E24-051", "T-E24-053", "INV-E24-13")
def test_t051_a_person_warning_is_visible_to_its_owner_only(world: h.World) -> None:
    w = person_warning(world)
    assert can_see(w, Viewer("crew", "p1")).full
    for other in (
        Viewer("crew", "p2"),
        Viewer("station", None),
        Viewer("ground", None),
        Viewer("operator", None),
        Viewer("adapter", None),
        Viewer("editor", None),
    ):
        assert not can_see(w, other).visible
    assert project_fields(w, can_see(w, Viewer("crew", "p2"))) == {}


@pytest.mark.trace("T-E24-053")
def test_t053_the_station_sees_habitat_and_crew_warnings_with_no_actions(world: h.World) -> None:
    w = habitat_warning(world)
    station = Viewer("station", None)
    assert can_see(w, station).full
    requester = Requester(station, SessionActor("station", "s1"), "none", None, False)
    assert allowed_actions(w, requester, CTX) == ()


@pytest.mark.trace("T-E24-132", "INV-E24-13")
def test_t132_officer_and_helper_status_alone_grant_no_read_only_an_explicit_grant_does(
    world: h.World,
) -> None:
    w = person_warning(world)
    officer = crew("p2", authority="crew_medical_officer")
    acting = crew("p3", authority="acting_medical_officer")
    for r in (officer, acting):
        assert not can_see(w, r.viewer, r.grant).visible  # authority is not a read grant (S8.4)
        assert allowed_actions(w, r, CTX) == ()
    grant = ReadGrant(w.warning_id, frozenset({"warning_id", "level", "title", "state"}))
    limited = can_see(w, Viewer("crew", "p4"), grant)
    assert limited.visible and not limited.full
    assert project_fields(w, limited) == {
        "level": w.level,
        "state": w.state,
        "title": w.title,
        "warning_id": w.warning_id,
    }
    assert allowed_actions(w, crew("p4", grant=grant), CTX) == ()  # a limited summary offers no action
    # a grant for another warning, or one naming no wire field, never widens access
    assert not can_see(w, Viewer("crew", "p4"), ReadGrant("wrn_other", WIRE_FIELDS)).visible
    assert not can_see(
        w, Viewer("crew", "p4"), ReadGrant(w.warning_id, frozenset({"pin", "trigger"}))
    ).visible
    full = ReadGrant(w.warning_id, WIRE_FIELDS)
    assert can_see(w, Viewer("crew", "p4"), full).full


@pytest.mark.trace("T-E24-004", "INV-E24-03")
def test_the_assistant_and_non_crew_sessions_get_no_action_and_cannot_apply_one(world: h.World) -> None:
    w = habitat_warning(world)
    for actor in (SessionActor("operator", "op"),):
        r = Requester(Viewer("operator", None), actor, "none", None, True)
        assert allowed_actions(w, r, CTX) == ()
        with pytest.raises(ActionNotAllowed):
            apply_action(w, r, CTX, WarningAction.ACKNOWLEDGE, at=h.T0, reason=None, note=None)


@pytest.mark.trace("T-E24-050", "T-E24-058")
def test_acknowledge_and_notes_are_allowed_to_crew_and_bounded(world: h.World) -> None:
    w = habitat_warning(world)
    r = crew("p1")
    assert allowed_actions(w, r, CTX) == (WarningAction.ACKNOWLEDGE, WarningAction.ADD_NOTE)
    acked = apply_action(w, r, CTX, WarningAction.ACKNOWLEDGE, at=h.T0, reason=None, note=None)
    assert allowed_actions(acked, r, CTX) == (WarningAction.ADD_NOTE,)
    with pytest.raises(InvalidTransition):
        apply_action(acked, r, CTX, WarningAction.ACKNOWLEDGE, at=h.T0, reason=None, note=None)
    noted = acked
    for i in range(CTX.max_notes):
        noted = apply_action(noted, r, CTX, WarningAction.ADD_NOTE, at=h.T0, reason=None, note=f"n{i}")
    assert allowed_actions(noted, r, CTX) == ()  # `add_note` leaves allowed_actions at the bound
    with pytest.raises(InvalidTransition):
        apply_action(noted, r, CTX, WarningAction.ADD_NOTE, at=h.T0, reason=None, note="more")


def test_an_invisible_warning_is_not_found_before_anything_else(world: h.World) -> None:
    w = person_warning(world)
    with pytest.raises(NotVisible):
        apply_action(w, crew("p2"), CTX, WarningAction.ACKNOWLEDGE, at=h.T0, reason=None, note=None)


# --- resolution: D1 and D21 --------------------------------------------------------------------------


@pytest.mark.trace("T-E24-059", "D1")
def test_the_owner_resolves_with_the_pinned_permission_a_reason_and_explicit_access(world: h.World) -> None:
    w = person_warning(world)
    assert w.pin.definition.clear.person_may_resolve
    assert WarningAction.RESOLVE in allowed_actions(w, crew("p1"), CTX)
    # self plus person_may_resolve is never enough without the explicit object and action access
    assert WarningAction.RESOLVE not in allowed_actions(w, crew("p1", resolve_access=False), CTX)
    done = apply_action(w, crew("p1"), CTX, WarningAction.RESOLVE, at=h.T0, reason="I feel fine", note=None)
    assert done.state is WarningState.RESOLVED and done.resolved is not None
    assert done.resolved.reason == "I feel fine" and done.resolved.role == "owner"
    with pytest.raises(InvalidTransition):
        apply_action(w, crew("p1"), CTX, WarningAction.RESOLVE, at=h.T0, reason=" ", note=None)


@pytest.mark.trace("D1", "D21", "T-E24-016")
def test_an_officer_or_the_acting_officer_resolves_an_edited_or_withdrawn_definition_with_a_reason(
    world: h.World,
) -> None:
    w = habitat_warning(world)  # a sensor warning: nobody may resolve it while its definition is current
    everyone: tuple[Authority, ...] = ("none", "crew_medical_officer", "acting_medical_officer")
    officers: tuple[Authority, ...] = ("crew_medical_officer", "acting_medical_officer")
    for who in everyone:
        assert resolve_role(w, crew("p2", authority=who), "current") is None
    for status in ("content_changed", "rule_changed", "withdrawn"):
        assert resolve_role(w, crew("p2"), status) is None  # authority is required
        for who in officers:
            assert resolve_role(w, crew("p2", authority=who), status) == who
    r = crew("p2", authority="acting_medical_officer")
    ctx = replace(CTX, definition_status="withdrawn")
    assert WarningAction.RESOLVE in allowed_actions(w, r, ctx)
    done = apply_action(w, r, ctx, WarningAction.RESOLVE, at=h.T0, reason="sensor retired", note=None)
    assert (
        done.resolved is not None
        and done.resolved.role == "acting_medical_officer"
        and done.resolved.reason == "sensor retired"
    )
    assert WarningAction.RESOLVE not in allowed_actions(
        w, crew("p2", authority="crew_medical_officer", resolve_access=False), ctx
    )


def test_an_assistant_warning_is_dismissed_by_its_owner_and_never_resolved(world: h.World) -> None:
    world.feed(h.insight_input())
    (w,) = world.by_definition("assistant_pattern")
    owner = crew("p1", authority="crew_medical_officer")
    assert WarningAction.DISMISS in allowed_actions(w, owner, CTX)
    assert WarningAction.RESOLVE not in allowed_actions(w, owner, replace(CTX, definition_status="withdrawn"))
    assert WarningAction.DISMISS not in allowed_actions(w, crew("p2"), CTX)  # not this person's insight
    done = apply_action(w, owner, CTX, WarningAction.DISMISS, at=h.T0, reason=None, note=None)
    assert done.state is WarningState.DISMISSED and done.dismissed is not None
    with pytest.raises(NotVisible):  # a person warning is invisible to another person, so there is no action
        apply_action(w, crew("p2"), CTX, WarningAction.DISMISS, at=h.T0, reason=None, note=None)


# --- ownership across nodes (INV-E24-18) -------------------------------------------------------------


@pytest.mark.trace("T-E24-125", "INV-E24-18")
def test_t125_only_the_origin_node_changes_a_warning_and_replicas_offer_acknowledge_and_note(
    world: h.World,
) -> None:
    w = habitat_warning(world)
    require_origin(w, h.NODE)
    with pytest.raises(OriginMismatch):
        require_origin(w, "node_b")
    replica_ctx = replace(CTX, node_id="node_b")
    r = crew("p1", authority="crew_medical_officer")
    assert allowed_actions(w, r, replace(replica_ctx, definition_status="withdrawn")) == REPLICA_ACTIONS
    info = replica_info(origin_node_id="node_a", now=h.T0 + timedelta(seconds=90), origin_version_time=h.T0)
    assert (info.origin_node_id, info.age_seconds) == ("node_a", 90)
    assert (
        replica_info(
            origin_node_id="node_a", now=h.T0, origin_version_time=h.T0 + timedelta(seconds=5)
        ).age_seconds
        == 0
    )


# --- activation never clears (INV-E24-15, D1) --------------------------------------------------------


@pytest.mark.trace("T-E24-013", "T-E24-016", "T-E24-127", "INV-E24-15", "D1")
def test_definition_status_shows_edit_and_withdrawal_but_changes_nothing(world: h.World) -> None:
    w = habitat_warning(world)
    defs = world.rules.by_id()
    fps, hashes = world.fingerprints(), world.content_hashes()
    env = EnvironmentId.STATION_LEO
    assert definition_status(w, defs, fps, hashes, environment_id=env) == "current"
    assert (
        definition_status(w, defs, {**fps, w.definition_id: "x"}, hashes, environment_id=env)
        == "rule_changed"
    )
    assert (
        definition_status(w, defs, fps, {**hashes, w.definition_id: "x"}, environment_id=env)
        == "content_changed"
    )
    gone = {k: v for k, v in defs.items() if k != w.definition_id}
    assert definition_status(w, gone, fps, hashes, environment_id=env) == "withdrawn"
    assert withdrawn([w], gone, fps, hashes, environment_id=env) == (w.warning_id,)
    assert (
        definition_status(w, defs, fps, hashes, environment_id=EnvironmentId.LUNAR_SURFACE) == "current"
    )  # co2 applies there too
    doc = parse_rules_document(h.deployment_rules())
    assert doc  # the release still parses; the warning is untouched
    assert w.active


@pytest.mark.trace("INV-E24-15", "D1")
def test_an_activation_decision_that_clears_anything_is_refused() -> None:
    assert forbid_clears(()) == ()
    with pytest.raises(InvariantViolation):
        forbid_clears((Clear("wrn_1", ClearReason.CONFIG_CHANGED),))
