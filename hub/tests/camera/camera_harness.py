"""Test doubles for the camera module: a scripted worker transport that checks every body it carries against the
worker's published protocol schema (m10: both sides test the same schema), reply builders that are themselves
checked against it, E01's memory task queue, the E05 attempt report and mission doubles, and a SQLite unit of work
factory carrying the camera migration. Production never binds anything here."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import jsonschema

import crew_health.camera.adapters as camera_adapters
from crew_health.camera.application.ports import MissionFacts, ReportResult, WorkerReply, WorkerUnreachable
from crew_health.contracts_gen.enums import ErrorCode, EventType
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.settings import SettingSpec
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.tasks import TaskRegistration
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryRelease,
    MemorySettings,
    MemoryStorage,
    MemoryTasks,
    MemoryUow,
    MemoryUowFactory,
    MemoryView,
)
from crew_health.kernel.uow import Actor, Audience, EventScope, ReadSnapshot, StoredEvent, UnitOfWork
from crew_health.measurement.application.commands import PROJECTION

CAMERA_MIGRATION = Path(camera_adapters.__file__).parent / "migrations" / "0001_camera.sql"
PROTOCOL = json.loads(
    (
        Path(__file__).resolve().parents[3] / "workers/camera/src/crew_health_camera/protocol.schema.json"
    ).read_text(encoding="utf-8")
)


def conforms(model: str, document: object) -> None:
    """The worker's published JSON schema for `model` accepts `document` (raises otherwise)."""
    jsonschema.Draft202012Validator(PROTOCOL["models"][model]).validate(document)


START = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
ADAPTER = "adp_camera"

type Step = WorkerReply | Exception | Callable[[str, str, bytes | None], WorkerReply]


def reply(status: int, body: object) -> WorkerReply:
    return WorkerReply(status, json.dumps(body).encode())


def describe(
    *,
    at: datetime = START,
    adapter_id: str = ADAPTER,
    origin: str = "SIM",
    methods: tuple[str, ...] = ("camera_estimate",),
    codes: tuple[str, ...] = ("pulse", "respiratory_rate"),
    cameras: int = 2,
    subject: str | None = "mara",
    max_ttl_ms: int = 600_000,
    version: str = "1",
) -> WorkerReply:
    """A worker describe reply in protocol v1 (DescribeReply), checked against the published schema."""
    body = {
        "protocol_version": version,
        "adapter_id": adapter_id,
        "origin_mode": origin,
        "policy": "camera-quality@1",
        "cameras": [
            {"device_id": f"camera_{i}", "name": f"Camera {i}", "kind": "station", "subject_id": subject}
            for i in range(1, cameras + 1)
        ],
        "ready_methods": list(methods),
        "capability_codes": list(codes),
        "max_ttl_ms": max_ttl_ms,
        "worker_utc": at.isoformat().replace("+00:00", "Z"),
    }
    if version == "1" and origin in ("LIVE", "REPLAY", "SIM"):
        conforms(
            "DescribeReply", body
        )  # well formed replies match the worker's schema; odd ones are on purpose
    return reply(200, body)


def status(
    acquisition_id: str, state: str, *, kind: str = "measurement", outcome: object = None
) -> WorkerReply:
    """A StatusReply, checked against the published schema."""
    body = {
        "acquisition_id": acquisition_id,
        "receipt_id": "rcp_1",
        "kind": kind,
        "state": state,
        "outcome": outcome,
        "delivery_code": None,
        "clock_uncertainty_ms": 1,
    }
    conforms("StatusReply", body)
    return reply(200, body)


@dataclass
class ScriptedTransport:
    """Answers each call from a script (a reply, an exception to raise, or a function); records every call. With an
    empty script it repeats `fallback`, which is unreachable unless set."""

    steps: list[Step] = field(default_factory=list)
    fallback: Step | None = None
    calls: list[tuple[str, str, dict[str, object] | None, float]] = field(default_factory=list)

    def call(
        self, method: Literal["GET", "POST"], path: str, body: bytes | None, *, timeout_s: float
    ) -> WorkerReply:
        sent = None if body is None else json.loads(body)
        if path == "/v1/acquisitions":
            conforms("StartRequest", sent)
        elif path.endswith("/cancel"):
            conforms("CancelRequest", sent)
        self.calls.append((method, path, sent, timeout_s))
        step = self.steps.pop(0) if self.steps else self.fallback
        if step is None:
            raise WorkerUnreachable("connection refused")
        if isinstance(step, Exception):
            raise step
        if isinstance(step, WorkerReply):
            return step
        return step(method, path, body)


