"""Allowed actions and their application (E24 3.3, 3.10, 4.1, D1, D21, R-E24-21).

`allowed_actions` is computed for a requester and never stored. Resolution of a rule or sensor warning
needs a current explicit object and action grant and a nonempty reason, and then either the warning's
own pinned `person_may_resolve`, or, once its definition was edited or withdrawn (D1), a crew medical
officer or the acting medical officer (D21). Self plus `person_may_resolve` alone is never enough, and
officer status alone grants no read access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.rules.domain.errors import ActionNotAllowed, InvalidTransition, NotVisible
from crew_health.rules.domain.ownership import REPLICA_ACTIONS, is_origin
from crew_health.rules.domain.views import DefinitionStatus
from crew_health.rules.domain.visibility import ReadGrant, Viewer, can_see
from crew_health.rules.domain.vocabulary import ACTIVE, RaisedBy, Scope, WarningAction
from crew_health.rules.domain.warning import (
    Actor,
    PersonActor,
    Warning,
    acknowledge,
    add_note,
    dismiss,
    resolve,
)

type Authority = Literal["crew_medical_officer", "acting_medical_officer", "none"]  # internal enum


@dataclass(frozen=True, slots=True)
class Requester:
    viewer: Viewer
    actor: Actor
    authority: Authority
    grant: ReadGrant | None
    resolve_access: bool  # a current explicit object and action grant to resolve this warning


@dataclass(frozen=True, slots=True)
class ActionContext:
    node_id: str
    definition_status: DefinitionStatus
    max_notes: int
    replica: bool


def _acting_person(r: Requester) -> str | None:
    if isinstance(r.actor, PersonActor) and r.viewer.kind == "crew":
        return r.actor.person_id
    return None


def resolve_role(w: Warning, r: Requester, status: DefinitionStatus) -> str | None:
    """The basis on which this requester may resolve, or `None`."""
    person = _acting_person(r)
    if person is None or not r.resolve_access or w.raised_by is RaisedBy.ASSISTANT:
        return None
    if r.authority != "none" and status != "current":
        return r.authority  # D1 and D21: an edited or withdrawn definition
    owns = w.scope is not Scope.PERSON or w.person_id == person
    if w.raised_by is RaisedBy.RULE and w.pin.definition.clear.person_may_resolve and owns:
        return "owner"
    return None


def allowed_actions(w: Warning, r: Requester, ctx: ActionContext) -> tuple[WarningAction, ...]:
    person = _acting_person(r)
    if w.state not in ACTIVE or person is None or not can_see(w, r.viewer, r.grant).full:
        return ()
    allowed: list[WarningAction] = []
    if all(a.person_id != person for a in w.acknowledgements):
        allowed.append(WarningAction.ACKNOWLEDGE)
    if len(w.notes) < ctx.max_notes:
        allowed.append(WarningAction.ADD_NOTE)
    if ctx.replica or not is_origin(w, ctx.node_id):
        return tuple(a for a in REPLICA_ACTIONS if a in allowed)
    if resolve_role(w, r, ctx.definition_status) is not None:
        allowed.append(WarningAction.RESOLVE)
    if w.raised_by is RaisedBy.ASSISTANT and w.person_id == person:
        allowed.append(WarningAction.DISMISS)
    return tuple(sorted(allowed, key=lambda a: list(WarningAction).index(a)))


def apply_action(
    w: Warning,
    r: Requester,
    ctx: ActionContext,
    action: WarningAction,
    *,
    at: datetime,
    reason: str | None,
    note: str | None,
) -> Warning:
    """Applies one action after the visibility, state and permission checks, in that order."""
    if not can_see(w, r.viewer, r.grant).full:
        raise NotVisible("not found")
    if w.state not in ACTIVE:
        raise InvalidTransition("a final warning accepts no action")
    if action is WarningAction.ACKNOWLEDGE and any(
        a.person_id == _acting_person(r) for a in w.acknowledgements
    ):
        raise InvalidTransition("already acknowledged by this person")
    if action is WarningAction.ADD_NOTE and len(w.notes) >= ctx.max_notes:
        raise InvalidTransition("the note bound is reached")
    if action not in allowed_actions(w, r, ctx):
        raise ActionNotAllowed(action.value)
    return _transition(w, r, ctx, action, at, reason, note)


def _transition(
    w: Warning,
    r: Requester,
    ctx: ActionContext,
    action: WarningAction,
    at: datetime,
    reason: str | None,
    note: str | None,
) -> Warning:
    match action:
        case WarningAction.ACKNOWLEDGE:
            return acknowledge(w, r.actor, at)
        case WarningAction.ADD_NOTE:
            return add_note(w, r.actor, at, note or "", max_notes=ctx.max_notes)
        case WarningAction.RESOLVE:
            return resolve(w, r.actor, at, reason or "", role=resolve_role(w, r, ctx.definition_status))
        case _:
            return dismiss(w, r.actor, at, reason)
