"""Transactional guided completions and durable help/handoff reconciliation."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import cast

from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import StoredEvent, UnitOfWork
from crew_health.plans.application.act import ReferenceInvalid, validate_references
from crew_health.plans.application.teach_copies import GuidedCheckCompleted
from crew_health.plans.application.world import PlansWorld
from crew_health.plans.domain.errors import PlansError
from crew_health.plans.domain.model import all_steps
from crew_health.plans.domain.run import (
    EscalationClosed,
    EscalationSeen,
    GuidedCheckDone,
    HandoffLinked,
    PlanRun,
    RunCommand,
    apply,
)

_LOGGER = "crew_health.plans"


class PlanConsumers:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def on_guided_check_completed(self, uow: UnitOfWork, event: GuidedCheckCompleted) -> None:
        if event.plan_run_id is None or event.step_id is None:
            return
        run = self.world.repository.load(uow, event.plan_run_id)
        if run is None or run.close is not None or self.world.guided_checks is None:
            self._log("guided_check_not_applied", event.plan_run_id)
            return
        context = replace(self.world.action_context(uow, run), actor=uow.actor)
        step = context.plan.step(event.step_id)
        facts = self.world.guided_checks.get(uow, event.guided_check_id)
        if event.outcome == "disagree":
            self._log("guided_check_disagree", run.plan_run_id)
            return
        if (
            step is None
            or facts is None
            or facts.guided_check_id != event.guided_check_id
            or facts.plan_run_id != run.plan_run_id
            or facts.subject_person_id != run.subject_person_id
            or facts.measured_record_id != run.subject_person_id
            or facts.skill_id != step.guided_check_skill_id
            or facts.origin_mode != run.origin_mode
            or facts.state != "checked"
            or facts.result != event.outcome
            or facts.observation_ids != event.observation_ids
            or len(event.observation_ids) > self.world.settings.max_observations_per_record
        ):
            self._log("guided_check_not_applied", run.plan_run_id)
            return
        try:
            validate_references(self.world, uow, run, step, event.observation_ids, event.guided_check_id)
        except ReferenceInvalid:
            self._log("guided_check_not_applied", run.plan_run_id)
            return
        if context.restricted:
            self._log("guided_check_not_applied", run.plan_run_id)
            return
        # A ledger refusal rolls back all its writes, while E30's valid measurement may still commit.
        try:
            with uow.savepoint("plans_guided_completion"):
                updated, effects = apply(
                    run,
                    GuidedCheckDone(
                        event.step_id, event.guided_check_id, event.outcome, event.observation_ids
                    ),
                    context,
                )
                updated = self.world.debit(uow, updated, effects)
                self.world.persist(uow, run, updated, effects)
        except ProblemError as error:
            if error.code != ErrorCode.SUPPLY_BELOW_ZERO:
                raise
            self._log("guided_check_supply_below_zero", run.plan_run_id)

    def on_help_session_changed(self, uow: UnitOfWork, event: StoredEvent) -> None:
        body = event_body(event)
        session_id = body.get("help_session_id")
        if not isinstance(session_id, str):
            return
        entry = body.get("entry")
        if (
            isinstance(entry, dict)
            and entry.get("kind") == "plan_step"
            and isinstance(entry.get("ref_id"), str)
        ):
            run = self.world.repository.load(uow, entry["ref_id"])
            if run is not None:
                self._help_seen(uow, run, body, session_id)
        if body.get("state") == "closed" or body.get("closed_at") is not None:
            for run in self.world.repository.runs_with_open_escalation(uow, session_id):
                self._apply(uow, run, EscalationClosed(session_id))

    def _help_seen(self, uow: UnitOfWork, run: PlanRun, body: dict[str, object], session_id: str) -> None:
        plan, _ = self.world.plan(uow, run)
        choices = tuple(s for s in all_steps(plan) if s.stage_id == "if_worse" and s.opens_help)
        flow = body.get("flow_id")
        if flow is None and isinstance(body.get("node"), dict):
            flow = cast(dict[str, object], body["node"]).get("id")
        pending = self.world.repository.pending_intents(uow, run.plan_run_id)
        intent = next((i for i in pending if i.flow_id == flow), pending[0] if pending else None)
        step = next((s for s in choices if s.opens_flow_id == flow), choices[0] if choices else None)
        if intent is not None:
            step = plan.step(intent.step_id)
        if step is not None:
            self._apply(
                uow,
                run,
                EscalationSeen(step.step_id, session_id, None if intent is None else intent.escalation_id),
            )

    def on_handoff_changed(self, uow: UnitOfWork, event: StoredEvent) -> None:
        body = event_body(event)
        run_id, handoff_id = body.get("plan_run_id"), body.get("handoff_id")
        if (
            not isinstance(run_id, str)
            or not isinstance(handoff_id, str)
            or body.get("state")
            not in (
                "queued",
                "in_transit",
                "delivered",
                "receipt_recorded",
                "delivery_failed",
                "cancelled",
                "expired",
            )
        ):
            return
        run = self.world.repository.load(uow, run_id)
        if run is not None:
            self._apply(uow, run, HandoffLinked(handoff_id))

    def _apply(self, uow: UnitOfWork, run: PlanRun, command: RunCommand) -> None:
        try:
            context = replace(self.world.action_context(uow, run), actor=uow.actor)
            updated, effects = apply(run, command, context)
        except PlansError:
            self._log("plan_event_not_applied", run.plan_run_id)
            return
        self.world.persist(uow, run, updated, effects)

    @staticmethod
    def _log(outcome: str, run_id: str) -> None:
        logging.getLogger(_LOGGER).warning("%s %s", outcome, run_id)


def event_body(event: StoredEvent) -> dict[str, object]:
    value: object = json.loads(event.data_json)
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, object], value)
