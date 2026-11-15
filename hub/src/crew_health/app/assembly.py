from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol, cast

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from crew_health.app.errors import to_envelope
from crew_health.app.import_contracts import verify_modules
from crew_health.app.middleware import HubMiddleware, OperationCatalog
from crew_health.app.registry import DeclarationRegistry, ModuleRegistry, Resolver, StartupPhase
from crew_health.clock.application.clock import ClockIdentity, PersistentClocks
from crew_health.clock.module import register as register_clock
from crew_health.config.api.router import ConfigHttpCommands
from crew_health.config.api.router import create_router as config_router
from crew_health.config.application.activation import ReleaseActivationService
from crew_health.config.application.loader import ConfigLoader, LoaderChecks, required_contributors
from crew_health.config.application.ports import ConfigReleaseRepository
from crew_health.config.module import register as register_config
from crew_health.contracts_gen.build import verify_base
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, EventType
from crew_health.contracts_gen.schemas.health_schema import Health
from crew_health.events.adapters.notifier import BoundedNotifier
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.events.application.subscriptions import DurableSubscriptions
from crew_health.events.module import register as register_events
from crew_health.kernel.clock import Clock, ClockCorrected
from crew_health.kernel.commands import (
    CommandIdentity,
    ResultVisibility,
    StoredResult,
    canonical_json,
    command_identity,
)
from crew_health.kernel.config import ActivationGuard, ReleaseCheck
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.private import PrivateRecordCoordinator
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.tasks import JobQueries, TaskQueue, TaskStatus
from crew_health.kernel.uow import Actor, DomainEvent, ReadSnapshot, SessionContext, StoredEvent, UnitOfWork
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.api.jobs import create_router as jobs_router
from crew_health.platform.application.commands import CommandExecutor, OperationPolicy
from crew_health.platform.module import register as register_platform
from crew_health.platform.storage import SQLiteDatabase
from crew_health.records.api.router import create_router as records_router
from crew_health.records.application.commands import RecordsCommands
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.domain.observations import ObservationInput, ObservationRecord
from crew_health.records.module import register as register_records


class HttpSecurity(Protocol):
    def authenticate(self, request: Request) -> SessionContext: ...
    def authorize(self, session: SessionContext, operation_id: str) -> None: ...


class DenySecurity:
    def authenticate(self, request: Request) -> SessionContext:
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)

    def authorize(self, session: SessionContext, operation_id: str) -> None:
        raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


