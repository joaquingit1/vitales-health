"""Test-only composition for E05: a real SQLite StorageSession on tmp_path, the kernel memory fakes for the shared
mechanisms, and small fakes of the consumed ports built from the contract example documents. Production never
binds anything here."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import crew_health.measurement.adapters as measurement_adapters
from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ErrorCode, Role
from crew_health.kernel.commands import CommandIdentity, CommandReceipts, IdempotencyStore
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryCommands,
    MemoryRelease,
    MemorySettings,
    MemoryStorage,
    MemoryUnits,
    MemoryUow,
    MemoryUowFactory,
    MemoryView,
)
from crew_health.kernel.units import Unit, UnitRegistry
from crew_health.kernel.uow import (
    Actor,
    ReadSnapshot,
    ReadSnapshotFactory,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.kitmap.application.ports import CapabilityWords, KitCheckFacts
from crew_health.measurement import module
from crew_health.measurement.application.ports import (
    AdapterAssignment,
    AdapterAssignmentPort,
    AdapterReadinessPort,
    CommandFingerprint,
    ConsentQueries,
    EpisodeFact,
    EpisodeFacts,
    GapPlan,
    GapPlanPort,
    GateOutcome,
    GateRow,
    KitCapabilitiesPort,
    KitCapability,
    KitGate,
    MissionFacts,
    MissionStatePort,
    ObservationCommands,
    ObservationDraft,
    SessionAuthenticator,
    View,
)
from crew_health.measurement.domain.model import OperatorRequirement

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "contracts" / "api" / "examples"
SCHEMAS = REPO / "contracts" / "api" / "schemas"
KIT_MAP = EXAMPLES / "kit-map.example.json"
UI_COPY = EXAMPLES / "config" / "ui-copy.example.json"
MIGRATION = Path(measurement_adapters.__file__).parent / "migrations" / "0001_measurement.sql"
FINGERPRINT_KEY = b"e05-test-install-key"
START = datetime(2026, 11, 13, 9, 31, 10, tzinfo=UTC)

_KERNEL_SETTINGS: tuple[tuple[str, int, int, int], ...] = (
    ("storage.max_rows", 1, 100000, 10000),
    ("storage.max_statement_bytes", 1, 8388608, 1048576),
    ("commands.cache_entries", 1, 100000, 10000),
    ("commands.max_bytes", 1, 8388608, 1048576),
    ("event_bus.max_events", 1, 10000, 1000),
    ("events.max_subscribers", 1, 1024, 64),
    ("scheduler.max_jobs", 1, 10000, 1000),
    ("registry.max_hooks", 1, 1024, 256),
)
MEASUREMENT_DEFAULTS: Mapping[str, int] = {
    "measurement.page_size": 50,
    "measurement.max_capabilities": 1024,
    "measurement.max_attempts": 4,
    "measurement.max_late_attempts": 4,
    "measurement.max_observations": 32,
    "measurement.max_text_bytes": 2048,
    "measurement.max_notes": 16,
    "measurement.max_context_entries": 16,
    "measurement.replay_window_ms": 600000,
}


def example(path: str) -> dict[str, object]:
    """The `value` of a contract example document (illustrative, never seed data)."""
    data = json.loads((EXAMPLES / path).read_text(encoding="utf-8"))
    value = data["value"]
    assert isinstance(value, dict)
    return value


# Storage ------------------------------------------------------------------------------------------------------


class SqliteSession(MemoryStorage):
    """A real SQLite StorageSession. `fail_on` injects a storage failure (disk full) on matching SQL."""

    def __init__(self, settings: MemorySettings, path: Path, *, readonly: bool = False) -> None:
        super().__init__(settings=settings, responses={}, results={}, readonly=readonly)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.fail_on: str | None = None

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        if self.readonly:
            raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, "readonly")
        if self.fail_on is not None and self.fail_on in sql:
            raise StorageUnavailable("disk_full")
        cursor = self.db.execute(sql, parameters)
        return StatementResult(cursor.rowcount, cursor.lastrowid)

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        result = tuple(self.db.execute(sql, parameters).fetchmany(limit + 1))
        if len(result) > limit:
            raise ProblemError(ErrorCode.PAYLOAD_TOO_LARGE, "rows")
        return result


class SqliteUowFactory(MemoryUowFactory):
    """One SQLite transaction per unit of work; kernel memory state (ledger, events, audit) commits with it."""

    def __init__(
        self, *, clock: MemoryClock, ids: IdFactory, settings: MemorySettings, path: Path, release_id: str
    ) -> None:
        self.writer = SqliteSession(settings, path)
        self.writer.db.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.reader = SqliteSession(settings, path, readonly=True)
        super().__init__(
            clock=clock,
            ids=ids,
            release=MemoryRelease(release_id, {}),
            settings=settings,
            connection=self.writer,
            node_id="node_hub",
            incarnation="inc_1",
        )

    def set_release(self, release_id: str) -> None:
        self.release = MemoryRelease(release_id, {})

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        if self.writing:
            raise ProblemError(ErrorCode.INVALID_TRANSITION, "nested")
        self.writing = True
        self.writer.db.execute("BEGIN IMMEDIATE")
        transaction = MemoryUow(self, actor, correlation_id)
        try:
            yield transaction
            self.writer.db.execute("COMMIT")
            self.state = transaction.state.copy()
        except BaseException:
            self.writer.db.execute("ROLLBACK")
            raise
        finally:
            self.writing = False

    @contextmanager
    def snapshot(self) -> Iterator[ReadSnapshot]:
        yield MemoryView(self.state, connection=self.reader, release=self.release, now=self.clock.now())

    def count(self, table: str) -> int:
        row = self.reader.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])


class CountingIds:
    def __init__(self) -> None:
        self.issued = 0

    def new(self, prefix: str) -> str:
        self.issued += 1
        return require_id(f"{prefix}_{self.issued:05d}")

    def derived(self, prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("/".join((prefix, *parts)).encode()).hexdigest()
        return require_id(prefix + "_" + digest[:32])


# Consumed port fakes ------------------------------------------------------------------------------------------


class KitMapFake:
    """E27 KitGate and KitCapabilitiesPort computed from a kit-map document per CONTRACTS 21 and E27 3."""

    def __init__(self, kit_map: Path, ui_copy: Path) -> None:
        self.kit_map = kit_map
        self.ui_copy = ui_copy

    def _documents(self) -> tuple[dict[str, Any], str, dict[str, str]]:
        raw = self.kit_map.read_bytes()
        document = json.loads(raw)
        copy = json.loads(self.ui_copy.read_text(encoding="utf-8"))["value"]["kit_check_templates"]
        return document, hashlib.sha256(raw).hexdigest(), copy

    def _providers(self, document: dict[str, Any], code: str) -> list[dict[str, Any]]:
        instruments = document["instruments"]
        assert isinstance(instruments, list)
        return [i for i in instruments if code in i["capability_codes"]]

    def capabilities(self, snapshot: ReadSnapshot, environment_id: str) -> tuple[KitCapability, ...]:
        document, _, _ = self._documents()
        kits = document["kits"]
        capabilities = document["capabilities"]
        assert isinstance(kits, dict) and isinstance(capabilities, list)
        if environment_id not in kits:
            raise ProblemError(ErrorCode.INVALID_VALUE, "environment")
        packed = kits[environment_id]
        result = []
        for capability in capabilities:
            providers = self._providers(document, capability["code"])
            ids = tuple(p["id"] for p in providers if p["id"] in packed)
            usable = [p for p in providers if p["id"] in packed] or providers
            methods = tuple(dict.fromkeys(str(p.get("method", "manual_instrument")) for p in usable))
            requirement: OperatorRequirement = capability["operator_requirement"]
            result.append(
                KitCapability(
                    code=capability["code"],
                    label=capability["label"],
                    body_region=capability["body_region"],
                    operator_requirement=requirement,
                    expected_duration_seconds=capability["expected_duration_seconds"],
                    methods=methods,
                    instrument_ids=ids,
                    missing_instrument_ids=tuple(p["id"] for p in providers if p["id"] not in packed),
                    applicable_environments=tuple(
                        env for env, kit in kits.items() if any(p["id"] in kit for p in providers)
                    ),
                    kit_status="available" if ids else "unavailable",
                    kit_reason=None if ids else "not_in_kit",
                    components=tuple(capability.get("components", ())),
                )
            )
        return tuple(result)

    def check(
        self, uow: UnitOfWork, *, capability_code: str, environment_id: str, condition_ids: Sequence[str]
    ) -> GateOutcome:
        document, version, templates = self._documents()
        kits = document["kits"]
        conditions = document["conditions"]
        assert isinstance(kits, dict) and isinstance(conditions, list)
        providers = self._providers(document, capability_code)
        if not providers or environment_id not in kits:
            raise ProblemError(ErrorCode.INVALID_VALUE, "kit_gate_invalid")
        by_id = {c["condition_id"]: c for c in conditions}
        if any(c not in by_id for c in condition_ids):
            raise ProblemError(ErrorCode.INVALID_VALUE, "kit_gate_invalid")
        packed = [p for p in providers if p["id"] in kits[environment_id]]
        missing = [p for p in providers if p["id"] not in kits[environment_id]]
        if packed:
            text = templates["can"].format(instruments=", ".join(str(p["name"]) for p in packed))
        else:
            needs = ", ".join(str(p.get("needs_text", p["name"])) for p in missing)
            text = templates["cannot"].format(instruments=needs)
        kit = KitCheckFacts(
            can=bool(packed),
            instrument_ids=tuple(str(p["id"]) for p in packed),
            missing_instrument_ids=() if packed else tuple(str(p["id"]) for p in missing),
            condition_ids=tuple(condition_ids),
            cliff_urls=tuple(str(by_id[c]["cliff_url"]) for c in condition_ids),
            text=text,
            kit_map_version=version,
            environment_id=environment_id,
        )
        rows = tuple(GateRow(c, by_id[c].get("plan_id")) for c in condition_ids)
        words = next(c for c in document["capabilities"] if c["code"] == capability_code)
        return GateOutcome(kit, CapabilityWords(**words), rows)


@dataclass
class MissionFake:
    facts: MissionFacts | None

    def current(self, uow: View | None = None) -> MissionFacts | None:
        return self.facts


@dataclass
class EpisodesFake:
    episodes: dict[str, EpisodeFact] = field(default_factory=dict)

    def get(self, uow: UnitOfWork, episode_id: str) -> EpisodeFact | None:
        return self.episodes.get(episode_id)

    def visible_to(self, uow: UnitOfWork, episode_id: str, actor: Actor) -> bool:
        fact = self.episodes.get(episode_id)
        return (
            fact is not None
            and actor.kind in ("person", "engine", "assistant")
            and (actor.kind != "person" or actor.id == fact.patient_id)
        )


@dataclass
class ConsentFake:
    grants: set[tuple[str, str, str]] = field(default_factory=set)

    def allows(self, view: View, *, subject_person_id: str, recipient: str, scope: tuple[str, str]) -> bool:
        return (subject_person_id, recipient, scope[1]) in self.grants and scope[0] == "measurement"


@dataclass
class ObservationsFake:
    drafts: list[ObservationDraft] = field(default_factory=list)

    def append_for_attempt(self, uow: UnitOfWork, draft: ObservationDraft) -> str:
        self.drafts.append(draft)
        return require_id(f"obs_{len(self.drafts):04d}")

    def canonical_unit(self, code: str) -> Unit:
        raise NotImplementedError("teach observations are not part of the measurement scenario")

    def append_observation(self, uow: UnitOfWork, draft: object, *, idempotency_key: str) -> str:
        raise NotImplementedError("teach observations are not part of the measurement scenario")


@dataclass
class GapPlansFake:
    kit_map: Path

    def plan_for(
        self, *, condition_ids: Sequence[str], environment_id: str, subject_person_id: str | None
    ) -> GapPlan | None:
        conditions = json.loads(self.kit_map.read_text(encoding="utf-8"))["conditions"]
        plans = [c["plan_id"] for c in conditions if c["condition_id"] in condition_ids and c["plan_id"]]
        return GapPlan(plans[0], ()) if plans else None


@dataclass
class ReadinessFake:
    ready: frozenset[str] | None

    def ready_methods(self, view: View) -> frozenset[str] | None:
        return self.ready


@dataclass
class AssignmentsFake:
    """Which adapter process serves a method (E06 camera worker). The camera name comes from the example."""

    by_method: dict[str, AdapterAssignment] = field(default_factory=dict)

    def assign(
        self, view: View, *, capability_code: str, methods: tuple[str, ...]
    ) -> AdapterAssignment | None:
        return next((self.by_method[m] for m in methods if m in self.by_method), None)


class SessionsFake:
    """Bearer token to E02 SessionContext. Unknown token: not signed in."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionContext] = {}

    def crew(self, token: str, person: str, *roles: Role) -> None:
        self.sessions[token] = SessionContext(
            "ses_" + token, "crew", Actor("person", person), person, frozenset(roles or (Role.CREW,))
        )

    def other(self, token: str, kind: str, actor_kind: str, actor_id: str) -> None:
        self.sessions[token] = SessionContext(
            "ses_" + token,
            kind,  # type: ignore[arg-type]
            Actor(actor_kind, actor_id),  # type: ignore[arg-type]
            None,
            frozenset(),
        )

    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext | None:
        value = headers.get("authorization", "")
        return self.sessions.get(value.removeprefix("Bearer "))


