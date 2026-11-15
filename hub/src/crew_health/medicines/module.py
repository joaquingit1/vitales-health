# mypy: disable-error-code="type-abstract"
# E01's registry keys providers by Protocol class (registry.provide and Resolver.require take type[P]); mypy rejects a
# Protocol class there, so only this check is off here and only in this file.
"""Registration of the medicines module (SYSTEM 4.2 row `medicines`, 5.12); no side effects at import."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import NoReturn, Protocol, cast

from fastapi import APIRouter, Request

from crew_health.app.registry import StartupPhase
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.clock import Clock, ClockCorrections
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.tasks import TaskQueue, TaskRegistration
from crew_health.kernel.uow import (
    AudienceRule,
    LocalProjectionRegistration,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWorkFactory,
)
from crew_health.medicines.adapters.documents import parse, read_documents, read_pharmacy
from crew_health.medicines.adapters.potency import PharmacyBundleCheck, PotencyWorker
from crew_health.medicines.adapters.runs import SqlitePotencyStore
from crew_health.medicines.adapters.sqlite import SqliteMedicines
from crew_health.medicines.api.router import build_router
from crew_health.medicines.application import assessments, events, scheduling
from crew_health.medicines.application.ports import (
    ConsentQueries,
    MedicationRecordReader,
    PlanMedicines,
    WeightReader,
)
from crew_health.medicines.application.settings import CONFIG_DIR, ENGINE_PYTHON, read_limits, specs
from crew_health.medicines.application.use_cases import Deps, Documents, active_medicine_names
from crew_health.medicines.domain.documents import DocumentsInvalid
from crew_health.roles.application.ports import ClinicalAuthorityQuery
from crew_health.supplies.application.ports import MissionStatePort


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    def router(self, router: APIRouter) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...
    def task(self, registration: TaskRegistration) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def local_projection(self, registration: LocalProjectionRegistration) -> None: ...
    def on_startup(self, phase: StartupPhase, fn: Callable[[], None]) -> None: ...


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RequestAuthenticator(Protocol):
    def session(self, request: Request, operation_id: str) -> SessionContext: ...


def condition_ids(snapshots: ReadSnapshotFactory) -> frozenset[str]:
    """Condition IDs of the active kit map (M-L8 and recordWorkingDiagnosis)."""
    with snapshots.snapshot() as view:
        raw = json.loads(view.release.document(ConfigName.KIT_MAP).raw)
    conditions = cast(list[dict[str, object]], raw.get("conditions") or [])
    return frozenset(str(c["condition_id"]) for c in conditions)


class DocumentsHolder:
    """The documents over the active pharmacy bundle; replaced only by a successful import."""

    def __init__(self) -> None:
        self.current: Documents | None = None
        self.configured = True

    def __call__(self) -> Documents:
        if not self.configured:  # `medicines.config_dir` empty: this hub carries no medicines documents
            raise ProblemError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "no medicines configuration on this hub", retryable=False
            )
        if self.current is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "medicines documents not loaded", retryable=True)
        return self.current


class NoMission:
    """No mission state provider (E23) is composed: no mission is current, so every partitioned command refuses
    with CONTEXT_INAPPLICABLE and start-up schedules no run (fail closed, AGENTS 6), instead of the hub failing to
    start. Day boundaries need the mission's day length, so asking for one fails closed too."""

    def current(self, uow: object = None) -> None:
        return None

    def day_end(self, instant: datetime) -> NoReturn:
        raise ProblemError(
            ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value, retryable=False
        )


def _weights(resolver: Resolver) -> WeightReader | None:
    """The assembly binds records' store as `RecordsWeightReader(store)` (medicines may not import records' port
    module, which imports records' domain); without that binding weight stays absent and per-kilogram rules refuse."""
    return resolver.optional(cast(type[object], WeightReader))  # type: ignore[return-value]


def build_deps(resolver: Resolver, documents: DocumentsHolder) -> Deps:
    settings = resolver.require(HubSettings)
    mission = resolver.optional(MissionStatePort) or NoMission()
    python = settings.text(ENGINE_PYTHON)
    limits = read_limits(settings)
    clock = resolver.require(Clock)
    engine = PotencyWorker(
        Path(python) if python else None,
        timeout_seconds=settings.integer("medicines.assessment_timeout_seconds"),
        memory_limit_bytes=settings.integer("medicines.worker_memory_limit_bytes"),
        memory_sample_ms=settings.integer("medicines.memory_sample_ms"),
    )
    return Deps(
        uow=resolver.require(UnitOfWorkFactory),
        snapshots=resolver.require(ReadSnapshotFactory),
        ids=resolver.require(IdFactory),
        idempotency=resolver.require(IdempotencyStore),
        repository=SqliteMedicines(),
        mission=mission,
        authority=resolver.optional(ClinicalAuthorityQuery),
        documents=documents,
        limits=limits,
        process_epoch=clock.process_epoch(),
        store=SqlitePotencyStore(max_rows=limits.max_rows),
        engine=engine,
        bundle_check=PharmacyBundleCheck(engine),
        tasks=resolver.optional(TaskQueue),
        weights=_weights(resolver),
        clock=clock,
        corrections=resolver.optional(ClockCorrections),
        consent=resolver.optional(ConsentQueries),
        plans=resolver.optional(PlanMedicines),
    )


