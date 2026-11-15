"""Test-only composition for E32 medicines: E01's real SQLiteDatabase, command ledger and audit on a tmp file, the
real medicines module registered into E01's DeclarationRegistry and the repository's own configuration (config/
treatments.json, medicines.json, pharmacy formulary and lots, the kit map). Fakes stand in for providers not on main
(E02 sessions, E23 mission, E31 acting officer). Production never binds anything here."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.routing import APIRouter

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.kernel.clock import Clock
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemoryIds, MemoryRelease
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.medicines import module as medicines_module
from crew_health.medicines.application import settings as medicines_settings
from crew_health.medicines.application.ports import ActingOfficerPort, MedicationRecordReader
from crew_health.medicines.application.use_cases import Deps
from crew_health.platform import module as platform_module
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import test_settings
from crew_health.supplies.application.ports import MissionFacts, MissionStatePort

with warnings.catch_warnings():  # starlette's own deprecation notice on importing its test client, not ours
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "config"
SCHEMAS = REPO / "contracts/api/schemas"
MIGRATION = REPO / "hub/src/crew_health/medicines/adapters/migrations/0001_medicines.sql"
NOW = datetime(
    2028, 3, 1, 8, 0, tzinfo=UTC
)  # every lot sits in moon_base_pharmacy, before any labeled expiry
CMO, CREW_B, SUBJECT, OPERATOR = "person_cmo", "person_b", "person_a", "person_op"


def medicines_values(config_dir: Path) -> dict[str, object]:
    return {
        "medicines.max_window_rows": 1000,
        "medicines.future_tolerance_seconds": 300,
        "medicines.clock_backward_tolerance_seconds": 60,
        "medicines.config_dir": str(config_dir),
    }


class Settings:
    """E01's hermetic test settings plus the medicines.* settings validated against this module's own specs."""

    def __init__(self, config_dir: Path) -> None:
        self.base = test_settings()
        self.own = ValidatedSettings(medicines_settings.specs(), medicines_values(config_dir))

    def _pick(self, key: str) -> HubSettings:
        return self.own if key.startswith("medicines.") else self.base

    def integer(self, key: str) -> int:
        return self._pick(key).integer(key)

    def quantity(self, key: str) -> Quantity:
        return self._pick(key).quantity(key)

    def boolean(self, key: str) -> bool:
        return self._pick(key).boolean(key)

    def text(self, key: str) -> str:
        return self._pick(key).text(key)


@dataclass
class StepClock:
    at: datetime = NOW
    epoch: str = "epoch_1"

    def now(self) -> datetime:
        return self.at

    def monotonic_ns(self) -> int:
        return int((self.at - NOW).total_seconds() * 1e9)

    def process_epoch(self) -> str:
        return self.epoch


@dataclass
class Mission:
    facts: MissionFacts | None

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        return self.facts

    def day_end(self, instant: datetime) -> datetime:
        return instant


@dataclass
class Acting:
    """E31 fake: (officer, subject) pairs for whom the officer is acting now."""

    pairs: set[tuple[str, str]] = field(default_factory=set)

    def is_acting(self, view: UnitOfWork | ReadSnapshot, person_id: str, subject_person_id: str) -> bool:
        return (person_id, subject_person_id) in self.pairs


def session(person: str, roles: frozenset[Role]) -> SessionContext:
    return SessionContext("session_" + person, "crew", Actor("person", person), person, roles)


SESSIONS: Mapping[str, SessionContext] = {
    "cmo": session(CMO, frozenset({Role.CREW_MEDICAL_OFFICER, Role.CREW})),
    "crew_b": session(CREW_B, frozenset({Role.CREW})),
    "subject": session(SUBJECT, frozenset({Role.CREW})),
    "subject_cmo": session(SUBJECT, frozenset({Role.CREW_MEDICAL_OFFICER, Role.CREW})),
    "operator": session(OPERATOR, frozenset({Role.OPERATOR})),
}


@dataclass
class Sessions:
    def session(self, request: Request, operation_id: str) -> SessionContext:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        found = SESSIONS.get(token)
        if found is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return found


def kit_map_release() -> MemoryRelease:
    raw = (CONFIG / "kit-map.json").read_bytes()
    document = ConfigDocumentView(ConfigName.KIT_MAP, hashlib.sha256(raw).hexdigest(), raw)
    return MemoryRelease("release_1", {ConfigName.KIT_MAP: document})


def audit_policies() -> tuple[AuditPolicy, ...]:
    """The audit entries this module writes (m11: registered by the harness until E01's registry hooks merge)."""
    return (
        AuditPolicy("medicines.administered", {}, frozenset(), {}),
        AuditPolicy("medicines.corrected", {}, frozenset(), {}),
    )


def config_copy(directory: Path, edit: Callable[[dict[str, object], dict[str, object]], None]) -> Path:
    """A copy of config/ in which `edit(treatments, medicines)` changed the two medicines documents."""
    target = directory / "config"
    shutil.copytree(CONFIG, target)
    treatments = json.loads((target / "treatments.json").read_text(encoding="utf-8"))
    policy = json.loads((target / "medicines.json").read_text(encoding="utf-8"))
    edit(treatments, policy)
    (target / "treatments.json").write_text(json.dumps(treatments), encoding="utf-8")
    (target / "medicines.json").write_text(json.dumps(policy), encoding="utf-8")
    return target


def lunar(mode: str = "SIM") -> MissionFacts:
    return MissionFacts("mission_1", mode, "lunar_surface", 180, "node_1")  # type: ignore[arg-type]


@dataclass
class Station:
    db: SQLiteDatabase
    clock: StepClock
    mission: Mission
    acting: Acting
    resolver: Resolver
    client: TestClient
    runtime_deps: Callable[[], Deps]

    @property
    def deps(self) -> Deps:
        return self.runtime_deps()

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        with sqlite3.connect(self.db.path) as connection:
            return list(connection.execute(sql).fetchall())


def build_station(
    directory: Path,
    *,
    config_dir: Path = CONFIG,
    mission: MissionFacts | None = None,
    clock: StepClock | None = None,
) -> Station:
    clock = clock or StepClock()
    settings = Settings(config_dir)
    db = SQLiteDatabase(
        directory / "hub.sqlite",
        clock=clock,
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(100_000))),
        release=kit_map_release(),
        settings=settings,
        origin_node_id="node_1",
        origin_incarnation="incarnation_1",
        initialize=not (directory / "hub.sqlite").exists(),
    )
    raw = MIGRATION.read_text(encoding="utf-8")
    db.migrate_tracked("medicines.0001", hashlib.sha256(raw.encode()).hexdigest(), raw)
    for policy in audit_policies():
        db.register_audit_policy(policy)
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    fake_mission, acting = Mission(mission if mission is not None else lunar()), Acting()
    registry.provide(SQLiteDatabase, lambda _: db)
    registry.provide(HubSettings, lambda _: db.settings)  # type: ignore[type-abstract]
    registry.provide(IdFactory, lambda _: db.ids)  # type: ignore[type-abstract]
    registry.provide(Clock, lambda _: clock)  # type: ignore[type-abstract]
    registry.provide(MissionStatePort, lambda _: fake_mission)  # type: ignore[type-abstract]
    registry.provide(ActingOfficerPort, lambda _: acting)  # type: ignore[type-abstract]
    registry.provide(medicines_module.RequestAuthenticator, lambda _: Sessions())  # type: ignore[type-abstract]
    platform_module.register(registry)
    medicines_module.register(registry)
    resolver = registry.resolve()
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        if isinstance(router, APIRouter):
            app.include_router(router)
    deps = _runtime_deps(resolver)
    return Station(db, clock, fake_mission, acting, resolver, TestClient(app), deps)


def _runtime_deps(resolver: Resolver) -> Callable[[], Deps]:
    """The bound Deps, reached through the provided port's runtime (white-box access for assertions only)."""
    provided = resolver.require(MedicationRecordReader)  # type: ignore[type-abstract]
    return provided._runtime.need  # type: ignore[attr-defined,no-any-return]


def headers(token: str = "cmo", key: str = "key-00000001", **extra: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key, **extra}