class HmacFingerprint:
    def fingerprint(self, canonical_body: bytes) -> str:
        return hmac.new(FINGERPRINT_KEY, canonical_body, hashlib.sha256).hexdigest()


# Composition ---------------------------------------------------------------------------------------------------


@dataclass
class Hub:
    client: TestClient
    uows: SqliteUowFactory
    clock: MemoryClock
    registry: DeclarationRegistry
    kit: KitMapFake
    mission: MissionFake
    episodes: EpisodesFake
    consent: ConsentFake
    observations: ObservationsFake
    readiness: ReadinessFake
    sessions: SessionsFake
    assignments: AssignmentsFake

    def advance(self, seconds: float) -> None:
        self.clock.at = self.clock.at + timedelta(seconds=seconds)

    def events(self) -> list[dict[str, object]]:
        return [json.loads(e.data_json) for e in self.uows.state.events]

    def audit_types(self) -> list[str]:
        return [a.event_type for a in self.uows.state.audit]


def settings_for(values: Mapping[str, int]) -> MemorySettings:
    specs = [
        SettingSpec(k, "integer", None, Decimal(lo), Decimal(hi), None, "test")
        for k, lo, hi, _ in _KERNEL_SETTINGS
    ]
    specs += [SettingSpec(k, "integer", None, Decimal(1), Decimal(86400000), None, "test") for k in values]
    merged: dict[str, object] = {k: v for k, _, _, v in _KERNEL_SETTINGS}
    merged.update(values)
    return MemorySettings(specs, merged)


