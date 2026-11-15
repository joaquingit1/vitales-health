"""The plan run or help session a use names (E32 3.3 use admission): read through its owner's port, outside the
write transaction and bounded, then checked against the mission inside it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from crew_health.kernel.uow import SessionContext
from crew_health.supplies.application.context import Deps
from crew_health.supplies.application.ports import MissionFacts, SupplyNeed
from crew_health.supplies.domain.errors import FieldIssue, ReferenceNotVisible, UseInvalid


@dataclass(frozen=True, slots=True)
class Reference:
    kind: Literal["none", "plan_run", "help_session"]  # internal enum
    origin_mode: str | None
    environment_id: str | None
    pinned_needs: Mapping[str, tuple[SupplyNeed, ...]] | None  # plan runs only


NO_REFERENCE = Reference("none", None, None, None)


def read_reference(
    deps: Deps, session: SessionContext, plan_run_id: str | None, help_session_id: str | None
) -> Reference:
    """A reference whose owner is absent, unknown or not visible to the session is refused (fail closed)."""
    if plan_run_id is not None:
        runs = deps.plan_runs
        if runs is None:
            raise ReferenceNotVisible()
        run = deps.references.call(lambda: runs.context(session, plan_run_id))
        if run is None or not run.visible:
            raise ReferenceNotVisible()
        return Reference("plan_run", run.origin_mode, run.environment_id, run.pinned_needs)
    if help_session_id is not None:
        sessions = deps.help_sessions
        if sessions is None:
            raise ReferenceNotVisible()
        found = deps.references.call(lambda: sessions.context(session, help_session_id))
        if not found.visible:
            raise ReferenceNotVisible()
        return Reference("help_session", found.origin_mode, found.environment_id, None)
    return NO_REFERENCE


def _issue(pointer: str, message: str) -> UseInvalid:
    return UseInvalid((FieldIssue(pointer, "INVALID_VALUE", message),))


def check_reference(reference: Reference, facts: MissionFacts, supply_id: str, step_id: str | None) -> None:
    """Origin mode and environment are known and equal the mission's (INVALID_VALUE until R-E32-23 and R-E32-26;
    an unknown one is never a match, AGENTS rule 2); a plan run's
    step is in its pinned plan and the supply's pinned need there has no per-use amount (that one is debited
    when the step is done, so a direct use would count it twice)."""
    if reference.kind == "none":
        return
    pointer = "/plan_run_id" if reference.kind == "plan_run" else "/help_session_id"
    if reference.origin_mode is None or reference.origin_mode != facts.mission_mode:
        raise _issue(pointer, "the reference's origin mode is unknown or another one")
    if reference.environment_id is None or reference.environment_id != facts.active_environment_id:
        raise _issue(pointer, "the reference's environment is unknown or another one")
    if reference.kind != "plan_run":
        return
    needs = reference.pinned_needs or {}
    if step_id is None or step_id not in needs:
        raise _issue("/step_id", "the step is not in the run's pinned plan")
    if any(n.supply_id == supply_id and n.per_use is not None for n in needs[step_id]):
        raise _issue("/step_id", "this step debits the supply when it is done")
