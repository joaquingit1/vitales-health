"""Test-only wiring for the help module: SQLite storage on a tmp file, a durable command ledger, kernel fakes and
small fakes of the optional ports. Production code never imports this file.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.help.adapters.sqlite_repository import SqliteGroundMessages, SqliteHelpSessions
from crew_health.help.api.router import HelpRuntime, build_router
from crew_health.help.application import ports
from crew_health.help.application.service import HelpService
from crew_health.help.application.settings import help_setting_defaults, help_setting_specs
from crew_health.help.application.support import HelpDeps
from crew_health.help.domain.docs import ConfigPin, PinnedHelpDocs, pin_documents
from crew_health.help.domain.session import HelpLimits
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import CommandIdentity
from crew_health.kernel.config import ConfigDocumentView, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MetLabel, MissionClock, MissionClockVersion
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.storage import SqlValue, StatementResult, StorageSession
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryConfig,
    MemoryMissionClock,
    MemoryRelease,
    MemorySettings,
)
from crew_health.kernel.uow import (
    Actor,
    DomainEvent,
    OutboxEvent,
    ReadSnapshot,
    ReplicationChange,
    ReplicationRef,
    SessionContext,
    StoredEvent,
    UnitOfWork,
)
from crew_health.platform.adapters.ledger import CommandLedger

REPO = Path(__file__).resolve().parents[3]
MIGRATION = REPO / "hub/src/crew_health/help/adapters/migrations/0001_help.sql"
EXAMPLES = REPO / "contracts/api/examples"
SCHEMAS = REPO / "contracts/api/schemas"
EPOCH = datetime(2026, 11, 13, tzinfo=UTC)
T0 = datetime(2026, 11, 13, 12, 2, tzinfo=UTC)
HELP_DOC_NAMES = (ConfigName.RESPONDER_FLOWS, ConfigName.HELP_PRESENTATION, ConfigName.UI_COPY)


def example_value(relative: str) -> dict[str, object]:
    loaded = json.loads((EXAMPLES / relative).read_bytes())
    value = loaded.get("value", loaded) if isinstance(loaded, dict) else loaded
    if not isinstance(value, dict):
        raise TypeError(relative)
    return value


def config_raw(name: str) -> bytes:
    return (REPO / "config" / f"{name}.json").read_bytes()


def ui_copy_raw() -> bytes:
    return json.dumps(example_value("config/ui-copy.example.json"), indent=2).encode()


def document(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def release(
    release_id: str,
    *,
    flows: bytes | None = None,
    presentation: bytes | None = None,
    ui: bytes | None = None,
) -> MemoryRelease:
    raws = {
        ConfigName.RESPONDER_FLOWS: flows if flows is not None else config_raw("responder-flows"),
        ConfigName.HELP_PRESENTATION: presentation
        if presentation is not None
        else config_raw("help-presentation"),
        ConfigName.UI_COPY: ui if ui is not None else ui_copy_raw(),
    }
    return MemoryRelease(release_id, {name: document(name, raw) for name, raw in raws.items()})


def pinned_docs(rel: MemoryRelease | None = None) -> PinnedHelpDocs:
    chosen = rel if rel is not None else release("rel_test_1")
    views = [chosen.document(name) for name in HELP_DOC_NAMES]
    return pin_documents(
        chosen.release_id,
        flows_raw=views[0].raw,
        presentation_raw=views[1].raw,
        ui_raw=views[2].raw,
        pins=tuple(ConfigPin(v.name.value, v.version) for v in views),
    )


def limits() -> HelpLimits:
    values = help_setting_defaults()

    def num(key: str) -> int:
        value = values[key]
        if not isinstance(value, int):
            raise TypeError(key)
        return value

    return HelpLimits(
        log_soft_cap_bytes=num("help.log_soft_cap_bytes"),
        log_hard_cap_bytes=num("help.log_hard_cap_bytes"),
        path_max_entries=num("help.path_max_entries"),
        note_max_chars=num("help.note_max_chars"),
        place_max_chars=num("help.note_max_chars"),
        offline_path_max=num("help.offline_path_max"),
    )


# Storage: a StorageSession over sqlite3, one connection per transaction, BEGIN IMMEDIATE for writers.


class SqliteSession(StorageSession):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        cursor = self._connection.execute(sql, parameters)
        return StatementResult(cursor.rowcount, cursor.lastrowid)

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        cursor = self._connection.execute(sql, parameters)
        return tuple(tuple(row) for row in cursor.fetchmany(limit))


PLATFORM_MIGRATION = REPO / "hub/src/crew_health/platform/adapters/migrations/0001_platform.sql"
_LEDGER_TABLES = ("platform_command_ledger ", "platform_idempotency ")


def _ledger_ddl() -> tuple[str, ...]:
    """E01's own command ledger tables and append-only triggers, taken from its migration."""
    lines = PLATFORM_MIGRATION.read_text(encoding="utf-8").splitlines()
    return tuple(
        line for line in lines if any(t in line for t in _LEDGER_TABLES) and line.startswith("CREATE ")
    )


