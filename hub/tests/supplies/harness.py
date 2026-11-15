"""Test-only composition for E32 supplies: E01's real SQLiteDatabase, command ledger, event repository, audit and
replication on a tmp file, the real supplies module registered into E01's DeclarationRegistry, and small fakes of
the providers that are not on main yet (E02 sessions, E23 mission, E28 plan runs). Content comes from the package's
example configuration documents, so the tests follow the content. Production never binds anything here."""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.routing import APIRouter

from crew_health.app.registry import DeclarationRegistry, Resolver
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.help.application.ports import (
    HelpContext,
    HelpFlowRef,
    HelpSessionReader,
    HelpSessionSnapshot,
)
from crew_health.kernel.clock import Clock
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.testing import MemoryIds, MemoryRelease
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork
from crew_health.platform import module as platform_module
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.settings import ValidatedSettings
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import test_settings
from crew_health.supplies import module as supplies_module
from crew_health.supplies.api.router import RequestAuthenticator
from crew_health.supplies.application import settings as supplies_settings
from crew_health.supplies.application.ports import MissionFacts, MissionStatePort, PlanRunReader, RunContext

with warnings.catch_warnings():  # starlette's own deprecation notice on importing its test client, not ours
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "contracts/api/examples"
SCHEMAS = REPO / "contracts/api/schemas"
MIGRATION = REPO / "hub/src/crew_health/supplies/adapters/migrations/0001_supplies.sql"
EVENTS_MIGRATION = REPO / "hub/src/crew_health/events/adapters/migrations/0001_events.sql"
NOW = datetime(2026, 11, 14, 12, 10, tzinfo=UTC)
SOURCES: Mapping[ConfigName, str] = {
    ConfigName.KIT_MAP: "kit-map.example.json",
    ConfigName.ONBOARD_PLANS: "config/onboard-plans.example.json",
    ConfigName.WARNING_RULES: "config/warning-rules.example.json",
    ConfigName.UI_COPY: "config/ui-copy.example.json",
    ConfigName.SKILLS_CATALOG: "config/skills-catalog.example.json",
}
# Hermetic operational values for the supplies.* hub settings, each inside its registered bound.
SUPPLIES_VALUES: Mapping[str, int] = {
    "supplies.max_quantity": 1_000_000,
    "supplies.max_quantity_scale": 6,
    "supplies.max_supplies": 512,
    "supplies.cover_batch_rows": 1000,
    "supplies.max_step_uses": 64,
    "supplies.release_cache": 4,
    "supplies.verify_batch_rows": 2,
    "supplies.verify_max_batches": 1000,
    "supplies.reference_timeout_ms": 500,
    "supplies.reference_inflight": 4,
}


def limits(values: Mapping[str, int] = SUPPLIES_VALUES) -> supplies_settings.SuppliesLimits:
    """The supplies limits a station built with these values reads (storage.max_rows from E01's test settings)."""
    return supplies_settings.SuppliesLimits.read(Settings(values))


def example(relative: str) -> object:
    """An example document; the config examples wrap theirs in {"value": ...}, the kit map does not."""
    data = json.loads((EXAMPLES / relative).read_text(encoding="utf-8"))
    return data["value"] if isinstance(data, dict) and "value" in data else data


def document(name: ConfigName, value: object) -> ConfigDocumentView:
    raw = json.dumps(value, sort_keys=True).encode()
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def release(
    release_id: str = "release_1", overrides: Mapping[ConfigName, object] | None = None
) -> MemoryRelease:
    values: dict[ConfigName, object] = {name: example(path) for name, path in SOURCES.items()}
    values.update(overrides or {})
    return MemoryRelease(release_id, {name: document(name, value) for name, value in values.items()})


class Settings:
    """E01's hermetic test settings plus the supplies.* settings validated against this module's own specs."""

    def __init__(self, values: Mapping[str, int] = SUPPLIES_VALUES) -> None:
        self.base = test_settings()
        self.own = ValidatedSettings(supplies_settings.specs(), dict(values))

    def _pick(self, key: str) -> HubSettings:
        return self.own if key.startswith("supplies.") else self.base

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

    def now(self) -> datetime:
        return self.at

    def monotonic_ns(self) -> int:
        return int((self.at - NOW).total_seconds() * 1e9)

    def process_epoch(self) -> str:
        return "epoch_1"


@dataclass
class Mission:
    """E23 MissionStatePort fake (not on main): the current mission, or None when unknown."""

    facts: MissionFacts | None

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None:
        return self.facts

    def day_end(self, instant: datetime) -> datetime:
        return instant


@dataclass
class HelpSessions:
    """Fake of help's HelpSessionReader with only the context answers a test sets."""

    contexts: dict[str, HelpContext] = field(default_factory=dict)

    def context(self, session: SessionContext, help_session_id: str) -> HelpContext:
        return self.contexts.get(help_session_id, HelpContext(False, None, None))

    def readable_by(self, session: SessionContext, help_session_id: str) -> bool:
        return self.context(session, help_session_id).visible

    def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
        return None

    def qualifying_sessions(self) -> Sequence[HelpFlowRef]:
        return ()


