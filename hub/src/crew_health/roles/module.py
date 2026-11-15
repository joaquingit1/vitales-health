# mypy: disable-error-code="type-abstract"
# E01's registry keys providers by Protocol class (registry.provide and Resolver.require take type[P]); mypy rejects a
# Protocol class there, so only this check is off here and only in this file.
"""Registration of the roles module (SYSTEM 4.2 row `roles`); no side effects at import."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol

from fastapi import APIRouter, Request

from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.guidance.application.ports import Fact, OperatorFact, SkillFacts
from crew_health.help.application.ports import BroadcastRole, BroadcastRolesSource
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigReader, ReleaseCheck
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import WorkerLifecycle
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import (
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.roles.adapters.sqlite_repository import SqliteRolesRepository
from crew_health.roles.api.router import RequestAuthenticator, build_router
from crew_health.roles.application.actions import RoleActions, RoleCommand
from crew_health.roles.application.config import ConfigCache, RolesReleaseCheck
from crew_health.roles.application.ports import (
    AssignmentRecord,
    AuthorityDecision,
    ClinicalAuthorityQuery,
    DayLabelPort,
    DoseAuthorityContext,
    DrillReader,
    DrillRef,
    HelpSessionReader,
    LocationPort,
    MissionStatePort,
    OverlayPort,
    PlanRunReader,
    RoleAssignmentReader,
    RoleAssignmentRef,
    RoleQueryPort,
    RosterPort,
    SkillHolderPort,
    SupplyAboardPort,
)
from crew_health.roles.application.proposals import Proposals
from crew_health.roles.application.provided import RolesProvided
from crew_health.roles.application.settings import RolesSettings
from crew_health.roles.application.skills import DrillFinish, DrillStart, Skills
from crew_health.roles.application.world import Deps

_LOGGER = "crew_health.roles"
WORKER_NAME = "roles.recompute"


def context_events() -> frozenset[EventType]:
    """CONTRACTS 18 events whose objects change who may help (E31 design section 3 and 4 event table)."""
    return frozenset(
        {
            EventType.HELP_SESSION_CHANGED,
            EventType.PLAN_RUN_CHANGED,
            EventType.PLAN_RUN_STEP_DONE,
            EventType.OVERLAY_CHANGED,
            EventType.GUIDED_CHECK_CHECKED,
            EventType.SKILL_PRACTICED,
            EventType.CONFIG_RELEASE_ACTIVATED,
            EventType.ENVIRONMENT_ACTIVATED,
        }
    )


# The slice of E01's ModuleRegistry and Resolver (crew_health.app.registry) this module uses; a structural copy
# because a feature module may not import crew_health.app (import contract app-only-assembles).
class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    def router(self, router: APIRouter) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def durable_subscriber(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


def build_deps(resolver: Resolver, configs: ConfigCache) -> Deps:
    """Production wiring: every required port from its provider; optional ones stay absent (fail closed)."""
    return Deps(
        uow=resolver.require(UnitOfWorkFactory),
        snapshots=resolver.require(ReadSnapshotFactory),
        repository=SqliteRolesRepository,
        clock=resolver.require(Clock),
        ids=resolver.require(IdFactory),
        settings=RolesSettings.from_hub(resolver.require(HubSettings)),
        idempotency=resolver.require(IdempotencyStore),
        roster=resolver.require(RosterPort),
        overlays=resolver.require(OverlayPort),
        mission=resolver.require(MissionStatePort),
        day_labels=resolver.require(DayLabelPort),
        help_sessions=resolver.require(HelpSessionReader),
        locations=resolver.optional(LocationPort),
        plan_runs=resolver.optional(PlanRunReader),
        supplies=resolver.optional(SupplyAboardPort),
        config_reader=resolver.optional(ConfigReader),
        configs=configs,
    )


class RolesRuntime:
    """The services the routes, event handlers and worker use once `bind` resolved every port. Until then each
    call fails loudly with a retryable INTERNAL_ERROR; nothing answers from a partial wiring."""

    def __init__(self) -> None:
        self.actions: RoleActions | None = None
        self.skills: Skills | None = None
        self.proposals: Proposals | None = None
        self.authenticator: RequestAuthenticator | None = None
        self.provided: RolesProvided | None = None
        self.wake = threading.Event()

    def _need[T](self, value: T | None) -> T:
        if value is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "roles module is not bound yet", retryable=True)
        return value

    # RoleActionsApi
    def act(self, session: SessionContext, cmd: RoleCommand) -> tuple[int, bytes]:
        return self._need(self.actions).act(session, cmd)

    def help_roles(self, session: SessionContext, help_session_id: str) -> dict[str, object]:
        return self._need(self.actions).help_roles(session, help_session_id)

    def plan_run_roles(
        self, session: SessionContext, plan_run_id: str, offset: int, limit: int
    ) -> dict[str, object]:
        return self._need(self.actions).plan_run_roles(session, plan_run_id, offset, limit)

    # SkillsApi
    def my_skills(self, session: SessionContext) -> dict[str, object]:
        return self._need(self.skills).my_skills(session)

    def readiness(self, session: SessionContext) -> dict[str, object]:
        return self._need(self.skills).readiness(session)

    def start_drill(self, session: SessionContext, cmd: DrillStart) -> tuple[int, bytes]:
        return self._need(self.skills).start_drill(session, cmd)

    def get_drill(self, session: SessionContext, drill_id: str) -> dict[str, object]:
        return self._need(self.skills).get_drill(session, drill_id)

    def complete_drill(self, session: SessionContext, cmd: DrillFinish) -> tuple[int, bytes]:
        return self._need(self.skills).complete_drill(session, cmd)

    def authenticate(self, request: Request, operation_id: str) -> SessionContext:
        return self._need(self.authenticator).session(request, operation_id)

    def on_event(self, uow: UnitOfWork, event: StoredEvent) -> None:
        """Durable subscriber: dispatch one CONTRACTS 18 event to its handler inside the delivering transaction.
        An unreadable body changes nothing (the startup sweep and the next event recompute)."""
        try:
            body = json.loads(event.data_json)
        except ValueError, UnicodeError:
            logging.getLogger(_LOGGER).error(
                "roles: unreadable %s event %s", event.type.value, event.event_id
            )
            return
        if not isinstance(body, dict):
            return
        dispatch_event(self._need(self.proposals), self._need(self.skills), uow, event, body)
        self.wake.set()


def dispatch_event(
    proposals: Proposals, skills: Skills, uow: UnitOfWork, event: StoredEvent, body: Mapping[str, object]
) -> None:
    kind = event.type
    if kind is EventType.HELP_SESSION_CHANGED:
        proposals.on_help_session_changed(uow, body)
    elif kind in (EventType.PLAN_RUN_CHANGED, EventType.PLAN_RUN_STEP_DONE):
        proposals.on_plan_run_changed(uow, body)
    elif kind is EventType.OVERLAY_CHANGED:
        proposals.on_overlay_changed(uow, body)
    elif kind is EventType.GUIDED_CHECK_CHECKED:
        # The GuidedCheck object has no occurred_at: the practice time is when the event says it happened (D3).
        skills.on_guided_check(uow, {**body, "occurred_at": event.occurred_at.isoformat()})
    elif kind is EventType.SKILL_PRACTICED:
        proposals.on_practice(uow)
    elif kind is EventType.CONFIG_RELEASE_ACTIVATED:
        proposals.on_release_activated(uow)
    elif kind is EventType.ENVIRONMENT_ACTIVATED:
        proposals.on_environment_activated(uow)


def run_worker(runtime: RolesRuntime, interval_seconds: float, stop: threading.Event) -> None:
    """The recompute worker of E31 design 5.2: wakes on a handled event or every interval, runs one bounded pass.
    A failed pass is logged with its error class and retried on the next wake; the durable work list loses
    nothing."""
    while not stop.is_set():
        runtime.wake.wait(timeout=interval_seconds)
        runtime.wake.clear()
        if stop.is_set():
            return
        proposals = runtime.proposals
        if proposals is None:
            continue
        try:
            proposals.run_due()
        except Exception as error:  # the loop survives to retry; the error is logged loudly
            logging.getLogger(_LOGGER).error("roles: recompute pass failed: %s", type(error).__name__)


def start_worker(runtime: RolesRuntime, interval_seconds: float, lifecycle: WorkerLifecycle) -> None:
    """Report the recompute worker to the hub's WorkerLifecycle (SYSTEM 5.12), then start it. Its stop sets the stop
    flag, wakes the loop and joins within the given budget; a refused report starts nothing."""
    stop = threading.Event()
    thread = threading.Thread(
        target=run_worker, args=(runtime, interval_seconds, stop), name="roles-recompute", daemon=True
    )

    def halt(timeout_seconds: float) -> bool:
        stop.set()
        runtime.wake.set()
        if thread.ident is None:
            return True
        thread.join(max(0.0, timeout_seconds))
        return not thread.is_alive()

    lifecycle.started(WORKER_NAME, halt)
    thread.start()


class _Provided:
    """Factories need no dependencies; methods delegate only after the roles binding is complete."""

    def __init__(self, runtime: RolesRuntime) -> None:
        self._runtime = runtime

    def _service(self) -> RolesProvided:
        return self._runtime._need(self._runtime.provided)

    def authority(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        *,
        person_id: str,
        subject_person_id: str,
        action: Literal["urgency_disposition", "dose_confirmation", "record_review", "clinician_assessed"],
        dose: DoseAuthorityContext | None,
        at: datetime,
    ) -> AuthorityDecision:
        return self._service().authority(
            snapshot,
            person_id=person_id,
            subject_person_id=subject_person_id,
            action=action,
            dose=dose,
            at=at,
        )

    def plan_run_person_ids(self, plan_run_id: str) -> frozenset[str]:
        return self._service().plan_run_person_ids(plan_run_id)

    def medical_officer_assignments(self, subject_person_id: str) -> Sequence[RoleAssignmentRef]:
        return self._service().medical_officer_assignments(subject_person_id)

    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]:
        return self._service().operators(snapshot, subject_person_id, patient_location_id)

    def holders(self, skill_id: str, at: datetime) -> Sequence[str]:
        return self._service().holders(skill_id, at)

    def running_drill(self, drill_id: str) -> DrillRef | None:
        return self._service().running_drill(drill_id)

    def get(self, role_assignment_id: str) -> AssignmentRecord | None:
        return self._service().get(role_assignment_id)

    def roles_for_session(self, help_session_id: str) -> Sequence[BroadcastRole]:
        return self._service().roles_for_session(help_session_id)


def register(registry: ModuleRegistry) -> None:
    configs = ConfigCache()
    runtime = RolesRuntime()

    def provided(_: Resolver) -> _Provided:
        return _Provided(runtime)

    def bind(resolver: Resolver) -> None:
        deps = build_deps(resolver, configs)
        runtime.actions = RoleActions(deps)
        runtime.skills = Skills(deps)
        runtime.proposals = Proposals(deps)
        runtime.authenticator = resolver.require(RequestAuthenticator)
        runtime.proposals.startup_sweep()  # design 5.2: every open context is recomputed after a restart
        runtime.provided = RolesProvided(deps)
        # Started once every port resolved: the worker never runs against a partial wiring.
        start_worker(runtime, float(deps.settings.retry_base_seconds), resolver.require(WorkerLifecycle))

    for record_type, prefix in (("role_assignment", "rol"), ("drill", "drl"), ("skill_practice", "prc")):
        registry.id_prefix(record_type=record_type, prefix=prefix)
    for spec in RolesSettings.specs():
        registry.setting(spec)
    registry.loader_check(RolesReleaseCheck())
    registry.router(build_router(runtime, runtime, runtime.authenticate))
    registry.provide(ClinicalAuthorityQuery, provided)
    registry.provide(RoleQueryPort, provided)
    registry.provide(SkillFacts, provided)
    registry.provide(SkillHolderPort, provided)
    registry.provide(DrillReader, provided)
    registry.provide(RoleAssignmentReader, provided)
    registry.provide(BroadcastRolesSource, provided)
    registry.bind("roles", bind)
    registry.durable_subscriber("roles.context", context_events(), runtime.on_event)