def create_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(MIGRATION.read_text())
        for statement in _ledger_ddl():
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


class TestView:
    def __init__(self, connection: sqlite3.Connection, rel: ReleaseHandle, now: datetime) -> None:
        self._session = SqliteSession(connection)
        self._release = rel
        self._now = now

    @property
    def connection(self) -> StorageSession:
        return self._session

    @property
    def release(self) -> ReleaseHandle:
        return self._release

    @property
    def now(self) -> datetime:
        return self._now


class TestUow(TestView):
    def __init__(
        self,
        connection: sqlite3.Connection,
        rel: ReleaseHandle,
        now: datetime,
        actor: Actor,
        correlation_id: str,
    ) -> None:
        super().__init__(connection, rel, now)
        self._actor = actor
        self._correlation_id = correlation_id
        self.events: list[StoredEvent] = []
        self.audits: list[AuditEntry] = []
        self.replicated: list[ReplicationChange] = []
        self.published: list[DomainEvent] = []

    @property
    def actor(self) -> Actor:
        return self._actor

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    def publish(self, event: DomainEvent) -> None:
        self.published.append(event)

    def emit(self, event: OutboxEvent) -> StoredEvent:
        stored = StoredEvent(
            len(self.events) + 1,
            f"evt_{len(self.events) + 1}",
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
        raise NotImplementedError("help schedules nothing")

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        raise NotImplementedError("help schedules nothing")

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        """Help replicates only the local-only references its audit entries name (help.local audience)."""
        if change.audience is None or change.audience.rule != "help.local":
            raise NotImplementedError("help replicates only its local audit references")
        self.replicated.append(change)
        return ReplicationRef("node_test", "incarnation_test", len(self.replicated))

    @contextmanager
    def savepoint(self, name: str) -> Iterator[None]:
        self._session.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self._session.execute(f"ROLLBACK TO {name}")
            raise
        self._session.execute(f"RELEASE {name}")


class TestStore:
    """UnitOfWorkFactory and ReadSnapshotFactory over one SQLite file; events and audit kept only on commit."""

    def __init__(self, path: Path, clock: MemoryClock, config: MemoryConfig) -> None:
        self.path, self.clock, self.config = path, clock, config
        self.events: list[StoredEvent] = []
        self.audits: list[AuditEntry] = []
        self.fail_commit = False
        self.transactions = 0

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        uow = TestUow(connection, self.config.active(), self.clock.now(), actor, correlation_id)
        try:
            yield uow
            if self.fail_commit:
                raise sqlite3.OperationalError("injected commit failure")
            connection.execute("COMMIT")
            self.transactions += 1
            self.events.extend(uow.events)
            self.audits.extend(uow.audits)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @contextmanager
    def snapshot(self) -> Iterator[ReadSnapshot]:
        connection = self._connect()
        connection.execute("BEGIN")
        try:
            yield TestView(connection, self.config.active(), self.clock.now())
        finally:
            connection.execute("ROLLBACK")
            connection.close()

    def audit_types(self) -> list[str]:
        return [a.event_type for a in self.audits]

    def count(self, table: str) -> int:
        connection = self._connect()
        try:
            row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            return int(row[0])
        finally:
            connection.close()


class CountingIds:
    def __init__(self) -> None:
        self.issued = 0

    def new(self, prefix: str) -> str:
        self.issued += 1
        return f"{prefix}_{self.issued:05d}"

    def derived(self, prefix: str, *parts: str) -> str:
        return f"{prefix}_{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]}"


def mission_clock() -> MemoryMissionClock:
    return MemoryMissionClock([MissionClockVersion("mc1", 1, EPOCH, EPOCH, None, "Day {day}")])


# Fakes of the optional ports.


@dataclass(frozen=True, slots=True)
class Member:
    person_id: str
    display_name: str


CREW_NAMES = {"mara": "Mara Okafor", "tomas": "Tomas Lind", "ilya": "Ilya Brandt", "noor": "Noor Haddad"}


class Roster:
    def __init__(self, names: Mapping[str, str] = CREW_NAMES) -> None:
        self.names = dict(names)

    def crew(self, at: datetime) -> Sequence[ports.CrewMember]:
        return tuple(Member(p, n) for p, n in sorted(self.names.items()))


class FailingPort:
    """Every call raises: an optional provider that is present but broken."""

    def __init__(self) -> None:
        self.calls = 0

    def _fail(self) -> RuntimeError:
        self.calls += 1
        return RuntimeError("optional provider down")

    def crew(self, at: datetime) -> Sequence[ports.CrewMember]:
        raise self._fail()

    def current(self, uow: ReadSnapshot | None = None) -> ports.MissionFacts | None:
        raise self._fail()

    def roles_for_session(self, help_session_id: str) -> Sequence[ports.BroadcastRole]:
        raise self._fail()

    def start_help_turn(
        self, help_session_id: str, help_turn_id: str, actor: Actor, text: str, conversation_id: str | None
    ) -> ports.AssistantStart:
        raise self._fail()

    def cancel_help_turn(self, help_turn_id: str) -> None:
        raise self._fail()

    def visible(self, session: SessionContext, warning_id: str) -> bool:
        raise self._fail()

    def link(self) -> ports.LinkSnapshot:
        raise self._fail()

    def for_help(self, snapshot: ports.HelpLogSnapshotView) -> ports.CatchUpLines:
        raise self._fail()

    def version_at(self, at: datetime) -> MissionClockVersion:
        raise self._fail()

    def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
        raise self._fail()

    def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]:
        raise self._fail()

    def mission_day(self, at: datetime, *, version: str | None = None) -> int:
        raise self._fail()

    def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]:
        raise self._fail()

    def latest_accepted(
        self, person_id: str, codes: Sequence[str], since: datetime, until: datetime, limit_per_code: int
    ) -> Sequence[ports.ReadingView]:
        raise self._fail()

    def allergies_and_medicines(self, person_id: str) -> ports.ProfileFacts:
        raise self._fail()


