# mypy: disable-error-code="type-abstract"
# The SYSTEM 6 registry keys ports by their Protocol classes; mypy flags every Protocol passed as type[P].
"""End to end over HTTP (E30 12.2; D37 to D39): the real domain, application, SQLite adapters
(migration applied to a tmp file) and router, composed through module.register, with fakes only for the ports of
other epics. Covers the three verdicts, duplicate beats, clock jumps during a count, a hub restart, incomplete
captures, concurrent edits, a crash inside finish and privacy between crew members."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import crew_health.teach.adapters as teach_adapters
from crew_health.app.registry import DeclarationRegistry
from crew_health.contracts_gen.enums import ConfigName, ErrorCode, EventType, Role
from crew_health.contracts_gen.schemas.error_schema import ErrorEnvelope
from crew_health.contracts_gen.schemas.guided_check_schema import GuidedCheck as GuidedCheckModel
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    CommandReceipts,
    IdempotencyStore,
    StoredResult,
)
from crew_health.kernel.config import (
    ConfigDocumentView,
    ConfigReader,
    ConfigReleaseReader,
    ReleaseHandle,
    TemplateRenderer,
)
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryConfig,
    MemoryRelease,
    MemorySettings,
    MemoryTemplateRenderer,
)
from crew_health.kernel.units import Quantity, Unit, UnitRegistry
from crew_health.kernel.uow import (
    Actor,
    AudienceRule,
    DomainEvent,
    OutboxEvent,
    ReadSnapshotFactory,
    ReplicationChange,
    ReplicationRef,
    SessionContext,
    StoredEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.roles.application.ports import AssignmentRecord
from crew_health.teach.adapters.sqlite_repository import SqliteCoachingSamples, SqliteGuidedChecks
from crew_health.teach.application.commands import AUDIENCE_RULE, TeachCore, TeachPorts
from crew_health.teach.application.content import RENDER_FIELDS, ContentLoader
from crew_health.teach.application.ports import (
    AnswerCommands,
    AnswerDraft,
    CameraReferencePort,
    GuidanceDecision,
    GuidanceEligibility,
    GuidanceRequest,
    GuidedCheckCompleted,
    GuidedStepRef,
    HelpContext,
    HelpSessionReader,
    ObservationCommands,
    ObservationDraft,
    OriginMode,
    PersonDirectory,
    PersonRef,
    PlanRunReader,
    RecordRouting,
    ReferenceOutcome,
    ReferenceTicket,
    ReferenceWindowRequest,
    RequestAuthenticator,
    RoleAssignmentReader,
    StoredSample,
)
from crew_health.teach.application.reads import CoachingSamples
from crew_health.teach.application.settings import SPECS, TeachSettings
from crew_health.teach.domain.coaching import RawRateSample
from crew_health.teach.module import register

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = Path(teach_adapters.__file__).parent / "migrations" / "0001_teach.sql"
START = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
NS = 1_000_000_000
PULSE = Unit("/min")
SETTINGS: dict[str, object] = {
    "teach.max_count_seconds": 120,
    "teach.max_beats_per_second": 5,
    "teach.count_max_gap_seconds": 3,
    "teach.max_attempts": 10,
    "teach.max_active_checks": 64,
    "teach.camera_begin_timeout_ms": 250,
    "teach.camera_call_timeout_ms": 250,
    "teach.reference_wait_ms": 3000,
    "teach.camera_ticket_margin_s": 30,
    "teach.clock_tolerance_ms": 500,
    "teach.camera_clock_tolerance_ms": 200,
    "teach.list_page_max": 200,
    "teach.content_cache_max": 8,
    "teach.rate_display_decimals": 0,
}
TEST_TABLES = """
CREATE TABLE test_idempotency (key TEXT, actor TEXT, operation TEXT, target TEXT, params TEXT, status INTEGER,
    resource_ref TEXT, receipt_id TEXT, accepted_at TEXT, PRIMARY KEY (key, actor)) STRICT;
CREATE TABLE test_observation (observation_id TEXT PRIMARY KEY, idem_key TEXT UNIQUE, patient_id TEXT, code TEXT,
    value TEXT, unit TEXT, observed_at TEXT, origin_mode TEXT, method TEXT, context_json TEXT) STRICT;
CREATE TABLE test_answer (answer_id TEXT PRIMARY KEY, idem_key TEXT UNIQUE, record_id TEXT, question_id TEXT,
    value TEXT, presence TEXT, supersedes TEXT) STRICT;