def build_hub(
    tmp_path: Path,
    *,
    kit_map: Path = KIT_MAP,
    environment: str | None = "lunar_surface",
    mode: str = "LIVE",
    overrides: Mapping[str, int] | None = None,
    omit: frozenset[type[object]] = frozenset(),
    ready: frozenset[str] | None = frozenset({"camera_estimate"}),
) -> Hub:
    values = dict(MEASUREMENT_DEFAULTS)
    values.update(overrides or {})
    settings = settings_for(values)
    clock = MemoryClock(START, 0, "epoch_1")
    ids = CountingIds()
    uows = SqliteUowFactory(
        clock=clock, ids=ids, settings=settings, path=tmp_path / "hub.db", release_id="rel_1"
    )
    commands = MemoryCommands(ids=ids, settings=settings, authorize=lambda *_: True, operations={})
    kit = KitMapFake(kit_map, UI_COPY)
    facts = None if environment is None else MissionFacts("mis_1", mode, environment, None, "node_hub")  # type: ignore[arg-type]
    mission = MissionFake(facts)
    episodes = EpisodesFake()
    consent = ConsentFake()
    observations = ObservationsFake()
    readiness = ReadinessFake(ready)
    sessions = SessionsFake()
    observation = example("clinical/observation-pulse.json")
    camera = AdapterAssignment(
        "adp_camera", str(observation["acquisition_method"]), str(observation["device_id"])
    )
    assignments = AssignmentsFake({camera.method: camera})
    providers: list[tuple[type[object], object]] = [
        (HubSettings, settings),
        (UnitOfWorkFactory, uows),
        (ReadSnapshotFactory, uows),
        (IdFactory, ids),
        (IdempotencyStore, commands),
        (CommandReceipts, commands),
        (UnitRegistry, MemoryUnits({"/min": ("frequency", Decimal(1), Decimal(0))})),
        (KitGate, kit),
        (KitCapabilitiesPort, kit),
        (MissionStatePort, mission),
        (EpisodeFacts, episodes),
        (ConsentQueries, consent),
        (ObservationCommands, observations),
        (GapPlanPort, GapPlansFake(kit_map)),
        (AdapterReadinessPort, readiness),
        (SessionAuthenticator, sessions),
        (CommandFingerprint, HmacFingerprint()),
        (AdapterAssignmentPort, assignments),
    ]
    registry = DeclarationRegistry(max_ports=64, max_hooks=256)
    for port, value in providers:
        if port not in omit:
            registry.provide(port, _const(value))
    module.register(registry)
    registry.resolve()
    app = FastAPI()
    for router in registry.declarations["routers"].values():
        app.include_router(router)  # type: ignore[arg-type]
    return Hub(
        TestClient(app),
        uows,
        clock,
        registry,
        kit,
        mission,
        episodes,
        consent,
        observations,
        readiness,
        sessions,
        assignments,
    )