class SlowPort(FailingPort):
    """Every call sleeps, then fails: an optional provider that hangs (AGENTS rule 3, E26 4.5)."""

    def __init__(self, delay_s: float) -> None:
        super().__init__()
        self.delay_s = delay_s

    def _fail(self) -> RuntimeError:
        time.sleep(self.delay_s)
        return super()._fail()


def all_slow(delay_s: float) -> Providers:
    slow = SlowPort(delay_s)
    return Providers(
        mission_clock=slow,
        mission=slow,
        roster=slow,
        roles=slow,
        assistant=slow,
        warnings=slow,
        composer=slow,
        observations=slow,
        profile=slow,
    )


@dataclass
class Assistant:
    """E13 fake: answers `start_help_turn` with a fixed status and records every call."""

    status: Literal["waiting", "stopped", "unavailable"]
    started: list[tuple[str, str]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    turn_ids: list[str] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)

    def start_help_turn(
        self, help_session_id: str, help_turn_id: str, actor: Actor, text: str, conversation_id: str | None
    ) -> ports.AssistantStart:
        self.started.append((help_session_id, text))
        self.turn_ids.append(help_turn_id)
        self.actors.append(actor)
        conversation = "cnv_help_1" if self.status == "waiting" else None
        return ports.AssistantStart(self.status, conversation)

    def cancel_help_turn(self, help_turn_id: str) -> None:
        self.cancelled.append(help_turn_id)