@dataclass(frozen=True, slots=True)
class BuildCatalog:
    root: Path
    inventory: Mapping[str, str]
    registrations: tuple[Callable[[ModuleRegistry], None], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory", MappingProxyType(dict(self.inventory)))

    @classmethod
    def core(cls, root: Path) -> BuildCatalog:
        """Build-time helper for the core static catalog, never an external bundle verifier."""
        registrations = (
            register_clock,
            register_config,
            register_events,
            register_platform,
            register_records,
        )
        inventory = {
            fn.__module__: hashlib.sha256(
                (root / fn.__module__.split(".")[1] / "module.py").read_bytes()
            ).hexdigest()
            for fn in registrations
        }
        return cls(root, inventory, registrations)

    def verify(self, maximum: int) -> None:
        if set(self.inventory) != {fn.__module__ for fn in self.registrations} or len(
            self.registrations
        ) != len(self.inventory):
            raise ValueError("static catalog differs from imports")
        verify_modules(self.root, dict(self.inventory), max_modules=maximum)


@dataclass(frozen=True, slots=True)
class ConfigHttpMetadata:
    basis_summaries: Mapping[ConfigName, str]
    origin: Callable[[ReadSnapshot], Literal["LIVE", "REPLAY", "SIM"]]
    visibility: ResultVisibility


@dataclass(frozen=True, slots=True)
class RecordHttpServices:
    commands: RecordsCommands
    queries: RecordsQueries
    visibility: ResultVisibility


@dataclass(frozen=True, slots=True)
class TaskHttpServices:
    queue: TaskQueue
    queries: JobQueries
    visibility: ResultVisibility


def _identity(request: Request, session: SessionContext, operation: str, target: str) -> CommandIdentity:
    from crew_health.kernel.ids import require_id

    raw: object = getattr(request.state, "canonical_body", b"")
    if not isinstance(raw, bytes):
        raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
    parameters = canonical_json(
        {
            "Config-Release": request.headers.get("Config-Release"),
            "If-Match": request.headers.get("If-Match"),
            "query": list(request.query_params.multi_items()),
        }
    )
    return command_identity(
        key=require_id(request.headers.get("Idempotency-Key", "")),
        actor_ref=session.actor.id,
        operation=operation,
        target=target,
        parameters_json=parameters,
        body_fingerprint=hashlib.sha256(raw).hexdigest(),
        fingerprint_kind="sha256",
    )


def _record_router(runtime: Runtime, services: RecordHttpServices, executor: CommandExecutor) -> APIRouter:
    def record(request: Request, session: SessionContext, value: ObservationInput) -> ObservationRecord:
        identity = _identity(request, session, "recordObservation", "/v1/observations")

        def apply(uow: UnitOfWork) -> StoredResult:
            release = request.headers.get("Config-Release")
            if release is not None and release != uow.release.release_id:
                raise ProblemError(ErrorCode.CONFIG_VERSION_MISMATCH, ErrorCode.CONFIG_VERSION_MISMATCH.value)
            result = services.commands.record_observation(uow, session, value)
            receipt = CommandLedger(runtime.database.ids, settings=runtime.database.settings).accepted(
                uow, identity
            )
            return StoredResult(receipt, 201, result.observation_id, "accepted", None)

        result = executor.execute(session, identity, apply)
        if result.resource_ref is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        with runtime.database.snapshot() as view:
            return services.queries.observation(view, session, result.resource_ref)

    return records_router(
        snapshots=runtime.database, queries=services.queries, authenticate=request_session, record=record
    )


def _job_router(runtime: Runtime, services: TaskHttpServices, executor: CommandExecutor) -> APIRouter:
    def cancel(request: Request, session: SessionContext, job_id: str) -> TaskStatus:
        from crew_health.kernel.ids import require_id

        identity = _identity(request, session, "cancelJob", "/v1/jobs/" + require_id(job_id) + "/cancel")

        def apply(uow: UnitOfWork) -> StoredResult:
            services.queue.cancel(uow, session, job_id)
            receipt = CommandLedger(runtime.database.ids, settings=runtime.database.settings).accepted(
                uow, identity
            )
            return StoredResult(receipt, 200, job_id, "cancelled", None)

        executor.execute(session, identity, apply)
        with runtime.database.snapshot() as view:
            return services.queries.get(view, session, job_id)

    return jobs_router(
        snapshots=runtime.database, queries=services.queries, authenticate=request_session, cancel=cancel
    )


class Runtime:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.serving = False
        self.fault_code: str | None = None
        self.completed_phases: list[StartupPhase] = []
        self.resolver: Resolver | None = None
        self.registry = DeclarationRegistry(
            max_ports=database.settings.integer("registry.max_ports"),
            max_hooks=database.settings.integer("registry.max_hooks"),
        )
        self.policy: OperationPolicy | None = None

    def fail(self, code: str) -> None:
        self.serving, self.fault_code = False, code


def request_session(request: Request) -> SessionContext:
    session: object = getattr(request.state, "session", None)
    if not isinstance(session, SessionContext):
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return session


def _settings(registry: DeclarationRegistry, settings: HubSettings) -> None:
    for value in registry.declarations.get("settings", {}).values():
        spec = cast(SettingSpec, value)
        if spec.kind == "integer":
            number = settings.integer(spec.key)
            if spec.minimum is None or spec.maximum is None or not spec.minimum <= number <= spec.maximum:
                raise ValueError("setting outside registered bound")
        elif spec.kind == "quantity":
            quantity = settings.quantity(spec.key)
            if (
                quantity.unit != spec.unit
                or spec.minimum is None
                or spec.maximum is None
                or not spec.minimum <= quantity.value <= spec.maximum
            ):
                raise ValueError("quantity setting outside registered bound")
        elif spec.kind == "boolean":
            settings.boolean(spec.key)
        else:
            text = settings.text(spec.key)
            if spec.max_text_bytes is None or len(text.encode()) > spec.max_text_bytes:
                raise ValueError("text setting outside registered bound")


def _migrate(database: SQLiteDatabase) -> None:
    root = Path(__file__).parents[1]
    for module in ("platform", "config", "clock", "events", "records"):
        path = root / module / "adapters/migrations" / ("0001_" + module + ".sql")
        raw = path.read_bytes()
        database.migrate_tracked(module + ".0001", hashlib.sha256(raw).hexdigest(), raw.decode())


def _configuration(runtime: Runtime) -> ReleaseActivationService:
    resolver = runtime.resolver
    if resolver is None:
        raise ValueError("unresolved runtime")
    checks = LoaderChecks(maximum=runtime.database.settings.integer("registry.max_hooks"))
    for value in runtime.registry.declarations.get("loader_checks", {}).values():
        checks.register(cast(ReleaseCheck, value))
    for value in runtime.registry.declarations.get("activation_guards", {}).values():
        checks.register_guard(cast(ActivationGuard, value))
    checks.freeze()
    metadata = resolver.require(ConfigHttpMetadata)
    repository = resolver.require(ConfigReleaseRepository)  # type: ignore[type-abstract]
    service = ReleaseActivationService(
        repository,
        ConfigLoader(checks, max_document_bytes=runtime.database.settings.integer("commands.max_bytes")),
        runtime.database.ids,
        origin=metadata.origin,
    )
    with runtime.database.snapshot() as view:
        service.validate_active(view)
    runtime.database.set_release_reader(lambda view: repository.release(view, repository.active_id(view)))
    return service


def _wire_declarations(runtime: Runtime) -> None:
    resolver, db, registry = runtime.resolver, runtime.database, runtime.registry
    if resolver is None:
        raise ValueError("unresolved runtime")
    for name, raw in sorted(
        registry.declarations.get("subscriptions", {}).items(), key=lambda pair: str(pair[0])
    ):
        event, handler = cast(tuple[type[DomainEvent], Callable[[UnitOfWork, DomainEvent], None]], raw)
        db.bus.subscribe(event, handler, name=str(name))
    subscriptions = resolver.require(DurableSubscriptions)
    for name, raw in sorted(
        registry.declarations.get("durable_subscribers", {}).items(), key=lambda pair: str(pair[0])
    ):
        types, subscriber_handler = cast(
            tuple[frozenset[EventType], Callable[[UnitOfWork, StoredEvent], None]], raw
        )
        subscriptions.register(str(name), types, subscriber_handler)
    for name, raw in sorted(
        registry.declarations.get("scheduler_handlers", {}).items(), key=lambda pair: str(pair[0])
    ):
        db.scheduler.register(str(name), cast(Callable[[UnitOfWork, ScheduledJob], None], raw))
    clocks = resolver.require(PersistentClocks)

    def deadline(uow: UnitOfWork, job: ScheduledJob) -> datetime | None:
        from crew_health.kernel.scheduler import AbsoluteDeadline

        if isinstance(job.deadline, AbsoluteDeadline):
            return job.deadline.at
        normalized = clocks.normalize(uow, job.deadline.anchor)
        if (
            normalized.state != "known"
            or normalized.corrected_at is None
            or normalized.uncertainty_ms is None
        ):
            return None
        return (
            normalized.corrected_at
            + job.deadline.duration
            + timedelta(milliseconds=normalized.uncertainty_ms)
        )

    db.scheduler.deadline = deadline

    def corrected(uow: UnitOfWork, event: ClockCorrected) -> None:
        db.scheduler.recalculate_all(uow)

    db.bus.subscribe(ClockCorrected, corrected, name="platform.scheduler.clock_correction")


def _startup(
    runtime: Runtime,
    build: BuildCatalog,
    configure: Callable[[ModuleRegistry], None],
    validator: EventValidator,
    catalog: OperationCatalog,
) -> ReleaseActivationService:
    db, registry = runtime.database, runtime.registry
    build.verify(db.settings.integer("registry.max_modules"))
    registry.provide(SQLiteDatabase, lambda resolver: db)
    registry.provide(HubSettings, lambda resolver: db.settings)  # type: ignore[type-abstract]
    registry.provide(IdFactory, lambda resolver: db.ids)  # type: ignore[type-abstract]
    registry.provide(Clock, lambda resolver: db.clock)  # type: ignore[type-abstract]
    registry.provide(ClockIdentity, lambda resolver: ClockIdentity(db.origin_node_id, db.origin_incarnation))
    registry.provide(EventValidator, lambda resolver: validator)
    for register in sorted(build.registrations, key=lambda fn: fn.__module__):
        register(registry)
    configure(registry)
    runtime.resolver = registry.resolve()
    registry.freeze(required_loader_checks=required_contributors())
    _settings(registry, db.settings)
    resolver = runtime.resolver
    sink = resolver.require(EventRepository)
    db.event_sink = sink.append
    db.add_postcommit(resolver.require(BoundedNotifier).notify)
    _wire_declarations(runtime)
    runtime.policy = OperationPolicy(
        {operation.operation_id: operation.ledger for operation in catalog.operations if operation.mutation}
    )
    service: ReleaseActivationService | None = None
    for phase in StartupPhase:
        started = db.clock.monotonic_ns()
        if phase == StartupPhase.STORAGE:
            _migrate(db)
            private = resolver.optional(PrivateRecordCoordinator)  # type: ignore[type-abstract]
            if private is not None:
                private.recover(limit=db.settings.integer("private.recovery_batch"))
        elif phase == StartupPhase.CONFIG:
            service = _configuration(runtime)
        elif phase == StartupPhase.CLOCK:
            clocks = resolver.require(PersistentClocks)
            with db.begin(actor=Actor("engine", "engine_clock"), correlation_id=db.ids.new("startup")) as uow:
                clocks.check_regression(uow)
                clocks.tick(uow)
        for key, callback in sorted(
            registry.declarations.get("startup", {}).items(), key=lambda item: str(item[0])
        ):
            if cast(tuple[StartupPhase, str, str], key)[0] == phase:
                cast(Callable[[], None], callback)()
        if db.clock.monotonic_ns() - started > db.settings.integer("registry.startup_timeout_ms") * 1000000:
            raise ValueError("startup phase exceeded bound")
        runtime.completed_phases.append(phase)
    if service is None:
        raise ValueError("configuration validation absent")
    runtime.serving, runtime.fault_code = True, None
    return service


def _routers(
    app: FastAPI, runtime: Runtime, service: ReleaseActivationService, security: HttpSecurity
) -> None:
    resolver = runtime.resolver
    if resolver is None:
        raise ValueError("unresolved runtime")
    metadata = resolver.require(ConfigHttpMetadata)
    repository = resolver.require(ConfigReleaseRepository)  # type: ignore[type-abstract]
    executor = CommandExecutor(
        transactions=runtime.database,
        ledger=resolver.require(CommandLedger),
        visibility=metadata.visibility,
        authorize=security.authorize,
    )
    app.include_router(
        config_router(
            snapshots=runtime.database,
            repository=repository,
            authenticate=request_session,
            commands=ConfigHttpCommands(
                execute=executor, service=service, repository=repository, ids=runtime.database.ids
            ),
            basis_summaries=metadata.basis_summaries,
            model_release_id=lambda: None,
        )
    )
    records = resolver.optional(RecordHttpServices)
    if records is not None:
        record_executor = CommandExecutor(
            transactions=runtime.database,
            ledger=resolver.require(CommandLedger),
            visibility=records.visibility,
            authorize=security.authorize,
        )
        app.include_router(_record_router(runtime, records, record_executor))
    tasks = resolver.optional(TaskHttpServices)
    if tasks is not None:
        task_executor = CommandExecutor(
            transactions=runtime.database,
            ledger=resolver.require(CommandLedger),
            visibility=tasks.visibility,
            authorize=security.authorize,
        )
        app.include_router(_job_router(runtime, tasks, task_executor))
    for router in runtime.registry.declarations.get("routers", {}).values():
        app.include_router(cast(APIRouter, router))


def _mounted_operations(app: FastAPI) -> set[str]:
    schema: object = app.openapi()
    if not isinstance(schema, dict) or not isinstance(schema.get("paths"), dict):
        raise ValueError("invalid assembled API")
    result: set[str] = set()
    for path in schema["paths"].values():
        if not isinstance(path, dict):
            raise ValueError("invalid assembled path")
        for operation in path.values():
            if isinstance(operation, dict) and isinstance(operation.get("operationId"), str):
                result.add(operation["operationId"])
    return result


def _correlation(request: Request, ids: IdFactory) -> str:
    value: object = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else ids.new("correlation")


def _noop(registry: ModuleRegistry) -> None:
    pass


def create_app(
    *,
    database: SQLiteDatabase,
    build: BuildCatalog,
    contract_path: Path,
    schema_directory: Path,
    security: HttpSecurity | None = None,
    configure: Callable[[ModuleRegistry], None] = _noop,
    hub_version: str = "0.1.0",
) -> FastAPI:
    runtime = Runtime(database)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    access = security or DenySecurity()
    catalog: OperationCatalog | None = None
    try:
        verify_base(contract_path.parents[2])
        if schema_directory.resolve() != (contract_path.parent / "schemas").resolve():
            raise ValueError("schema location differs from guarded contract")
        catalog = OperationCatalog.load(
            contract_path, max_operations=database.settings.integer("registry.max_hooks")
        )
        validator = EventValidator(schema_directory=schema_directory)
        service = _startup(runtime, build, configure, validator, catalog)
        _routers(app, runtime, service, access)
    except Exception as error:
        runtime.fail(error.code.value if isinstance(error, ProblemError) else "INTERNAL_ERROR")

    @app.get("/v1/health", operation_id="getHealth")
    def health(request: Request) -> Response:
        release_id = None
        try:
            with database.snapshot() as view:
                release_id = view.release.release_id
        except ProblemError:
            runtime.fail("INTERNAL_ERROR")
        body = Health.model_validate(
            {
                "status": "ok" if runtime.serving else "degraded",
                "hub_version": hub_version,
                "config_release_id": release_id,
                "assistant_state": "unavailable",
                "adapters": [],
                "server_time": database.clock.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            }
        )
        return Response(
            body.model_dump_json(),
            media_type="application/json",
            headers={
                "X-Correlation-Id": _correlation(request, database.ids),
                **({} if release_id is None else {"Config-Release": release_id}),
            },
        )

    unavailable_operations = {
        "getConfigIndex",
        "getConfigDocument",
        "listConfigReleases",
        "createConfigRelease",
        "getConfigRelease",
        "activateConfigRelease",
        "getEvents",
        "recordObservation",
        "getObservation",
        "getJob",
        "cancelJob",
    }

    async def unavailable(request: Request) -> Response:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)

    mounted = _mounted_operations(app)
    for operation in () if catalog is None else catalog.operations:
        if operation.operation_id in unavailable_operations and operation.operation_id not in mounted:
            app.add_api_route(
                operation.path, unavailable, methods=[operation.method], operation_id=operation.operation_id
            )

    app.openapi_schema = None

    @app.exception_handler(ProblemError)
    async def problem(request: Request, error: ProblemError) -> JSONResponse:
        return JSONResponse(
            to_envelope(error, _correlation(request, database.ids)).model_dump(mode="json"),
            status_code=error.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, error: RequestValidationError) -> JSONResponse:
        fault = ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        return JSONResponse(
            to_envelope(fault, _correlation(request, database.ids)).model_dump(mode="json"),
            status_code=fault.status,
        )

    if catalog is not None:
        app.add_middleware(
            HubMiddleware,
            catalog=catalog,
            settings=database.settings,
            ids=database.ids,
            authenticate=access.authenticate,
            authorize=access.authorize,
            allow_mutations=lambda: runtime.serving,
            allow_request=lambda operation: runtime.serving or operation.operation_id == "getHealth",
        )
    return app
