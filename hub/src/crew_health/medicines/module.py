# mypy: disable-error-code="type-abstract"
# E01's registry keys providers by Protocol class (registry.provide and Resolver.require take type[P]); mypy rejects a
# Protocol class there, so only this check is off here and only in this file.
"""Registration of the medicines module (SYSTEM 4.2 row `medicines`, 5.12); no side effects at import."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from fastapi import APIRouter, Request

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.uow import ReadSnapshotFactory, SessionContext, UnitOfWorkFactory
from crew_health.medicines.adapters.documents import load
from crew_health.medicines.adapters.sqlite import SqliteMedicines
from crew_health.medicines.api.router import build_router
from crew_health.medicines.application.ports import ActingOfficerPort, MedicationRecordReader
from crew_health.medicines.application.settings import CONFIG_DIR, read_limits, specs
from crew_health.medicines.application.use_cases import Deps, Documents, active_medicine_names
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


# Provided by E02 (crew_health.identity); structural copy until it is on main.
class RequestAuthenticator(Protocol):
    def session(self, request: Request, operation_id: str) -> SessionContext: ...


def condition_ids(snapshots: ReadSnapshotFactory) -> frozenset[str]:
    """Condition IDs of the active kit map (M-L8 and recordWorkingDiagnosis)."""
    with snapshots.snapshot() as view:
        raw = json.loads(view.release.document(ConfigName.KIT_MAP).raw)
    conditions = cast(list[dict[str, object]], raw.get("conditions") or [])
    return frozenset(str(c["condition_id"]) for c in conditions)


def build_deps(resolver: Resolver) -> Deps:
    """Fails loudly (DocumentsInvalid) on any invalid medicines document: the hub does not start a medicines module
    on configuration it could not check."""
    settings = resolver.require(HubSettings)
    snapshots = resolver.require(ReadSnapshotFactory)
    conditions = condition_ids(snapshots)
    loaded = load(Path(settings.text(CONFIG_DIR)), conditions)
    documents = Documents(loaded.treatments, loaded.policy, loaded.formulary, loaded.lots, conditions)
    mission = resolver.optional(MissionStatePort)
    if mission is None:
        raise ProblemError(
            ErrorCode.INTERNAL_ERROR, "medicines needs the mission state port", retryable=False
        )
    return Deps(
        uow=resolver.require(UnitOfWorkFactory),
        snapshots=snapshots,
        ids=resolver.require(IdFactory),
        idempotency=resolver.require(IdempotencyStore),
        repository=SqliteMedicines(),
        mission=mission,
        acting=resolver.optional(ActingOfficerPort),
        documents=lambda: documents,
        limits=read_limits(settings),
        process_epoch=resolver.require(Clock).process_epoch(),
    )


class MedicinesRuntime:
    """Routes and provided ports call through this facade; until `bind` ran each call fails loudly (retryable)."""

    def __init__(self) -> None:
        self.deps: Deps | None = None
        self.authenticator: RequestAuthenticator | None = None

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

    def bind(resolver: Resolver) -> None:
        runtime.authenticator = resolver.optional(RequestAuthenticator)
        runtime.deps = build_deps(resolver)

    for record_type, prefix in (
        ("medicine_inspection", "medi"),
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
    registry.router(build_router(runtime.need, runtime.authenticate))
    registry.provide(MedicationRecordReader, lambda _: MedicinesProvided(runtime))
    registry.bind("medicines", bind)