@dataclass(frozen=True, slots=True)
class CatchUp:
    what_happened: tuple[str, ...]
    what_was_done: tuple[str, ...]
    being_watched: tuple[str, ...]
    advice_welcome: tuple[str, ...]


class Composer:
    """E29 fake: the example's link (down, next contact) and the example's catch-up lines."""

    def __init__(self, status: Literal["up", "delayed", "down"] = "down") -> None:
        preview = example_value("help/ground-message-preview.json")
        link = preview["ground_link"]
        catch = preview["catch_up"]
        if not isinstance(link, dict) or not isinstance(catch, dict):
            raise TypeError("example")
        self.contract: dict[str, object] = dict(link)
        self.contract["status"] = status
        self.status: Literal["up", "delayed", "down"] = status
        self.next_contact = datetime.fromisoformat(str(link["next_contact_at"]))
        self.catch = CatchUp(*(tuple(str(x) for x in catch[k]) for k in ports_catch_keys()))
        self.snapshots: list[ports.HelpLogSnapshotView] = []

    def link(self) -> ports.LinkSnapshot:
        return ports.LinkSnapshot(self.status, self.next_contact, True, self.contract)

    def for_help(self, snapshot: ports.HelpLogSnapshotView) -> ports.CatchUpLines:
        self.snapshots.append(snapshot)
        return self.catch


def ports_catch_keys() -> tuple[str, str, str, str]:
    return ("what_happened", "what_was_done", "being_watched", "advice_welcome")


class Observations:
    def __init__(self, rows: Sequence[ports.ReadingView]) -> None:
        self.rows = tuple(rows)
        self.asked: list[str] = []

    def latest_accepted(
        self, person_id: str, codes: Sequence[str], since: datetime, until: datetime, limit_per_code: int
    ) -> Sequence[ports.ReadingView]:
        self.asked.append(person_id)
        return tuple(r for r in self.rows if r.code in codes)


@dataclass
class Providers:
    """The optional providers bound in a harness; all absent by default."""

    mission_clock: MissionClock | None = None
    mission: ports.MissionStatePort | None = None
    roster: ports.RosterPort | None = None
    roles: ports.BroadcastRolesSource | None = None
    assistant: ports.HelpAssistantPort | None = None
    warnings: ports.WarningQueries | None = None
    composer: ports.GroundLogComposer | None = None
    text_rules: ports.TextRulesPort | None = None
    measurement_gate: ports.MeasurementGate | None = None
    observations: ports.ObservationReadPort | None = None
    profile: ports.ProfileFactsPort | None = None


def all_failing() -> Providers:
    broken = FailingPort()
    return Providers(
        mission_clock=broken,
        mission=broken,
        roster=broken,
        roles=broken,
        assistant=broken,
        warnings=broken,
        composer=broken,
        observations=broken,
        profile=broken,
    )


# Sessions and the role gate.


def crew(person: str, *, cmo: bool = False) -> SessionContext:
    roles = frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER} if cmo else {Role.CREW})
    return SessionContext(f"ses_{person}", "crew", Actor("person", person), person, roles)


STATION = SessionContext(
    "ses_station_galley", "station", Actor("station", "st_galley"), None, frozenset({Role.STATION})
)
GROUND = SessionContext(
    "ses_ground", "ground", Actor("ground", "grd_flight_surgeon"), None, frozenset({Role.GROUND})
)
# E13 acting inside Mara's crew session: the actor stays the assistant (rule 4).
ASSISTANT_IN_CREW = SessionContext(
    "ses_mara", "crew", Actor("assistant", "e13"), "mara", frozenset({Role.CREW})
)
SESSIONS: dict[str, SessionContext] = {
    "assistant": ASSISTANT_IN_CREW,
    "mara": crew("mara"),
    "tomas": crew("tomas"),
    "ilya": crew("ilya"),
    "noor": crew("noor"),
    "station": STATION,
    "ground": GROUND,
}


class HeaderGate:
    """E02 `require_roles` stand in: the test names its session in `X-Test-Session`."""

    def session_for(self, request: Request, operation_id: str) -> SessionContext:
        name = request.headers.get("X-Test-Session")
        if name is None or name not in SESSIONS:
            raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
        return SESSIONS[name]


