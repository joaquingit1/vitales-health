"""Read-only plan tools, scoped exclusively by the assistant controller."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import JsonValue

from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.uow import UnitOfWork
from crew_health.plans.application.assistant_copies import (
    AnswerScope,
    ApprovedPassages,
    Deadline,
    HandlerNoResult,
    HandlerOk,
    HandlerOutcome,
    HandlerRejected,
    ToolScope,
)
from crew_health.plans.application.views import PlanProjection, PlanViews
from crew_health.plans.application.world import PlansWorld, authorize, fail
from crew_health.plans.domain.run import PlanRun, actionable
from crew_health.plans.domain.tool_rules import choose_plan_step_reason, explain_step_reason


def arguments_valid(
    arguments: Mapping[str, JsonValue], required: frozenset[str], optional: frozenset[str] = frozenset()
) -> bool:
    return (
        required <= arguments.keys()
        and arguments.keys() <= required | optional
        and all(isinstance(value, str) for value in arguments.values())
    )


def rejected(name: str, scope: ToolScope, reason: str) -> HandlerRejected:
    logging.getLogger("crew_health.plans").warning("%s %s %s", name, scope.turn_id, reason)
    return HandlerRejected(reason)


@dataclass(frozen=True)
class ScopedPlan:
    projection: PlanProjection
    run: PlanRun | None

    def actionable(self, step_id: str) -> bool:
        if self.run is None:
            step = self.projection.plan.step(step_id)
            return step is not None and not step.substitute_only
        run_step = self.run.step(step_id)
        return run_step is not None and actionable(self.run, run_step)


class PlanTools:
    def __init__(
        self,
        world: PlansWorld | Callable[[], PlansWorld],
        *,
        answers: AnswerScope | None = None,
        passages: ApprovedPassages | None = None,
    ) -> None:
        self._world = world
        self.answers, self.passages = answers, passages
        self.get_plan = GetPlan(self)
        self.choose_plan_step = ChoosePlanStep(self)
        self.explain_step = ExplainStep(self)

    @property
    def world(self) -> PlansWorld:
        return self._world() if callable(self._world) else self._world

    def scoped(self, uow: UnitOfWork, scope: ToolScope) -> ScopedPlan | None:
        world = self.world
        if scope.release_id != uow.release.release_id:
            raise fail(ErrorCode.CONFIG_VERSION_MISMATCH)
        PlanViews._authorize(scope.session)
        if scope.session.kind == "crew":
            authorize(scope.session, scope.subject_person_id)
        if (scope.episode_id is None) == (scope.help_session_id is None):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        run = None if scope.plan_run_id is None else world.repository.load(uow, scope.plan_run_id)
        if scope.plan_run_id is not None and (
            run is None
            or run.subject_person_id != scope.subject_person_id
            or run.episode_id != scope.episode_id
            or run.help_session_id != scope.help_session_id
        ):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        plan_id = self._plan_id(uow, scope, run)
        if plan_id is None:
            return None
        projection = PlanViews(world).scoped(uow, scope.session, plan_id, scope.subject_person_id)
        return None if projection is None else ScopedPlan(projection, run)

    def _plan_id(self, uow: UnitOfWork, scope: ToolScope, run: PlanRun | None) -> str | None:
        world = self.world
        if scope.episode_id is not None:
            episode = (
                None if world.episodes is None else world.episodes.visible(scope.session, scope.episode_id)
            )
            if episode is None or episode.subject_person_id != scope.subject_person_id:
                raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
            # E15's current reader exposes identity only. A controller-pinned run supplies the plan reference.
            return None if run is None else run.plan_id
        sessions = world.help_sessions
        if sessions is None or scope.help_session_id is None:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if not sessions.readable_by(scope.session, scope.help_session_id):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        help_session = sessions.snapshot(scope.help_session_id)
        if help_session is None or help_session.subject_person_id != scope.subject_person_id:
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        document = world.catalog.document(uow.release)
        plan_id = (
            None
            if document is None
            else next(
                (
                    p.plan_id
                    for p in document.plans
                    if p.flow_id is not None and p.flow_id == help_session.flow_id
                ),
                None,
            )
        )
        return plan_id if run is None or run.plan_id == plan_id else None


class GetPlan:
    name: str = "get_plan"
    effect: Literal["read", "compute", "proposal"] = "read"

    def __init__(self, tools: PlanTools) -> None:
        self.tools = tools

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, JsonValue], deadline: Deadline
    ) -> HandlerOutcome:
        if not arguments_valid(arguments, frozenset(), frozenset({"plan_id"})):
            return rejected(self.name, scope, "answer_out_of_scope")
        scoped = self.tools.scoped(uow, scope)
        if scoped is None:
            return HandlerNoResult("plan_not_active")
        projection = scoped.projection
        if "plan_id" in arguments and arguments["plan_id"] != projection.plan.plan_id:
            return rejected(self.name, scope, "answer_out_of_scope")
        return HandlerOk(cast(Mapping[str, JsonValue], projection.wire))


class ChoosePlanStep:
    name: str = "choose_plan_step"
    effect: Literal["read", "compute", "proposal"] = "proposal"

    def __init__(self, tools: PlanTools) -> None:
        self.tools = tools

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, JsonValue], deadline: Deadline
    ) -> HandlerOutcome:
        required = frozenset({"plan_id", "step_id", "because_answer_id"})
        if not arguments_valid(arguments, required, frozenset({"note"})):
            return rejected(self.name, scope, "answer_out_of_scope")
        strings = cast(Mapping[str, str], arguments)
        scoped = self.tools.scoped(uow, scope)
        projection = None if scoped is None else scoped.projection
        plan = None if projection is None else projection.plan
        step_id = strings["step_id"]
        answer_in_scope = self.tools.answers is not None and self.tools.answers.answer_belongs(
            uow, strings["because_answer_id"], scope.episode_id, scope.help_session_id
        )
        reason = choose_plan_step_reason(
            plan_active=plan is not None and plan.plan_id == strings["plan_id"],
            plan=plan,
            step_id=step_id,
            actionable=scoped is not None and scoped.actionable(step_id),
            runnable=projection is not None and projection.runnable.get(step_id, False),
            answer_in_scope=answer_in_scope,
            string_arguments=tuple(strings.values()),
        )
        if reason is not None:
            return rejected(self.name, scope, reason)
        return HandlerOk({"plan_id": strings["plan_id"], "step_id": step_id, "proposed": True})


class ExplainStep:
    name: str = "explain_step"
    effect: Literal["read", "compute", "proposal"] = "read"

    def __init__(self, tools: PlanTools) -> None:
        self.tools = tools

    def handle(
        self, uow: UnitOfWork, scope: ToolScope, arguments: Mapping[str, JsonValue], deadline: Deadline
    ) -> HandlerOutcome:
        if not arguments_valid(arguments, frozenset({"plan_id", "step_id"})):
            return rejected(self.name, scope, "answer_out_of_scope")
        strings = cast(Mapping[str, str], arguments)
        scoped = self.tools.scoped(uow, scope)
        plan = None if scoped is None else scoped.projection.plan
        reason = explain_step_reason(
            plan_active=plan is not None and plan.plan_id == strings["plan_id"],
            plan=plan,
            step_id=strings["step_id"],
        )
        if reason is not None:
            return rejected(self.name, scope, reason)
        step = None if plan is None else plan.step(strings["step_id"])
        if step is None:
            raise fail(ErrorCode.INTERNAL_ERROR)
        passages = self.tools.passages
        return HandlerOk(
            {
                "text": step.text,
                "text_first_time": step.text_first_time,
                "source_ids": list(step.source_ids),
                "passages": []
                if passages is None
                else cast(list[JsonValue], list(passages.passages(uow, scope.release_id, step.source_ids))),
            }
        )