def _const(value: object) -> Callable[[object], object]:
    return lambda _resolver: value


def seed_crew(hub: Hub) -> None:
    """The two crew members of the examples and their episode (identifiers from the contract examples)."""
    pulse = example("clinical/measurement-request-pulse.json")
    episode_id = str(pulse["episode_id"])
    hub.sessions.crew("mara", "mara")
    hub.sessions.crew("kai", "kai")
    hub.sessions.crew("cmo", "cmo", Role.CREW_MEDICAL_OFFICER)
    hub.sessions.other("camera", "adapter", "adapter", "adp_camera")
    hub.sessions.other("rogue", "adapter", "adapter", "adp_rogue")
    hub.sessions.other("station", "station", "station", "stn_galley")
    hub.episodes.episodes[episode_id] = EpisodeFact(
        episode_id, "mara", "mis_1", "LIVE", "open", 1, "q1", (), None, START
    )


def headers(token: str, key: str | None = None, **extra: str) -> dict[str, str]:
    result = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if key is not None:
        result["Idempotency-Key"] = key
    result.update({k.replace("_", "-"): v for k, v in extra.items()})
    return result


def pulse_body() -> dict[str, object]:
    pulse = example("clinical/measurement-request-pulse.json")
    return {
        "episode_id": pulse["episode_id"],
        "capability_code": pulse["capability_code"],
        "reason": pulse["reason"],
        "decision_affected": pulse["decision_affected"],
    }