@dataclass
class TickingClock:
    """A MemoryClock whose reads advance by `step` (simulates a round trip of 2 x step)."""

    clock: MemoryClock
    step: timedelta = timedelta(0)

    def now(self) -> datetime:
        value = self.clock.at
        self.clock.at = value + self.step
        return value

    def monotonic_ns(self) -> int:
        return self.clock.monotonic_ns()

    def process_epoch(self) -> str:
        return self.clock.process_epoch()


CAMERA_SETTINGS: Mapping[str, int] = {
    "workers.camera.timeout_ms": 2000,
    "workers.camera.health_ms": 1000,
    "workers.camera.stale_ms": 5000,
    "workers.camera.dispatch_batch": 8,
    "workers.camera.dispatch_deadline_ms": 30_000,
    "workers.camera.acquisition_ttl_ms": 120_000,
    "workers.camera.warmup_max_ms": 5_000,
    "workers.camera.poll_ms": 100,
    "workers.camera.max_polls": 5,
    "workers.camera.max_reply_bytes": 65_536,
}
TASK_SETTINGS: Mapping[str, int] = {
    "tasks.max_pending": 1000,
    "tasks.max_input_bytes": 65_536,
    "tasks.max_result_bytes": 65_536,
    "tasks.claim_batch": 64,
    "tasks.max_attempts": 4,
    "tasks.lease_ms": 60_000,
}


def camera_settings(
    base: MemorySettings, *, enabled: bool = False, texts: Mapping[str, str] | None = None
) -> MemorySettings:
    """The base settings plus the camera module's keys and E01's task bounds (test values inside their bounds)."""
    text_values = {
        "workers.camera.adapter_id": ADAPTER,
        "workers.camera.url": "",
        "workers.camera.token_file": "",
    }
    text_values.update(texts or {})
    integers = {**CAMERA_SETTINGS, **{k: v for k, v in TASK_SETTINGS.items() if k not in base.values}}
    specs = [s for s in base.specs.values()]
    specs += [SettingSpec(k, "integer", None, Decimal(0), Decimal(3_600_000), None, "test") for k in integers]
    specs += [SettingSpec(k, "text", None, None, None, 4096, "test") for k in text_values]
    specs.append(SettingSpec("workers.camera.enabled", "boolean", None, None, None, None, "test"))
    values: dict[str, object] = dict(base.values)
    values.update(integers)
    values.update(text_values)
    values["workers.camera.enabled"] = enabled
    return MemorySettings(specs, values)


def memory_tasks(settings: MemorySettings, registrations: list[TaskRegistration]) -> MemoryTasks:
    """E01's memory task queue and runner over the unit of work's memory state."""
    return MemoryTasks(
        settings=settings,
        registrations=registrations,
        authorize=lambda *_: True,
        capture=lambda at: TimeInput(at, "clock", "segment", 1, "hub_corrected", "LIVE"),
        elapsed=lambda view, start, end: end.original_at - start.original_at,
    )


@dataclass
class Mission:
    """MissionStatePort double (E23 via E05's copy)."""

    mode: Literal["LIVE", "REPLAY", "SIM"] | None = "SIM"

    def current(self, uow: object = None) -> MissionFacts | None:
        if self.mode is None:
            return None
        return MissionFacts("mis_1", self.mode, "lunar_surface", None, "node_hub")


@dataclass
class Reports:
    """AttemptReports double: answers from a script (default recorded) and records every report."""

    answers: list[ReportResult] = field(default_factory=list)
    calls: list[tuple[str, int, str, str]] = field(default_factory=list)

    def failed(
        self, request_id: str, *, sequence: int, at: datetime, reason: str, idempotency_key: str
    ) -> ReportResult:
        self.calls.append((request_id, sequence, reason, idempotency_key))
        return self.answers.pop(0) if self.answers else "recorded"


