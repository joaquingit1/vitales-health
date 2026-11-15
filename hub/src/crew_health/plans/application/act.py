"""Run actions and SYSTEM 8.1 durable, separately committed help escalation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime
from typing import Literal, cast

from crew_health.contracts_gen.enums import Role
from crew_health.contracts_gen.schemas.requests_schema import PlanRunAction
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import CommandIdentity, PendingCommand, StoredResult, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.plans.application.errors import problem
from crew_health.plans.application.ports import EscalationIntent, PlanEscalation
from crew_health.plans.application.runs import identity_for, replay, result_for
from crew_health.plans.application.world import PlansWorld, View, authorize, fail, human, internal
from crew_health.plans.domain.errors import PlansError
from crew_health.plans.domain.model import Step
from crew_health.plans.domain.run import (
    ActionContext,
    Close,
    Escalate,
    EscalationClosed,
    EscalationSeen,
    NotAvailable,
    PlanRun,
    PublicCommand,
    RunEffects,
    StepDone,
    UseSubstitute,
    apply,
)


class ReferenceInvalid(ProblemError):
    """A semantic reference refusal raised by this module, distinct from provider failures."""

    def __init__(self, pointer: str) -> None:
        failure = fail(ErrorCode.INVALID_VALUE, pointer)
        super().__init__(failure.code, str(failure), field_errors=failure.field_errors)


def validate_references(
    world: PlansWorld,
    view: View,
    run: PlanRun,
    step: Step | None,
    observations: tuple[str, ...],
    guided_id: str | None,
) -> None:
    if observations:
        if world.observations is None or len(set(observations)) != len(observations):
            raise ReferenceInvalid("/observation_ids")
        found = world.observations.get_many(observations)
        for reference in observations:
            row = found.get(reference)
            if (
                row is None
                or row.patient_id != run.subject_person_id
                or row.origin_mode != run.origin_mode
                or row.quality_status != "accepted"
                or row.episode_id not in (None, run.episode_id)
            ):
                raise ReferenceInvalid("/observation_ids")
    if guided_id is not None:
        facts = None if world.guided_checks is None else world.guided_checks.get(view, guided_id)
        if (
            step is None
            or step.guided_check_skill_id is None
            or facts is None
            or facts.plan_run_id != run.plan_run_id
            or facts.subject_person_id != run.subject_person_id
            or facts.measured_record_id != run.subject_person_id
            or facts.skill_id != step.guided_check_skill_id
            or facts.state != "checked"
            or facts.result not in ("agree", "no_reference")
            or facts.origin_mode != run.origin_mode
            or facts.observation_ids != observations
        ):
            raise ReferenceInvalid("/guided_check_id")


def public_command(action: PlanRunAction, revision: int) -> PublicCommand:
    kind = action.action
    if kind == "close":
        if action.disposition is None:
            raise fail(ErrorCode.INVALID_VALUE, "/disposition")
        return Close(action.disposition, action.note, revision)
    if action.step_id is None:
        raise fail(ErrorCode.INVALID_VALUE, "/step_id")
    step_id = action.step_id.root
    if kind == "step_done":
        return StepDone(
            step_id,
            tuple(x.root for x in action.observation_ids or ()),
            None if action.guided_check_id is None else action.guided_check_id.root,
            revision,
        )
    if kind == "not_available":
        if action.reason is None:
            raise fail(ErrorCode.INVALID_VALUE, "/reason")
        return NotAvailable(step_id, action.reason, revision)
    if kind == "use_substitute":
        return UseSubstitute(step_id, revision)
    # Escalation receipt is supplied only after the intent and help commits.
    return Escalate(step_id, "pending", None, revision)


class ActOnPlanRun:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def act(
        self,
        session: SessionContext,
        plan_run_id: str,
        action: Mapping[str, object] | PlanRunAction,
        key: str,
        if_match: int | None,
        config_release: str | None = None,
    ) -> dict[str, object]:
        human(session)
        if if_match is None:
            raise fail(ErrorCode.VERSION_REQUIRED)
        request = action if isinstance(action, PlanRunAction) else PlanRunAction.model_validate(action)
        self._bounds(request)
        identity = identity_for(
            session,
            key,
            "actOnPlanRun",
            plan_run_id,
            {"body": request.model_dump(mode="json"), "if_match": if_match},
        )
        if request.action == "escalate":
            return self._escalate(session, plan_run_id, request, identity, if_match, config_release)
        with self.world.uow.begin(
            actor=session.actor, command=identity, correlation_id=self.world.ids.new("cor")
        ) as uow:
            stored = self.world.idempotency.lookup(uow, identity)
            if stored is not None:
                return replay(self.world, uow, session, stored)
            self._config(uow, config_release)
            run = self.world.load(uow, plan_run_id)
            authorize(session, run.subject_person_id)
            context = replace(self.world.action_context(uow, run), actor=session.actor)
            cmd = public_command(request, if_match)
            if isinstance(cmd, StepDone):
                validate_references(
                    self.world,
                    uow,
                    run,
                    context.plan.step(cmd.step_id),
                    cmd.observation_ids,
                    cmd.guided_check_id,
                )
            try:
                updated, effects = apply(run, cmd, context)
            except PlansError as error:
                raise problem(error, self.world.wire(uow, run, session)) from error
            updated = self.world.debit(uow, updated, effects)
            self.world.persist(uow, run, updated, effects)
            self.world.idempotency.record(
                uow, identity, result_for(self.world, uow, identity, plan_run_id, 200)
            )
            return self.world.wire(uow, updated, session)

    def _bounds(self, action: PlanRunAction) -> None:
        for name, value in (("reason", action.reason), ("note", action.note)):
            if value is not None and len(value) > self.world.settings.max_text_chars:
                raise fail(ErrorCode.INVALID_VALUE, "/" + name)
        if len(action.observation_ids or ()) > self.world.settings.max_observations_per_record:
            raise fail(ErrorCode.INVALID_VALUE, "/observation_ids")

    @staticmethod
    def _config(view: View, config_release: str | None) -> None:
        if config_release is not None and config_release != view.release.release_id:
            raise fail(ErrorCode.CONFIG_VERSION_MISMATCH)

    def _escalate(
        self,
        session: SessionContext,
        run_id: str,
        action: PlanRunAction,
        identity: CommandIdentity,
        revision: int,
        config_release: str | None,
    ) -> dict[str, object]:
        with self.world.uow.begin(
            actor=session.actor, command=identity, correlation_id=self.world.ids.new("cor")
        ) as uow:
            stored = self.world.idempotency.lookup(uow, identity)
            if stored is not None:
                return replay(self.world, uow, session, stored)
            pending = self.world.intents.pending(uow, identity)
            run = self.world.load(uow, run_id)
            authorize(session, run.subject_person_id)
            if pending is None:
                self._config(uow, config_release)
                pending = self._reserve(uow, session, run, action, identity, revision)
        EscalationResumer(self.world).resume(session, pending)
        with self.world.snapshots.snapshot() as view:
            current = self.world.load(view, run_id)
            authorize(session, current.subject_person_id)
            return self.world.wire(view, current, session)

    def _reserve(
        self,
        uow: UnitOfWork,
        session: SessionContext,
        run: PlanRun,
        action: PlanRunAction,
        identity: CommandIdentity,
        revision: int,
    ) -> PendingCommand:
        context = self.world.action_context(uow, run)
        cmd = public_command(action, revision)
        try:
            apply(run, cmd, context)  # Validate before assigning the durable occurrence; no changes saved.
        except PlansError as error:
            raise problem(error, self.world.wire(uow, run, session)) from error
        if not isinstance(cmd, Escalate):
            raise fail(ErrorCode.INVALID_VALUE, "/action")
        step = context.plan.step(cmd.step_id)
        if step is None:
            raise fail(ErrorCode.INVALID_VALUE, "/step_id")
        count = self.world.repository.intent_count(uow, run.plan_run_id)
        unkeyed = sum(e.escalation_id is None for e in run.escalations)
        if count + unkeyed >= self.world.settings.max_escalations_per_run:
            raise fail(ErrorCode.INVALID_VALUE, "/step_id")
        occurrence = self.world.repository.next_occurrence(uow, run.plan_run_id, cmd.step_id)
        occurrence = max(occurrence, 1 + sum(e.step_id == cmd.step_id for e in run.escalations))
        escalation_id = self.world.ids.derived("pes", run.plan_run_id, cmd.step_id, str(occurrence))
        serialized = json.dumps(
            {
                "session_id": session.session_id,
                "kind": session.kind,
                "actor": {"kind": session.actor.kind, "id": session.actor.id},
                "person_id": session.person_id,
                "roles": sorted(r.value for r in session.roles),
                "parent_command": asdict(identity),
            }
        )
        intent = EscalationIntent(
            escalation_id,
            run.plan_run_id,
            cmd.step_id,
            occurrence,
            revision,
            run.subject_person_id,
            step.opens_flow_id,
            run.opened_release_id,
            run.origin_mode,
            action.client_time.root.isoformat(),
            serialized,
            identity.key,
            identity.actor_ref,
            "pending",
            None,
        )
        self.world.repository.insert_intent(uow, intent)
        return self.world.intents.reserve(
            uow, identity, resume_handler="plans.escalation", input_ref=escalation_id
        )


def escalation_request(row: EscalationIntent, parent: CommandIdentity, world: PlansWorld) -> PlanEscalation:
    value = cast(dict[str, object], json.loads(row.session_json))
    actor_value = cast(dict[str, str], value["actor"])
    actor = Actor(cast(ActorKind, actor_value["kind"]), actor_value["id"])
    session = SessionContext(
        cast(str, value["session_id"]),
        cast(SessionKind, value["kind"]),
        actor,
        cast(str | None, value["person_id"]),
        frozenset(Role(r) for r in cast(list[str], value["roles"])),
    )
    child = command_identity(
        key=row.escalation_id,
        actor_ref=actor.id,
        operation="internal.plan_help_entry",
        target=row.escalation_id,
        parameters_json=b"{}",
        body_fingerprint=parent.body_fingerprint,
        fingerprint_kind="sha256",
    )
    # The request contains no calibrated clock segment. Preserve raw client time as unmapped,
    # never the hub's current process segment or an invented correction revision (SYSTEM 5.3).
    client = TimeInput(
        datetime.fromisoformat(row.client_time),
        session.session_id,
        "unknown",
        0,
        "source_raw",
        row.origin_mode,
    )
    return PlanEscalation(
        row.escalation_id,
        row.plan_run_id,
        row.step_id,
        row.occurrence,
        row.expected_run_revision,
        row.subject_person_id,
        row.flow_id,
        row.pinned_release_id,
        row.origin_mode,
        client,
        session,
        parent,
        child,
    )


# Exact literal vocabularies of the kernel boundary, for decoding the trusted stored session.

type ActorKind = Literal["person", "station", "ground", "operator", "adapter", "engine", "assistant"]
type SessionKind = Literal["crew", "station", "ground", "operator", "editor", "adapter"]


class EscalationResumer:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def resume(self, session: SessionContext, intent: PendingCommand) -> StoredResult | None:
        human(session)
        with self.world.snapshots.snapshot() as view:
            row = self.world.repository.intent(view, intent.input_ref)
            if row is None:
                raise fail(ErrorCode.NOT_FOUND)
            authorize(session, row.subject_person_id)
            request = escalation_request(row, intent.identity, self.world)
        if row.state == "recorded":
            with self.world.uow.begin(actor=session.actor, correlation_id=self.world.ids.new("cor")) as uow:
                return self.world.idempotency.lookup(uow, intent.identity)
        if self.world.help_entry is None:
            raise internal()
        try:
            with self.world.snapshots.snapshot() as view:
                receipt = self.world.help_entry.receipt(view, request.session, row.escalation_id)
            if receipt is None:
                receipt = self.world.help_entry.open_from_plan_step(request)
        except ProblemError:
            raise  # Storage, integrity, and typed provider errors abort loudly.
        except Exception as error:
            raise internal() from error
        if receipt.escalation_id != row.escalation_id:
            raise internal()
        with self.world.uow.begin(
            actor=request.session.actor, command=intent.identity, correlation_id=self.world.ids.new("cor")
        ) as uow:
            stored = self.world.idempotency.lookup(uow, intent.identity)
            if stored is not None:
                return stored
            current = self.world.repository.intent(uow, row.escalation_id)
            if current is None:
                raise internal()
            run = self.world.load(uow, row.plan_run_id)
            context = replace(self.world.action_context(uow, run), actor=request.session.actor)
            command = (
                EscalationSeen(row.step_id, receipt.help_session_id, row.escalation_id)
                if run.close is not None
                else Escalate(row.step_id, receipt.help_session_id, row.escalation_id, run.revision)
            )
            try:
                updated, effects = apply(run, command, context)
            except PlansError as error:
                raise problem(error, self.world.wire(uow, run)) from error
            updated, effects = self._reconcile_closed(run, updated, effects, receipt.help_session_id, context)
            self.world.repository.record_intent(uow, row.escalation_id, receipt.help_session_id)
            self.world.persist(uow, run, updated, effects)
            result = result_for(self.world, uow, intent.identity, run.plan_run_id, 200)
            self.world.intents.complete(uow, intent, result)
            return result

    def _reconcile_closed(
        self, before: PlanRun, updated: PlanRun, effects: RunEffects, session_id: str, context: ActionContext
    ) -> tuple[PlanRun, RunEffects]:
        if self.world.help_sessions is None:
            raise internal()
        try:
            help_state = self.world.help_sessions.snapshot(session_id)
        except ProblemError:
            raise
        except Exception as error:
            raise internal() from error
        if help_state is None or help_state.help_session_id != session_id:
            raise internal()
        if help_state.open and help_state.closed_at is None:
            return updated, effects
        closed, closure_effects = apply(updated, EscalationClosed(session_id), context)
        if closed.revision == updated.revision:
            return updated, effects
        # Both domain commands form one serialized change, with one revision and one final-state event.
        return replace(closed, revision=before.revision + 1), effects if effects.event else closure_effects


class PlanEscalationIntents:
    def __init__(self, world: PlansWorld) -> None:
        self.world = world

    def read(self, snapshot: ReadSnapshot, session: SessionContext, escalation_id: str) -> PlanEscalation:
        row = self.world.repository.intent(snapshot, escalation_id)
        if row is None:
            raise fail(ErrorCode.NOT_FOUND)
        session_data = cast(dict[str, object], json.loads(row.session_json))
        if session.kind != "crew" or session.person_id != session_data.get("person_id"):
            raise fail(ErrorCode.UNAUTHORIZED_SCOPE)
        # Keep the parent identity in the feature-owned intent; never query another module's tables.
        value = cast(dict[str, str], session_data["parent_command"])
        identity = CommandIdentity(
            value["key"],
            value["actor_ref"],
            value["operation"],
            value["target"],
            value["parameters_sha256"],
            value["body_fingerprint"],
            cast(FingerprintKind, value["fingerprint_kind"]),
        )
        return escalation_request(row, identity, self.world)


type FingerprintKind = Literal["sha256", "hmac_sha256"]