@dataclass
class Harness:
    path: Path
    clock: MemoryClock
    ids: CountingIds
    config: MemoryConfig
    store: TestStore
    ledger: CommandLedger
    optional: Providers
    service: HelpService
    client: TestClient

    def restart(self, optional: Providers | None = None) -> Harness:
        """A new process on the same database file: fresh caches and objects, the same ids and clock."""
        return build(self.path, optional or self.optional, clock=self.clock, ids=self.ids, config=self.config)

    def drain(self) -> None:
        """One help worker pass: the after-commit assistant calls and broadcast refreshes (E26 4.5)."""
        self.service.worker.run_once()

    def advance(self, seconds: float) -> None:
        self.clock.at = self.clock.at + timedelta(seconds=seconds)

    def at(self, value: datetime) -> None:
        self.clock.at = value


def build(
    path: Path,
    optional: Providers | None = None,
    *,
    clock: MemoryClock | None = None,
    ids: CountingIds | None = None,
    config: MemoryConfig | None = None,
) -> Harness:
    if not path.exists():
        create_schema(path)
    chosen = optional if optional is not None else Providers()
    the_clock = clock if clock is not None else MemoryClock(T0, 1, "proc_test")
    the_ids = ids if ids is not None else CountingIds()
    the_config = (
        config if config is not None else MemoryConfig([release("rel_test_1")], active_id="rel_test_1")
    )
    store = TestStore(path, the_clock, the_config)
    ledger = CommandLedger(the_ids)
    deps = HelpDeps(
        uows=store,
        snapshots=store,
        config=the_config,
        settings=MemorySettings(help_setting_specs(), help_setting_defaults()),
        ids=the_ids,
        clock=the_clock,
        commands=ledger,
        receipts=ledger,
        sessions=SqliteHelpSessions(),
        messages=SqliteGroundMessages(),
    )
    deps = replace(
        deps,
        mission_clock=chosen.mission_clock,
        mission=chosen.mission,
        roster=chosen.roster,
        roles=chosen.roles,
        assistant=chosen.assistant,
        warnings=chosen.warnings,
        composer=chosen.composer,
        text_rules=chosen.text_rules,
        measurement_gate=chosen.measurement_gate,
        observations=chosen.observations,
        profile=chosen.profile,
    )
    service = HelpService(deps, the_config)
    runtime = HelpRuntime()
    runtime.bind(service, HeaderGate())
    app = FastAPI()
    app.include_router(build_router(runtime))
    client = TestClient(app, raise_server_exceptions=False)
    return Harness(path, the_clock, the_ids, the_config, store, ledger, chosen, service, client)


def headers(session: str, key: str | None = None, if_match: int | None = None) -> dict[str, str]:
    values = {"X-Test-Session": session}
    if key is not None:
        values["Idempotency-Key"] = key
    if if_match is not None:
        values["If-Match"] = f'"{if_match}"'
    return values


def open_body(
    subject: str = "self",
    person: str | None = None,
    *,
    place: str | None = None,
    entry: str = "button",
    ref: str | None = None,
    flow: str | None = None,
    at: datetime = T0,
    offline: Sequence[tuple[str, int, datetime]] = (),
) -> dict[str, object]:
    who: dict[str, object] = {"kind": subject}
    if person is not None:
        who["person_id"] = person
    origin: dict[str, object] = {"kind": entry}
    if ref is not None:
        origin["ref_id"] = ref
    body: dict[str, object] = {
        "subject": who,
        "entry": origin,
        "client_opened_at": at.isoformat().replace("+00:00", "Z"),
    }
    if place is not None:
        body["place"] = place
    if flow is not None:
        body["flow_id"] = flow
    if offline:
        body["offline_path"] = [
            {"node_id": n, "answer_index": i, "at": t.isoformat().replace("+00:00", "Z")}
            for n, i, t in offline
        ]
    return body


def act_body(action: str, at: datetime = T0, **fields: object) -> dict[str, object]:
    return {"action": action, "client_time": at.isoformat().replace("+00:00", "Z"), **fields}


def json_of(response: httpx.Response) -> dict[str, object]:
    data = response.json()
    if not isinstance(data, dict):
        raise TypeError("expected a JSON object")
    return data


