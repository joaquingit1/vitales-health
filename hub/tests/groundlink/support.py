# mypy: disable-error-code="type-abstract"
import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ConfigName, EventType, Role
from crew_health.groundlink.api.router import RoleGate
from crew_health.groundlink.application.ports import (
    BundleInbox,
    BundleOutbox,
    BundleStatus,
    GroundLinkQuery,
    MissionFacts,
    MissionStatePort,
    OutboxItem,
)
from crew_health.groundlink.application.settings import groundlink_setting_specs
from crew_health.groundlink.module import GroundLinkRuntime, register
from crew_health.help.application.ports import EventOutcome, GroundMessageLifecyclePort
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigDocumentView
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.testing import MemoryClock, MemoryRelease, MemorySettings
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.testing.sqlite import database

ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 11, 14, tzinfo=UTC)


def profiles() -> dict[str, object]:
    data: dict[str, object] = json.loads(
        (ROOT / "contracts/api/examples/config/environment-profiles.example.json").read_bytes()
    )["value"]
    return data


def profile_rows(data: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data["profiles"])


def lunar_window(data: dict[str, object], *, gap: str = "planned_line_of_sight") -> None:
    lunar = next(p for p in profile_rows(data) if p["environment_id"] == "lunar_surface")
    cast(dict[str, object], lunar["link"])["sim_schedule"] = [
        {
            "starts_at": "2026-11-14T00:00:00Z",
            "ends_at": "2026-11-15T02:00:00Z",
            "status": "down",
            "gap_kind": gap,
            "planned": True,
        }
    ]


def hazards() -> dict[str, object]:
    data: dict[str, object] = json.loads((ROOT / "docs/product/environment-hazards.json").read_bytes())
    return data


def validate_document(name: str, data: dict[str, object]) -> None:
    root = ROOT / "contracts/api/schemas"
    resources: Registry[Mapping[str, object] | bool] = Registry()
    for path in sorted(root.glob("*.json")):
        schema: dict[str, object] = json.loads(path.read_bytes())
        resources = resources.with_resource(path.name, Resource.from_contents(schema))
    schema = json.loads((root / name).read_bytes())
    Draft202012Validator(schema, registry=resources).validate(data)


def release(data: dict[str, object] | None = None, hazard: dict[str, object] | None = None) -> MemoryRelease:
    documents = {}
    for name, value in (
        (ConfigName.ENVIRONMENT_PROFILES, data if data is not None else profiles()),
        (ConfigName.ENVIRONMENT_HAZARDS, hazard if hazard is not None else hazards()),
    ):
        raw = json.dumps(value).encode()
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    return MemoryRelease("release_1", documents)


@dataclass
class Facts:
    mission_id: str = "mission_1"
    mission_mode: Literal["SIM", "REPLAY", "LIVE"] = "SIM"
    active_environment_id: str = "station_leo"


class Mission:
    def __init__(self) -> None:
        self.facts = Facts()

    def current(self, uow: ReadSnapshot | None = None) -> MissionFacts | None:
        return self.facts


class Gate:
    def session_for(self, request: Request, operation_id: str) -> SessionContext:
        value = request.headers.get("role", "operator")
        roles = frozenset({Role(value)}) if value in {r.value for r in Role} else frozenset()
        actor = Actor(
            "assistant" if request.headers.get("actor") == "assistant" else "operator", "operator_1"
        )
        return SessionContext("session_1", "operator", actor, None, roles)


class Lifecycle:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, datetime, datetime | None]] = []
        self.seen: set[str] = set()
        self.fail_after_commit = False
        self.fail = False
        self.block: threading.Event | None = None
        self.before: Callable[[], None] | None = None
        self.lock = threading.Lock()

    def _call(
        self, kind: str, ref: str, event: str, at: datetime, arrived: datetime | None = None
    ) -> EventOutcome:
        if self.before:
            self.before()
        if self.block:
            self.block.wait(2)
        if self.fail:
            raise RuntimeError("private text must never enter last_error")
        with self.lock:
            self.calls.append((kind, ref, event, at, arrived))
            duplicate = event in self.seen
            self.seen.add(event)
        if self.fail_after_commit and not duplicate:
            raise RuntimeError("crash after owner commit")
        return "duplicate" if duplicate else "applied"

    def mark_sent(
        self,
        message_id: str,
        event_id: str,
        sent_at: datetime,
        one_way_delay_s: float | None,
        simulated: bool,
    ) -> EventOutcome:
        return self._call("sent", message_id, event_id, sent_at)

    def record_arrival(self, message_id: str, event_id: str, arrived_at: datetime) -> EventOutcome:
        return self._call("arrived", message_id, event_id, arrived_at)

    def record_receipt(
        self, message_id: str, event_id: str, received_at: datetime, arrived_at: datetime
    ) -> EventOutcome:
        return self._call("receipt", message_id, event_id, received_at, arrived_at)

    def record_reply(
        self, message_id: str, event_id: str, text: str, by: Actor, replied_at: datetime, arrived_at: datetime
    ) -> EventOutcome:
        return self._call("reply", message_id, event_id, replied_at, arrived_at)


