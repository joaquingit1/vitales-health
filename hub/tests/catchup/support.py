# mypy: disable-error-code="type-abstract"
import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from crew_health.app.registry import DeclarationRegistry
from crew_health.catchup.api.router import RoleGate as CatchUpRoleGate
from crew_health.catchup.application.ports import ConsentQueries
from crew_health.catchup.application.settings import catchup_setting_specs
from crew_health.catchup.module import CatchUpRuntime
from crew_health.catchup.module import register as register_catchup
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, EventType, Role
from crew_health.events.settings import specifications as event_setting_specs
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
from crew_health.groundlink.module import GroundLinkRuntime
from crew_health.groundlink.module import register as register_groundlink
from crew_health.help.application.ports import (
    EventOutcome,
    GroundLogComposer,
    GroundMessageLifecyclePort,
    HelpLogSnapshots,
    HelpLogSnapshotView,
)
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigDocumentView, ConfigReader
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
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

from .test_compose import Snapshot

ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 11, 20, tzinfo=UTC)


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
            "starts_at": "2026-11-20T00:00:00Z",
            "ends_at": "2026-12-03T15:51:35Z",
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
        (
            ConfigName.ENVIRONMENT_PROFILES,
            data
            if data is not None
            else json.loads((ROOT / "config/environment-profiles.json").read_bytes()),
        ),
        (ConfigName.ENVIRONMENT_HAZARDS, hazard if hazard is not None else hazards()),
    ):
        raw = json.dumps(value).encode()
        documents[name] = ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)
    for name in (ConfigName.UI_COPY, ConfigName.HELP_PRESENTATION):
        raw = (ROOT / "config" / (name.value + ".json")).read_bytes()
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
        person = request.headers.get("person", "person_ground_1")
        return SessionContext("session_1", "operator", actor, None if person == "none" else person, roles)


class Consent:
    """E02 ConsentQueries fake: grants every (subject, recipient) pair unless denied."""

    def __init__(self) -> None:
        self.denied: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str, tuple[str, str]]] = []

    def allows(
        self, view: ReadSnapshot, *, subject_person_id: str, recipient: str, scope: tuple[str, str]
    ) -> bool:
        self.calls.append((subject_person_id, recipient, scope))
        return (subject_person_id, recipient) not in self.denied


class Versions:
    """ConfigReader fake keeping every document version ever released, as the E01 store does."""

    def __init__(self, *releases: MemoryRelease) -> None:
        self.documents: dict[tuple[ConfigName, str], ConfigDocumentView] = {}
        for item in releases:
            self.add(item)

    def add(self, item: MemoryRelease) -> None:
        for name, document in item.documents.items():
            self.documents[(name, document.version)] = document

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        found = self.documents.get((name, version or ""))
        if found is None:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        return found


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


