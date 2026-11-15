"""The assistant never changes safety state (AGENTS 1 rule 4, E24 3.4, H1, H2), at the domain transition.

Property tests over every level, every raiser, every scope, both active states and every action: the assistant
cannot hold or raise act or emergency, and no transition or action accepts an assistant actor, so it can never
lower, clear, resolve, dismiss or acknowledge a rule or sensor warning. Even with every grant and authority a
requester could carry, the assistant gets no allowed action. The served HTTP and AssistantWarningGuard halves of
the same rule are in `test_assistant_safety_served.py`.

T-E24-002, 003, 004, 005, 062; INV-E24-02, INV-E24-03; H1, H2.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
import rules_harness as h
from hypothesis import given
from hypothesis import strategies as st

from crew_health.rules.domain import actions
from crew_health.rules.domain.definitions import parse_rules_document
from crew_health.rules.domain.errors import (
    ActionNotAllowed,
    DefinitionInvalid,
    InvalidTransition,
    LevelNotAllowed,
    NotVisible,
    RulesDomainError,
)
from crew_health.rules.domain.visibility import WIRE_FIELDS, ReadGrant, Viewer
from crew_health.rules.domain.vocabulary import (
    ACTIVE,
    ASSISTANT_LEVELS,
    ClearReason,
    Level,
    RaisedBy,
    Scope,
    WarningAction,
    WarningState,
)
from crew_health.rules.domain.warning import (
    Acknowledgement,
    AssistantActor,
    Warning,
    acknowledge,
    add_note,
    attach_overrides,
    clear,
    dismiss,
    resolve,
)

pytestmark = [pytest.mark.E24, pytest.mark.trace("INV-E24-02", "INV-E24-03", "H1", "H2")]

LEVELS = st.sampled_from(list(Level))
RAISERS = st.sampled_from(list(RaisedBy))
SCOPES = st.sampled_from(list(Scope))
ACTIVE_STATES = st.sampled_from(sorted(ACTIVE))
ACTIONS = st.sampled_from(list(WarningAction))
REASONS = st.sampled_from(list(ClearReason))
AUTHORITIES = st.sampled_from(["crew_medical_officer", "acting_medical_officer", "none"])
STATUSES = st.sampled_from(["current", "content_changed", "rule_changed", "withdrawn"])

_TEMPLATE: list[Warning] = []


def template() -> Warning:
    """One real CO2 act warning raised through the engine from the deployment release, built once."""
    if not _TEMPLATE:
        world = h.World(parse_rules_document(h.deployment_rules()), h.release_documents())
        world.feed(world.sensor(results=[h.result("co2_1h_act", crossed=True, clear_holds=False)]))
        (w,) = world.by_definition("co2_above_limit")
        _TEMPLATE.append(w)
    return _TEMPLATE[0]


def variant(level: Level, raiser: RaisedBy, scope: Scope, state: WarningState) -> Warning:
    """The template moved to any level, raiser, scope and active state; construction checks every invariant."""
    w = template()
    person = "p1" if scope is Scope.PERSON or raiser is RaisedBy.ASSISTANT else None
    acks = (Acknowledgement("p2", h.T0),) if state is WarningState.ACKNOWLEDGED else ()
    return replace(
        w,
        level=level,
        raised_by=raiser,
        scope=Scope.PERSON if raiser is RaisedBy.ASSISTANT else scope,
        person_id=person,
        insight_id="ins_1" if raiser is RaisedBy.ASSISTANT else None,
        state=state,
        acknowledgements=acks,
    )


@pytest.mark.trace("T-E24-002", "T-E24-003")
@given(level=LEVELS, scope=SCOPES, state=ACTIVE_STATES)
@h.PROP
def test_no_warning_value_raised_by_the_assistant_holds_act_or_emergency(
    level: Level, scope: Scope, state: WarningState
) -> None:
    if level in ASSISTANT_LEVELS:
        assert variant(level, RaisedBy.ASSISTANT, scope, state).level is level
    else:
        with pytest.raises(LevelNotAllowed):
            variant(level, RaisedBy.ASSISTANT, scope, state)


@pytest.mark.trace("T-E24-002", "T-E24-003")
@given(index=st.integers(min_value=0, max_value=10_000), level=LEVELS)
@h.PROP
def test_no_definition_lets_the_assistant_raise_act_or_emergency(index: int, level: Level) -> None:
    """Any deployment definition, of any kind, switched to the assistant raiser at act or emergency is refused
    when the release is parsed, so no such definition can ever be active."""
    raw = copy.deepcopy(h.deployment_rules())
    definitions = raw["definitions"]
    assert isinstance(definitions, list)
    chosen = definitions[index % len(definitions)]
    chosen["raised_by"] = "assistant"
    chosen["level"] = level.value
    if level in ASSISTANT_LEVELS and chosen["condition"]["kind"] == "assistant_insight":
        assert parse_rules_document(raw).by_id()[chosen["id"]].level is level
        return
    with pytest.raises(DefinitionInvalid):
        parse_rules_document(raw)


@pytest.mark.trace("T-E24-004", "T-E24-005", "T-E24-062")
@given(level=LEVELS, raiser=RAISERS, scope=SCOPES, state=ACTIVE_STATES, reason=REASONS)
@h.PROP
def test_no_transition_accepts_the_assistant_at_any_level_or_kind(
    level: Level, raiser: RaisedBy, scope: Scope, state: WarningState, reason: ClearReason
) -> None:
    if raiser is RaisedBy.ASSISTANT and level not in ASSISTANT_LEVELS:
        return  # unrepresentable, proven above
    w = variant(level, raiser, scope, state)
    bot = AssistantActor("assistant_1")
    at = h.T0 + timedelta(minutes=5)
    attempts: tuple[Callable[[], object], ...] = (
        lambda: acknowledge(w, bot, at),
        lambda: add_note(w, bot, at, "note", max_notes=10),
        lambda: resolve(w, bot, at, "reason", role="crew_medical_officer"),
        lambda: dismiss(w, bot, at, "reason"),
        lambda: clear(w, bot, reason, at),
    )
    for attempt in attempts:
        with pytest.raises(ActionNotAllowed):
            attempt()
    assert attach_overrides(w, ()) == w  # nothing else moves the warning either


@pytest.mark.trace("T-E24-004", "T-E24-062")
@given(
    level=LEVELS, raiser=RAISERS, scope=SCOPES, state=ACTIVE_STATES, action=ACTIONS,
    authority=AUTHORITIES, status=STATUSES, replica=st.booleans(),
)  # fmt: skip
@h.PROP
def test_the_assistant_gets_no_action_even_with_every_grant_and_authority(
    level: Level, raiser: RaisedBy, scope: Scope, state: WarningState, action: WarningAction,
    authority: actions.Authority, status: str, replica: bool,
) -> None:  # fmt: skip
    if raiser is RaisedBy.ASSISTANT and level not in ASSISTANT_LEVELS:
        return
    w = variant(level, raiser, scope, state)
    requester = actions.Requester(
        Viewer("crew", "p1"), AssistantActor("assistant_1"), authority,
        ReadGrant(w.warning_id, WIRE_FIELDS), True,
    )  # fmt: skip
    ctx = actions.ActionContext(w.origin_node_id, status, 10, replica)  # type: ignore[arg-type]
    assert actions.allowed_actions(w, requester, ctx) == ()
    assert actions.resolve_role(w, requester, status) is None  # type: ignore[arg-type]
    with pytest.raises((ActionNotAllowed, InvalidTransition, NotVisible)) as raised:
        actions.apply_action(w, requester, ctx, action, at=h.T0, reason="reason", note="note")
    assert isinstance(raised.value, RulesDomainError)
    if action in (WarningAction.RESOLVE, WarningAction.DISMISS):
        assert isinstance(raised.value, ActionNotAllowed)