@dataclass
class PlanRuns:
    """E28 PlanRunReader fake (not on main)."""

    contexts: dict[str, RunContext] = field(default_factory=dict)

    def context(self, session: SessionContext, plan_run_id: str) -> RunContext | None:
        return self.contexts.get(plan_run_id)


def crew_session(person: str = "person_a", roles: frozenset[Role] = frozenset({Role.CREW})) -> SessionContext:
    return SessionContext("session_" + person, "crew", Actor("person", person), person, roles)


@dataclass
class Sessions:
    """E02 RequestAuthenticator fake: the bearer token names a session the test registered."""

    by_token: dict[str, SessionContext] = field(default_factory=dict)

    def session(self, request: Request, operation_id: str) -> SessionContext:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        found = self.by_token.get(token)
        if found is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return found


def audit_policies() -> tuple[AuditPolicy, ...]:
    """The audit entries this module writes, registered the way E01 registers its core policies."""
    return (
        AuditPolicy(
            "supplies.use_recorded", {}, frozenset({"supply_use"}), {"supply_use": frozenset({"supply_use"})}
        ),
        AuditPolicy(
            "supplies.use_refused",
            {"code": frozenset({"SUPPLY_BELOW_ZERO"})},
            frozenset({"supply_use_refusal"}),
            {"supply_use_refusal": frozenset({"supply_use_refusal"})},
        ),
    )


def open_database(
    directory: Path, clock: StepClock, active: MemoryRelease, settings: Settings
) -> SQLiteDatabase:
    ids = MemoryIds(identifiers=tuple(str(i) for i in range(100_000)))
    db = SQLiteDatabase(
        directory / "hub.sqlite",
        clock=clock,
        ids=ids,
        release=active,
        settings=settings,
        origin_node_id="node_1",
        origin_incarnation="incarnation_1",
        initialize=not (directory / "hub.sqlite").exists(),
    )
    raw = MIGRATION.read_text(encoding="utf-8")
    db.migrate_tracked("supplies.0001", hashlib.sha256(raw.encode()).hexdigest(), raw)
    events = EVENTS_MIGRATION.read_text(encoding="utf-8")
    db.migrate_tracked("events.0001", hashlib.sha256(events.encode()).hexdigest(), events)
    repository = EventRepository(
        settings=settings, ids=ids, validator=EventValidator(schema_directory=SCHEMAS)
    )
    db.event_sink = repository.append
    for policy in audit_policies():
        db.register_audit_policy(policy)
    db.register_replicated_table("supplies_use", record_type="supply_use", key_column="supply_use_id")
    db.register_replicated_table(
        "supplies_use_refused", record_type="supply_use_refusal", key_column="refusal_id"
    )
    return db


@dataclass
class Station:
    db: SQLiteDatabase
    clock: StepClock
    registry: DeclarationRegistry
    resolver: Resolver
    mission: Mission
    help: HelpSessions
    runs: PlanRuns
    sessions: Sessions
    client: TestClient

    def set_release(self, active: MemoryRelease) -> None:
        self.db.set_release_provider(lambda: active)

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        import sqlite3

        with sqlite3.connect(self.db.path) as connection:
            return list(connection.execute(sql).fetchall())


def lunar_mission(mode: str = "SIM") -> MissionFacts:
    return MissionFacts("mission_1", mode, "lunar_surface", 180, "node_1")  # type: ignore[arg-type]


def build_station(
    directory: Path,
    *,
    active: MemoryRelease | None = None,
    mission: MissionFacts | None = None,
    settings: Settings | None = None,
) -> Station:
    clock = StepClock()
    db = open_database(directory, clock, active or release(), settings or Settings())
    registry = DeclarationRegistry(max_ports=1024, max_hooks=1024)
    fakes = (
        Mission(mission if mission is not None else lunar_mission()),
        HelpSessions(),
        PlanRuns(),
        Sessions(),
    )
    registry.provide(SQLiteDatabase, lambda _: db)
    registry.provide(HubSettings, lambda _: db.settings)  # type: ignore[type-abstract]
    registry.provide(IdFactory, lambda _: db.ids)  # type: ignore[type-abstract]
    registry.provide(Clock, lambda _: clock)  # type: ignore[type-abstract]
    registry.provide(MissionStatePort, lambda _: fakes[0])  # type: ignore[type-abstract]
    registry.provide(HelpSessionReader, lambda _: fakes[1])  # type: ignore[type-abstract]
    registry.provide(PlanRunReader, lambda _: fakes[2])  # type: ignore[type-abstract]
    registry.provide(RequestAuthenticator, lambda _: fakes[3])  # type: ignore[type-abstract]
    platform_module.register(registry)
    supplies_module.register(registry)
    resolver = registry.resolve()
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        if isinstance(router, APIRouter):
            app.include_router(router)
    return Station(db, clock, registry, resolver, *fakes, TestClient(app))


def use_body(quantity: object, **extra: object) -> dict[str, object]:
    return {"quantity": quantity, "client_time": "2026-11-14T12:05:00Z", **extra}


def headers(token: str = "crew", key: str = "use-key-0001", **extra: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key, **extra}


def decimal_of(value: object) -> Decimal:
    return Decimal(str(value))