class Snapshots:
    def __init__(self) -> None:
        self.value: HelpLogSnapshotView | None = Snapshot()
        self.values: dict[str, HelpLogSnapshotView] = {}
        self.calls = 0
        self.before: Callable[[], None] | None = None
        self.failure: Exception | None = None
        self.block: threading.Event | None = None

    def for_message(self, ground_message_id: str) -> HelpLogSnapshotView | None:
        self.calls += 1
        if self.before is not None:
            self.before()
        if self.block is not None:
            self.block.wait(2)
        if self.failure is not None:
            raise self.failure
        return self.values.get(ground_message_id, self.value)


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
        lifecycle: GroundMessageLifecyclePort | None = None,
        help_snapshots: HelpLogSnapshots | None = None,
        log_max_items: int = 100,
        max_lines: int = 64,
        at: datetime = AT,
        port_timeout_ms: int = 100,
        consent: bool = True,
        config: bool = True,
        config_reader: ConfigReader | None = None,
    ) -> None:
        self.clock = MemoryClock(at, 0, "epoch_1")
        self.db = database(path, clock=self.clock)
        self.release = release(data)
        self.versions = Versions(self.release)
        self.consent = Consent()
        self.db.set_release_provider(lambda: self.release)
        self.db.migrate(
            (ROOT / "hub/src/crew_health/groundlink/adapters/migrations/0001_groundlink.sql").read_text(
                encoding="utf-8"
            )
        )
        self.db.migrate(
            (ROOT / "hub/src/crew_health/catchup/adapters/migrations/0001_catchup.sql").read_text(
                encoding="utf-8"
            )
        )
        self.events: list[StoredEvent] = []
        self.db.event_sink = self.emit
        self.mission = Mission()
        self.mission.facts.active_environment_id = environment
        self.owner = Lifecycle()
        self.registry = DeclarationRegistry(max_ports=64, max_hooks=256)
        register_groundlink(self.registry)
        register_catchup(self.registry)
        self.snapshots = Snapshots()
        values: dict[str, object] = {
            "bundle.max_inflight": 17,
            "bundle.retry_max": 5,
            "bundle.retry_backoff_ms": 100,
            "bundle.renewal_batch": 31,
            "groundlink.backoff_cap_ms": 60000,
            "groundlink.port_timeout_ms": 500,
            "groundlink.agent_poll_ms": 100,
            "groundlink.agent_enabled": enabled,
            "groundlink.max_reply_bytes": 1024,
            "groundlink.history_rows": 1000,
            "catchup.log_max_items": log_max_items,
            "catchup.port_timeout_ms": port_timeout_ms,
            "catchup.max_lines": max_lines,
            "catchup.subscriber_max_attempts": 3,
            "groundlink.subscriber_max_attempts": 3,
            "groundlink.history_retention_rows": 10000,
            "events.page_size": 50,
        }
        events = tuple(spec for spec in event_setting_specs() if spec.key == "events.page_size")
        specs = (*groundlink_setting_specs(), *catchup_setting_specs(), *events)
        known = {spec.key for spec in specs}
        settings = MemorySettings(specs, {k: v for k, v in values.items() if k in known})
        self.registry.provide(UnitOfWorkFactory, lambda _: self.db)
        self.registry.provide(ReadSnapshotFactory, lambda _: self.db)
        self.registry.provide(HubSettings, lambda _: settings)
        self.registry.provide(IdFactory, lambda _: self.db.ids)
        self.registry.provide(Clock, lambda _: self.clock)
        self.registry.provide(IdempotencyStore, lambda _: CommandLedger(self.db.ids))
        if gate:
            self.registry.provide(RoleGate, lambda _: Gate())
            self.registry.provide(CatchUpRoleGate, lambda _: Gate())
        if mission:
            self.registry.provide(MissionStatePort, lambda _: self.mission)
        if owner:
            self.registry.provide(
                GroundMessageLifecyclePort, lambda _: lifecycle if lifecycle is not None else self.owner
            )
        self.registry.provide(
            HelpLogSnapshots, lambda _: help_snapshots if help_snapshots is not None else self.snapshots
        )
        if consent:
            self.registry.provide(ConsentQueries, lambda _: self.consent)
        if config:
            reader: ConfigReader = config_reader if config_reader is not None else self.versions
            self.registry.provide(ConfigReader, lambda _: reader)
        resolver = self.registry.resolve()
        self.catchup = resolver.require(CatchUpRuntime)
        self.composer = resolver.require(GroundLogComposer)
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


def message_event(ref: str = "msg_1", *, snapshot: Snapshot | None = None) -> StoredEvent:
    facts = snapshot if snapshot is not None else Snapshot()
    data: dict[str, object] = json.loads(
        (ROOT / "contracts/api/examples/help/ground-message-queued.json").read_bytes()
    )["value"]
    data.update(
        ground_message_id=ref,
        help_session_id=facts.help_session_id,
        session_revision=facts.session_revision,
        queued_at=facts.queued_at.isoformat().replace("+00:00", "Z"),
    )
    return replace(event(EventType.GROUND_MESSAGE_CHANGED, data), event_id=f"evt_{ref}")


def feed(h: Harness, message: StoredEvent) -> None:
    for name in ("groundlink.outbox", "catchup.ground_message"):
        declaration = h.registry.declarations["durable_subscribers"][name]
        assert isinstance(declaration, tuple)
        handler = cast(Callable[[UnitOfWork, StoredEvent], None], declaration[1])
        with h.db.begin(actor=Actor("engine", "subscriber"), correlation_id="cor_subscriber") as uow:
            handler(uow, message)
