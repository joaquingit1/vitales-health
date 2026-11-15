# mypy: disable-error-code="type-abstract"
# The SYSTEM 6 registry keys ports by their Protocol classes; mypy flags every Protocol passed as type[P].
"""Registration of the camera module, the hub side of the E06 camera worker (coordinator decision A7).

Pass 1 declares the `workers.camera.*` settings (SYSTEM 5.11, DECISIONS A7: an adapter epic registers its own, with
basis and maximum), the ID prefixes, the durable subscriber that binds E05 starts to the worker, the camera's E01
task registrations (M6) as late facades, and the provided ports as late facades. The binding builds the worker link,
the dispatcher, the task handlers and the providers once settings resolve, and starts the probe loop only when the
camera is enabled. Until bound, and whenever a consumed piece is missing (E01's task queue and runner, the E05
attempt report, the mission state), every facade fails closed: readiness unknown, no assignment, reference
unavailable, and E05's manual entry stays the path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from crew_health.camera.adapters.http_transport import configured_transport, pause
from crew_health.camera.adapters.repository import SqliteDispatchStore, SqliteReferenceStore
from crew_health.camera.application.assignments import CameraAssignments
from crew_health.camera.application.dispatch import (
    CANCEL_TASK,
    REPORT_TASK,
    START_TASK,
    TASK_VERSION,
    Dispatcher,
    DispatchLimits,
)
from crew_health.camera.application.link import WorkerLink
from crew_health.camera.application.loop import CameraLoop
from crew_health.camera.application.ports import (
    AdapterAssignment,
    AdapterAssignmentPort,
    AdapterReadinessPort,
    AttemptReports,
    CameraReferencePort,
    MissionStatePort,
    ObservationCommands,
    ReferenceOutcome,
    ReferenceTicket,
    ReferenceWindowRequest,
    View,
    WorkerTransport,
)
from crew_health.camera.application.reference import CameraReference, ReferenceLimits
from crew_health.camera.application.tasks import CameraTasks, Unanswered
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.clock import Clock
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import WorkerLifecycle
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.tasks import TaskLease, TaskQueue, TaskRegistration, TaskResult, TaskRunner
from crew_health.kernel.units import UnitRegistry
from crew_health.kernel.uow import ReadSnapshotFactory, StoredEvent, UnitOfWork, UnitOfWorkFactory

SUBSCRIBER = "camera.dispatch"
_CALL = (
    "SYSTEM 3: every call into a worker has a timeout from the hub settings document; SYSTEM 5.11 "
    "workers.<name>.timeout_ms; ceiling E20 11 (60000 ms)"
)
_HEALTH = (
    "SYSTEM 3 camera worker row: own /health every 5 s; SYSTEM 5.11 workers.<name>.health_ms; ceiling E20 11"
)
_E06 = "E06 hub side bound (DECISIONS A7); finite ceiling so the optional camera never runs an unbounded loop"
# workers.camera.timeout_ms and health_ms are registered here (A7: the adapter epic registers its worker's keys); E20
# reads them and never registers them again. The supervision descriptors (restart_max, restart_window_ms,
# backoff_max_ms, rss_mb, cpus, stop_grace_ms, stable_run_ms, idle_unload_ms) are E20's and are not registered here.
_INTEGERS: tuple[tuple[str, int, int, str], ...] = (
    ("workers.camera.timeout_ms", 1, 60_000, _CALL),
    (
        "workers.camera.health_ms",
        1,
        60_000,
        _HEALTH + "; also the probe, reconciliation and task pass interval",
    ),
    (
        "workers.camera.startup_probe_ms",
        50,
        500,
        _E06
        + ": probe interval from hub start until the worker first answers, so the camera reads ready as soon as"
        " it does instead of one health interval later; the probes stop at the first answer or after stale_ms",
    ),
    ("workers.camera.stale_ms", 1, 600_000, _E06 + ": a probe older than this is unknown readiness"),
    ("workers.camera.dispatch_batch", 1, 64, _E06 + ": tasks run and acquisitions reconciled per pass"),
    (
        "workers.camera.dispatch_deadline_ms",
        1,
        600_000,
        _E06
        + " (M2): a start not sent, or an accepted acquisition not answered, this long past its lifetime is"
        " reported Not measured with its reason",
    ),
    (
        "workers.camera.acquisition_ttl_ms",
        1,
        600_000,
        _E06
        + " (m7): lifetime of one acquisition; ceiling the worker's max_ttl_ms ceiling, and each start uses at"
        " most what the worker's describe accepts",
    ),
    (
        "workers.camera.warmup_max_ms",
        1_000,
        60_000,
        _E06
        + ": longest a camera may take to settle its exposure before a capture counts, sent with every start; floor"
        " the worker's one second settling look back, ceiling the worker protocol's; a team recording of a laptop"
        " camera settled in about 1.3 s (worker camera-quality.json capture.settled_change_per_s)",
    ),
    ("workers.camera.poll_ms", 1, 10_000, _E06 + ": reference outcome poll interval and status call bound"),
    ("workers.camera.max_polls", 1, 1_000, _E06 + ": reference outcome polls per call"),
    ("workers.camera.max_reply_bytes", 1_024, 1_048_576, _E06 + ": largest worker reply read"),
)
_TEXTS: tuple[tuple[str, int, str], ...] = (
    (
        "workers.camera.adapter_id",
        128,
        "DEPLOYMENT 7.3: the adapter ID the camera worker's token is issued for",
    ),
    ("workers.camera.url", 2048, "DEPLOYMENT 7.3: loopback or private service address of the camera worker"),
    ("workers.camera.token_file", 4096, "DEPLOYMENT 7.3: protected file holding the hub to worker token"),
)
_ENABLED = "DEPLOYMENT 7.1: the camera worker runs only where a camera is present"
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("camera_acquisition", "cad"),
    ("camera_reference", "crf"),
    ("camera_task", "ctk"),
)
TASK_NAMES = (START_TASK, CANCEL_TASK, REPORT_TASK)


class Resolver(Protocol):
    """Structural subset of E01 app.registry.Resolver (a feature module never imports the app package)."""

    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class Registry(Protocol):
    """Structural subset of E01 app.registry.ModuleRegistry used by this module."""

    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def durable_subscriber(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...
    def task(self, registration: TaskRegistration) -> None: ...


@dataclass
class Live:
    assignments: CameraAssignments | None = None
    reference: CameraReference | None = None
    dispatcher: Dispatcher | None = None
    tasks: CameraTasks | None = None
    loop: CameraLoop | None = None


class _Task:
    """Late facade of one camera task handler: until bound, a claimed task is unanswered and retried."""

    def __init__(self, live: Live, name: str) -> None:
        self._live, self._name = live, name

    def execute(self, lease: TaskLease) -> TaskResult:
        if self._live.tasks is None:
            raise Unanswered("camera not bound")
        return self._live.tasks.handlers[self._name].execute(lease)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        if self._live.tasks is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "camera_not_bound", retryable=True)
        self._live.tasks.handlers[self._name].apply(uow, lease, result)

    def discard(self, result: TaskResult) -> None:
        return


class _Readiness:
    def __init__(self, live: Live) -> None:
        self._live = live

    def ready_methods(self, view: View) -> frozenset[str] | None:
        bound = self._live.assignments
        return None if bound is None else bound.ready_methods(view)


class _Assignment:
    def __init__(self, live: Live) -> None:
        self._live = live

    def assign(
        self, view: View, *, capability_code: str, methods: tuple[str, ...]
    ) -> AdapterAssignment | None:
        bound = self._live.assignments
        return None if bound is None else bound.assign(view, capability_code=capability_code, methods=methods)


class _Reference:
    def __init__(self, live: Live) -> None:
        self._live = live

    def begin_window(self, request: ReferenceWindowRequest, *, timeout: timedelta) -> ReferenceTicket:
        bound = self._live.reference
        return (
            ReferenceTicket(None, "camera_unavailable")
            if bound is None
            else bound.begin_window(request, timeout=timeout)
        )

    def outcome(self, ticket_id: str, *, wait: timedelta) -> ReferenceOutcome:
        bound = self._live.reference
        if bound is None:
            return ReferenceOutcome("unavailable", None, None, "camera_unavailable", None, None, 0)
        return bound.outcome(ticket_id, wait=wait)

    def abandon(self, ticket_id: str, *, timeout: timedelta) -> None:
        bound = self._live.reference
        if bound is not None:
            bound.abandon(ticket_id, timeout=timeout)


def _subscriber(live: Live) -> Callable[[UnitOfWork, StoredEvent], None]:
    def handle(uow: UnitOfWork, event: StoredEvent) -> None:
        if live.dispatcher is None:  # an unbound subscriber must not advance its cursor
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "camera_not_bound", retryable=True)
        live.dispatcher.on_changed(uow, event)

    return handle


def _bind(live: Live) -> Callable[[Resolver], None]:
    def bind(resolver: Resolver) -> None:
        settings = resolver.require(HubSettings)
        value = settings.integer
        enabled = settings.boolean("workers.camera.enabled")
        transport = resolver.optional(WorkerTransport) or configured_transport(
            settings.text("workers.camera.url") if enabled else "",
            settings.text("workers.camera.token_file") if enabled else "",
            max_reply_bytes=value("workers.camera.max_reply_bytes"),
        )
        clock, ids = resolver.require(Clock), resolver.require(IdFactory)
        uows, snapshots = resolver.require(UnitOfWorkFactory), resolver.require(ReadSnapshotFactory)
        queue, runner = resolver.optional(TaskQueue), resolver.optional(TaskRunner)
        reports, mission = resolver.optional(AttemptReports), resolver.optional(MissionStatePort)
        store, adapter_id = SqliteDispatchStore(), settings.text("workers.camera.adapter_id")
        link = WorkerLink(
            transport,
            clock,
            timeout_ms=value("workers.camera.timeout_ms"),
            stale_ms=value("workers.camera.stale_ms"),
        )
        live.assignments = CameraAssignments(
            link,
            store,
            adapter_id=adapter_id,
            mission=mission,
            usable=queue is not None and runner is not None and reports is not None,
        )
        live.reference = CameraReference(
            link=link,
            clock=clock,
            ids=ids,
            units=resolver.optional(UnitRegistry),
            limits=ReferenceLimits(
                value("workers.camera.timeout_ms"),
                value("workers.camera.poll_ms"),
                value("workers.camera.max_polls"),
                value("workers.camera.warmup_max_ms"),
            ),
            pause=pause,
            uows=uows,
            snapshots=snapshots,
            store=SqliteReferenceStore(),
            observations=resolver.optional(ObservationCommands),
            adapter_id=adapter_id,
        )
        if queue is None or runner is None or reports is None or mission is None:
            return  # nothing can be dispatched: assignment stays closed and the reference port still answers
        limits = DispatchLimits(
            batch=value("workers.camera.dispatch_batch"),
            timeout_ms=value("workers.camera.timeout_ms"),
            acquisition_ttl_ms=value("workers.camera.acquisition_ttl_ms"),
            dispatch_deadline_ms=value("workers.camera.dispatch_deadline_ms"),
            max_per_request=value("workers.camera.dispatch_batch"),
            warmup_max_ms=value("workers.camera.warmup_max_ms"),
        )
        live.dispatcher = Dispatcher(
            store=store, link=link, queue=queue, mission=mission, uows=uows, snapshots=snapshots, ids=ids,
            clock=clock, limits=limits,
        )  # fmt: skip
        live.tasks = CameraTasks(
            store=store, link=link, queue=queue, runner=runner, reports=reports, uows=uows, snapshots=snapshots,
            ids=ids, clock=clock, limits=limits,
        )  # fmt: skip
        startup_ms = value("workers.camera.startup_probe_ms")
        live.loop = CameraLoop(
            link,
            live.dispatcher,
            live.tasks,
            interval_ms=value("workers.camera.health_ms"),
            startup_probe_ms=startup_ms,
            startup_probes=max(1, value("workers.camera.stale_ms") // startup_ms),
        )
        if enabled:
            lifecycle = resolver.optional(WorkerLifecycle)
            if lifecycle is not None:  # the hub stops the loop at shutdown (SYSTEM 5.12)
                lifecycle.started("camera", live.loop.stop_within)
            live.loop.start()

    return bind


def register(registry: Registry, live: Live | None = None) -> None:
    state = live if live is not None else Live()
    for key, low, high, basis in _INTEGERS:
        registry.setting(SettingSpec(key, "integer", None, Decimal(low), Decimal(high), None, basis))
    for key, size, basis in _TEXTS:
        registry.setting(SettingSpec(key, "text", None, None, None, size, basis))
    registry.setting(SettingSpec("workers.camera.enabled", "boolean", None, None, None, None, _ENABLED))
    for record_type, prefix in _PREFIXES:
        registry.id_prefix(record_type=record_type, prefix=prefix)
    registry.durable_subscriber(
        SUBSCRIBER, frozenset({EventType.MEASUREMENT_REQUEST_CHANGED}), _subscriber(state)
    )
    for name in TASK_NAMES:
        registry.task(TaskRegistration(name, TASK_VERSION, _Task(state, name), None, None))
    registry.provide(AdapterReadinessPort, lambda _: _Readiness(state))
    registry.provide(AdapterAssignmentPort, lambda _: _Assignment(state))
    registry.provide(CameraReferencePort, lambda _: _Reference(state))
    registry.bind("camera", _bind(state))