"""


# Storage and unit of work over sqlite3 (test only) -------------------------------------------------------------


class Session:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        cursor = self.db.execute(sql, parameters)
        return StatementResult(cursor.rowcount, cursor.lastrowid)

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        return tuple(tuple(row) for row in self.db.execute(sql, parameters).fetchmany(limit))


@dataclass
class Sink:
    events: list[StoredEvent] = field(default_factory=list)
    audits: list[AuditEntry] = field(default_factory=list)
    published: list[DomainEvent] = field(default_factory=list)


class Uow:
    def __init__(self, harness: Stack, actor: Actor, correlation_id: str) -> None:
        self._h = harness
        self._actor = actor
        self._correlation = correlation_id
        self.events: list[StoredEvent] = []
        self.audits: list[AuditEntry] = []
        self.published: list[DomainEvent] = []

    @property
    def connection(self) -> Session:
        return self._h.session

    @property
    def now(self) -> datetime:
        return self._h.clock.now()

    @property
    def actor(self) -> Actor:
        return self._actor

    @property
    def release(self) -> ReleaseHandle:
        return self._h.config.active()

    @property
    def correlation_id(self) -> str:
        return self._correlation

    def publish(self, event: DomainEvent) -> None:
        self.published.append(event)

    def emit(self, event: OutboxEvent) -> StoredEvent:
        seq = len(self._h.sink.events) + len(self.events) + 1
        stored = StoredEvent(
            seq,
            f"evt_{seq}",
            event.type,
            event.occurred_at,
            self.now,
            event.scope,
            event.config_release_id,
            event.correlation_id,
            event.data_json,
            event.audience,
        )
        self.events.append(stored)
        return stored

    def audit(self, entry: AuditEntry) -> None:
        self.audits.append(entry)

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "teach schedules nothing")

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "teach schedules nothing")

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, "teach replicates nothing")

    @contextmanager
    def savepoint(self, name: str) -> Iterator[None]:
        yield


class Uows:
    def __init__(self, harness: Stack) -> None:
        self._h = harness
        self._lock = threading.Lock()
        self.fail_next = False

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[Uow]:
        if self.fail_next:
            self.fail_next = False
            raise StorageUnavailable("disk_full")
        with self._lock:
            self._h.db.execute("BEGIN IMMEDIATE")
            uow = Uow(self._h, actor, correlation_id)
            try:
                yield uow
            except BaseException:
                self._h.db.execute("ROLLBACK")
                raise
            self._h.db.execute("COMMIT")
            self._h.sink.events.extend(uow.events)
            self._h.sink.audits.extend(uow.audits)
            self._h.sink.published.extend(uow.published)


class Snapshot:
    def __init__(self, harness: Stack) -> None:
        self._h = harness

    @property
    def connection(self) -> Session:
        return self._h.session

    @property
    def release(self) -> ReleaseHandle:
        return self._h.config.active()

    @property
    def now(self) -> datetime:
        return self._h.clock.now()


class Snapshots:
    def __init__(self, harness: Stack) -> None:
        self._h = harness

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        yield Snapshot(self._h)


class Idempotency:
    def lookup(self, uow: UnitOfWork, identity: CommandIdentity) -> StoredResult | None:
        rows = uow.connection.rows(
            "SELECT operation, target, params, status, resource_ref, receipt_id, accepted_at FROM test_idempotency"
            " WHERE key = ? AND actor = ?",
            (identity.key, identity.actor_ref),
            limit=1,
        )
        if not rows:
            return None
        op, target, params, status, ref, receipt_id, at = rows[0]
        if (op, target, params) != (identity.operation, identity.target, identity.parameters_sha256):
            raise ProblemError(ErrorCode.DUPLICATE_COMMAND, ErrorCode.DUPLICATE_COMMAND.value)
        receipt = AcceptedReceipt(
            identity.key, str(op), str(target), str(receipt_id), datetime.fromisoformat(str(at))
        )
        return StoredResult(receipt, cast(int, status), cast(str | None, ref), "ok", None)

    def record(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None:
        uow.connection.execute(
            "INSERT INTO test_idempotency VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity.key,
                identity.actor_ref,
                identity.operation,
                identity.target,
                identity.parameters_sha256,
                result.status,
                result.resource_ref,
                result.receipt.receipt_id,
                result.receipt.accepted_at.isoformat(),
            ),
        )


class Receipts:
    def accepted(self, uow: UnitOfWork, identity: CommandIdentity) -> AcceptedReceipt:
        receipt_id = "rcp_" + hashlib.sha256((identity.key + identity.actor_ref).encode()).hexdigest()[:16]
        return AcceptedReceipt(identity.key, identity.operation, identity.target, receipt_id, uow.now)

    def read(self, view: object, session: SessionContext, identity: CommandIdentity) -> AcceptedReceipt:
        raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)


class CountingIds:
    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def new(self, prefix: str) -> str:
        with self._lock:
            self._n += 1
            return require_id(f"{prefix}_{self._n:04d}")

    def derived(self, prefix: str, *parts: str) -> str:
        return require_id(prefix + "_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24])


# Fakes of other epics' ports -----------------------------------------------------------------------------------


class Units:
    def parse(self, code: str) -> Unit:
        return Unit(code)

    def convert(self, quantity: Quantity, to: Unit) -> Quantity:
        if quantity.unit != to:
            raise ProblemError(ErrorCode.INVALID_UNIT, ErrorCode.INVALID_UNIT.value)
        return quantity

    def commensurable(self, a: Unit, b: Unit) -> bool:
        return a == b


class Observations:
    def __init__(self) -> None:
        self.fail_next = False

    def canonical_unit(self, code: str) -> Unit:
        return PULSE

    def append_observation(self, uow: UnitOfWork, draft: ObservationDraft, *, idempotency_key: str) -> str:
        if self.fail_next:
            self.fail_next = False
            raise StorageUnavailable("disk_full")
        found = uow.connection.rows(
            "SELECT observation_id FROM test_observation WHERE idem_key = ?", (idempotency_key,), limit=1
        )
        if found:
            return str(found[0][0])
        observation_id = "obs_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        uow.connection.execute(
            "INSERT INTO test_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id,
                idempotency_key,
                draft.patient_id,
                draft.code,
                str(draft.value.value),
                draft.value.unit.code,
                draft.observed_at.isoformat(),
                draft.origin_mode,
                draft.acquisition_method,
                json.dumps(dict(draft.context), sort_keys=True),
            ),
        )
        return observation_id


class Answers:
    def append_answer(self, uow: UnitOfWork, draft: AnswerDraft, *, idempotency_key: str) -> str:
        answer_id = "ans_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        uow.connection.execute(
            "INSERT INTO test_answer VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                answer_id,
                idempotency_key,
                draft.record_id,
                draft.question_id,
                draft.value,
                draft.presence,
                draft.supersedes,
            ),
        )
        return answer_id


class Plans:
    def __init__(self) -> None:
        self.steps: dict[str, GuidedStepRef] = {}

    def guided_step(self, plan_run_id: str, skill_id: str) -> GuidedStepRef | None:
        return self.steps.get(plan_run_id)


class Eligibility:
    def __init__(self) -> None:
        self.requests: list[GuidanceRequest] = []
        self.outcome = "show"

    def evaluate(self, snapshot: object, request: GuidanceRequest) -> GuidanceDecision:
        self.requests.append(request)
        shown = self.outcome == "show"
        return GuidanceDecision(
            outcome="show" if shown else "not_here",
            availability="aboard" if shown else "not_aboard",
            executable=shown,
            who_can=(),
            reasons=(),
            unknown=(),
            label="live",
            clinical_review="not_clinically_reviewed",
            substitute=None,
        )


class People:
    def __init__(self) -> None:
        self.people = {
            "tomas": PersonRef("tomas", "Tomas", True),
            "mara": PersonRef("mara", "Mara", True),
            "ana": PersonRef("ana", "Ana", True),
            "op_other": PersonRef("op_other", "Operator", True),
        }

    def person(self, person_id: str) -> PersonRef | None:
        return self.people.get(person_id)


class Routing:
    def __init__(self) -> None:
        self.routes: dict[str, str] = {}

    def record_origin(self, person_id: str) -> OriginMode:
        return "SIM" if person_id in self.routes else "LIVE"

    def record_for(self, person_id: str, origin: OriginMode) -> str:
        return self.routes.get(person_id, person_id)


class Help:
    def __init__(self) -> None:
        self.readers: dict[str, set[str]] = {}

    def context(self, session: SessionContext, help_session_id: str) -> HelpContext | None:
        return None

    def readable_by(self, session: SessionContext, help_session_id: str) -> bool:
        return session.person_id in self.readers.get(help_session_id, set())


class Camera:
    """mode: reading (a gated camera value), no_reading (the camera's reason), raise (port failure)."""

    def __init__(self) -> None:
        self.mode = "reading"
        self.value = Decimal(96)
        self.origin: OriginMode = "LIVE"
        self.requests: list[ReferenceWindowRequest] = []
        self.abandoned: list[str] = []
        self.after_begin: Callable[[], None] | None = None

    def begin_window(self, request: ReferenceWindowRequest, *, timeout: timedelta) -> ReferenceTicket:
        if self.mode == "raise":
            raise TimeoutError("camera did not answer")
        self.requests.append(request)
        if self.after_begin is not None:
            self.after_begin()
        return ReferenceTicket(f"tkt_{len(self.requests)}", None)

    def outcome(self, ticket_id: str, *, wait: timedelta) -> ReferenceOutcome:
        if self.mode == "reading":
            return ReferenceOutcome(
                "reading", "obs_cam_1", Quantity(self.value, PULSE), None, self.origin, 50, 2
            )
        return ReferenceOutcome("no_reading", None, None, "too much movement", self.origin, None, 2)

    def abandon(self, ticket_id: str, *, timeout: timedelta) -> None:
        self.abandoned.append(ticket_id)


class Roles:
    def __init__(self) -> None:
        self.assignments: dict[str, AssignmentRecord] = {
            "rol_tomas": AssignmentRecord(
                "rol_tomas", "tomas", "accepted", "pulse_check_wrist", "prn_1", None, False
            ),
            "rol_ana_proposed": AssignmentRecord(
                "rol_ana_proposed", "ana", "proposed", "pulse_check_wrist", "prn_1", None, False
            ),
            "rol_ana_other_run": AssignmentRecord(
                "rol_ana_other_run", "ana", "accepted", "pulse_check_wrist", "prn_2", None, False
            ),
        }

    def get(self, role_assignment_id: str) -> AssignmentRecord | None:
        return self.assignments.get(role_assignment_id)


class Authenticator:
    def __init__(self) -> None:
        roles = frozenset({Role.CREW})
        self.sessions = {
            f"tok_{name}": SessionContext(f"ses_{name}", "crew", Actor("person", name), name, roles)
            for name in ("tomas", "mara", "ana")
        }
        self.sessions["tok_station"] = SessionContext(
            "ses_station", "station", Actor("station", "stn_1"), None, frozenset({Role.STATION})
        )
        self.sessions["tok_assistant"] = SessionContext(
            "ses_assistant", "crew", Actor("assistant", "assistant"), "tomas", roles
        )

    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext:
        token = headers.get("authorization", "").removeprefix("Bearer ")
        session = self.sessions.get(token)
        if session is None:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return session


# The stack --------------------------------------------------------------------------------------------------


def _view(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def _example(path: str) -> bytes:
    return json.dumps(json.loads((ROOT / path).read_text(encoding="utf-8"))["value"]).encode()


def release(catalog: bytes, release_id: str = "rel_1") -> MemoryRelease:
    return MemoryRelease(
        release_id,
        {
            ConfigName.SKILLS_CATALOG: _view(ConfigName.SKILLS_CATALOG, catalog),
            ConfigName.UI_COPY: _view(
                ConfigName.UI_COPY, _example("contracts/api/examples/config/ui-copy.example.json")
            ),
            ConfigName.INDICATOR_CATALOG: _view(
                ConfigName.INDICATOR_CATALOG,
                _example("contracts/api/examples/config/indicator-catalog.example.json"),
            ),
            ConfigName.RESPONDER_FLOWS: _view(
                ConfigName.RESPONDER_FLOWS, (ROOT / "docs/product/responder-flows.json").read_bytes()
            ),
        },
    )


class Stack:
    def __init__(
        self, tmp_path: Path, *, camera: bool = True, catalog: bytes | None = None, roles: bool = True
    ) -> None:
        self.db = sqlite3.connect(tmp_path / "hub.sqlite3", check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.db.executescript(TEST_TABLES)
        self.session = Session(self.db)
        self.sink = Sink()
        self.clock = MemoryClock(START, 5 * NS, "gen1")
        self.client_ns = 0
        self.keys = 0
        raw = catalog if catalog is not None else (ROOT / "config/skills-catalog.json").read_bytes()
        self.config = MemoryConfig([release(raw)], active_id="rel_1")
        self.camera = Camera()
        self.observations = Observations()
        self.plans = Plans()
        self.eligibility = Eligibility()
        self.routing = Routing()
        self.help = Help()
        self.registry = DeclarationRegistry(max_ports=64, max_hooks=128)
        register(self.registry)
        self.uows = Uows(self)
        self.roles = Roles()
        self._provide(camera, roles)
        self.resolver = self.registry.resolve()
        routers = list(self.registry.declarations["routers"].values())
        app = FastAPI()
        app.include_router(cast(APIRouter, routers[0]))
        self.client = TestClient(app)
        self.audience = cast(AudienceRule, self.registry.declarations["audiences"][AUDIENCE_RULE])

    def _provide(self, camera: bool, roles: bool) -> None:
        r = self.registry
        r.provide(UnitOfWorkFactory, lambda _: self.uows)
        r.provide(ReadSnapshotFactory, lambda _: Snapshots(self))
        r.provide(Clock, lambda _: self.clock)
        r.provide(IdFactory, lambda _: CountingIds())
        r.provide(HubSettings, lambda _: MemorySettings(SPECS, SETTINGS))
        r.provide(ConfigReleaseReader, lambda _: self.config)
        r.provide(ConfigReader, lambda _: self.config)
        r.provide(TemplateRenderer, lambda _: MemoryTemplateRenderer("unknown", RENDER_FIELDS))
        r.provide(IdempotencyStore, lambda _: Idempotency())
        r.provide(CommandReceipts, lambda _: Receipts())
        r.provide(UnitRegistry, lambda _: Units())
        r.provide(ObservationCommands, lambda _: self.observations)
        r.provide(AnswerCommands, lambda _: Answers())
        r.provide(GuidanceEligibility, lambda _: self.eligibility)
        r.provide(PersonDirectory, lambda _: People())
        r.provide(RecordRouting, lambda _: self.routing)
        r.provide(RequestAuthenticator, lambda _: Authenticator())
        r.provide(PlanRunReader, lambda _: self.plans)
        r.provide(HelpSessionReader, lambda _: self.help)
        if camera:
            r.provide(CameraReferencePort, lambda _: self.camera)
        if roles:
            r.provide(RoleAssignmentReader, lambda _: self.roles)

    # Clock -------------------------------------------------------------------------------------------------

    def advance(self, ns: int, *, wall_ns: int | None = None) -> None:
        """Advance monotonic time; the hub wall clock follows unless a jump is injected. The client device keeps
        its own clock, which never jumps with the hub's."""
        self.clock.monotonic += ns
        self.client_ns += ns
        self.clock.at += timedelta(microseconds=(ns if wall_ns is None else wall_ns) // 1000)

    def client_time(self) -> str:
        at = START + timedelta(microseconds=self.client_ns // 1000)
        return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def key(self, prefix: str) -> str:
        self.keys += 1
        return f"{prefix}-{self.keys}"

    # HTTP --------------------------------------------------------------------------------------------------

    def post(
        self, path: str, who: str, body: Mapping[str, object], key: str, if_match: int | None = None
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer tok_{who}", "Idempotency-Key": key}
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        response = self.client.post(path, content=json.dumps(dict(body)), headers=headers)
        payload = cast(dict[str, object], response.json())
        payload["_status"] = response.status_code
        return payload

    def get(self, path: str, who: str) -> dict[str, object]:
        response = self.client.get(path, headers={"Authorization": f"Bearer tok_{who}"})
        payload = cast(dict[str, object], response.json())
        payload["_status"] = response.status_code
        return payload

    def start(self, who: str = "mara", **extra: object) -> dict[str, object]:
        body = {
            "skill_id": "pulse_check_wrist",
            "helper_person_id": "tomas",
            "subject_person_id": "mara",
            **extra,
        }
        return self.post("/v1/guided-checks", who, body, self.key("start"))

    def act(
        self,
        gid: str,
        action: str,
        revision: int,
        *,
        who: str = "tomas",
        key: str | None = None,
        **extra: object,
    ) -> dict[str, object]:
        body = {"action": action, "client_time": self.client_time(), **extra}
        return self.post(f"/v1/guided-checks/{gid}/actions", who, body, key or self.key(action), revision)

    def beat(
        self,
        gid: str,
        sequence: int,
        *,
        key: str | None = None,
        client_time: str | None = None,
        who: str = "tomas",
    ) -> dict[str, object]:
        body = {"sequence": sequence, "client_time": client_time or self.client_time()}
        return self.post(
            f"/v1/guided-checks/{gid}/beats",
            who,
            body,
            key or self.key("beat"),
        )

    def teach_to_count(self, gid: str) -> int:
        revision = 1
        for step_no in (1, 2, 3):
            response = self.act(gid, "step_done", revision, step_no=step_no)
            assert response["_status"] == 200, response
            revision = cast(int, response["revision"])
        started = self.act(gid, "start_count", revision)
        assert started["_status"] == 200, started
        assert started["state"] == "counting"
        return cast(int, started["revision"])

    def tap(self, gid: str, beats: int, *, seconds: int = 60, until: int | None = None) -> dict[str, object]:
        """Taps evenly over the window from the start_count reading, then moves past the window end."""
        origin = self.clock.monotonic
        last: dict[str, object] = {}
        for sequence in range(1, beats + 1):
            target = origin + (2 * sequence - 1) * seconds * NS // (2 * beats)
            self.advance(target - self.clock.monotonic)
            last = self.beat(gid, sequence)
            assert last["_status"] == 200, last
            if until is not None and sequence >= until:
                break
        self.advance(origin + seconds * NS + NS // 1000 - self.clock.monotonic)
        return last

    def finish(self, gid: str, *, answer: str = "regular") -> dict[str, object]:
        current = self.get(f"/v1/guided-checks/{gid}", "tomas")
        answered = self.act(gid, "answer", cast(int, current["revision"]), answer=answer)
        assert answered["_status"] == 200, answered
        return self.act(gid, "finish", cast(int, answered["revision"]))

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        return [tuple(row) for row in self.db.execute(sql).fetchall()]


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    built = Stack(tmp_path)
    yield built
    built.db.close()


def plan(stack: Stack) -> None:
    pinned = stack.config.active()
    stack.plans.steps["prn_1"] = GuidedStepRef(
        "prn_1", "mara", "epi_1", "stp_check", "env_cabin", pinned.version_of(ConfigName.SKILLS_CATALOG),
        pinned.version_of(ConfigName.UI_COPY), True, True,
    )  # fmt: skip


def run_count(stack: Stack, beats: int) -> tuple[str, dict[str, object]]:
    started = stack.start()
    assert started["_status"] == 201, started
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    stack.tap(gid, beats)
    return gid, stack.finish(gid)


def valid(body: dict[str, object]) -> GuidedCheckModel:
    return GuidedCheckModel.model_validate({k: v for k, v in body.items() if k != "_status"})


def error(body: dict[str, object], code: ErrorCode) -> ErrorEnvelope:
    envelope = ErrorEnvelope.model_validate({k: v for k, v in body.items() if k != "_status"})
    assert body["code"] == code.value, body
    return envelope


# The three verdicts --------------------------------------------------------------------------------------------


@pytest.mark.trace("T30-01", "T30-23", "D37", "D38", "V85")
def test_agree_count_92_against_cameras_96(stack: Stack) -> None:
    gid, done = run_count(stack, 92)
    assert done["_status"] == 200, done
    valid(done)
    assert done["state"] == "checked" and done["result"] == "agree"
    assert done["result_text"] == "Your count 92 · cameras 96 · they agree"
    assert done["beat_count"] == 92 and done["count_per_minute"] == 92 and done["difference"] == -4
    assert done["tolerance"] == {
        "value": 10,
        "unit": "beats per minute",
        "basis": "team default",
        "to_be_set_by": "clinical review",
    }
    assert done["camera_reference"] == {
        "observation_id": "obs_cam_1",
        "value": 96,
        "unit": "/min",
        "no_reading_reason": None,
        "origin_mode": "LIVE",
    }
    assert done["allowed_actions"] == [] and done["observation_id"] is not None
    observation = stack.rows(
        "SELECT patient_id, code, value, unit, observed_at, method, context_json FROM test_observation"
    )
    window_end = (START + timedelta(seconds=60)).isoformat()
    assert observation[0][:6] == ("mara", "pulse", "92", "/min", window_end, "crewmate_palpation")
    context = json.loads(cast(str, observation[0][6]))
    assert context == {"camera_agreement": "agree", "count_seconds": "60", "guided_check_id": gid}
    assert len(observation) == 1
    answers = stack.rows("SELECT record_id, question_id, value, presence FROM test_answer")
    assert answers == [("mara", "pulse_check_wrist_q5", "regular", "present")]
    checked = [e for e in stack.sink.events if e.type is EventType.GUIDED_CHECK_CHECKED]
    assert sorted(e.scope.person_id or "" for e in checked) == ["mara", "tomas"]
    valid(json.loads(checked[0].data_json))
    completed = [e for e in stack.sink.published if isinstance(e, GuidedCheckCompleted)]
    assert completed == [GuidedCheckCompleted(gid, None, None, "agree", (cast(str, done["observation_id"]),))]


@pytest.mark.trace("T30-02", "T30-03", "D39", "V86", "I30-03")
def test_disagree_keeps_both_readings_and_never_completes(stack: Stack) -> None:
    plan(stack)
    started = stack.start("tomas", plan_run_id="prn_1", role_assignment_id="rol_tomas")
    assert started["_status"] == 201, started
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    stack.tap(gid, 65)
    done = stack.finish(gid)
    assert done["_status"] == 200, done
    valid(done)
    assert done["state"] == "retry" and done["result"] == "disagree" and done["difference"] == -31
    assert done["result_text"] == (
        "Your count is 31 lower than the cameras. Move your fingers a little toward the thumb, and count again."
    )
    assert done["observation_id"] is None  # schema interim X16; the Observation is still written (Q8, OD1)
    written = stack.rows("SELECT value, context_json FROM test_observation")
    assert written[0][0] == "65" and json.loads(cast(str, written[0][1]))["camera_agreement"] == "disagree"
    completed = [e for e in stack.sink.published if isinstance(e, GuidedCheckCompleted)]
    assert [(c.outcome, c.plan_run_id, c.step_id) for c in completed] == [("disagree", "prn_1", "stp_check")]
    assert done["allowed_actions"] == ["retry", "cancel"]
    retried = stack.act(gid, "retry", cast(int, done["revision"]))
    assert retried["_status"] == 200 and retried["state"] == "counting" and retried["beat_count"] == 0
    assert len(stack.camera.requests) == 2 and stack.camera.requests[1].attempt_no == 2
    stack.tap(gid, 94)
    again = stack.finish(gid)
    assert again["result"] == "agree" and again["state"] == "checked", again
    supersedes = stack.rows("SELECT supersedes FROM test_answer ORDER BY rowid")
    assert supersedes[0][0] is None and supersedes[1][0] is not None


@pytest.mark.trace("T30-04", "T30-05", "D39", "V87", "I30-07")
@pytest.mark.parametrize("mode", ["absent", "no_reading", "raise", "sim_origin"])
def test_no_reference_keeps_the_count_unchecked(tmp_path: Path, mode: str) -> None:
    stack = Stack(tmp_path, camera=mode != "absent")
    stack.camera.mode = "no_reading" if mode == "no_reading" else "raise" if mode == "raise" else "reading"
    if mode == "sim_origin":
        stack.camera.origin = "SIM"
    gid, done = run_count(stack, 92)
    assert done["_status"] == 200, done
    valid(done)
    assert done["result"] == "no_reference" and done["state"] == "checked"
    reason = "too much movement" if mode == "no_reading" else "Not available here"
    assert done["result_text"] == f"Cameras: No reading, {reason}. Your count is kept, unchecked."
    camera = cast(dict[str, object], done["camera_reference"])
    assert (
        camera["value"] is None and camera["no_reading_reason"] == reason
    )  # never a fabricated camera value
    assert done["difference"] is None and done["count_per_minute"] == 92
    assert stack.rows("SELECT value FROM test_observation") == [("92",)]
    stack.db.close()


# Duplicate beats ------------------------------------------------------------------------------------------------


@pytest.mark.trace("T30-17", "T30-35")
def test_duplicate_beats(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    stack.advance(NS // 2)
    first = stack.beat(gid, 1, key="k1", client_time="2026-11-14T10:00:00.500Z")
    replay = stack.beat(gid, 1, key="k1", client_time="2026-11-14T10:00:00.500Z")
    assert first["beat_count"] == 1 and replay["beat_count"] == 1 and replay["_status"] == 200
    resent = stack.beat(gid, 1, key="k2", client_time="2026-11-14T10:00:00.500Z")
    assert resent["_status"] == 200 and resent["beat_count"] == 1
    reused = stack.beat(gid, 2, key="k1")
    error(reused, ErrorCode.DUPLICATE_COMMAND)
    assert stack.rows("SELECT COUNT(*) FROM teach_beat") == [(1,)]
    beat_events = [e for e in stack.sink.events if e.type is EventType.GUIDED_CHECK_BEAT]
    assert len(beat_events) == 2  # one accepted beat, delivered to helper and subject
    collision = stack.beat(gid, 1, key="k3", client_time="2026-11-14T10:00:00.700Z")
    envelope = error(collision, ErrorCode.INVALID_TRANSITION)
    assert envelope.current_state is not None
    stack.tap(gid, 0)
    done = stack.finish(gid)
    fields = [
        f["field"]
        for f in cast(list[dict[str, str]], error(done, ErrorCode.INVALID_VALUE).model_dump()["field_errors"])
    ]
    assert "/beats/sequence_conflict" in fields
    assert stack.rows("SELECT COUNT(*) FROM test_observation") == [(0,)]


# Clock jumps and restarts ---------------------------------------------------------------------------------------


@pytest.mark.trace("T30-07", "I30-05", "H11")
@pytest.mark.parametrize("jump_s", [30, -30])
def test_wall_clock_jump_during_count_fails_the_capture(stack: Stack, jump_s: int) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    anchor = stack.clock.at
    stack.advance(NS // 2)
    first = stack.beat(gid, 1)
    assert cast(list[str], first["beats"])[0] == "2026-11-14T10:00:00.500Z" and anchor == START
    stack.advance(NS // 2, wall_ns=NS // 2 + jump_s * NS)
    jumped = stack.beat(gid, 2)
    assert jumped["_status"] == 200
    assert (
        cast(list[str], jumped["beats"])[1] == "2026-11-14T10:00:01.000Z"
    )  # hub elapsed time, not the stepped wall clock
    for sequence in range(3, 119):
        stack.advance(NS // 2)
        assert stack.beat(gid, sequence)["_status"] == 200
    stack.advance(NS)
    done = stack.finish(gid)
    fields = [f.field for f in error(done, ErrorCode.INVALID_VALUE).field_errors]
    assert fields == ["/beats/clock_discontinuity"]


@pytest.mark.trace("T30-07", "CR4")
def test_hub_restart_during_count_is_recorded_and_refused(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    stack.advance(NS // 2)
    stack.beat(gid, 1)
    stack.clock.epoch = "gen2"
    stack.clock.monotonic = NS  # a new process starts its monotonic clock again
    refused = stack.beat(gid, 2)
    error(refused, ErrorCode.INVALID_TRANSITION)
    flags = stack.rows("SELECT flags_json FROM teach_attempt")
    assert flags == [('["hub_restart"]',)]  # the refusal committed its mark (3.3 step 4)
    stack.clock.at = START + timedelta(seconds=61)
    done = stack.finish(gid)
    fields = [f.field for f in error(done, ErrorCode.INVALID_VALUE).field_errors]
    assert "/beats/hub_restart" in fields


@pytest.mark.trace("T30-18", "T30-34", "H4")
def test_interrupted_count_never_becomes_a_rate(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    stack.tap(gid, 92, until=46)
    done = stack.finish(gid)
    fields = [f.field for f in error(done, ErrorCode.INVALID_VALUE).field_errors]
    assert fields == ["/beats/lost_end"]
    assert done["supported_alternative"] is None  # the package ui-copy has no guided_check_words.count_again
    current = stack.get(f"/v1/guided-checks/{gid}", "tomas")
    valid(current)
    assert current["state"] == "retry" and current["count_per_minute"] is None and current["result"] is None
    assert current["allowed_actions"] == ["retry", "cancel"]  # never finish again on this attempt
    assert stack.rows("SELECT status, flags_json FROM teach_attempt") == [("incomplete", '["lost_end"]')]
    assert stack.rows("SELECT COUNT(*) FROM test_observation") == [(0,)]
    retried = stack.act(gid, "retry", cast(int, current["revision"]))
    assert retried["state"] == "counting" and retried["beat_count"] == 0
    stack.tap(gid, 92)
    assert stack.finish(gid)["result"] == "agree"


# Versions, idempotent finish and a crash inside finish ----------------------------------------------------------


@pytest.mark.trace("T30-16", "T30-17", "H13")
def test_finish_rolls_back_on_storage_failure_and_replays(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    stack.teach_to_count(gid)
    stack.tap(gid, 92)
    current = stack.get(f"/v1/guided-checks/{gid}", "tomas")
    answered = stack.act(gid, "answer", cast(int, current["revision"]), answer="irregular")
    revision = cast(int, answered["revision"])
    missing = stack.post(
        f"/v1/guided-checks/{gid}/actions",
        "tomas",
        {"action": "finish", "client_time": stack.client_time()},
        "fin-0",
    )
    error(missing, ErrorCode.VERSION_REQUIRED)
    stale = stack.act(gid, "finish", revision - 1, key="fin-1")
    assert error(stale, ErrorCode.STALE_VERSION).current_state is not None
    stack.observations.fail_next = True
    crashed = stack.act(gid, "finish", revision, key="fin-2")
    assert crashed["_status"] == 500 and crashed["retryable"] is True
    assert stack.get(f"/v1/guided-checks/{gid}", "tomas")["state"] == "counting"
    assert stack.rows("SELECT COUNT(*) FROM test_observation") == [(0,)]
    done = stack.act(gid, "finish", revision, key="fin-2")
    replay = stack.act(gid, "finish", revision, key="fin-2")
    assert done["state"] == "checked" and replay["_status"] == 200 and replay["revision"] == done["revision"]
    assert stack.rows("SELECT COUNT(*) FROM test_observation") == [(1,)]
    assert len([e for e in stack.sink.published if isinstance(e, GuidedCheckCompleted)]) == 1


# Privacy between crew members ----------------------------------------------------------------------------------


@pytest.mark.trace("T30-15", "H12", "H14", "I30-11")
def test_privacy_between_crew_members(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    revision = stack.teach_to_count(gid)
    stack.advance(NS // 2)
    stack.beat(gid, 1)
    for response in (
        stack.get(f"/v1/guided-checks/{gid}", "ana"),
        stack.get(f"/v1/guided-checks/{gid}/steps", "ana"),
        stack.beat(gid, 2, who="ana"),
        stack.act(gid, "cancel", revision, who="ana"),
    ):
        envelope = error(response, ErrorCode.UNAUTHORIZED_SCOPE)
        text = json.dumps(envelope.model_dump(mode="json"))
        assert "Mara" not in text and "Tomas" not in text and "pulse" not in text
    error(stack.beat(gid, 2, who="mara"), ErrorCode.UNAUTHORIZED_SCOPE)  # the subject does not tap
    error(stack.beat(gid, 2, who="assistant"), ErrorCode.ACTION_NOT_ALLOWED)
    error(stack.start("ana"), ErrorCode.UNAUTHORIZED_SCOPE)  # nobody starts a check on another person
    assert stack.get(f"/v1/guided-checks/{gid}", "mara")["_status"] == 200
    events = [e for e in stack.sink.events if e.audience.object_ref == gid]
    sessions = Authenticator().sessions
    snapshot = Snapshot(stack)
    assert events
    for event in events:
        assert stack.audience.admits(snapshot, sessions["tok_tomas"], event.audience)
        assert stack.audience.admits(snapshot, sessions["tok_mara"], event.audience)
        assert not stack.audience.admits(snapshot, sessions["tok_ana"], event.audience)
    cancelled = stack.act(
        gid, "cancel", cast(int, stack.get(f"/v1/guided-checks/{gid}", "mara")["revision"]), who="mara"
    )
    assert cancelled["state"] == "cancelled" and cancelled["beats"] == []
    assert stack.camera.abandoned == ["tkt_1"]


@pytest.mark.trace("T30-13", "H3")
def test_live_count_on_a_sim_persona_goes_to_the_operator_record(stack: Stack) -> None:
    stack.routing.routes["mara"] = "op_other"
    gid, done = run_count(stack, 92)
    assert done["result"] == "agree"
    assert stack.rows("SELECT patient_id FROM test_observation") == [("op_other",)]
    assert stack.camera.requests[0].record_id == "op_other"
    completed = [e for e in stack.sink.published if isinstance(e, GuidedCheckCompleted)]
    assert completed[0].observation_ids == ()  # the persona's plan never references another record
    scopes = sorted({e.scope.person_id or "" for e in stack.sink.events})
    assert scopes == ["op_other", "tomas"]


@pytest.mark.trace("T30-08", "I30-17")
def test_steps_render_from_the_pinned_catalog(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    steps = stack.get(f"/v1/guided-checks/{gid}/steps", "tomas")
    teach_steps = cast(list[dict[str, object]], steps["teach_steps"])
    assert [s["kind"] for s in teach_steps] == [
        "instruction",
        "instruction",
        "instruction",
        "count",
        "question",
    ]
    assert cast(str, teach_steps[1]["text"]).startswith(
        "Tomas: place the pads of your first and middle fingers"
    )
    assert "Mara's wrist" in cast(str, teach_steps[1]["text"])
    assert steps["notice"] == "Unreviewed draft: needs clinician review"


@pytest.mark.trace("T30-14")
def test_start_refusals(stack: Stack) -> None:
    error(stack.start(helper_person_id="mara"), ErrorCode.ROLE_NOT_ELIGIBLE)
    error(stack.start(skill_id="no_such_skill"), ErrorCode.INVALID_VALUE)
    error(stack.start(skill_id="eye_rinse"), ErrorCode.CONTEXT_INAPPLICABLE)
    error(stack.start(drill_id="drl_1"), ErrorCode.CONTEXT_INAPPLICABLE)
    error(stack.start("mara", plan_run_id="prn_unknown"), ErrorCode.CONTEXT_INAPPLICABLE)
    stack.eligibility.outcome = "not_here"
    error(stack.start(), ErrorCode.CONTEXT_INAPPLICABLE)
    stack.eligibility.outcome = "show"
    assert stack.start()["_status"] == 201
    error(stack.start(), ErrorCode.CONTEXT_INAPPLICABLE)  # one active check per subject and skill
    response = stack.client.post(
        "/v1/guided-checks",
        content=json.dumps(
            {"skill_id": "pulse_check_wrist", "helper_person_id": "tomas", "subject_person_id": "ana"}
        ),
        headers={"Authorization": "Bearer tok_ana", "Idempotency-Key": "s1", "Config-Release": "rel_old"},
    )
    assert response.status_code == 409 and response.json()["code"] == "CONFIG_VERSION_MISMATCH"
    assert stack.client.get("/v1/guided-checks/gck_none").status_code == 401


@pytest.mark.trace("T30-36", "I30-14")
def test_coaching_samples_page_and_privacy(stack: Stack) -> None:
    samples = SqliteCoachingSamples()
    with Uows(stack).begin(actor=Actor("engine", "teach"), correlation_id="cor_1") as uow:
        for i in range(5):
            rate = None if i == 2 else Decimal(100 + i)
            sample = StoredSample(
                f"cs_{i}", "hs_1", "cpr", "cpr_rate", START + timedelta(seconds=i), rate,
                "cannot_see_hands" if rate is None else None, None if rate is None else True, None, "text", True,
            )  # fmt: skip
            samples.add(uow, sample, release_id="rel_1")
    error(stack.get("/v1/help-sessions/hs_1/coaching", "ana"), ErrorCode.NOT_FOUND)
    stack.help.readers["hs_1"] = {"mara"}
    first = stack.get("/v1/help-sessions/hs_1/coaching?limit=2", "mara")
    items = cast(list[dict[str, object]], first["items"])
    assert [i["sample_id"] for i in items] == ["cs_0", "cs_1"] and items[0]["rate_per_min"] == 100
    cursor = cast(dict[str, str], first["page"])["next_cursor"]
    second = stack.get(f"/v1/help-sessions/hs_1/coaching?limit=3&cursor={cursor}", "mara")
    rest = cast(list[dict[str, object]], second["items"])
    assert [i["sample_id"] for i in rest] == ["cs_2", "cs_3", "cs_4"]
    assert rest[0]["rate_per_min"] is None and rest[0]["in_band"] is None  # no rate, no number, no band
    assert cast(dict[str, object], second["page"])["next_cursor"] is None


@pytest.mark.trace("T30-20", "H6", "H7")
def test_coaching_recorder_stores_and_emits_only_fresh_samples(stack: Stack) -> None:
    r = stack.resolver
    settings = TeachSettings.read(r.require(HubSettings))
    ports = TeachPorts(
        uow=r.require(UnitOfWorkFactory), snapshots=r.require(ReadSnapshotFactory), clock=r.require(Clock),
        ids=r.require(IdFactory), settings=settings, releases=r.require(ConfigReleaseReader),
        content=ContentLoader(r.require(ConfigReader), max_cached=2), renderer=r.require(TemplateRenderer),
        idempotency=r.require(IdempotencyStore), receipts=r.require(CommandReceipts), units=r.require(UnitRegistry),
        repository=SqliteGuidedChecks(), observations=r.require(ObservationCommands),
        answers=r.require(AnswerCommands), eligibility=r.require(GuidanceEligibility),
        people=r.require(PersonDirectory), routing=r.require(RecordRouting), plans=None, camera=None, roles=None,
    )  # fmt: skip
    recorder = CoachingSamples(TeachCore(ports), SqliteCoachingSamples(), stack.help)
    pins = ContentLoader.pins_of(stack.config.active())
    with Uows(stack).begin(actor=Actor("engine", "teach"), correlation_id="cor_2") as uow:
        fresh = recorder.record(uow, "hs_2", "cpr", RawRateSample(START, 10, Fraction(108), None), pins)
        stack.advance(NS)
        blind = recorder.record(
            uow,
            "hs_2",
            "cpr",
            RawRateSample(START + timedelta(seconds=1), 10, None, "cannot_see_hands"),
            pins,
        )
        repeat = recorder.record(uow, "hs_2", "cpr", RawRateSample(START, 10, Fraction(90), None), pins)
    assert fresh is not None and fresh.in_band is True and fresh.text == "Cameras: 108 a minute · in range"
    assert blind is not None and blind.rate_per_min is None and blind.text == "Cameras: can't see the hands"
    assert repeat is None  # not newer than the last stored sample: dropped, never shown as current
    emitted = [e for e in stack.sink.events if e.type is EventType.COACHING_SAMPLE]
    assert [e.scope.kind for e in emitted] == ["crew", "crew"]


# Fix pass: findings of the independent review ------------------------------------------------------------------


def start_as(stack: Stack, who: str, helper: str, **extra: object) -> dict[str, object]:
    body = {"skill_id": "pulse_check_wrist", "helper_person_id": helper, "subject_person_id": "mara", **extra}
    return stack.post("/v1/guided-checks", who, body, stack.key("start"))


@pytest.mark.trace("T30-15", "H14", "D9")
def test_nobody_self_appoints_as_helper_on_a_plan_run(stack: Stack) -> None:
    plan(stack)
    error(start_as(stack, "ana", "ana", plan_run_id="prn_1"), ErrorCode.UNAUTHORIZED_SCOPE)
    for assignment in ("rol_tomas", "rol_ana_proposed", "rol_ana_other_run", "rol_unknown"):
        refused = start_as(stack, "ana", "ana", plan_run_id="prn_1", role_assignment_id=assignment)
        error(refused, ErrorCode.UNAUTHORIZED_SCOPE)
    error(start_as(stack, "tomas", "tomas", plan_run_id="prn_1"), ErrorCode.UNAUTHORIZED_SCOPE)
    error(start_as(stack, "station", "tomas", plan_run_id="prn_1"), ErrorCode.UNAUTHORIZED_SCOPE)
    error(
        start_as(stack, "mara", "ana", plan_run_id="prn_1", role_assignment_id="rol_tomas"),
        ErrorCode.UNAUTHORIZED_SCOPE,
    )
    assert stack.rows("SELECT COUNT(*) FROM teach_guided_check") == [(0,)]  # nothing written
    station = start_as(stack, "station", "tomas", plan_run_id="prn_1", role_assignment_id="rol_tomas")
    assert station["_status"] == 201, station


def test_without_the_roles_port_only_the_subject_starts(tmp_path: Path) -> None:
    stack = Stack(tmp_path, roles=False)
    plan(stack)
    refused = start_as(stack, "tomas", "tomas", plan_run_id="prn_1", role_assignment_id="rol_tomas")
    error(refused, ErrorCode.UNAUTHORIZED_SCOPE)
    assert start_as(stack, "mara", "tomas")["_status"] == 201
    stack.db.close()


@pytest.mark.trace("T30-01", "I30-06")
@pytest.mark.parametrize(
    ("camera", "result", "difference", "text"),
    [
        ("96.4", "disagree", -10.4, "Your count is 10.4 lower than the cameras."),
        ("75.6", "disagree", 10.4, "Your count is 10.4 higher than the cameras."),
        ("95.6", "agree", -10, "Your count 86 · cameras 96 · they agree"),
        ("96", "agree", -10, "Your count 86 · cameras 96 · they agree"),
    ],
)
def test_shown_numbers_never_contradict_the_verdict(
    tmp_path: Path, camera: str, result: str, difference: float, text: str
) -> None:
    stack = Stack(tmp_path)
    stack.camera.value = Decimal(camera)
    _, done = run_count(stack, 86)
    assert done["result"] == result and done["difference"] == difference, done
    assert cast(str, done["result_text"]).startswith(text)
    valid(done)
    stack.db.close()


@pytest.mark.trace("T30-16", "H13")
def test_lost_ticket_save_is_recovered_by_the_same_key(stack: Stack) -> None:
    started = stack.start()
    gid = cast(str, started["guided_check_id"])
    revision = 1
    for step_no in (1, 2, 3):
        revision = cast(int, stack.act(gid, "step_done", revision, step_no=step_no)["revision"])

    def fail_t2() -> None:
        stack.uows.fail_next = True

    stack.camera.after_begin = fail_t2
    failed = stack.act(gid, "start_count", revision, key="count-1")
    assert failed["_status"] == 500 and failed["retryable"] is True
    assert stack.camera.abandoned == ["tkt_1"]
    assert stack.rows("SELECT camera_ticket_id FROM teach_attempt") == [(None,)]
    stack.camera.after_begin = None
    replayed = stack.act(gid, "start_count", revision, key="count-1")
    assert replayed["_status"] == 200 and replayed["state"] == "counting"
    assert stack.rows("SELECT camera_ticket_id FROM teach_attempt") == [("tkt_2",)]
    again = stack.act(gid, "start_count", revision, key="count-1")
    assert again["_status"] == 200 and len(stack.camera.requests) == 2  # a stored ticket is never asked again
    stack.tap(gid, 92)
    assert stack.finish(gid)["result"] == "agree"


def test_config_release_header_on_every_check_response(stack: Stack) -> None:
    headers = {"Authorization": "Bearer tok_mara", "Idempotency-Key": "h1"}
    body = {"skill_id": "pulse_check_wrist", "helper_person_id": "tomas", "subject_person_id": "mara"}
    started = stack.client.post("/v1/guided-checks", content=json.dumps(body), headers=headers)
    gid = started.json()["guided_check_id"]
    tomas = {"Authorization": "Bearer tok_tomas"}
    read = stack.client.get(f"/v1/guided-checks/{gid}", headers=tomas)
    action = stack.client.post(
        f"/v1/guided-checks/{gid}/actions",
        content=json.dumps({"action": "step_done", "client_time": stack.client_time(), "step_no": 1}),
        headers={**tomas, "Idempotency-Key": "h2", "If-Match": '"1"'},
    )
    for response in (started, read, action):
        assert response.headers["Config-Release"] == "rel_1" and response.headers["ETag"]
    stack.help.readers["hs_1"] = {"mara"}
    listed = stack.client.get("/v1/help-sessions/hs_1/coaching", headers={"Authorization": "Bearer tok_mara"})
    assert listed.headers["Config-Release"] == "rel_1"