class Harness:
    def __init__(
        self,
        path: Path,
        *,
        environment: str = "station_leo",
        gate: bool = True,
        mission: bool = True,
        owner: bool = True,
        data: dict[str, object] | None = None,
        enabled: bool = False,
    ) -> None:
        self.clock = MemoryClock(AT, 0, "epoch_1")
        self.db = database(path, clock=self.clock)
        self.release = release(data)
        self.db.set_release_provider(lambda: self.release)
        self.db.migrate(
            (ROOT / "hub/src/crew_health/groundlink/adapters/migrations/0001_groundlink.sql").read_text(
                encoding="utf-8"
            )
        )
        self.events: list[StoredEvent] = []
        self.db.event_sink = self.emit
        self.mission = Mission()
        self.mission.facts.active_environment_id = environment
        self.owner = Lifecycle()
        self.registry = DeclarationRegistry(max_ports=64, max_hooks=256)
        register(self.registry)
        values: dict[str, object] = {
            "bundle.max_inflight": 17,
            "bundle.retry_max": 5,
            "bundle.retry_backoff_ms": 100,
            "bundle.renewal_batch": 31,
            "groundlink.backoff_cap_ms": 60000,
            "groundlink.port_timeout_ms": 30,
            "groundlink.agent_poll_ms": 100,
            "groundlink.agent_enabled": enabled,
            "groundlink.max_reply_bytes": 1024,
            "groundlink.history_rows": 1000,
            "groundlink.subscriber_max_attempts": 3,
            "groundlink.history_retention_rows": 1000,
            "events.page_size": 50,
        }
        settings = MemorySettings(
            groundlink_setting_specs()
            + (
                SettingSpec(
                    "events.page_size",
                    "integer",
                    None,
                    Decimal(1),
                    Decimal(1000),
                    None,
                    "SYSTEM 5.11: events scanned in one durable subscription page",
                ),
            ),
            values,
        )
        self.registry.provide(UnitOfWorkFactory, lambda _: self.db)
        self.registry.provide(ReadSnapshotFactory, lambda _: self.db)
        self.registry.provide(HubSettings, lambda _: settings)
        self.registry.provide(IdFactory, lambda _: self.db.ids)
        self.registry.provide(Clock, lambda _: self.clock)
        self.registry.provide(IdempotencyStore, lambda _: CommandLedger(self.db.ids))
        if gate:
            self.registry.provide(RoleGate, lambda _: Gate())
        if mission:
            self.registry.provide(MissionStatePort, lambda _: self.mission)
        if owner:
            self.registry.provide(GroundMessageLifecyclePort, lambda _: self.owner)
        resolver = self.registry.resolve()
        self.query = resolver.require(GroundLinkQuery)
        self.outbox = resolver.require(BundleOutbox)
        self.inbox = resolver.require(BundleInbox)
        self.runtime = resolver.require(GroundLinkRuntime)
        app = FastAPI()
        for router in self.registry.declarations["routers"].values():
            assert isinstance(router, APIRouter)
            app.include_router(router)
        self.client = TestClient(app)

    def emit(self, uow: UnitOfWork, event: OutboxEvent) -> StoredEvent:
        stored = StoredEvent(
            len(self.events) + 1,
            self.db.ids.new("evt"),
            event.type,
            event.occurred_at,
            uow.now,
            event.scope,
            event.config_release_id,
            event.correlation_id,
            event.data_json,
            event.audience,
        )
        self.events.append(stored)
        return stored

    def enqueue(self, count: int = 1) -> list[str]:
        ids = []
        with self.db.begin(actor=Actor("operator", "operator_1"), correlation_id="cor_1") as uow:
            for i in range(count):
                ids.append(
                    self.outbox.enqueue(
                        uow, OutboxItem("ground_message", f"msg_{i:04}", "a" * 64, self.clock.at, True)
                    )
                )
        return ids

    def status(self, ref: str = "msg_0000") -> BundleStatus:
        with self.db.snapshot() as view:
            result = self.outbox.status(view, "ground_message", ref)
            assert result is not None
            return result


def event(kind: EventType, data: dict[str, object], *, scope: str = "crew") -> StoredEvent:
    return StoredEvent(
        1,
        "evt_input",
        kind,
        AT,
        AT,
        EventScope(cast(Literal["crew"], scope), None, None),
        "release_1",
        None,
        json.dumps(data).encode(),
        Audience(
            "groundlink.link", "ground-link", AudienceProjectionKey("groundlink", "ground_link", 1), "1"
        ),
    )