class Driver:
    """Drives the HTTP API like the crew app: If-Match from the last response, a fresh key per command."""

    def __init__(self, harness: Harness) -> None:
        self.h = harness

    def key(self) -> str:
        return self.h.ids.new("key")

    def open(self, who: str, body: dict[str, object], key: str | None = None) -> httpx.Response:
        response: httpx.Response = self.h.client.post(
            "/v1/help-sessions", json=body, headers=headers(who, key or self.key())
        )
        return response

    def post_action(
        self,
        who: str,
        session: Mapping[str, object],
        action: str,
        fields: Mapping[str, object],
        *,
        key: str | None = None,
        if_match: int | None = None,
        at: datetime | None = None,
    ) -> httpx.Response:
        revision = session["revision"]
        if not isinstance(revision, int):
            raise TypeError("revision")
        response: httpx.Response = self.h.client.post(
            f"/v1/help-sessions/{session['help_session_id']}/actions",
            json=act_body(action, at or self.h.clock.now(), **fields),
            headers=headers(who, key or self.key(), revision if if_match is None else if_match),
        )
        return response

    def act(self, who: str, session: Mapping[str, object], action: str, **fields: object) -> httpx.Response:
        return self.post_action(who, session, action, fields)

    def ok(self, who: str, session: Mapping[str, object], action: str, **fields: object) -> dict[str, object]:
        response = self.post_action(who, session, action, fields)
        if response.status_code != 200:
            raise AssertionError(f"{action}: {response.status_code} {response.text}")
        return json_of(response)

    def get(self, who: str, help_session_id: object) -> httpx.Response:
        response: httpx.Response = self.h.client.get(
            f"/v1/help-sessions/{help_session_id}", headers=headers(who)
        )
        return response


EYE_ANSWERS = (("air_safe", 0), ("responding", 0), ("severe_bleeding_check", 1), ("what_happened", 6))


def drive_eye(driver: Driver) -> dict[str, object]:
    """CONTRACTS 16 vector: Mara presses for Tomas at the airlock, four answers reach `eye_injury`, Ilya joins,
    steps 0, 2 and 3 are ticked and the crew is called, at the example's times."""
    h = driver.h
    h.at(datetime(2026, 11, 13, 12, 2, 0, tzinfo=UTC))
    opened = driver.open("mara", open_body("crewmate", "tomas", place="airlock"))
    if opened.status_code != 201:
        raise AssertionError(opened.text)
    session = json_of(opened)
    for second, (node, index) in enumerate(EYE_ANSWERS, start=1):
        h.at(datetime(2026, 11, 13, 12, 2, 5 * second, tzinfo=UTC))
        session = driver.ok("mara", session, "answer", node_id=node, answer_index=index)
    h.at(datetime(2026, 11, 13, 12, 2, 30, tzinfo=UTC))
    session = driver.ok("mara", session, "call_crew")
    h.at(datetime(2026, 11, 13, 12, 2, 50, tzinfo=UTC))
    session = driver.ok("mara", session, "tick_step", step_index=0)
    h.at(datetime(2026, 11, 13, 12, 3, 5, tzinfo=UTC))
    session = driver.ok("mara", session, "tick_step", step_index=2)
    h.at(datetime(2026, 11, 13, 12, 3, 20, tzinfo=UTC))
    session = driver.ok("mara", session, "tick_step", step_index=3)
    h.at(datetime(2026, 11, 13, 12, 4, 10, tzinfo=UTC))
    return driver.ok("ilya", session, "join")


def dig(value: object, *path: str | int) -> object:
    """Walks a decoded JSON body by keys and indexes; a wrong shape is a test failure, never a default."""
    current = value
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list):
                raise TypeError(f"not a list at {key}")
            current = current[key]
        else:
            if not isinstance(current, dict):
                raise TypeError(f"not an object at {key}")
            current = current[key]
    return current


def rows(value: object, *path: str | int) -> list[dict[str, object]]:
    found = dig(value, *path)
    if not isinstance(found, list) or not all(isinstance(row, dict) for row in found):
        raise TypeError("not a list of objects")
    return found


def num(value: object, *path: str | int) -> int:
    found = dig(value, *path)
    if not isinstance(found, int):
        raise TypeError("not an integer")
    return found