def ecg_body() -> dict[str, object]:
    return dict(example("requests/measurement-request.json"))


def camera_value(
    value: object, *, device: str = "camera_2", origin: str = "LIVE", unit: str = "/min"
) -> dict[str, object]:
    observation = example("clinical/observation-pulse.json")
    return {
        "patient_id": observation["patient_id"],
        "episode_id": observation["episode_id"],
        "code": observation["code"],
        "value": value,
        "unit": unit,
        "observed_at": "2026-11-13T09:33:30Z",
        "origin_mode": origin,
        "acquisition_method": observation["acquisition_method"],
        "device_id": device,
        "context": observation["context"],
    }


def failed_attempt(sequence: int = 1) -> dict[str, object]:
    first = example("clinical/measurement-request-pulse.json")["attempts"]
    assert isinstance(first, list)
    reason = first[0]["failure_reason"]
    return {
        "sequence": sequence,
        "started_at": "2026-11-13T09:32:00Z",
        "ended_at": "2026-11-13T09:32:30Z",
        "outcome": "failed",
        "failure_reason": reason,
        "observations": [],
        "quality_notes": [reason],
    }


def success_attempt(sequence: int = 2, value: object = 96, **kw: str) -> dict[str, object]:
    return {
        "sequence": sequence,
        "started_at": "2026-11-13T09:33:00Z",
        "ended_at": "2026-11-13T09:33:30Z",
        "outcome": "success",
        "observations": [camera_value(value, **kw)],
    }


def act(hub: Hub, token: str, request_id: str, action: str, revision: int, key: str) -> dict[str, object]:
    response = hub.client.post(
        f"/v1/measurement-requests/{request_id}/actions",
        json={"action": action},
        headers=headers(token, key, If_Match=f'"{revision}"'),
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert isinstance(body, dict)
    return body


def started_pulse(hub: Hub) -> dict[str, object]:
    """Pulse proposed by the subject, submitted, accepted and started by her (self operator)."""
    created = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-create-1")
    )
    assert created.status_code == 201, created.json()
    body = created.json()
    request_id = str(body["request_id"])
    body = act(hub, "mara", request_id, "submit", 1, "idem-key-submit-1")
    body = act(hub, "mara", request_id, "accept", 2, "idem-key-accept-1")
    return act(hub, "mara", request_id, "start", 3, "idem-key-start-1")


def attempt(hub: Hub, token: str, request_id: str, body: dict[str, object], key: str) -> httpx.Response:
    response: httpx.Response = hub.client.post(
        f"/v1/measurement-requests/{request_id}/attempts", json=body, headers=headers(token, key)
    )
    return response


def schema_validator(name: str) -> object:
    """jsonschema validator for a contract schema with its sibling $refs resolved from disk."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    resources = []
    for path in sorted(SCHEMAS.glob("*.json")):
        resources.append((path.name, Resource.from_contents(json.loads(path.read_text(encoding="utf-8")))))
    registry = Registry().with_resources(resources)
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=registry)


def scratch_kit_map(tmp_path: Path, edit: Callable[[dict[str, Any]], None]) -> Path:
    """A scratch copy of the kit map with one content edit; the shipped document is never touched."""
    document = json.loads(KIT_MAP.read_text(encoding="utf-8"))
    edit(document)
    validator: Any = schema_validator("config-kit-map.schema.json")
    validator.validate(document)
    path = tmp_path / "kit-map.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path