def start(deps: Deps, documents: DocumentsHolder, config_dir: Path, conditions: frozenset[str]) -> None:
    """ImportPharmacyBundle at start-up from the configured directory; when it is refused the last good stored
    bundle stays, and with none the hub does not start a medicines module (DocumentsInvalid or CONFIG_INVALID).
    Then every unfinished run is marked interrupted and one run is scheduled."""
    treatments, policy = read_documents(config_dir)

    def documents_of(files: Mapping[str, bytes]) -> Documents:
        loaded = parse(treatments, policy, files, conditions)
        return Documents(loaded.treatments, loaded.policy, loaded.formulary, loaded.lots, conditions)

    try:
        documents.current = assessments.import_bundle(
            deps, assessments.STARTUP, read_pharmacy(config_dir / "pharmacy"), documents_of
        )
    except ProblemError, DocumentsInvalid:
        documents.current = assessments.active_documents(deps, documents_of)
        if documents.current is None:
            raise
    assessments.recover(deps, assessments.STARTUP)


class MedicinesRuntime:
    """Routes and provided ports call through this facade; until `bind` ran each call fails loudly (retryable)."""

    def __init__(self) -> None:
        self.deps: Deps | None = None
        self.authenticator: RequestAuthenticator | None = None

    def consent(self) -> ConsentQueries | None:
        return None if self.deps is None else self.deps.consent

    def need(self) -> Deps:
        if self.deps is None:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, "medicines module is not bound yet", retryable=True)
        return self.deps

    def authenticate(self, request: Request, operation_id: str) -> SessionContext:
        if self.authenticator is None:  # E02 absent: nobody is signed in (fail closed)
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return self.authenticator.session(request, operation_id)


class MedicinesProvided:
    """MedicationRecordReader (E32 4.3) for E11: active medicine names only, scope checked."""

    def __init__(self, runtime: MedicinesRuntime) -> None:
        self._runtime = runtime

    def active_medicine_names(self, session: SessionContext, person_id: str) -> tuple[str, ...]:
        return active_medicine_names(self._runtime.need(), session, person_id)


def register(registry: ModuleRegistry) -> None:
    runtime = MedicinesRuntime()
    documents = DocumentsHolder()

    config_dir: list[str] = []

    def bind(resolver: Resolver) -> None:
        runtime.authenticator = resolver.optional(RequestAuthenticator)
        runtime.deps = build_deps(resolver, documents)
        config_dir.append(resolver.require(HubSettings).text(CONFIG_DIR))

    def load() -> None:
        """After the CONFIG phase (SYSTEM 5.12): the kit map's condition IDs come from the active release, which
        the first start only activates in that phase, after every binding resolved. An empty
        `medicines.config_dir` means no medicines on this hub, like an empty `medicines.engine_python`: the module
        composes and every medicines call fails closed with CAPABILITY_UNAVAILABLE (AGENTS 2, contain failures)."""
        deps = runtime.need()
        if not config_dir[-1]:
            documents.configured = False
            return
        start(deps, documents, Path(config_dir[-1]), condition_ids(deps.snapshots))

    for record_type, prefix in (
        ("medicine_inspection", "medi"),
        ("medicine_assay", "medy"),
        ("medication_context", "medc"),
        ("working_diagnosis", "medd"),
        ("dose_proposal", "medp"),
        ("medication_administration", "meda"),
        ("medicine_movement", "medm"),
        ("medicine_assessment", "medr"),
    ):
        registry.id_prefix(record_type=record_type, prefix=prefix)
    for spec in specs():
        registry.setting(spec)
    registry.audience_rule(events.CREW_RULE, events.CrewAudience())
    person = events.PersonAudience(runtime.consent)
    registry.audience_rule(events.PERSON_RULE, person)
    for projection in events.projections(person):
        registry.local_projection(projection)
    registry.task(
        TaskRegistration(
            scheduling.HANDLER,
            scheduling.HANDLER_VERSION,
            assessments.AssessmentHandler(runtime.need),
            None,
            None,
        )
    )
    registry.router(build_router(runtime.need, runtime.authenticate))
    registry.provide(MedicationRecordReader, lambda _: MedicinesProvided(runtime))
    registry.bind("medicines", bind)
    registry.on_startup(StartupPhase.CONFIG, load)
