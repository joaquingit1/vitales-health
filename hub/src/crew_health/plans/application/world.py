"""Injected dependencies and shared, transaction-scoped plan decisions."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import cast

from crew_health.contracts_gen.enums import EventType
from crew_health.guidance.application.ports import GuidanceEligibility, SkillFacts
from crew_health.help.application.ports import HelpSessionReader
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandIntents, IdempotencyStore
from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReadSnapshotFactory,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.plans.application.eligibility import StepGate
from crew_health.plans.application.environment import EnvironmentReader
from crew_health.plans.application.ground_log import GroundLine
from crew_health.plans.application.pinning import PinnedContext, PinnedContexts
from crew_health.plans.application.ports import (
    EpisodeReader,
    ObservationRefReader,
    PlanHelpEntry,
    StockReader,
    SupplyCoverPort,
    SupplyLedgerPort,
    SupplyNeed,
)
from crew_health.plans.application.release import PlansCatalog, UiCopy, kit_supplies, month_days
from crew_health.plans.application.repository import PlanRunsRepository
from crew_health.plans.application.settings import PlansSettings
from crew_health.plans.application.teach_copies import GuidedCheckQueries
from crew_health.plans.application.wire import instant, run_wire
from crew_health.plans.domain.availability import EnvContext, assess_step
from crew_health.plans.domain.errors import PlansError
from crew_health.plans.domain.model import Plan, all_steps
from crew_health.plans.domain.model import SupplyNeed as DomainSupplyNeed
from crew_health.plans.domain.recheck import due_words
from crew_health.plans.domain.run import (
    ActionContext,
    Escalate,
    NotAvailable,
    PlanRun,
    RunEffects,
    RunRecheck,
    StepDone,
    UseSubstitute,
    apply,
)
from crew_health.roles.application import ports as roles_ports

_LOGGER = "crew_health.plans"
type View = ReadSnapshot | UnitOfWork


def fail(code: ErrorCode, pointer: str | None = None) -> ProblemError:
    return ProblemError(
        code,
        code.value,
        field_errors=() if pointer is None else (FieldError(pointer, "invalid", code.value),),
    )


def internal() -> ProblemError:
    return ProblemError(ErrorCode.INTERNAL_ERROR, "plans_provider_unavailable", retryable=True)


def visible(session: SessionContext, subject: str) -> bool:
    return session.kind == "crew" and session.person_id == subject


def authorize(session: SessionContext, subject: str) -> None:
    if not visible(session, subject):
        raise fail(ErrorCode.UNAUTHORIZED_SCOPE)


def human(session: SessionContext) -> None:
    if session.actor.kind != "person":
        raise fail(ErrorCode.ACTION_NOT_ALLOWED)


@dataclass(frozen=True, slots=True)
class PlansWorld:
    uow: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    clock: Clock
    ids: IdFactory
    settings: PlansSettings
    idempotency: IdempotencyStore
    intents: CommandIntents
    repository: PlanRunsRepository
    catalog: PlansCatalog
    guidance: GuidanceEligibility | None = None
    skills: SkillFacts | None = None
    roster: roles_ports.RosterPort | None = None
    mission: roles_ports.MissionStatePort | None = None
    help_sessions: HelpSessionReader | None = None
    stock: StockReader | None = None
    cover: SupplyCoverPort | None = None
    supply_ledger: SupplyLedgerPort | None = None
    help_entry: PlanHelpEntry | None = None
    guided_checks: GuidedCheckQueries | None = None
    episodes: EpisodeReader | None = None
    observations: ObservationRefReader | None = None
    day_labels: roles_ports.DayLabelPort | None = None
    ground_line: GroundLine | None = None

    _contexts: PinnedContexts = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_contexts",
            PinnedContexts(self.settings.parsed_plan_cache_entries, self.settings.max_document_bytes),
        )

    def recovery(self, view: View, run_id: str) -> PinnedContext | None:
        return self._contexts.load(self.repository, view, run_id)

    def environment(
        self,
        view: View,
        environment_id: str,
        subject: str | None,
        needs: Sequence[DomainSupplyNeed],
        pinned: PinnedContext | None = None,
    ) -> EnvContext:
        # Review item 4: an open run keeps its pinned kit semantics; stock, people, location and the
        # return option stay live.
        env = EnvironmentReader(self.skills, self.roster, self.stock, self.cover).context(
            view, environment_id=environment_id, subject_person_id=subject, needs=needs
        )
        if pinned is None or pinned.kits is None:
            return env
        return replace(env, kit=pinned.kits.get(environment_id))

    def current_mission(self, view: View) -> roles_ports.MissionFacts | None:
        return None if self.mission is None else self.mission.current(view)

    def load(self, view: View, run_id: str) -> PlanRun:
        run = self.repository.load(view, run_id)
        if run is None:
            raise fail(ErrorCode.NOT_FOUND)
        return run

    def plan(self, view: View, run: PlanRun) -> tuple[Plan, bool]:
        version = self.repository.version(view, run.plan_run_id)
        row = None if version is None else self.repository.pinned_plan_json(view, version, run.plan_id)
        backup = self.recovery(view, run.plan_run_id)
        active_doc = self.catalog.document(view.release)
        restricted = backup is None or active_doc is None or active_doc.plan(run.plan_id) is None
        supplies = kit_supplies(view.release) if backup is None else backup.supplies
        if row is not None and version is not None:
            raw, digest = row
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() == digest:
                try:
                    plan = self.catalog.parse_pinned(version, run.plan_id, raw, supplies)
                    if restricted:
                        logging.getLogger(_LOGGER).error("plan_restricted %s", run.plan_run_id)
                    return plan, restricted
                except ValueError, PlansError:
                    pass  # Only corrupt content, never a storage error, becomes restricted.
        logging.getLogger(_LOGGER).error("pinned_plan_unavailable %s", run.plan_run_id)
        if backup is not None and version is not None:
            try:
                return self.catalog.parse_pinned(
                    version, run.plan_id, backup.plan_json, backup.supplies
                ), True
            except ValueError, PlansError:
                pass
        # No guidance or replacement flow is invented when both immutable copies are lost.
        # Close remains available, with configured Unknown labels for unavailable recheck content.
        return Plan(
            run.plan_id,
            "",
            None,
            None,
            "condition",
            "resolve",
            frozenset(),
            (),
            (),
            None,
            (),
            False,
            (),
            "unreviewed",
            None,
            None,
            "",
        ), True

    def action_context(self, view: View, run: PlanRun) -> ActionContext:
        plan, restricted = self.plan(view, run)
        mission = self.current_mission(view)
        runnable: dict[str, bool] = {}
        availability = {}
        backup = self.recovery(view, run.plan_run_id)
        if mission is not None:
            env = self.environment(
                view,
                mission.active_environment_id,
                run.subject_person_id,
                tuple(n for s in all_steps(plan) for n in s.needs.supplies),
                backup,
            )
            gate = StepGate(self.guidance)
            for step in all_steps(plan):
                assessment = assess_step(step, plan, env)
                availability[step.step_id] = assessment.availability
                runnable[step.step_id] = gate.runnable(
                    view,
                    plan=plan,
                    step=step,
                    origin_mode=run.origin_mode,
                    subject_person_id=run.subject_person_id,
                    context=env,
                    release_id=run.opened_release_id,
                )
        return ActionContext(
            plan,
            runnable,
            availability,
            run.opened_by,
            view.now,
            restricted,
            self.settings.max_rechecks_per_step,
            self.settings.max_escalations_per_run,
            backup.month_days if backup is not None else month_days(view.release),
        )

    def wire(self, view: View, run: PlanRun, session: SessionContext | None = None) -> dict[str, object]:
        if self.roster is None:
            raise internal()
        subject = next((p for p in self.roster.crew(view.now) if p.person_id == run.subject_person_id), None)
        if subject is None:
            raise internal()
        context = self.action_context(view, run)
        copy = UiCopy.from_release(view.release)
        if copy is None:
            raise internal()
        backup = self.recovery(view, run.plan_run_id)
        if backup is not None and backup.copy is not None:
            copy = backup.copy
        actions = (
            allowed_actions(run, context)
            if session is None or visible(session, run.subject_person_id)
            else ()
        )
        return run_wire(
            run,
            subject_display_name=subject.display_name,
            allowed_actions=actions,
            due_text_for=lambda recheck: due_text(self, copy, context.plan, recheck),
        )

    def persist(self, uow: UnitOfWork, old: PlanRun | None, new: PlanRun, effects: RunEffects) -> None:
        if old is not None and old.revision == new.revision:
            return
        self.repository.save(uow, new)
        event_type = EventType(effects.event or "plan_run.changed")
        audience = Audience(
            "plans.run_viewers", new.plan_run_id, AudienceProjectionKey("plans", event_type.value, 1), "1"
        )
        # SYSTEM 5.7: register the local record reference atomically for reference-only audit.
        # No export projection exists for clinical plans until the consented handoff seam lands.
        uow.replicate(
            ReplicationChange(
                None,
                "plan_run",
                new.plan_run_id,
                "create" if old is None else "version",
                json.dumps({"plan_run_id": new.plan_run_id, "revision": new.revision}).encode("utf-8"),
                (),
                uow.release.release_id,
                audience,
            )
        )
        uow.audit(
            AuditEntry(
                self.ids.new("aud"),
                event_type.value,
                1,
                uow.actor,
                {"plan_run_id": new.plan_run_id},
                uow.correlation_id,
                None,
                {},
            )
        )
        uow.emit(
            OutboxEvent(
                event_type,
                uow.now,
                EventScope("person", new.subject_person_id, new.environment_id),
                uow.release.release_id,
                uow.correlation_id,
                json.dumps(self.wire(uow, new), sort_keys=True, separators=(",", ":")).encode("utf-8"),
                Audience(
                    "plans.run_viewers",
                    new.plan_run_id,
                    AudienceProjectionKey("plans", event_type.value, 1),
                    "1",
                ),
            )
        )

    def debit(self, uow: UnitOfWork, run: PlanRun, effects: RunEffects) -> PlanRun:
        updated = run
        mission = self.current_mission(uow) if effects.debits else None
        if effects.debits and mission is None:
            raise fail(ErrorCode.STEP_NOT_AVAILABLE)
        for debit in effects.debits:
            if mission is None:
                raise fail(ErrorCode.STEP_NOT_AVAILABLE)
            if self.supply_ledger is None:
                raise internal()
            uses = tuple(SupplyNeed(n.plan_id, n.step_id, n.supply_id, n.per_use) for n in debit.needs)
            ids = tuple(
                self.supply_ledger.record_step_uses(
                    uow,
                    environment_id=mission.active_environment_id,
                    plan_run_id=run.plan_run_id,
                    step_id=debit.step_id,
                    occurrence=debit.occurrence,
                    pinned_release_id=run.opened_release_id,
                    uses=uses,
                    actor=uow.actor,
                    at=uow.now,
                )
            )
            updated = replace(
                updated,
                steps=tuple(
                    replace(s, supply_use_ids=ids) if s.step_id == debit.step_id else s for s in updated.steps
                ),
            )
        return updated


def allowed_actions(run: PlanRun, context: ActionContext) -> tuple[str, ...]:
    if run.state == "closed":
        return ()
    if context.restricted:
        return ("escalate", "close")
    accepted: set[str] = {"close"}
    for step in run.steps:
        proposals = (
            ("step_done", StepDone(step.step_id, (), None, run.revision)),
            ("not_available", NotAvailable(step.step_id, "", run.revision)),
            ("use_substitute", UseSubstitute(step.step_id, run.revision)),
            ("escalate", Escalate(step.step_id, "probe", None, run.revision)),
        )
        for name, command in proposals:
            try:
                apply(run, command, context)
            except PlansError:
                continue
            accepted.add(name)
    return tuple(
        a for a in ("step_done", "not_available", "use_substitute", "escalate", "close") if a in accepted
    )


def due_text(world: PlansWorld, copy: UiCopy, plan: Plan, recheck: RunRecheck) -> str:
    step = plan.step(recheck.step_id)
    if step is None or step.recheck is None:
        unknown = copy.text("designed_states", "unknown")
        if unknown is None:
            raise internal()
        return unknown
    kind, word = due_words(step.recheck, recheck.due_at)
    values: dict[str, str] = {}
    if kind == "pending":
        template = copy.text("plan_lines", "recheck_pending")
        values["to_be_set_by"] = word or ""
    elif kind == "at" and recheck.due_at is not None:
        template = copy.text("plan_lines", "recheck_at")
        values["time"] = (
            world.day_labels.inline_label_at(recheck.due_at)
            if world.day_labels is not None
            else cast(str, instant(recheck.due_at))
        )
    elif word is None:
        # An interval recheck before its first check has no time yet (design 3.4): the designed unknown state.
        template = copy.text("designed_states", "unknown")
    else:
        template = copy.text("cadence_words", word)
        values["n"] = str(step.recheck.n) if step.recheck.n is not None else ""
    if not template:
        raise internal()
    return copy.render(template, values)
