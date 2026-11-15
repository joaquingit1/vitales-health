"""Pure PlanRun aggregate, commuting commands and attributed reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.kernel.uow import Actor
from crew_health.plans.domain.availability import Availability, step_applies
from crew_health.plans.domain.errors import InvalidField, InvalidTransition, StaleVersion, StepNotAvailable
from crew_health.plans.domain.model import Plan, StageId, Step, SupplyNeed
from crew_health.plans.domain.recheck import due_at

RunState = Literal["active", "watching", "escalated", "closed"]
StepState = Literal["open", "done", "not_available", "substituted"]
OriginMode = Literal["LIVE", "REPLAY", "SIM"]
EventKind = Literal["plan_run.changed", "plan_run.step_done", "plan_run.escalated"]
CloseDisposition = Literal[
    "resolved_on_board", "handed_to_medical_officer", "watch_ended", "preparing_return", "other"
]


@dataclass(frozen=True, slots=True)
class RunStep:
    step_id: str
    stage_id: StageId
    substitute_only: bool
    principal_step_id: str | None
    state: StepState
    by: Actor | None
    at: datetime | None
    substitute_step_id: str | None
    guided_check_id: str | None
    observation_ids: tuple[str, ...]
    supply_use_ids: tuple[str, ...]
    runs: int
    recorded_revision: int | None
    not_available_reason: str | None


@dataclass(frozen=True, slots=True)
class RunRecheck:
    step_id: str
    due_at: datetime | None
    done_at: datetime | None
    observation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Escalation:
    step_id: str
    help_session_id: str
    at: datetime
    session_closed: bool
    escalation_id: str | None


@dataclass(frozen=True, slots=True)
class Closure:
    disposition: CloseDisposition
    note: str | None
    by: Actor
    at: datetime


@dataclass(frozen=True, slots=True)
class LinkAtOpen:
    status: Literal["up", "delayed", "down"]
    gap_kind: str | None
    next_contact_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlanRun:
    plan_run_id: str
    revision: int
    plan_id: str
    pins: tuple[tuple[str, str], ...]
    opened_release_id: str
    subject_person_id: str
    episode_id: str | None
    help_session_id: str | None
    environment_id: str
    origin_mode: OriginMode
    opened_by: Actor
    opened_at: datetime
    steps: tuple[RunStep, ...]
    rechecks: tuple[RunRecheck, ...]
    escalations: tuple[Escalation, ...]
    handoff_id: str | None
    link_state_at_open: LinkAtOpen
    close: Closure | None
    state: RunState

    def step(self, step_id: str) -> RunStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)


@dataclass(frozen=True, slots=True)
class StepDone:
    step_id: str
    observation_ids: tuple[str, ...]
    guided_check_id: str | None
    if_match: int


@dataclass(frozen=True, slots=True)
class NotAvailable:
    step_id: str
    reason: str
    if_match: int


@dataclass(frozen=True, slots=True)
class UseSubstitute:
    step_id: str
    if_match: int


@dataclass(frozen=True, slots=True)
class Escalate:
    step_id: str
    help_session_id: str
    escalation_id: str | None
    if_match: int


@dataclass(frozen=True, slots=True)
class Close:
    disposition: CloseDisposition
    note: str | None
    if_match: int


@dataclass(frozen=True, slots=True)
class GuidedCheckDone:
    step_id: str
    guided_check_id: str
    outcome: Literal["agree", "disagree", "no_reference"]
    observation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EscalationSeen:
    step_id: str
    help_session_id: str
    escalation_id: str | None


@dataclass(frozen=True, slots=True)
class EscalationClosed:
    help_session_id: str


@dataclass(frozen=True, slots=True)
class HandoffLinked:
    handoff_id: str


type RunCommand = (
    StepDone
    | NotAvailable
    | UseSubstitute
    | Escalate
    | Close
    | GuidedCheckDone
    | EscalationSeen
    | EscalationClosed
    | HandoffLinked
)
type PublicCommand = StepDone | NotAvailable | UseSubstitute | Escalate | Close


@dataclass(frozen=True, slots=True)
class ActionContext:
    plan: Plan
    runnable: Mapping[str, bool]
    availability: Mapping[str, Availability]
    actor: Actor
    at: datetime
    restricted: bool
    max_rechecks_per_step: int
    max_escalations_per_run: int
    month_days: int | None


@dataclass(frozen=True, slots=True)
class Debit:
    step_id: str
    occurrence: int
    needs: tuple[SupplyNeed, ...]


@dataclass(frozen=True, slots=True)
class RunEffects:
    debits: tuple[Debit, ...]
    event: EventKind | None
    outcome: str


def actionable(run: PlanRun, step: RunStep) -> bool:
    if not step.substitute_only:
        return True
    principal = run.step(step.principal_step_id) if step.principal_step_id is not None else None
    return principal is not None and principal.state in ("not_available", "substituted")


def derive_state(run: PlanRun) -> RunState:
    if run.close is not None:
        return "closed"
    if any(not e.session_closed for e in run.escalations):
        return "escalated"
    if any(
        s.state == "open" and s.stage_id in ("check_another_way", "do_now") and actionable(run, s)
        for s in run.steps
    ):
        return "active"
    return "watching"


def _open_steps(applicable: tuple[Step, ...]) -> tuple[RunStep, ...]:
    principals = {s.substitute_step_id: s.step_id for s in applicable if s.substitute_step_id is not None}
    return tuple(
        RunStep(
            s.step_id,
            s.stage_id,
            s.substitute_only,
            principals.get(s.step_id) if s.substitute_only else None,
            "open",
            None,
            None,
            s.substitute_step_id,
            None,
            (),
            (),
            0,
            None,
            None,
        )
        for s in applicable
    )


def open_run(
    *,
    plan: Plan,
    environment_id: str,
    plan_run_id: str,
    pins: tuple[tuple[str, str], ...],
    opened_release_id: str,
    subject_person_id: str,
    episode_id: str | None,
    help_session_id: str | None,
    origin_mode: OriginMode,
    opened_by: Actor,
    at: datetime,
    link: LinkAtOpen,
    return_option_present: bool,
) -> PlanRun:
    if (episode_id is None) == (help_session_id is None):
        raise InvalidField("/episode_id", "exactly_one_episode_or_help_session")
    content = plan.steps
    if (
        return_option_present
        and plan.return_stage is not None
        and environment_id in plan.return_stage.environments
    ):
        content += plan.return_stage.prepare
    applicable = tuple(s for s in content if step_applies(s, plan, environment_id))
    steps = _open_steps(applicable)
    rechecks = tuple(
        RunRecheck(s.step_id, None, None, ())
        for s in applicable
        if s.stage_id == "watch" and s.recheck is not None
    )
    run = PlanRun(
        plan_run_id,
        1,
        plan.plan_id,
        tuple(sorted(pins)),
        opened_release_id,
        subject_person_id,
        episode_id,
        help_session_id,
        environment_id,
        origin_mode,
        opened_by,
        at,
        steps,
        rechecks,
        (),
        None,
        link,
        None,
        "active",
    )
    return replace(run, state=derive_state(run))


def apply(run: PlanRun, cmd: RunCommand, ctx: ActionContext) -> tuple[PlanRun, RunEffects]:
    if isinstance(cmd, (StepDone, NotAvailable, UseSubstitute, Escalate, Close)):
        _public_guards(run, cmd, ctx)
        if isinstance(cmd, Close):
            return _finish(run, replace(run, close=Closure(cmd.disposition, cmd.note, ctx.actor, ctx.at)))
        step = run.step(cmd.step_id)
        if step is None:
            raise InvalidField("/step_id", "not_in_run")
        return _public_step(run, step, cmd, ctx)
    return _internal(run, cmd, ctx)


def _finish(
    run: PlanRun,
    candidate: PlanRun,
    *,
    debits: tuple[Debit, ...] = (),
    event: EventKind = "plan_run.changed",
    outcome: str = "applied",
) -> tuple[PlanRun, RunEffects]:
    candidate = replace(candidate, state=derive_state(candidate))
    if candidate == run:
        return run, RunEffects((), None, "merged_no_op" if outcome == "applied" else outcome)
    return replace(candidate, revision=run.revision + 1), RunEffects(debits, event, outcome)


def _public_guards(run: PlanRun, cmd: PublicCommand, ctx: ActionContext) -> None:
    # Design 3.5: the first failing guard decides, including on restricted runs.
    if run.close is not None:
        raise InvalidTransition("run_closed")
    if not isinstance(cmd, Close) and run.step(cmd.step_id) is None:
        raise InvalidField("/step_id", "not_in_run")
    if ctx.restricted and not isinstance(cmd, (Escalate, Close)):
        raise InvalidTransition("run_restricted")
    # An If-Match other than the current revision (older, or one the run never had) is stale for these two.
    if isinstance(cmd, (Escalate, Close)) and cmd.if_match != run.revision:
        raise StaleVersion()


def _replace_step(run: PlanRun, step: RunStep) -> PlanRun:
    return replace(run, steps=tuple(step if s.step_id == step.step_id else s for s in run.steps))


def _complete(
    run: PlanRun,
    step: RunStep,
    content: Step,
    observations: tuple[str, ...],
    guided_id: str | None,
    ctx: ActionContext,
) -> tuple[PlanRun, tuple[Debit, ...]]:
    occurrence = step.runs + 1
    completed = replace(
        step,
        state="done",
        by=ctx.actor,
        at=ctx.at,
        runs=occurrence,
        recorded_revision=run.revision + 1,
        observation_ids=observations,
        guided_check_id=guided_id,
    )
    candidate = _replace_step(run, completed)
    if content.stage_id == "watch" and content.recheck is not None:
        due = due_at(content.recheck, ctx.at, month_days=ctx.month_days)
        candidate = replace(
            candidate,
            rechecks=tuple(
                RunRecheck(r.step_id, due, ctx.at, observations) if r.step_id == step.step_id else r
                for r in run.rechecks
            ),
        )
    needs = tuple(n for n in content.needs.supplies if n.per_use is not None)
    return candidate, (Debit(step.step_id, occurrence, needs),) if needs else ()


def _is_runnable(step: RunStep, ctx: ActionContext) -> bool:
    return ctx.runnable.get(step.step_id, False) and ctx.availability.get(step.step_id) != "substituted"


def _step_done(run: PlanRun, step: RunStep, cmd: StepDone, ctx: ActionContext) -> tuple[PlanRun, RunEffects]:
    content = ctx.plan.step(step.step_id)
    if step.state == "done":
        if (
            content is None
            or step.stage_id != "watch"
            or content.recheck is None
            or step.recorded_revision is None
            or cmd.if_match < step.recorded_revision
        ):
            return _finish(run, run)
        if not _is_runnable(step, ctx) or any(n.per_use is not None for n in content.needs.supplies):
            raise StepNotAvailable(
                step.step_id
            )  # R28-20: no repeated supply use until the ledger seam ships.
        if step.runs >= 1 + ctx.max_rechecks_per_step:
            raise InvalidField("/step_id", "recheck_bound")
    elif step.state != "open":
        raise InvalidTransition("step_not_open")
    elif not actionable(run, step):
        raise InvalidTransition("substitute_not_actionable")
    if content is None or not _is_runnable(step, ctx):
        raise StepNotAvailable(step.step_id)
    candidate, debits = _complete(run, step, content, cmd.observation_ids, cmd.guided_check_id, ctx)
    return _finish(run, candidate, debits=debits, event="plan_run.step_done")


def _escalation_allowed(step_id: str, ctx: ActionContext) -> bool:
    content = ctx.plan.step(step_id)
    return content is not None and content.stage_id == "if_worse" and content.opens_help


def _escalate(run: PlanRun, cmd: Escalate | EscalationSeen, ctx: ActionContext) -> tuple[PlanRun, RunEffects]:
    if any(e.help_session_id == cmd.help_session_id for e in run.escalations):
        return _finish(run, run)
    if isinstance(cmd, Escalate) and len(run.escalations) >= ctx.max_escalations_per_run:
        raise InvalidField("/step_id", "escalation_bound")
    item = Escalation(cmd.step_id, cmd.help_session_id, ctx.at, False, cmd.escalation_id)
    return _finish(run, replace(run, escalations=run.escalations + (item,)), event="plan_run.escalated")


def _public_step(
    run: PlanRun, step: RunStep, cmd: StepDone | NotAvailable | UseSubstitute | Escalate, ctx: ActionContext
) -> tuple[PlanRun, RunEffects]:
    if isinstance(cmd, StepDone):
        return _step_done(run, step, cmd, ctx)
    if isinstance(cmd, Escalate):
        if not _escalation_allowed(cmd.step_id, ctx):
            raise InvalidField("/step_id", "not_escalation_step")
        return _escalate(run, cmd, ctx)
    if isinstance(cmd, UseSubstitute):
        if step.substitute_step_id is None:
            raise InvalidField("/step_id", "no_substitute")
        if step.state == "done":
            raise InvalidTransition("step_done")
        return _finish(run, _replace_step(run, replace(step, state="substituted")))
    if step.state in ("done", "substituted"):
        raise InvalidTransition("step_not_open")
    if step.state == "not_available":
        return _finish(run, run)
    return _finish(
        run, _replace_step(run, replace(step, state="not_available", not_available_reason=cmd.reason))
    )


def _guided(run: PlanRun, cmd: GuidedCheckDone, ctx: ActionContext) -> tuple[PlanRun, RunEffects]:
    if cmd.outcome == "disagree":
        return _finish(run, run, outcome="guided_check_disagree")
    if run.close is not None:
        return _finish(run, run, outcome="guided_check_not_applied")
    step = run.step(cmd.step_id)
    if step is not None and step.state == "done" and step.guided_check_id == cmd.guided_check_id:
        return _finish(run, run)
    content = ctx.plan.step(cmd.step_id)
    if (
        step is None
        or content is None
        or step.state != "open"
        or not actionable(run, step)
        or not _is_runnable(step, ctx)
    ):
        return _finish(run, run, outcome="guided_check_not_applied")
    candidate, debits = _complete(run, step, content, cmd.observation_ids, cmd.guided_check_id, ctx)
    return _finish(run, candidate, debits=debits, event="plan_run.step_done")


def _internal(
    run: PlanRun, cmd: GuidedCheckDone | EscalationSeen | EscalationClosed | HandoffLinked, ctx: ActionContext
) -> tuple[PlanRun, RunEffects]:
    if isinstance(cmd, GuidedCheckDone):
        return _guided(run, cmd, ctx)
    if isinstance(cmd, EscalationSeen):
        if run.step(cmd.step_id) is None or not _escalation_allowed(cmd.step_id, ctx):
            return _finish(run, run, outcome="escalation_not_applied")
        # SYSTEM 8.1: retain attributed history even when the run has already closed.
        return _escalate(run, cmd, ctx)
    if isinstance(cmd, EscalationClosed):
        candidate = replace(
            run,
            escalations=tuple(
                replace(e, session_closed=True) if e.help_session_id == cmd.help_session_id else e
                for e in run.escalations
            ),
        )
        return _finish(run, candidate)
    if run.handoff_id is None:
        return _finish(run, replace(run, handoff_id=cmd.handoff_id))
    return _finish(run, run, outcome="applied" if run.handoff_id == cmd.handoff_id else "handoff_conflict")