# Storage and events -------------------------------------------------------------------------------------------

_KERNEL_SETTINGS: Mapping[str, int] = {
    "storage.max_rows": 10_000,
    "storage.max_statement_bytes": 1_048_576,
    "commands.cache_entries": 10_000,
    "commands.max_bytes": 1_048_576,
    "event_bus.max_events": 1000,
    "events.max_subscribers": 64,
    "scheduler.max_jobs": 1000,
    "registry.max_hooks": 256,
    **TASK_SETTINGS,
}


def kernel_settings() -> MemorySettings:
    specs = [
        SettingSpec(k, "integer", None, Decimal(1), Decimal(8_388_608), None, "test")
        for k in _KERNEL_SETTINGS
    ]
    return MemorySettings(specs, dict(_KERNEL_SETTINGS))


class CountingIds:
    def __init__(self) -> None:
        self.issued = 0

    def new(self, prefix: str) -> str:
        self.issued += 1
        return require_id(f"{prefix}_{self.issued:05d}")

    def derived(self, prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("/".join((prefix, *parts)).encode()).hexdigest()
        return require_id(prefix + "_" + digest[:32])


class SqliteSession(MemoryStorage):
    """A real SQLite StorageSession on a file."""

    def __init__(self, settings: MemorySettings, path: Path, *, readonly: bool = False) -> None:
        super().__init__(settings=settings, responses={}, results={}, readonly=readonly)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        if self.readonly:
            raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, "readonly")
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
    """One SQLite transaction per unit of work; kernel memory state (events, audit) commits with it."""

    def __init__(self, *, clock: MemoryClock, ids: CountingIds, path: Path) -> None:
        settings = kernel_settings()
        self.writer = SqliteSession(settings, path)
        self.writer.db.executescript(CAMERA_MIGRATION.read_text(encoding="utf-8"))
        self.reader = SqliteSession(settings, path, readonly=True)
        super().__init__(
            clock=clock,
            ids=ids,
            release=MemoryRelease("rel_1", {}),
            settings=settings,
            connection=self.writer,
            node_id="node_hub",
            incarnation="inc_1",
        )

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        self.writer.db.execute("BEGIN IMMEDIATE")
        transaction = MemoryUow(self, actor, correlation_id)
        try:
            yield transaction
            self.writer.db.execute("COMMIT")
            self.state = transaction.state.copy()
        except BaseException:
            self.writer.db.execute("ROLLBACK")
            raise

    @contextmanager
    def snapshot(self) -> Iterator[ReadSnapshot]:
        yield MemoryView(self.state, connection=self.reader, release=self.release, now=self.clock.now())


@dataclass
class Store:
    uows: SqliteUowFactory
    clock: MemoryClock
    ids: CountingIds


def camera_store(tmp_path: Path) -> Store:
    """A real SQLite store with the camera migration, kernel memory fakes for the rest."""
    clock = MemoryClock(START, 0, "epoch_1")
    ids = CountingIds()
    return Store(SqliteUowFactory(clock=clock, ids=ids, path=tmp_path / "hub.db"), clock, ids)


def changed(
    correlation_id: str | None,
    *,
    request_id: str = "mrq_1",
    state: str = "in_progress",
    attempts: tuple[Mapping[str, object], ...] = (),
    patient: str | None = "mara",
    raw: bytes | None = None,
) -> StoredEvent:
    """A measurement_request.changed event as E05 emits it (wire request in data_json, person scope)."""
    data = {"request_id": request_id, "state": state, "attempts": list(attempts)}
    return StoredEvent(
        1,
        "evt_1",
        EventType.MEASUREMENT_REQUEST_CHANGED,
        START,
        START,
        EventScope("person", patient, None),
        "rel_1",
        correlation_id,
        raw if raw is not None else json.dumps(data).encode(),
        Audience("measurement.request", request_id, PROJECTION, "1"),
    )


def attempt_row(sequence: int, outcome: str) -> dict[str, object]:
    return {"sequence": sequence, "outcome": outcome}
