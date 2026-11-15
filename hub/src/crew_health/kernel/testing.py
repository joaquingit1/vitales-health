"""SYSTEM 10 bounded component fakes. No device, filesystem or production fallback."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from crew_health.contracts_gen.enums import ConfigName, ErrorCode, EventType, Role
from crew_health.kernel.audit import AuditEntry, AuditEnvelope, AuditVerification, audit_hash
from crew_health.kernel.clock import (
    Clock,
    ClockCorrected,
    ClockCorrection,
    ClockSegment,
    Hlc,
    NormalizedTime,
    TimeInput,
    require_utc,
)
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    PendingCommand,
    StoredResult,
    canonical_json,
)
from crew_health.kernel.config import (
    ActivationGuard,
    CandidateRelease,
    ConfigDocumentView,
    Finding,
    ReleaseCheck,
    ReleaseHandle,
    ReleaseRevision,
    TemplateRenderer,
    _FrozenMapping,
)
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import (
    EvaluationInput,
    EvaluatorComputationFailure,
    EvaluatorRegistration,
    PendingEvaluation,
)
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.met import MetLabel, MissionClockVersion
from crew_health.kernel.ownership import OwnedResource, Transfer, TransferReceipt
from crew_health.kernel.private import (
    PrivateDeletionResult,
    PrivateIntent,
    PrivatePublicationRejected,
    PrivateReceipt,
    PrivateReferenceWriter,
    RevocationReceipt,
    SealedBlob,
    SealedPrivateRef,
)
from crew_health.kernel.replication import (
    Delivery,
    ProjectionBody,
    Recipient,
    RevocationControl,
)
from crew_health.kernel.scheduler import (
    ComponentHealth,
    ElapsedDeadline,
    HealthSnapshot,
    ScheduledJob,
)
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.storage import SqlValue, StatementResult, StorageSession
from crew_health.kernel.tasks import (
    JobResultRef,
    TaskInput,
    TaskLease,
    TaskRegistration,
    TaskResult,
    TaskStatus,
)
from crew_health.kernel.units import ConvertedQuantity, PressureContext, Quantity, Unit, WireQuantity
from crew_health.kernel.uow import (
    Actor,
    Audience,
    DomainEvent,
    OutboxEvent,
    ReadSnapshot,
    ReplicationChange,
    ReplicationRef,
    SessionContext,
    StoredEvent,
    UnitOfWork,
)


def _fail(code: ErrorCode) -> ProblemError:
    return ProblemError(code, code.value)


def _bound(size: int, maximum: int) -> None:
    if not 0 <= size <= maximum:
        raise _fail(ErrorCode.PAYLOAD_TOO_LARGE)


class MemoryIds:
    def __init__(self, *, identifiers: Sequence[str]) -> None:
        self._identifiers = tuple(identifiers)
        self._position = 0

    def new(self, prefix: str) -> str:
        if self._position >= len(self._identifiers):
            raise _fail(ErrorCode.INTERNAL_ERROR)
        value = require_id(prefix + "_" + self._identifiers[self._position])
        self._position += 1
        return value

    def derived(self, prefix: str, *parts: str) -> str:
        try:
            digest = hashlib.sha256(canonical_json([prefix, *parts])).hexdigest()
        except (ValueError, UnicodeError) as error:
            raise _fail(ErrorCode.MALFORMED_REQUEST) from error
        return require_id(prefix + "_" + digest[:32])


@dataclass
class MemoryClock:
    at: datetime
    monotonic: int
    epoch: str

    def __post_init__(self) -> None:
        require_utc(self.at)
        require_id(self.epoch)
        if self.monotonic < 0:
            raise _fail(ErrorCode.INVALID_VALUE)

    def now(self) -> datetime:
        require_utc(self.at)
        return self.at

    def monotonic_ns(self) -> int:
        return self.monotonic

    def process_epoch(self) -> str:
        return self.epoch


class MemorySettings:
    def __init__(self, specs: Sequence[SettingSpec], values: Mapping[str, object]) -> None:
        self.specs = {s.key: s for s in specs}
        self.values = dict(values)
        if len(self.specs) != len(specs) or self.specs.keys() != self.values.keys():
            raise _fail(ErrorCode.CONFIG_INVALID)
        for spec in specs:
            value = self.values[spec.key]
            valid = False
            if spec.kind == "integer" and type(value) is int:
                self._range(spec, Decimal(value))
                valid = True
            elif spec.kind == "quantity" and isinstance(value, Quantity) and value.unit == spec.unit:
                self._range(spec, value.value)
                valid = True
            elif spec.kind == "boolean" and type(value) is bool:
                valid = True
            elif spec.kind == "text" and isinstance(value, str) and spec.max_text_bytes is not None:
                valid = len(value.encode()) <= spec.max_text_bytes
            if not valid or not spec.basis:
                raise _fail(ErrorCode.CONFIG_INVALID)

    def _range(self, spec: SettingSpec, value: Decimal) -> None:
        if (
            not value.is_finite()
            or spec.minimum is None
            or spec.maximum is None
            or not spec.minimum <= value <= spec.maximum
        ):
            raise _fail(ErrorCode.CONFIG_INVALID)

    def integer(self, key: str) -> int:
        value = self.values.get(key)
        if type(value) is not int:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return value

    def quantity(self, key: str) -> Quantity:
        value = self.values.get(key)
        if not isinstance(value, Quantity):
            raise _fail(ErrorCode.CONFIG_INVALID)
        return value

    def boolean(self, key: str) -> bool:
        value = self.values.get(key)
        if type(value) is not bool:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return value

    def text(self, key: str) -> str:
        value = self.values.get(key)
        if not isinstance(value, str):
            raise _fail(ErrorCode.CONFIG_INVALID)
        return value


@dataclass(frozen=True)
class MemoryRelease:
    release_id: str
    documents: Mapping[ConfigName, ConfigDocumentView]

    def __post_init__(self) -> None:
        require_id(self.release_id)
        for name, document in self.documents.items():
            if name != document.name or hashlib.sha256(document.raw).hexdigest() != document.version:
                raise _fail(ErrorCode.CONFIG_INVALID)
        object.__setattr__(self, "documents", _FrozenMapping(self.documents))

    def document(self, name: ConfigName) -> ConfigDocumentView:
        if name not in self.documents:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return self.documents[name]

    def version_of(self, name: ConfigName) -> str:
        return self.document(name).version


class MemoryConfig:
    def __init__(self, releases: Sequence[MemoryRelease], *, active_id: str) -> None:
        self.releases = {r.release_id: r for r in releases}
        if active_id not in self.releases or len(self.releases) != len(releases):
            raise _fail(ErrorCode.CONFIG_INVALID)
        self.active_id = active_id

    def active(self) -> ReleaseHandle:
        return self.release(self.active_id)

    def release(self, release_id: str) -> ReleaseHandle:
        if release_id not in self.releases:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return self.releases[release_id]

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        if version is None:
            return self.active().document(name)
        for release in self.releases.values():
            if name in release.documents and release.version_of(name) == version:
                return release.document(name)
        raise _fail(ErrorCode.CONFIG_INVALID)


class MemoryLoaderChecks:
    def __init__(self, *, maximum: int) -> None:
        self.maximum = maximum
        self.checks: dict[str, ReleaseCheck] = {}
        self.guards: dict[str, ActivationGuard] = {}

    def register(self, check: ReleaseCheck) -> None:
        if check.check_id in self.checks:
            raise _fail(ErrorCode.CONFIG_INVALID)
        _bound(len(self.checks) + 1, self.maximum)
        self.checks[check.check_id] = check

    def register_guard(self, guard: ActivationGuard) -> None:
        if guard.check_id in self.guards:
            raise _fail(ErrorCode.CONFIG_INVALID)
        _bound(len(self.guards) + 1, self.maximum)
        self.guards[guard.check_id] = guard


@dataclass(frozen=True)
class MemoryReleaseCheck:
    check_id: str
    evaluate: Callable[[CandidateRelease], tuple[Finding, ...]]

    def check(self, candidate: CandidateRelease) -> tuple[Finding, ...]:
        return self.evaluate(candidate)


@dataclass(frozen=True)
class MemoryActivationGuard:
    check_id: str
    evaluate: Callable[[UnitOfWork, CandidateRelease], tuple[Finding, ...]]

    def check(self, uow: UnitOfWork, candidate: CandidateRelease) -> tuple[Finding, ...]:
        return self.evaluate(uow, candidate)


@dataclass(frozen=True)
class MemoryTemplateRenderer:
    unknown: str
    allowed: Mapping[str, frozenset[str]]

    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str:
        if field_key not in self.allowed:
            raise _fail(ErrorCode.CONFIG_INVALID)
        selected = {key: values.get(key, self.unknown) for key in self.allowed[field_key]}
        try:
            return template.format_map(selected)
        except (KeyError, ValueError) as error:
            raise _fail(ErrorCode.CONFIG_INVALID) from error


@dataclass(frozen=True)
class MemoryBoundConfig:
    """Readers bound to one view: the active release is the view's own. Pinned reads go to `config`, and fail closed
    when the fake was built without one; rendering delegates to `renderer`."""

    view: ReadSnapshot | UnitOfWork
    renderer: TemplateRenderer
    config: MemoryConfig | None = None

    def _pinned(self) -> MemoryConfig:
        if self.config is None:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return self.config

    def active(self) -> ReleaseHandle:
        return self.view.release

    def release(self, release_id: str) -> ReleaseHandle:
        return self._pinned().release(release_id)

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        if version is None:
            return self.view.release.document(name)
        return self._pinned().document(name, version)

    def render(self, field_key: str, template: str, values: Mapping[str, str]) -> str:
        return self.renderer.render(field_key, template, values)


@dataclass(frozen=True)
class MemoryConfigBinding:
    renderer: TemplateRenderer
    config: MemoryConfig | None = None

    def bind(self, view: ReadSnapshot | UnitOfWork) -> MemoryBoundConfig:
        return MemoryBoundConfig(view, self.renderer, self.config)


class MemoryStorage:
    """Injected exact query responses, never an SQL interpreter."""

    def __init__(
        self,
        *,
        settings: MemorySettings,
        responses: Mapping[tuple[str, tuple[SqlValue, ...]], tuple[tuple[SqlValue, ...], ...]],
        results: Mapping[tuple[str, tuple[SqlValue, ...]], StatementResult],
        readonly: bool = False,
    ) -> None:
        self.settings = settings
        self.responses = _FrozenMapping(responses)
        self.results = _FrozenMapping(results)
        self.readonly = readonly

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        if self.readonly:
            raise _fail(ErrorCode.ACTION_NOT_ALLOWED)
        _bound(
            len(sql.encode()) + sum(len(str(p).encode()) for p in parameters),
            self.settings.integer("storage.max_statement_bytes"),
        )
        if (sql, parameters) not in self.results:
            raise _fail(ErrorCode.INVALID_VALUE)
        return self.results[sql, parameters]

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        _bound(limit, self.settings.integer("storage.max_rows"))
        if (sql, parameters) not in self.responses:
            raise _fail(ErrorCode.INVALID_VALUE)
        result = self.responses[sql, parameters]
        _bound(len(result), limit)
        return result


@dataclass
class MemoryState:
    receipts: dict[tuple[str, str], tuple[CommandIdentity, AcceptedReceipt]] = field(default_factory=dict)
    release_revisions: dict[str, ReleaseRevision] = field(default_factory=dict)
    task_attempts: dict[str, int] = field(default_factory=dict)
    private_receipts: dict[str, PrivateReceipt] = field(default_factory=dict)
    events: list[StoredEvent] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)
    replication: list[ReplicationChange] = field(default_factory=list)
    schedules: dict[str, tuple[ScheduledJob, int, bool]] = field(default_factory=dict)
    ledger: dict[tuple[str, str], tuple[CommandIdentity, StoredResult]] = field(default_factory=dict)
    intents: dict[tuple[str, str], PendingCommand] = field(default_factory=dict)
    corrections: list[ClockCorrection] = field(default_factory=list)
    segments: list[ClockSegment] = field(default_factory=list)
    hlc: Hlc | None = None
    tasks: dict[str, tuple[TaskInput, TaskStatus, TaskLease | None, int]] = field(default_factory=dict)
    pending: dict[str, PendingEvaluation] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    transfers: dict[str, TransferReceipt] = field(default_factory=dict)
    owned: dict[tuple[str, str], OwnedResource] = field(default_factory=dict)
    deliveries: list[Delivery] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)
    revocations: dict[str, RevocationControl] = field(default_factory=dict)
    references: dict[str, SealedPrivateRef] = field(default_factory=dict)

    def copy(self) -> MemoryState:
        return replace(
            self,
            receipts=dict(self.receipts),
            release_revisions=dict(self.release_revisions),
            task_attempts=dict(self.task_attempts),
            private_receipts=dict(self.private_receipts),
            events=list(self.events),
            audit=list(self.audit),
            replication=list(self.replication),
            schedules=dict(self.schedules),
            ledger=dict(self.ledger),
            intents=dict(self.intents),
            corrections=list(self.corrections),
            segments=list(self.segments),
            tasks=dict(self.tasks),
            pending=dict(self.pending),
            completed=set(self.completed),
            transfers=dict(self.transfers),
            owned=dict(self.owned),
            deliveries=list(self.deliveries),
            cursors=dict(self.cursors),
            revocations=dict(self.revocations),
            references=dict(self.references),
        )


class MemoryView:
    def __init__(
        self, state: MemoryState, *, connection: StorageSession, release: ReleaseHandle, now: datetime
    ) -> None:
        self.state = state.copy()
        self._connection = connection
        self._release = release
        self._now = now

    @property
    def connection(self) -> StorageSession:
        return self._connection

    @property
    def release(self) -> ReleaseHandle:
        return self._release

    @property
    def now(self) -> datetime:
        return self._now


def _state(view: ReadSnapshot | UnitOfWork) -> MemoryState:
    if not isinstance(view, MemoryView):
        raise _fail(ErrorCode.INVALID_VALUE)
    return view.state


class MemoryUow(MemoryView):
    def __init__(self, factory: MemoryUowFactory, actor: Actor, correlation_id: str) -> None:
        super().__init__(
            factory.state, connection=factory.connection, release=factory.release, now=factory.clock.now()
        )
        self.factory = factory
        self._actor = actor
        self._correlation_id = correlation_id

    @property
    def actor(self) -> Actor:
        return self._actor

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    def publish(self, event: DomainEvent) -> None:
        self.factory.bus.publish(self, event)

    def emit(self, event: OutboxEvent) -> StoredEvent:
        _bound(len(self.state.events) + 1, self.factory.settings.integer("storage.max_rows"))
        stored = StoredEvent(
            len(self.state.events) + 1,
            self.factory.ids.new("event"),
            event.type,
            event.occurred_at,
            self.now,
            event.scope,
            event.config_release_id,
            event.correlation_id,
            event.data_json,
            event.audience,
        )
        self.state.events.append(stored)
        return stored

    def audit(self, entry: AuditEntry) -> None:
        _bound(len(self.state.audit) + 1, self.factory.settings.integer("storage.max_rows"))
        self.state.audit.append(entry)

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        return self.factory.scheduler.replace(self, job, expected_generation=expected_generation)

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        return self.factory.scheduler.cancel(self, key, expected_generation=expected_generation)

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        _bound(len(self.state.replication) + 1, self.factory.settings.integer("storage.max_rows"))
        self.state.replication.append(change)
        return ReplicationRef(self.factory.node_id, self.factory.incarnation, len(self.state.replication))

    @contextmanager
    def savepoint(self, name: str) -> Iterator[None]:
        require_id(name)
        before = self.state.copy()
        buffered = tuple(self.factory.bus.queue)
        successful = False
        try:
            yield
            successful = True
        finally:
            if not successful:
                self.state = before
                self.factory.bus.queue[:] = buffered


class MemoryUowFactory:
    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdFactory,
        release: ReleaseHandle,
        settings: MemorySettings,
        connection: MemoryStorage,
        node_id: str,
        incarnation: str,
    ) -> None:
        self.clock, self.ids, self.release, self.settings = clock, ids, release, settings
        self.connection = connection
        self.node_id, self.incarnation = require_id(node_id), require_id(incarnation)
        self.state = MemoryState()
        self.bus = MemoryEventBus(settings=settings)
        self.scheduler = MemoryScheduler(settings=settings)
        self.writing = False

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        if self.writing:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        self.writing = True
        transaction = MemoryUow(self, actor, correlation_id)
        try:
            yield transaction
            self.state = transaction.state.copy()
        finally:
            self.writing = False

    @contextmanager
    def snapshot(self) -> Iterator[ReadSnapshot]:
        connection = MemoryStorage(
            settings=self.settings, responses=self.connection.responses, results={}, readonly=True
        )
        yield MemoryView(self.state, connection=connection, release=self.release, now=self.clock.now())


class MemoryEventBus:
    def __init__(self, *, settings: MemorySettings) -> None:
        self.settings = settings
        self.handlers: dict[str, tuple[type[DomainEvent], Callable[[UnitOfWork, DomainEvent], None]]] = {}
        self.queue: list[DomainEvent] = []
        self.dispatching = False

    def subscribe[E: DomainEvent](
        self, event_type: type[E], handler: Callable[[UnitOfWork, E], None], *, name: str
    ) -> None:
        if name in self.handlers:
            raise _fail(ErrorCode.CONFIG_INVALID)
        _bound(len(self.handlers) + 1, self.settings.integer("events.max_subscribers"))

        def invoke(uow: UnitOfWork, event: DomainEvent) -> None:
            if isinstance(event, event_type):
                handler(uow, event)

        self.handlers[name] = (event_type, invoke)

    def publish(self, uow: UnitOfWork, event: DomainEvent) -> None:
        maximum = self.settings.integer("event_bus.max_events")
        _bound(len(self.queue) + 1, maximum)
        self.queue.append(event)
        if self.dispatching:
            return
        self.dispatching = True
        try:
            for _ in range(maximum):
                if not self.queue:
                    return
                current = self.queue.pop(0)
                for name in sorted(self.handlers):
                    self.handlers[name][1](uow, current)
            if self.queue:
                raise _fail(ErrorCode.INTERNAL_ERROR)
        finally:
            self.queue.clear()
            self.dispatching = False


class MemoryScheduler:
    def __init__(self, *, settings: MemorySettings) -> None:
        self.settings = settings
        self.handlers: dict[str, Callable[[UnitOfWork, ScheduledJob], None]] = {}

    def replace(self, uow: UnitOfWork, job: ScheduledJob, *, expected_generation: int | None) -> int:
        schedules = _state(uow).schedules
        current = schedules.get(job.key)
        if (current is None and expected_generation is not None) or (
            current is not None and current[1] != expected_generation
        ):
            raise _fail(ErrorCode.STALE_VERSION)
        _bound(len(schedules) + (current is None), self.settings.integer("scheduler.max_jobs"))
        generation = 1 if current is None else current[1] + 1
        schedules[job.key] = (job, generation, False)
        return generation

    def cancel(self, uow: UnitOfWork, key: str, *, expected_generation: int) -> bool:
        current = _state(uow).schedules.get(key)
        if current is None or current[1] != expected_generation:
            raise _fail(ErrorCode.STALE_VERSION)
        if current[2]:
            return False
        _state(uow).schedules[key] = (current[0], current[1], True)
        return True

    def register(self, handler: str, fn: Callable[[UnitOfWork, ScheduledJob], None]) -> None:
        if handler in self.handlers:
            raise _fail(ErrorCode.CONFIG_INVALID)
        _bound(len(self.handlers) + 1, self.settings.integer("registry.max_hooks"))
        self.handlers[handler] = fn


class MemoryCommands:
    def __init__(
        self,
        *,
        ids: IdFactory,
        settings: MemorySettings,
        authorize: Callable[[ReadSnapshot | UnitOfWork, SessionContext, CommandIdentity], bool],
        operations: Mapping[str, bool],
    ) -> None:
        self.ids, self.settings, self.authorization = ids, settings, authorize
        self.operations = _FrozenMapping(operations)

    def _identity(self, view: ReadSnapshot | UnitOfWork, identity: CommandIdentity) -> None:
        state = _state(view)
        key = (identity.actor_ref, identity.key)
        old = state.ledger.get(key)
        pending = state.intents.get(key)
        allocated = state.receipts.get(key)
        if allocated is not None and allocated[0] != identity:
            raise _fail(ErrorCode.DUPLICATE_COMMAND)
        if (old is not None and old[0] != identity) or (pending is not None and pending.identity != identity):
            raise _fail(ErrorCode.DUPLICATE_COMMAND)

    def lookup(self, uow: UnitOfWork, identity: CommandIdentity) -> StoredResult | None:
        self._identity(uow, identity)
        entry = _state(uow).ledger.get((identity.actor_ref, identity.key))
        return entry[1] if entry is not None else None

    def record(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None:
        self._identity(uow, identity)
        key = identity.actor_ref, identity.key
        state = _state(uow)
        old = state.ledger.get(key)
        if old is not None and old[1] != result:
            raise _fail(ErrorCode.DUPLICATE_COMMAND)
        _bound(len(state.ledger) + (old is None), self.settings.integer("commands.cache_entries"))
        if result.safe_body is not None:
            _bound(len(result.safe_body), self.settings.integer("commands.max_bytes"))
        state.ledger[key] = (identity, result)

    def authorize(
        self,
        snapshot: ReadSnapshot | UnitOfWork,
        session: SessionContext,
        identity: CommandIdentity,
        result: StoredResult,
    ) -> None:
        if not self.authorization(snapshot, session, identity):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)

    def accepted(self, uow: UnitOfWork, identity: CommandIdentity) -> AcceptedReceipt:
        self._identity(uow, identity)
        entry = _state(uow).ledger.get((identity.actor_ref, identity.key))
        if entry is not None:
            return entry[1].receipt
        key = identity.actor_ref, identity.key
        allocated = _state(uow).receipts.get(key)
        if allocated is not None:
            return allocated[1]
        _bound(len(_state(uow).receipts) + 1, self.settings.integer("commands.cache_entries"))
        receipt = AcceptedReceipt(
            identity.key, identity.operation, identity.target, self.ids.new("receipt"), uow.now
        )
        _state(uow).receipts[key] = identity, receipt
        return receipt

    def read(
        self, view: ReadSnapshot | UnitOfWork, session: SessionContext, identity: CommandIdentity
    ) -> AcceptedReceipt:
        self._identity(view, identity)
        entry = _state(view).ledger.get((identity.actor_ref, identity.key))
        if entry is None:
            raise _fail(ErrorCode.NOT_FOUND)
        self.authorize(view, session, identity, entry[1])
        return entry[1].receipt

    def requires_ledger(self, operation_id: str) -> bool:
        if operation_id not in self.operations:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return self.operations[operation_id]

    def reserve(
        self, uow: UnitOfWork, identity: CommandIdentity, *, resume_handler: str, input_ref: str
    ) -> PendingCommand:
        self._identity(uow, identity)
        state = _state(uow)
        key = identity.actor_ref, identity.key
        old = state.intents.get(key)
        if old is not None:
            if old.resume_handler != resume_handler or old.input_ref != input_ref:
                raise _fail(ErrorCode.DUPLICATE_COMMAND)
            return old
        _bound(len(state.intents) + 1, self.settings.integer("commands.cache_entries"))
        intent = PendingCommand(self.ids.new("intent"), identity, resume_handler, input_ref)
        state.intents[key] = intent
        return intent

    def pending(self, view: ReadSnapshot | UnitOfWork, identity: CommandIdentity) -> PendingCommand | None:
        self._identity(view, identity)
        return _state(view).intents.get((identity.actor_ref, identity.key))

    def complete(self, uow: UnitOfWork, intent: PendingCommand, result: StoredResult) -> None:
        if self.pending(uow, intent.identity) != intent:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        self.record(uow, intent.identity, result)
        del _state(uow).intents[intent.identity.actor_ref, intent.identity.key]


@dataclass(frozen=True)
class MemoryCommandResumer:
    action: Callable[[SessionContext, PendingCommand], StoredResult | None]

    def resume(self, session: SessionContext, intent: PendingCommand) -> StoredResult | None:
        return self.action(session, intent)


@dataclass(frozen=True)
class MemoryAudienceRule:
    grants: frozenset[tuple[str, str, str, str]]

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return (
            session.session_id,
            audience.rule,
            audience.object_ref,
            audience.policy_version,
        ) in self.grants


class MemorySubscriptions:
    def __init__(self, *, maximum: int) -> None:
        self.maximum = maximum
        self.handlers: dict[str, tuple[frozenset[EventType], Callable[[UnitOfWork, StoredEvent], None]]] = {}

    def register(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None:
        if subscriber in self.handlers:
            raise _fail(ErrorCode.CONFIG_INVALID)
        _bound(len(self.handlers) + 1, self.maximum)
        self.handlers[subscriber] = types, handler


class MemoryClocks:
    def __init__(self, *, settings: MemorySettings, clock: Clock, node_id: str, incarnation: str) -> None:
        self.settings, self.clock = settings, clock
        self.node_id, self.incarnation = node_id, incarnation

    def open(self, uow: UnitOfWork, segment: ClockSegment) -> None:
        state = _state(uow)
        if segment in state.segments:
            return
        if any(
            s.segment_id == segment.segment_id and s.source_clock_id == segment.source_clock_id
            for s in state.segments
        ):
            raise _fail(ErrorCode.STALE_VERSION)
        prior = [s for s in state.segments if s.source_clock_id == segment.source_clock_id]
        if prior and (
            prior[-1].segment_id != segment.predecessor_id
            or prior[-1].first_sequence >= segment.first_sequence
        ):
            raise _fail(ErrorCode.INVALID_VALUE)
        _bound(len(state.segments) + 1, self.settings.integer("storage.max_rows"))
        state.segments.append(segment)

    def for_sequence(
        self, snapshot: ReadSnapshot, *, source_clock_id: str, sequence: int
    ) -> ClockSegment | None:
        matches = [
            s
            for s in _state(snapshot).segments
            if s.source_clock_id == source_clock_id and s.first_sequence <= sequence
        ]
        return max(matches, key=lambda s: s.first_sequence) if matches else None

    def apply(self, uow: UnitOfWork, correction: ClockCorrection) -> ClockCorrected:
        state = _state(uow)
        if state.corrections and correction.version <= state.corrections[-1].version:
            raise _fail(ErrorCode.STALE_VERSION)
        if not any(
            s.source_clock_id == correction.source_clock_id and s.segment_id == correction.segment_id
            for s in state.segments
        ):
            raise _fail(ErrorCode.INVALID_VALUE)
        if correction.valid_until is not None and correction.valid_from >= correction.valid_until:
            raise _fail(ErrorCode.INVALID_VALUE)
        _bound(correction.uncertainty_ms, self.settings.integer("clock.max_uncertainty_ms"))
        _bound(len(state.corrections) + 1, self.settings.integer("storage.max_rows"))
        previous = next(
            (
                c.offset_ms
                for c in reversed(state.corrections)
                if c.source_clock_id == correction.source_clock_id and c.segment_id == correction.segment_id
            ),
            0,
        )
        state.corrections.append(correction)
        event = ClockCorrected(
            previous, correction.offset_ms, correction.recorded_at, correction.version, correction.source
        )
        uow.publish(event)
        return event

    def history(
        self, snapshot: ReadSnapshot, *, after_version: int, limit: int
    ) -> tuple[ClockCorrection, ...]:
        _bound(limit, self.settings.integer("clock.history_page_size"))
        return tuple(c for c in _state(snapshot).corrections if c.version > after_version)[:limit]

    def normalize(self, snapshot: ReadSnapshot, value: TimeInput) -> NormalizedTime:
        unknown = NormalizedTime(value.original_at, None, None, None, "unknown")
        history = [
            c
            for c in _state(snapshot).corrections
            if c.source_clock_id == value.source_clock_id and c.segment_id == value.segment_id
        ]
        if not any(
            s.source_clock_id == value.source_clock_id and s.segment_id == value.segment_id
            for s in _state(snapshot).segments
        ):
            return unknown
        raw = value.original_at
        if value.basis == "hub_corrected":
            captured = next((c for c in history if c.version == value.source_clock_version), None)
            if captured is None:
                return unknown
            raw -= timedelta(milliseconds=captured.offset_ms)
        selected = [
            c for c in history if c.valid_from <= raw and (c.valid_until is None or raw < c.valid_until)
        ]
        if not selected:
            return unknown
        correction = max(selected, key=lambda c: c.version)
        corrected = raw + timedelta(milliseconds=correction.offset_ms)
        state: Literal["known", "unknown", "future", "replay"] = (
            "replay" if value.origin_mode == "REPLAY" else "known"
        )
        if corrected > snapshot.now + timedelta(
            milliseconds=self.settings.integer("clock.future_tolerance_ms")
        ):
            state = "future"
        return NormalizedTime(
            value.original_at, corrected, correction.correction_id, correction.uncertainty_ms, state
        )

    def elapsed(self, snapshot: ReadSnapshot, start: TimeInput, end: TimeInput) -> timedelta | None:
        first, last = self.normalize(snapshot, start), self.normalize(snapshot, end)
        if (
            first.corrected_at is None
            or last.corrected_at is None
            or first.state == "future"
            or last.state == "future"
        ):
            return None
        elapsed = last.corrected_at - first.corrected_at
        return elapsed if elapsed >= timedelta(0) else None

    def tick(self, uow: UnitOfWork) -> Hlc:
        return self._advance(uow, None)

    def observe(self, uow: UnitOfWork, remote: Hlc) -> Hlc:
        if remote.physical_ms > self._milliseconds(uow.now) + self.settings.integer("clock.hlc_future_ms"):
            raise _fail(ErrorCode.INVALID_VALUE)
        return self._advance(uow, remote)

    def _milliseconds(self, at: datetime) -> int:
        from datetime import UTC

        delta = at - datetime(1970, 1, 1, tzinfo=UTC)
        return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000

    def _advance(self, uow: UnitOfWork, remote: Hlc | None) -> Hlc:
        prior = _state(uow).hlc
        p = self._milliseconds(uow.now)
        local = prior.physical_ms if prior is not None else -1
        counter = prior.logical if prior is not None else 0
        r, d = (remote.physical_ms, remote.logical) if remote is not None else (-1, 0)
        m = max(p, local, r)
        logical = (
            max(counter, d) + 1 if m == local == r else counter + 1 if m == local else d + 1 if m == r else 0
        )
        _bound(logical, self.settings.integer("clock.hlc_logical_max"))
        value = Hlc(m, logical, self.node_id, self.incarnation)
        _state(uow).hlc = value
        return value


class MemoryMissionClock:
    def __init__(self, versions: Sequence[MissionClockVersion]) -> None:
        self.versions = tuple(versions)
        if not versions or len({v.clock_version for v in versions}) != len(versions):
            raise _fail(ErrorCode.CONFIG_INVALID)
        for value in versions:
            require_utc(value.epoch)
            require_utc(value.effective_from)

    def version_at(self, at: datetime) -> MissionClockVersion:
        require_utc(at)
        candidates = [
            v
            for v in self.versions
            if v.effective_from <= at and (v.effective_until is None or at < v.effective_until)
        ]
        if not candidates:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return max(candidates, key=lambda v: v.generation)

    def _version(self, at: datetime, version: str | None) -> MissionClockVersion:
        if version is None:
            return self.version_at(at)
        for value in self.versions:
            if value.clock_version == version:
                return value
        raise _fail(ErrorCode.CONFIG_INVALID)

    def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
        require_utc(at)
        selected = self._version(at, version)
        seconds = (at - selected.epoch) // timedelta(seconds=1)
        day, remainder = divmod(seconds, 86400)
        if not 0 <= day <= 999:
            raise _fail(ErrorCode.INVALID_VALUE)
        hour, remainder = divmod(remainder, 3600)
        minute, second = divmod(remainder, 60)
        return MetLabel(
            f"{day:03}/{hour:02}:{minute:02}:{second:02}",
            day + 1,
            selected.day_label_template.format(day=day + 1),
            selected.clock_version,
        )

    def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]:
        value = self.met(at, version=version)
        return value.day_label, value.mission_day

    def mission_day(self, at: datetime, *, version: str | None = None) -> int:
        return self.met(at, version=version).mission_day

    def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]:
        selected = next((v for v in self.versions if v.clock_version == version), None)
        if selected is None:
            raise _fail(ErrorCode.CONFIG_INVALID)
        start = selected.epoch + timedelta(days=mission_day - 1)
        return start, start + timedelta(days=1)


class MemoryUnits:
    def __init__(self, definitions: Mapping[str, tuple[str, Decimal, Decimal]]) -> None:
        self.definitions = _FrozenMapping(definitions)
        for _, scale, offset in definitions.values():
            if not scale.is_finite() or not offset.is_finite() or scale <= 0:
                raise _fail(ErrorCode.CONFIG_INVALID)

    def parse(self, code: str) -> Unit:
        if code not in self.definitions:
            raise _fail(ErrorCode.INVALID_UNIT)
        return Unit(code)

    def commensurable(self, a: Unit, b: Unit) -> bool:
        self.parse(a.code)
        self.parse(b.code)
        return self.definitions[a.code][0] == self.definitions[b.code][0]

    def convert(self, quantity: Quantity, to: Unit) -> Quantity:
        if not self.commensurable(quantity.unit, to):
            raise _fail(ErrorCode.INVALID_UNIT)
        _, scale, offset = self.definitions[quantity.unit.code]
        _, target_scale, target_offset = self.definitions[to.code]
        return Quantity((quantity.value * scale + offset - target_offset) / target_scale, to)


@dataclass(frozen=True)
class MemoryWireConverter:
    convert_value: Callable[
        [WireQuantity, str, ReleaseHandle, Unit, PressureContext | None], ConvertedQuantity
    ]

    def convert(
        self,
        value: WireQuantity,
        *,
        indicator_code: str,
        release: ReleaseHandle,
        to: Unit,
        pressure: PressureContext | None = None,
    ) -> ConvertedQuantity:
        return self.convert_value(value, indicator_code, release, to, pressure)


@dataclass(frozen=True)
class MemoryAuditVerifier:
    envelopes: tuple[AuditEnvelope, ...]

    def verify(self, *, expected_head_seq: int, expected_head_hash: str) -> AuditVerification:
        previous = "0" * 64
        identities: set[str] = set()
        for sequence, envelope in enumerate(self.envelopes, start=1):
            if (
                envelope.audit_seq != sequence
                or envelope.prev_hash != previous
                or envelope.entry.event_id in identities
            ):
                return AuditVerification(False, sequence, sequence - 1, previous)
            previous = audit_hash(envelope)
            identities.add(envelope.entry.event_id)
        count = len(self.envelopes)
        valid = expected_head_seq == count and expected_head_hash == previous
        return AuditVerification(valid, None if valid else count + 1, count, previous)


class MemoryHealth:
    def __init__(self, *, maximum: int) -> None:
        self.maximum = maximum
        self.components: dict[str, ComponentHealth] = {}

    def mark_degraded(self, component: str, code: str, since: datetime) -> None:
        require_utc(since)
        _bound(len(self.components) + (component not in self.components), self.maximum)
        self.components[component] = ComponentHealth(component, code, since)

    def mark_ok(self, component: str) -> None:
        _bound(len(self.components) + (component not in self.components), self.maximum)
        self.components[component] = ComponentHealth(component, None, None)

    def snapshot(self) -> HealthSnapshot:
        return HealthSnapshot(
            "degraded" if any(c.code is not None for c in self.components.values()) else "ok",
            tuple(self.components[k] for k in sorted(self.components)),
        )


class MemoryTasks:
    def __init__(
        self,
        *,
        settings: MemorySettings,
        registrations: Sequence[TaskRegistration],
        authorize: Callable[[ReadSnapshot | UnitOfWork, SessionContext, TaskInput], bool],
        capture: Callable[[datetime], TimeInput],
        elapsed: Callable[[ReadSnapshot, TimeInput, TimeInput], timedelta | None],
    ) -> None:
        self.settings = settings
        self.registrations = {(r.name, r.version): r for r in registrations}
        self.authorization, self.capture, self.elapsed = authorize, capture, elapsed
        if len(self.registrations) != len(registrations):
            raise _fail(ErrorCode.CONFIG_INVALID)

    def submit(self, uow: UnitOfWork, task: TaskInput) -> TaskStatus:
        state = _state(uow)
        previous = state.tasks.get(task.task_id)
        if previous is not None:
            if previous[0] != task:
                raise _fail(ErrorCode.DUPLICATE_COMMAND)
            return previous[1]
        registration = self.registrations.get((task.handler, task.handler_version))
        if registration is None or registration.public_kind != task.public_kind:
            raise _fail(ErrorCode.CONFIG_INVALID)
        _bound(len(state.tasks) + 1, self.settings.integer("tasks.max_pending"))
        _bound(len(task.input_ref.encode()), self.settings.integer("tasks.max_input_bytes"))
        status = TaskStatus(
            task.task_id,
            task.task_id if task.public_kind is not None else None,
            task.public_kind,
            "queued",
            Quantity(Decimal(0), Unit("1")),
            None,
            None,
            uow.now,
            uow.now,
        )
        state.tasks[task.task_id] = task, status, None, 0
        return status

    def _visible(
        self, view: ReadSnapshot | UnitOfWork, session: SessionContext, task_id: str
    ) -> tuple[TaskInput, TaskStatus, TaskLease | None, int]:
        task = _state(view).tasks.get(task_id)
        if task is None:
            raise _fail(ErrorCode.NOT_FOUND)
        if not self.authorization(view, session, task[0]):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        return task

    def cancel(self, uow: UnitOfWork, session: SessionContext, task_id: str) -> TaskStatus:
        task, status, lease, generation = self._visible(uow, session, task_id)
        if status.state in {"succeeded", "failed", "cancelled"}:
            return status
        status = replace(status, state="cancelled", updated_at=uow.now)
        _state(uow).tasks[task_id] = task, status, None, generation + 1
        return status

    def claim(
        self, uow: UnitOfWork, *, worker_id: str, handlers: frozenset[str], limit: int
    ) -> tuple[TaskLease, ...]:
        _bound(limit, self.settings.integer("tasks.claim_batch"))
        selected: list[TaskLease] = []
        for task_id in sorted(_state(uow).tasks):
            if len(selected) == limit:
                break
            task, status, _, generation = _state(uow).tasks[task_id]
            if status.state != "queued" or task.handler not in handlers:
                continue
            attempts = _state(uow).task_attempts.get(task_id, 0)
            if attempts >= self.settings.integer("tasks.max_attempts"):
                continue
            _state(uow).task_attempts[task_id] = attempts + 1
            generation += 1
            lease = TaskLease(
                task,
                generation,
                worker_id,
                ElapsedDeadline(
                    self.capture(uow.now), timedelta(milliseconds=self.settings.integer("tasks.lease_ms"))
                ),
            )
            _state(uow).tasks[task_id] = (
                task,
                replace(status, state="running", updated_at=uow.now),
                lease,
                generation,
            )
            selected.append(lease)
        return tuple(selected)

    def _live(self, view: ReadSnapshot, lease: TaskLease) -> bool:
        task = _state(view).tasks.get(lease.task.task_id)
        interval = self.elapsed(view, lease.expires.anchor, self.capture(view.now))
        return (
            task is not None
            and task[1].state == "running"
            and task[2] == lease
            and interval is not None
            and interval < lease.expires.duration
        )

    def progress(self, uow: UnitOfWork, lease: TaskLease, value: Quantity) -> bool:
        if value.unit != Unit("1") or not Decimal(0) <= value.value <= Decimal(1):
            raise _fail(ErrorCode.INVALID_VALUE)
        if not self._live(uow, lease):
            return False
        task, status, current, generation = _state(uow).tasks[lease.task.task_id]
        _state(uow).tasks[task.task_id] = (
            task,
            replace(status, progress=value, updated_at=uow.now),
            current,
            generation,
        )
        return True

    def complete(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> bool:
        if (
            not self._live(uow, lease)
            or result.task_id != lease.task.task_id
            or result.generation != lease.generation
        ):
            return False
        _bound(len(result.result_ref.encode()), self.settings.integer("tasks.max_result_bytes"))
        task, status, _, generation = _state(uow).tasks[lease.task.task_id]
        registration = self.registrations[task.handler, task.handler_version]
        registration.handler.apply(uow, lease, result)
        status = replace(
            status,
            state="succeeded",
            progress=Quantity(Decimal(1), Unit("1")),
            result_ref=result.result_ref,
            updated_at=uow.now,
        )
        _state(uow).tasks[task.task_id] = task, status, None, generation
        return True

    def fail(self, uow: UnitOfWork, lease: TaskLease, *, code: ErrorCode, retryable: bool) -> bool:
        if not self._live(uow, lease):
            return False
        task, status, _, generation = _state(uow).tasks[lease.task.task_id]
        state: Literal["queued", "failed"] = (
            "queued"
            if retryable
            and _state(uow).task_attempts.get(task.task_id, 0) < self.settings.integer("tasks.max_attempts")
            else "failed"
        )
        _state(uow).tasks[task.task_id] = (
            task,
            replace(status, state=state, error_code=code, updated_at=uow.now),
            None,
            generation,
        )
        return True

    def recover(self, uow: UnitOfWork, *, limit: int) -> int:
        _bound(limit, self.settings.integer("tasks.claim_batch"))
        count = 0
        for task_id in sorted(_state(uow).tasks):
            if count == limit:
                break
            task, status, lease, generation = _state(uow).tasks[task_id]
            if lease is None:
                continue
            elapsed = self.elapsed(uow, lease.expires.anchor, self.capture(uow.now))
            if elapsed is None or elapsed < lease.expires.duration:
                continue
            state: Literal["queued", "failed"] = (
                "queued"
                if _state(uow).task_attempts.get(task.task_id, 0)
                < self.settings.integer("tasks.max_attempts")
                else "failed"
            )
            _state(uow).tasks[task_id] = (
                task,
                replace(status, state=state, updated_at=uow.now),
                None,
                generation + 1,
            )
            count += 1
        return count

    def get(self, snapshot: ReadSnapshot, session: SessionContext, job_id: str) -> TaskStatus:
        task = self._visible(snapshot, session, job_id)
        if task[1].job_id is None:
            raise _fail(ErrorCode.NOT_FOUND)
        return task[1]

    def result(self, snapshot: ReadSnapshot, session: SessionContext, job_id: str) -> JobResultRef | None:
        status = self.get(snapshot, session, job_id)
        if status.result_ref is None:
            return None
        task = _state(snapshot).tasks[job_id][0]
        projector = self.registrations[task.handler, task.handler_version].result_projector
        if projector is None:
            return None
        return projector.project(snapshot, session, status.result_ref)


@dataclass(frozen=True)
class MemoryTaskHandler:
    execute_fn: Callable[[TaskLease], TaskResult]
    apply_fn: Callable[[UnitOfWork, TaskLease, TaskResult], None]
    discard_fn: Callable[[TaskResult], None]

    def execute(self, lease: TaskLease) -> TaskResult:
        return self.execute_fn(lease)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        self.apply_fn(uow, lease, result)

    def discard(self, result: TaskResult) -> None:
        self.discard_fn(result)


@dataclass(frozen=True)
class MemoryJobProjector:
    project_fn: Callable[[ReadSnapshot | UnitOfWork, SessionContext, str], JobResultRef | None]

    def project(
        self, snapshot: ReadSnapshot | UnitOfWork, session: SessionContext, result_ref: str
    ) -> JobResultRef | None:
        return self.project_fn(snapshot, session, result_ref)


@dataclass(frozen=True)
class MemoryEvaluator:
    evaluate_fn: Callable[[UnitOfWork, EvaluationInput], None]

    def evaluate(self, uow: UnitOfWork, value: EvaluationInput) -> None:
        self.evaluate_fn(uow, value)


class MemoryEvaluation:
    def __init__(
        self,
        *,
        settings: MemorySettings,
        registrations: Sequence[EvaluatorRegistration],
        fault_codes: frozenset[str],
        retry_job: Callable[[UnitOfWork, PendingEvaluation], ScheduledJob],
        audit_entry: Callable[[UnitOfWork, PendingEvaluation], AuditEntry],
    ) -> None:
        self.settings = settings
        self.retry_job, self.audit_entry = retry_job, audit_entry
        self.registrations = {(r.name, r.version): r for r in registrations}
        self.fault_codes = fault_codes
        if len(self.registrations) != len(registrations) or any(not r.version for r in registrations):
            raise _fail(ErrorCode.CONFIG_INVALID)

    def run(self, uow: UnitOfWork, value: EvaluationInput) -> Literal["completed", "pending"]:
        state = _state(uow)
        if value.completion_id in state.completed:
            return "completed"
        registration = self.registrations.get((value.evaluator, value.evaluator_version))
        if registration is None:
            raise _fail(ErrorCode.CONFIG_INVALID)
        prior = state.pending.get(value.completion_id)
        if prior is not None and prior.input != value:
            raise _fail(ErrorCode.DUPLICATE_COMMAND)
        try:
            with uow.savepoint("evaluation"):
                registration.evaluator.evaluate(uow, value)
        except EvaluatorComputationFailure as error:
            if error.code not in self.fault_codes:
                raise
            _bound(len(_state(uow).pending) + (prior is None), self.settings.integer("evaluation.page_size"))
            pending = PendingEvaluation(
                value,
                prior.first_failed_at if prior is not None else uow.now,
                prior.attempts + 1 if prior is not None else 1,
                error.code,
            )
            _state(uow).pending[value.completion_id] = pending
            if pending.attempts <= self.settings.integer("evaluation.retry_max"):
                job = self.retry_job(uow, pending)
                previous = _state(uow).schedules.get(job.key)
                uow.schedule(job, expected_generation=previous[1] if previous is not None else None)
            uow.audit(self.audit_entry(uow, pending))
            return "pending"
        _state(uow).pending.pop(value.completion_id, None)
        _state(uow).completed.add(value.completion_id)
        _bound(len(_state(uow).completed), self.settings.integer("storage.max_rows"))
        return "completed"

    def retry(self, uow: UnitOfWork, *, completion_id: str) -> Literal["completed", "pending"]:
        if completion_id in _state(uow).completed:
            return "completed"
        pending = _state(uow).pending.get(completion_id)
        if pending is None:
            raise _fail(ErrorCode.NOT_FOUND)
        return self.run(uow, pending.input)

    def pending(
        self, snapshot: ReadSnapshot, *, after_id: str | None, limit: int
    ) -> tuple[PendingEvaluation, ...]:
        _bound(limit, self.settings.integer("evaluation.page_size"))
        return tuple(
            value
            for key, value in sorted(_state(snapshot).pending.items())
            if after_id is None or key > after_id
        )[:limit]


@dataclass(frozen=True)
class MemoryProjectionProvider:
    export_fn: Callable[[ReadSnapshot | UnitOfWork, ReplicationChange, Recipient], ProjectionBody | None]

    def export(
        self, snapshot: ReadSnapshot | UnitOfWork, change: ReplicationChange, recipient: Recipient
    ) -> ProjectionBody | None:
        return self.export_fn(snapshot, change, recipient)


@dataclass(frozen=True)
class MemoryReplicaApplier:
    apply_fn: Callable[[UnitOfWork, Delivery, Recipient], None]

    def apply(self, uow: UnitOfWork, delivery: Delivery, recipient: Recipient) -> None:
        self.apply_fn(uow, delivery, recipient)


class MemoryOwnership:
    def __init__(
        self,
        *,
        node_id: str,
        incarnation: str,
        settings: MemorySettings,
        validate: Callable[[Transfer, TransferReceipt | None], bool],
    ) -> None:
        self.node_id, self.incarnation, self.settings, self.validate = (
            node_id,
            incarnation,
            settings,
            validate,
        )

    def require(self, uow: UnitOfWork, resource: OwnedResource) -> None:
        current = _state(uow).owned.get((resource.kind, resource.resource_id))
        if (
            current != resource
            or resource.owner_node_id != self.node_id
            or resource.owner_incarnation != self.incarnation
        ):
            raise _fail(ErrorCode.STALE_VERSION)

    def _phase(
        self,
        uow: UnitOfWork,
        transfer: Transfer,
        previous: TransferReceipt | None,
        phase: Literal["prepared", "fenced", "accepted", "completed", "cancelled"],
    ) -> TransferReceipt:
        if not self.validate(transfer, previous) or transfer.new_epoch <= transfer.source.epoch:
            raise _fail(ErrorCode.INVALID_VALUE)
        state = _state(uow)
        current = state.transfers.get(transfer.transfer_id)
        if current is not None and current.phase == phase:
            return current
        _bound(len(state.transfers) + (current is None), self.settings.integer("sync.pending_max"))
        receipt = TransferReceipt(
            transfer.transfer_id,
            phase,
            canonical_json(
                {
                    "transfer_id": transfer.transfer_id,
                    "phase": phase,
                    "snapshot_sha256": transfer.snapshot_sha256,
                }
            ),
        )
        state.transfers[transfer.transfer_id] = receipt
        return receipt

    def prepare(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt:
        if (
            transfer.destination_node_id != self.node_id
            or transfer.destination_incarnation != self.incarnation
        ):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        return self._phase(uow, transfer, None, "prepared")

    def fence(self, uow: UnitOfWork, transfer: Transfer, prepared: TransferReceipt) -> TransferReceipt:
        if prepared.phase != "prepared":
            raise _fail(ErrorCode.INVALID_TRANSITION)
        current = _state(uow).transfers.get(transfer.transfer_id)
        if current is not None and current.phase == "fenced":
            if not self.validate(transfer, prepared):
                raise _fail(ErrorCode.INVALID_VALUE)
            return current
        self.require(uow, transfer.source)
        receipt = self._phase(uow, transfer, prepared, "fenced")
        _state(uow).owned.pop((transfer.source.kind, transfer.source.resource_id))
        return receipt

    def accept(self, uow: UnitOfWork, transfer: Transfer, fenced: TransferReceipt) -> TransferReceipt:
        if (
            fenced.phase != "fenced"
            or transfer.destination_node_id != self.node_id
            or transfer.destination_incarnation != self.incarnation
        ):
            raise _fail(ErrorCode.INVALID_TRANSITION)
        receipt = self._phase(uow, transfer, fenced, "accepted")
        _state(uow).owned[transfer.source.kind, transfer.source.resource_id] = replace(
            transfer.source,
            owner_node_id=self.node_id,
            owner_incarnation=self.incarnation,
            epoch=transfer.new_epoch,
        )
        return receipt

    def complete(self, uow: UnitOfWork, transfer: Transfer, accepted: TransferReceipt) -> TransferReceipt:
        if accepted.phase != "accepted" or transfer.source.owner_node_id != self.node_id:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        receipt = self._phase(uow, transfer, accepted, "completed")
        if _state(uow).owned.get((transfer.source.kind, transfer.source.resource_id)) == transfer.source:
            _state(uow).owned.pop((transfer.source.kind, transfer.source.resource_id))
        return receipt

    def cancel(self, uow: UnitOfWork, transfer: Transfer) -> TransferReceipt:
        prior = _state(uow).transfers.get(transfer.transfer_id)
        if prior is not None and prior.phase not in {"prepared", "cancelled"}:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        return self._phase(uow, transfer, prior, "cancelled")

    def status(self, snapshot: ReadSnapshot, transfer_id: str) -> TransferReceipt:
        value = _state(snapshot).transfers.get(transfer_id)
        if value is None:
            raise _fail(ErrorCode.NOT_FOUND)
        return value


class MemoryReplicaExchange:
    def __init__(
        self,
        *,
        settings: MemorySettings,
        ids: IdFactory,
        materializer: Callable[[UnitOfWork, Recipient, int], tuple[Delivery, ...]],
        validate: Callable[[Delivery, Recipient], bool],
        applier: Callable[[UnitOfWork, Delivery, Recipient], None],
        control: Callable[[Recipient, str, str], RevocationControl],
        verify_control: Callable[[RevocationControl], bool],
    ) -> None:
        self.settings, self.ids = settings, ids
        self.materializer, self.validate, self.applier = materializer, validate, applier
        self.control, self.verify_control = control, verify_control

    def revoke(self, uow: UnitOfWork, recipient: Recipient, *, stream_id: str) -> RevocationControl:
        state = _state(uow)
        prior = next(
            (
                c
                for c in state.revocations.values()
                if c.stream_id == stream_id and c.recipient_id == recipient.recipient_id
            ),
            None,
        )
        if prior is not None:
            return prior
        _bound(len(state.revocations) + 1, self.settings.integer("sync.pending_max"))
        control = self.control(recipient, stream_id, self.ids.new("control"))
        if not self.verify_control(control):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        state.revocations[control.control_id] = control
        return control

    def apply_revocation(self, uow: UnitOfWork, control: RevocationControl) -> str:
        if not self.verify_control(control) or control.new_policy_epoch <= control.old_policy_epoch:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        previous = _state(uow).revocations.get(control.control_id)
        if previous is not None and previous != control:
            raise _fail(ErrorCode.INVALID_VALUE)
        _bound(len(_state(uow).revocations) + (previous is None), self.settings.integer("sync.pending_max"))
        _state(uow).revocations[control.control_id] = control
        _state(uow).deliveries = [d for d in _state(uow).deliveries if d.stream_id != control.stream_id]
        return control.control_id

    def acknowledge_revocation(self, uow: UnitOfWork, *, control_id: str, recipient_id: str) -> None:
        value = _state(uow).revocations.get(control_id)
        if value is None or value.recipient_id != recipient_id:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        _state(uow).completed.add(control_id)

    def materialize(self, uow: UnitOfWork, recipient: Recipient, *, limit: int) -> int:
        _bound(limit, self.settings.integer("sync.page_entries"))
        deliveries = self.materializer(uow, recipient, limit)
        _bound(len(deliveries), limit)
        _bound(len(_state(uow).deliveries) + len(deliveries), self.settings.integer("sync.pending_max"))
        for delivery in deliveries:
            if not self.validate(delivery, recipient):
                raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        _state(uow).deliveries.extend(deliveries)
        return len(deliveries)

    def _fenced(self, view: ReadSnapshot | UnitOfWork, stream_id: str, epoch: int) -> bool:
        return any(
            c.stream_id == stream_id and c.old_policy_epoch >= epoch
            for c in _state(view).revocations.values()
        )

    def page(
        self, snapshot: ReadSnapshot, recipient: Recipient, *, stream_id: str, after: int, limit: int
    ) -> tuple[Delivery, ...]:
        _bound(limit, self.settings.integer("sync.page_entries"))
        if self._fenced(snapshot, stream_id, recipient.policy_epoch):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        return tuple(
            d
            for d in _state(snapshot).deliveries
            if d.stream_id == stream_id
            and d.recipient_id == recipient.recipient_id
            and d.policy_epoch == recipient.policy_epoch
            and d.delivery_seq > after
            and self.validate(d, recipient)
        )[:limit]

    def apply(self, uow: UnitOfWork, recipient: Recipient, delivery: Delivery) -> int:
        state = _state(uow)
        if self._fenced(uow, delivery.stream_id, delivery.policy_epoch) or not self.validate(
            delivery, recipient
        ):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        prior = state.cursors.get(delivery.stream_id, 0)
        duplicate = next(
            (
                d
                for d in state.deliveries
                if d.stream_id == delivery.stream_id and d.delivery_seq == delivery.delivery_seq
            ),
            None,
        )
        if delivery.delivery_seq <= prior:
            if duplicate != delivery:
                raise _fail(ErrorCode.INVALID_VALUE)
            return prior
        if delivery.delivery_seq != prior + 1:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        _bound(len(state.deliveries) + 1, self.settings.integer("sync.pending_max"))
        self.applier(uow, delivery, recipient)
        state.deliveries.append(delivery)
        state.cursors[delivery.stream_id] = delivery.delivery_seq
        return delivery.delivery_seq

    def acknowledge(
        self, uow: UnitOfWork, recipient: Recipient, *, stream_id: str, through: int, envelope_sha256: str
    ) -> None:
        matches = [
            d
            for d in _state(uow).deliveries
            if d.stream_id == stream_id
            and d.delivery_seq == through
            and d.recipient_id == recipient.recipient_id
        ]
        if not matches or matches[0].envelope_sha256 != envelope_sha256:
            raise _fail(ErrorCode.INVALID_VALUE)
        _state(uow).cursors[stream_id] = max(_state(uow).cursors.get(stream_id, 0), through)


class MemoryVault:
    """Opaque token fake, deliberately no cryptography or disk durability claim."""

    def __init__(
        self,
        *,
        ids: IdFactory,
        settings: MemorySettings,
        secrets: Mapping[str, bytes],
        authorize: Callable[[SessionContext, SealedPrivateRef], bool],
    ) -> None:
        self.ids, self.settings, self.secrets, self.authorization = (
            ids,
            settings,
            _FrozenMapping(secrets),
            authorize,
        )
        self.keys: dict[str, tuple[str, str]] = {}
        self.blobs: dict[bytes, tuple[str, bytes, bytes]] = {}
        self.revoked: set[str] = set()
        self.receipts: dict[str, RevocationReceipt] = {}
        self.commands: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {}

    def create_entry_key(self, *, owner_person_id: str, entry_id: str) -> str:
        if owner_person_id not in self.secrets or entry_id in self.revoked:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        _bound(len(self.keys) + 1, self.settings.integer("private.max_versions"))
        key = self.ids.new("key")
        self.keys[key] = owner_person_id, entry_id
        return key

    def revoke(
        self, *, owner_person_id: str, entry_ids: tuple[str, ...], command_key: str
    ) -> RevocationReceipt:
        if owner_person_id not in self.secrets:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        prior = self.commands.get((owner_person_id, command_key))
        if prior is not None:
            if prior[0] != entry_ids:
                raise _fail(ErrorCode.DUPLICATE_COMMAND)
            return self.receipts[prior[1]]
        _bound(len(self.receipts) + 1, self.settings.integer("private.max_versions"))
        for entry in entry_ids:
            if not any(
                owner == owner_person_id and stored_entry == entry
                for owner, stored_entry in self.keys.values()
            ):
                raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        receipt = RevocationReceipt(self.ids.new("revocation"), len(self.receipts) + 1, True, ())
        self.revoked.update(entry_ids)
        self.receipts[receipt.revocation_id] = receipt
        self.commands[owner_person_id, command_key] = entry_ids, receipt.revocation_id
        return receipt

    def revocation(self, revocation_id: str) -> RevocationReceipt:
        if revocation_id not in self.receipts:
            raise _fail(ErrorCode.NOT_FOUND)
        return self.receipts[revocation_id]

    def is_revoked(self, *, entry_id: str) -> bool:
        return entry_id in self.revoked

    def fingerprint(self, *, owner_person_id: str, canonical_identity_and_body: bytes) -> str:
        secret = self.secrets.get(owner_person_id)
        if secret is None:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        _bound(len(canonical_identity_and_body), self.settings.integer("private.max_entry_bytes"))
        return hmac.new(secret, canonical_identity_and_body, hashlib.sha256).hexdigest()

    def encrypt(self, *, key_id: str, plaintext: bytes, associated_data: bytes) -> SealedBlob:
        key = self.keys.get(key_id)
        if key is None or key[1] in self.revoked:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        _bound(len(plaintext), self.settings.integer("private.max_entry_bytes"))
        _bound(len(self.blobs) + 1, self.settings.integer("private.max_versions"))
        token = self.ids.new("blob").encode()
        self.blobs[token] = key_id, plaintext, associated_data
        return SealedBlob(token, token, key_id.encode())

    def decrypt(
        self,
        session: SessionContext,
        *,
        reference: SealedPrivateRef,
        blob: SealedBlob,
        associated_data: bytes,
    ) -> bytes:
        saved = self.blobs.get(blob.ciphertext)
        if not self.authorization(session, reference) or reference.entry_id in self.revoked or saved is None:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        if (
            saved[0] != reference.key_id
            or saved[2] != associated_data
            or self.keys.get(reference.key_id) != (reference.owner_person_id, reference.entry_id)
            or blob.nonce != blob.ciphertext
            or blob.wrapped_dek != reference.key_id.encode()
        ):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        return saved[1]


class MemoryPrivateStore:
    def __init__(self, *, vault: MemoryVault, settings: MemorySettings) -> None:
        self.vault, self.settings = vault, settings
        self.entries: dict[str, tuple[PrivateIntent, SealedPrivateRef, SealedBlob]] = {}
        self.receipts: dict[str, PrivateReceipt] = {}

    def seal(self, intent: PrivateIntent) -> SealedPrivateRef:
        prior = self.entries.get(intent.entry_id)
        if prior is not None:
            if prior[0] != intent:
                raise _fail(ErrorCode.DUPLICATE_COMMAND)
            return prior[1]
        _bound(len(self.entries) + 1, self.settings.integer("private.max_versions"))
        key = self.vault.create_entry_key(owner_person_id=intent.owner_person_id, entry_id=intent.entry_id)
        blob = self.vault.encrypt(
            key_id=key,
            plaintext=intent.plaintext,
            associated_data=canonical_json([intent.owner_person_id, intent.entry_id, intent.version_id]),
        )
        reference = SealedPrivateRef(
            intent.entry_id,
            intent.version_id,
            intent.record_kind,
            intent.owner_person_id,
            intent.identity.key,
            intent.identity.body_fingerprint,
            key,
            hashlib.sha256(blob.ciphertext).hexdigest(),
        )
        self.entries[intent.entry_id] = intent, reference, blob
        return reference

    def verify(self, reference: SealedPrivateRef) -> bool:
        saved = self.entries.get(reference.entry_id)
        return (
            saved is not None
            and saved[1] == reference
            and hashlib.sha256(saved[2].ciphertext).hexdigest() == reference.ciphertext_sha256
            and not self.vault.is_revoked(entry_id=reference.entry_id)
        )

    def acknowledge(self, reference: SealedPrivateRef, receipt: PrivateReceipt) -> None:
        if (
            not self.verify(reference)
            or receipt.entry_id != reference.entry_id
            or receipt.command_key != reference.command_key
        ):
            raise _fail(ErrorCode.INVALID_VALUE)
        previous = self.receipts.get(reference.entry_id)
        if previous is not None and previous != receipt:
            raise _fail(ErrorCode.INVALID_VALUE)
        self.receipts[reference.entry_id] = receipt

    def discard_orphan(self, reference: SealedPrivateRef) -> None:
        if reference.entry_id in self.receipts:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        self.vault.revoke(
            owner_person_id=reference.owner_person_id,
            entry_ids=(reference.entry_id,),
            command_key=reference.command_key,
        )
        self.entries.pop(reference.entry_id, None)

    def pending(self, *, after_entry_id: str | None, limit: int) -> tuple[SealedPrivateRef, ...]:
        _bound(limit, self.settings.integer("private.recovery_batch"))
        return tuple(
            value[1]
            for key, value in sorted(self.entries.items())
            if key not in self.receipts and (after_entry_id is None or key > after_entry_id)
        )[:limit]


class MemoryPrivateWriter:
    def attach(self, uow: UnitOfWork, reference: SealedPrivateRef, intent: PrivateIntent) -> None:
        if reference.owner_person_id != intent.owner_person_id or reference.entry_id != intent.entry_id:
            raise _fail(ErrorCode.IDENTITY_MISMATCH)
        _state(uow).references[reference.entry_id] = reference

    def tombstone(self, uow: UnitOfWork, *, entry_ids: tuple[str, ...], revocation_ref: str) -> None:
        require_id(revocation_ref)
        for entry_id in entry_ids:
            _state(uow).references.pop(entry_id, None)


class MemoryPrivateCoordinator:
    def __init__(
        self,
        *,
        factory: MemoryUowFactory,
        store: MemoryPrivateStore,
        vault: MemoryVault,
        writers: Mapping[str, PrivateReferenceWriter],
        commands: MemoryCommands,
        authorize: Callable[[SessionContext, str], bool],
        identity_bytes: Callable[[PrivateIntent], bytes],
    ) -> None:
        self.factory, self.store, self.vault = factory, store, vault
        self.identity_bytes = identity_bytes
        self.writers, self.commands, self.authorization = _FrozenMapping(writers), commands, authorize
        self.barrier = False

    def _verify_intent(self, session: SessionContext, intent: PrivateIntent) -> None:
        if not self.authorization(session, intent.owner_person_id) or intent.record_kind not in self.writers:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        fingerprint = self.vault.fingerprint(
            owner_person_id=intent.owner_person_id, canonical_identity_and_body=self.identity_bytes(intent)
        )
        if intent.identity.fingerprint_kind != "hmac_sha256" or not hmac.compare_digest(
            fingerprint, intent.identity.body_fingerprint
        ):
            raise _fail(ErrorCode.IDENTITY_MISMATCH)

    @contextmanager
    def backup_barrier(self) -> Iterator[None]:
        if self.barrier or self.factory.writing:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        self.barrier = True
        try:
            yield
        finally:
            self.barrier = False

    def publish(
        self,
        session: SessionContext,
        intent: PrivateIntent,
        reference: SealedPrivateRef,
        apply: Callable[[UnitOfWork], bool],
    ) -> PrivateReceipt:
        self._verify_intent(session, intent)
        if (
            reference.owner_person_id != intent.owner_person_id
            or reference.entry_id != intent.entry_id
            or reference.command_key != intent.identity.key
            or reference.fingerprint != intent.identity.body_fingerprint
        ):
            raise _fail(ErrorCode.IDENTITY_MISMATCH)
        if not self.store.verify(reference):
            raise _fail(ErrorCode.INVALID_VALUE)
        with self.backup_barrier():
            with self.factory.begin(
                actor=session.actor, command=intent.identity, correlation_id=intent.identity.key
            ) as uow:
                prior = self.commands.lookup(uow, intent.identity)
                if prior is None:
                    if not apply(uow):
                        raise PrivatePublicationRejected()
                    self.writers[intent.record_kind].attach(uow, reference, intent)
                    receipt = self.commands.accepted(uow, intent.identity)
                    self.commands.record(
                        uow, intent.identity, StoredResult(receipt, 201, reference.entry_id, "", None)
                    )
                else:
                    self.commands.authorize(uow, session, intent.identity, prior)
                result = _state(uow).private_receipts.get(reference.entry_id)
                if result is None:
                    result = PrivateReceipt(
                        reference.entry_id, intent.identity.key, len(_state(uow).references)
                    )
                    _state(uow).private_receipts[reference.entry_id] = result
            self.store.acknowledge(reference, result)
        return result

    def write(self, session: SessionContext, intent: PrivateIntent) -> PrivateReceipt:
        self._verify_intent(session, intent)
        if self.barrier:
            raise _fail(ErrorCode.INVALID_TRANSITION)
        return self.publish(session, intent, self.store.seal(intent), lambda uow: True)

    def delete(
        self,
        session: SessionContext,
        *,
        identity: CommandIdentity,
        owner_person_id: str,
        entry_ids: tuple[str, ...],
    ) -> PrivateDeletionResult:
        if not self.authorization(session, owner_person_id):
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        with self.backup_barrier():
            revocation = self.vault.revoke(
                owner_person_id=owner_person_id, entry_ids=entry_ids, command_key=identity.key
            )
            with self.factory.begin(
                actor=session.actor, command=identity, correlation_id=identity.key
            ) as uow:
                for entry_id in entry_ids:
                    reference = _state(uow).references.get(entry_id)
                    if reference is not None:
                        self.writers[reference.record_kind].tombstone(
                            uow, entry_ids=(entry_id,), revocation_ref=revocation.revocation_id
                        )
        return PrivateDeletionResult(entry_ids, identity.key, revocation, len(self.factory.state.references))

    def recover(self, *, limit: int) -> int:
        _bound(limit, self.factory.settings.integer("private.recovery_batch"))
        recovered = 0
        for reference in self.store.pending(after_entry_id=None, limit=limit):
            if reference.entry_id in self.factory.state.references:
                self.store.acknowledge(
                    reference,
                    PrivateReceipt(
                        reference.entry_id, reference.command_key, len(self.factory.state.references)
                    ),
                )
            else:
                self.store.discard_orphan(reference)
            recovered += 1
        return recovered


class MemoryReleaseActivation:
    def __init__(
        self,
        *,
        checks: MemoryLoaderChecks,
        candidates: Mapping[str, CandidateRelease],
        initial: Mapping[str, ReleaseRevision],
    ) -> None:
        self.checks, self.candidates, self.revisions = checks, _FrozenMapping(candidates), dict(initial)

    def revision(self, snapshot: ReadSnapshot, release_id: str) -> ReleaseRevision:
        if release_id not in self.revisions:
            raise _fail(ErrorCode.CONFIG_INVALID)
        return _state(snapshot).release_revisions.get(release_id, self.revisions[release_id])

    def activate(
        self, uow: UnitOfWork, session: SessionContext, *, release_id: str, expected_etag: str
    ) -> ReleaseRevision:
        if not session.roles & {Role.OPERATOR, Role.EDITOR}:
            raise _fail(ErrorCode.UNAUTHORIZED_SCOPE)
        current = self.revision(uow, release_id)
        if current.etag != expected_etag:
            raise _fail(ErrorCode.STALE_VERSION)
        candidate = self.candidates.get(release_id)
        if candidate is None:
            raise _fail(ErrorCode.CONFIG_INVALID)
        for check in self.checks.checks.values():
            if check.check(candidate):
                raise _fail(ErrorCode.CONFIG_INVALID)
        for guard in self.checks.guards.values():
            if guard.check(uow, candidate):
                raise _fail(ErrorCode.CONFIG_INVALID)
        revision = ReleaseRevision(release_id, current.revision + 1, '"' + str(current.revision + 1) + '"')
        _state(uow).release_revisions[release_id] = revision
        return revision
