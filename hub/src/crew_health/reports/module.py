"""E09 declarations: settings, audit, id prefixes, the private reference writer, the extraction task, the
owner audience, the three report routes and the composed workflows. Host composition supplies the private
store, vault and reader (E01/E02); until all three exist every report operation answers CAPABILITY_UNAVAILABLE
and nothing is stored, shown or defaulted (AGENTS 1.2, 6 safety)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter

from crew_health.app.registry import StartupPhase
from crew_health.kernel.audit import AuditPolicy
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ConfigReleaseReader
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.private import (
    PrivateKeyVault,
    PrivateRecordCoordinator,
    PrivateReferenceWriter,
    PrivateStore,
)
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.tasks import TaskLease, TaskQueue, TaskRegistration, TaskResult, TaskRunner
from crew_health.kernel.units import Quantity, UnitRegistry
from crew_health.kernel.uow import (
    AudienceProjectionKey,
    AudienceRule,
    LocalProjectionRegistration,
    ReadSnapshotFactory,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.reports.adapters.audience import ReportOwnerAudience
from crew_health.reports.adapters.contained import ContainedParser, ParserBounds, ParserUnavailable
from crew_health.reports.adapters.index import RECORD_KIND, ReportIndex
from crew_health.reports.adapters.lineage import SqliteConfirmationLineage
from crew_health.reports.adapters.parsing import ParseLimits
from crew_health.reports.adapters.policies import ConfigFindingPolicies, ConfigVocabulary
from crew_health.reports.adapters.recorder import DeferredObservationRecorder
from crew_health.reports.adapters.store import PrivateReader, PrivateReportStore, PrivateServices, StoreLimits
from crew_health.reports.adapters.writer import ReportReferenceWriter
from crew_health.reports.api.router import ReportsApi, build_router
from crew_health.reports.application.extraction import ENGINE_VERSION, ExtractionRunner, ParsedFile
from crew_health.reports.application.ports import FormDefinitions, RecordOrigin
from crew_health.reports.application.workflows import (
    EXTRACTION_TASK,
    REPORT_AUDIENCE,
    REPORT_PROJECTION,
    ReportPorts,
    ReportWorkflows,
)
from crew_health.reports.domain.extraction import ReportLimits
from crew_health.reports.domain.media import QuarantineReason


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class Registry(Protocol):
    def audit_policy(self, policy: AuditPolicy) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def local_projection(self, registration: LocalProjectionRegistration) -> None: ...
    def task(self, registration: TaskRegistration) -> None: ...
    def private_records(self, record_kind: str, writer: PrivateReferenceWriter) -> None: ...
    def on_startup(self, phase: StartupPhase, fn: Callable[[], None]) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def router(self, router: APIRouter) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


# Bound source: PLAN P6 asks for page, pixel, text and span bounds with provenance and no arbitrary default.
# Until E20 publishes the validated resource descriptors these are team assumptions, each a hub setting.
_BASIS = "team assumption pending E20 parser resource descriptors (PLAN P6): "
_SETTINGS = (
    ("max_pages", 1, 1000, "pages parsed per report"),
    ("max_pixels", 1, 500000000, "decoded pixels per image"),
    ("max_text_chars", 1, 10000000, "text characters extracted per report"),
    ("max_decoded_bytes", 1, 268435456, "bytes one compressed stream may expand to"),
    ("max_objects", 1, 1000000, "PDF objects or image segments walked"),
    ("parser_timeout_ms", 100, 300000, "contained parser wall time"),
    ("parser_max_output_bytes", 1024, 16777216, "contained parser answer size"),
    ("max_drafts", 1, 500, "drafts per report"),
    ("max_value_chars", 1, 1000, "characters of one textual draft value"),
    ("max_entry_attempts", 1, 64, "entry id attempts after a discarded orphan"),
    ("extraction_batch", 1, 32, "extraction tasks run per pass"),
)
HEALTH_COMPONENT = "reports.extraction"


def setting_specs() -> tuple[SettingSpec, ...]:
    return tuple(
        SettingSpec("reports." + key, "integer", None, Decimal(low), Decimal(high), None, _BASIS + what)
        for key, low, high, what in _SETTINGS
    )


def audit_policies() -> tuple[AuditPolicy, ...]:
    counts = frozenset(range(0, 501))
    return (
        AuditPolicy(
            "reports.confirmed",
            {"findings": counts, "rejected_total": counts, "revision": frozenset(range(1, 10001))},
            frozenset({"report_id"}),
            {"report_id": frozenset({"report"})},
        ),
    )


class _NoopHandler:
    """The extraction is run by ExtractionRunner outside the unit of work; completion has nothing to apply."""

    def execute(self, lease: TaskLease) -> TaskResult:
        raise ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)

    def apply(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> None:
        return

    def discard(self, result: TaskResult) -> None:
        return


@dataclass(slots=True)
class Live:
    runner: ExtractionRunner | None = None
    health: HealthState | None = None
    clock: Clock | None = None


def _parse_with(parser: ContainedParser) -> Callable[[str, bytes, dict[str, dict[str, object]]], ParsedFile]:
    def parse(kind: str, data: bytes, vocabulary: dict[str, dict[str, object]]) -> ParsedFile:
        try:
            out = parser.run("extract", kind, data, vocabulary)
        except ParserUnavailable:
            return ParsedFile(False, "parser_unavailable", (), ())
        return ParsedFile(out.ok, out.reason, out.page_text_lengths, out.proposals)

    return parse


def _inspect_with(parser: ContainedParser) -> Callable[[str, bytes], QuarantineReason | None]:
    def inspect(kind: str, data: bytes) -> QuarantineReason | None:
        try:
            out = parser.run("inspect", kind, data, {})
        except ParserUnavailable:
            return "unverified"  # kept encrypted and never used: an uninspected file is not trusted
        if out.ok:
            return None
        return "over_bounds" if out.reason == "too_large" else "corrupt"

    return inspect


def _private(resolver: Resolver) -> PrivateServices | None:
    coordinator = resolver.optional(PrivateRecordCoordinator)  # type: ignore[type-abstract]
    store = resolver.optional(PrivateStore)  # type: ignore[type-abstract]
    vault = resolver.optional(PrivateKeyVault)  # type: ignore[type-abstract]
    reader = resolver.optional(PrivateReader)  # type: ignore[type-abstract]
    if coordinator is None or store is None or vault is None or reader is None:
        return None
    return PrivateServices(coordinator, store, vault, reader)


def _bind(live: Live, api: ReportsApi) -> Callable[[Resolver], None]:
    def bind(resolver: Resolver) -> None:
        services = _private(resolver)
        routing = resolver.optional(RecordOrigin)  # type: ignore[type-abstract]
        runner = resolver.optional(TaskRunner)  # type: ignore[type-abstract]
        if services is None or routing is None:
            return
        settings = resolver.require(HubSettings)  # type: ignore[type-abstract]
        value = {key: settings.integer("reports." + key) for key, _, _, _ in _SETTINGS}
        ids = resolver.require(IdFactory)  # type: ignore[type-abstract]
        snapshots = resolver.require(ReadSnapshotFactory)  # type: ignore[type-abstract]
        uows = resolver.require(UnitOfWorkFactory)  # type: ignore[type-abstract]
        releases = resolver.require(ConfigReleaseReader)  # type: ignore[type-abstract]
        limits = ReportLimits(value["max_drafts"], value["max_value_chars"])
        store = PrivateReportStore(
            services=services,
            index=ReportIndex(),
            snapshots=snapshots,
            ids=ids,
            limits=StoreLimits(
                value["max_entry_attempts"],
                settings.integer("private.max_versions"),
                settings.integer("private.max_entry_bytes"),
            ),
        )
        bounds = ParserBounds(
            ParseLimits(
                value["max_pages"],
                value["max_pixels"],
                value["max_text_chars"],
                value["max_decoded_bytes"],
                value["max_objects"],
            ),
            value["parser_timeout_ms"],
            value["parser_max_output_bytes"],
            value["max_drafts"],
        )
        forms = resolver.optional(FormDefinitions)  # type: ignore[type-abstract]
        ports = ReportPorts(
            store=store,
            lineage=SqliteConfirmationLineage(),
            recorder=DeferredObservationRecorder(),
            policies=ConfigFindingPolicies(),
            forms=forms if forms is not None else _NoForms(),
            units=resolver.require(UnitRegistry),  # type: ignore[type-abstract]
            tasks=runner if runner is not None else _NoRunner(),
            uow=uows,
            snapshots=snapshots,
            idempotency=resolver.require(IdempotencyStore),  # type: ignore[type-abstract]
            receipts=resolver.require(CommandReceipts),  # type: ignore[type-abstract]
            releases=releases,
            ids=ids,
            clock=resolver.require(Clock),  # type: ignore[type-abstract]
            settings=settings,
            limits=limits,
            inspector=_inspect_with(ContainedParser(bounds)),
            queue=resolver.optional(TaskQueue) if runner is not None else None,  # type: ignore[type-abstract]
            extraction_version=ENGINE_VERSION,
        )
        workflows = ReportWorkflows(ports)
        vocabulary = ConfigVocabulary()
        if runner is not None:
            live.runner = ExtractionRunner(
                workflows=workflows,
                runner=runner,
                uows=uows,
                releases=releases,
                parse=_parse_with(ContainedParser(bounds)),
                vocabulary=vocabulary.of,
                batch=value["extraction_batch"],
            )
        api.workflows = workflows
        api.origin_of = lambda person: routing.record_origin(person)
        api.drain = live.runner.drain if live.runner is not None else None
        live.health = resolver.optional(HealthState)  # type: ignore[type-abstract]
        live.clock = ports.clock

    return bind


class _NoForms:
    def get(self, release: object, form_id: str) -> None:
        return None


def _unavailable() -> ProblemError:
    return ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)


class _NoRunner:
    """No task runner is composed: extraction is unavailable and a report is reviewed and entered by hand."""

    def claim(
        self, uow: UnitOfWork, *, worker_id: str, handlers: frozenset[str], limit: int
    ) -> tuple[TaskLease, ...]:
        raise _unavailable()

    def progress(self, uow: UnitOfWork, lease: TaskLease, value: Quantity) -> bool:
        raise _unavailable()

    def complete(self, uow: UnitOfWork, lease: TaskLease, result: TaskResult) -> bool:
        raise _unavailable()

    def fail(self, uow: UnitOfWork, lease: TaskLease, *, code: ErrorCode, retryable: bool) -> bool:
        raise _unavailable()

    def recover(self, uow: UnitOfWork, *, limit: int) -> int:
        raise _unavailable()


def register(registry: Registry) -> None:
    live = Live()
    api = ReportsApi()
    for policy in audit_policies():
        registry.audit_policy(policy)
    for spec in setting_specs():
        registry.setting(spec)
    for record_type, prefix in (("report", "rpt"), ("report_version", "rvr"), ("extraction_draft", "drf")):
        registry.id_prefix(record_type="report." + record_type, prefix=prefix)
    index = ReportIndex()
    audience = ReportOwnerAudience(index)
    registry.audience_rule(REPORT_AUDIENCE, audience)
    for key, pointer in (
        (REPORT_PROJECTION, "report_id"),
        (AudienceProjectionKey("reports", "extraction", 1), "task_id"),
    ):
        registry.local_projection(
            LocalProjectionRegistration(
                key=key,
                payload_schema_json=(
                    '{"type":"object","properties":{"' + pointer + '":{"type":"string"}}}'
                ).encode(),
                required_field_pointers=("/" + pointer,),
                rule=audience,
            )
        )
    registry.private_records(RECORD_KIND, ReportReferenceWriter())
    registry.task(TaskRegistration(EXTRACTION_TASK, ENGINE_VERSION, _NoopHandler(), None, None))

    def start() -> None:
        """Resume extractions queued before a restart. A refusal degrades the component, never the start."""
        if live.runner is None:
            return
        try:
            live.runner.drain()
        except ProblemError as error:
            if live.health is not None and live.clock is not None:
                live.health.mark_degraded(HEALTH_COMPONENT, error.code.value, live.clock.now())

    registry.on_startup(StartupPhase.STATE_SWEEP, start)
    registry.bind("reports", _bind(live, api))
    registry.router(build_router(api))
