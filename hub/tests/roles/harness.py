"""Test-only wiring: a StorageSession and UnitOfWork over sqlite3 on tmp_path, port fakes and the HTTP app.

Production never binds anything here. Configuration comes from the repository's config/role-rules.json and the
package's example documents (contracts/api/examples/config), so the tests follow the content.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import warnings
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request

from crew_health.contracts_gen.enums import ConfigName, ErrorCode, Role
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import AcceptedReceipt, CommandIdentity, StoredResult
from crew_health.kernel.config import ConfigDocumentView, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.testing import MemoryClock, MemoryRelease
from crew_health.kernel.uow import (
    Actor,
    DomainEvent,
    OutboxEvent,
    ReplicationChange,
    ReplicationRef,
    SessionContext,
    StoredEvent,
    UnitOfWork,
)
from crew_health.roles.adapters.sqlite_repository import SqliteRolesRepository, migration_sql
from crew_health.roles.api.router import build_router
from crew_health.roles.application.actions import RoleActions
from crew_health.roles.application.config import ConfigCache
from crew_health.roles.application.ports import (
    HelpSessionSnapshot,
    MissionFacts,
    NodeRef,
    OverlayFact,
    PlanRunSnapshot,
    RosterMember,
)
from crew_health.roles.application.proposals import Proposals
from crew_health.roles.application.provided import RolesProvided
from crew_health.roles.application.settings import RolesSettings
from crew_health.roles.application.skills import Skills
from crew_health.roles.application.world import Deps
from crew_health.roles.domain.freshness import Practice

with warnings.catch_warnings():  # starlette's own deprecation notice on importing its test client, not ours
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "contracts/api/examples"
SCHEMAS = REPO / "contracts/api/schemas"
ROLE_RULES = REPO / "config/role-rules.json"
MISSION_START = datetime(2026, 11, 1, tzinfo=UTC)  # day 12 is 2026-11-12
NOW = datetime(2026, 11, 13, 9, 36, 30, tzinfo=UTC)
WALK_END = datetime(2026, 11, 13, 11, 40, tzinfo=UTC)
DAY_12 = datetime(2026, 11, 12, 15, 20, tzinfo=UTC)


def example(name: str) -> dict[str, object]:
    value = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))["value"]
    if not isinstance(value, dict):
        raise TypeError(name)
    return value


def document(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def example_raw(name: str) -> bytes:
    return json.dumps(example(name), sort_keys=True).encode()


def documents(role_rules: bytes | None = None) -> dict[ConfigName, ConfigDocumentView]:
    raws = {
        ConfigName.ROLE_RULES: ROLE_RULES.read_bytes() if role_rules is None else role_rules,
        ConfigName.SKILLS_CATALOG: example_raw("config/skills-catalog.example.json"),
        ConfigName.UI_COPY: example_raw("config/ui-copy.example.json"),
        ConfigName.ROUTINE_RULES: example_raw("config/routine-rules.example.json"),
        ConfigName.ENVIRONMENT_PROFILES: example_raw("config/environment-profiles.example.json"),
        ConfigName.PARTICIPATION_RULES: example_raw("config/participation-rules.example.json"),
    }
    return {name: document(name, raw) for name, raw in raws.items()}


# ------------------------------------------------------------------------------------------------------- storage
class SqliteSession:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        cursor = self.connection.execute(sql, parameters)
        return StatementResult(cursor.rowcount, cursor.lastrowid)

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        return tuple(tuple(r) for r in self.connection.execute(sql, parameters).fetchmany(limit))


class SqliteUow:
    def __init__(
        self, factory: UowFactory, connection: sqlite3.Connection, actor: Actor, correlation_id: str
    ) -> None:
        self.factory = factory
        self._session = SqliteSession(connection)
        self._actor = actor
        self._correlation = correlation_id
        self._now = factory.clock.now()
        self.events: list[OutboxEvent] = []
        self.audits: list[AuditEntry] = []

    @property
    def connection(self) -> SqliteSession:
        return self._session

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def actor(self) -> Actor:
        return self._actor

    @property
    def release(self) -> ReleaseHandle:
        return self.factory.release

    @property
    def correlation_id(self) -> str:
        return self._correlation

    def publish(self, event: DomainEvent) -> None:
        raise NotImplementedError

    def emit(self, event: OutboxEvent) -> StoredEvent:
        self.events.append(event)
        seq = len(self.factory.events) + len(self.events)
        return StoredEvent(
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

    def audit(self, entry: AuditEntry) -> None:
        self.audits.append(entry)

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        raise NotImplementedError

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        raise NotImplementedError

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        raise NotImplementedError

    def savepoint(self, name: str) -> AbstractContextManager[None]:
        raise NotImplementedError


class UowFactory:
    """One connection per unit of work, BEGIN IMMEDIATE, commit or roll back; events and audits kept on commit."""

    def __init__(self, path: Path, clock: MemoryClock, release: ReleaseHandle) -> None:
        self.path = path
        self.clock = clock
        self.release = release
        self.events: list[OutboxEvent] = []
        self.audits: list[AuditEntry] = []
        self.lock = threading.Lock()
        with sqlite3.connect(path) as connection:
            connection.executescript(migration_sql())
            connection.execute(
                "CREATE TABLE IF NOT EXISTS test_idempotency (actor_ref TEXT, key TEXT, identity TEXT, status INTEGER, "
                "resource_ref TEXT, result_code TEXT, body BLOB, receipt TEXT, PRIMARY KEY (actor_ref, key))"
            )

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[SqliteUow]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        uow = SqliteUow(self, connection, actor, correlation_id)
        try:
            yield uow
        except BaseException:
            connection.execute("ROLLBACK")
            connection.close()
            raise
        connection.execute("COMMIT")
        connection.close()
        with self.lock:
            self.events.extend(uow.events)
            self.audits.extend(uow.audits)

    def event_types(self) -> list[str]:
        return [e.type.value for e in self.events]


class SqliteIdempotency:
    """Same key with another operation, target or body is DUPLICATE_COMMAND, never a replay (AGENTS 6)."""

    def lookup(self, uow: UnitOfWork, identity: CommandIdentity) -> StoredResult | None:
        rows = uow.connection.rows(
            "SELECT identity, status, resource_ref, result_code, body, receipt FROM test_idempotency "
            "WHERE actor_ref = ? AND key = ?",
            (identity.actor_ref, identity.key),
            limit=1,
        )
        if not rows:
            return None
        stored, status, ref, code, body, receipt = rows[0]
        if stored != _identity_text(identity):
            raise ProblemError(ErrorCode.DUPLICATE_COMMAND, ErrorCode.DUPLICATE_COMMAND.value)
        r = json.loads(str(receipt))
        accepted = AcceptedReceipt(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4]))
        if not isinstance(status, int) or not isinstance(body, bytes):
            raise TypeError("stored result")
        return StoredResult(accepted, status, None if ref is None else str(ref), str(code), body)

    def record(self, uow: UnitOfWork, identity: CommandIdentity, result: StoredResult) -> None:
        receipt = result.receipt
        uow.connection.execute(
            "INSERT INTO test_idempotency VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity.actor_ref,
                identity.key,
                _identity_text(identity),
                result.status,
                result.resource_ref,
                result.result_code,
                result.safe_body,
                json.dumps(
                    [
                        receipt.command_key,
                        receipt.operation_id,
                        receipt.target,
                        receipt.receipt_id,
                        receipt.accepted_at.isoformat(),
                    ]
                ),
            ),
        )


def _identity_text(identity: CommandIdentity) -> str:
    return "|".join(
        (identity.operation, identity.target, identity.parameters_sha256, identity.body_fingerprint)
    )


class CountingIds:
    def __init__(self) -> None:
        self.count = 0
        self.lock = threading.Lock()

    def new(self, prefix: str) -> str:
        with self.lock:
            self.count += 1
            return f"{prefix}_{self.count:05d}"

    def derived(self, prefix: str, *parts: str) -> str:
        return prefix + "_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------------------------------- port fakes
CREW = (
    RosterMember("mara", "Mara Okafor", 0, None),
    RosterMember("ilya", "Ilya Brandt", 1, 1),
    RosterMember("sofia", "Sofia Reyes", 2, 2),
    RosterMember("tomas", "Tomas Lind", 3, None),
)


class PortDown(RuntimeError):
    pass


@dataclass
class Facts:
    """Everything the fakes answer; tests change these, never code."""

    roster: list[RosterMember] = field(default_factory=lambda: list(CREW))
    overlays: dict[str, list[OverlayFact]] = field(default_factory=dict)
    locations: dict[str, str | None] = field(default_factory=dict)
    mission: MissionFacts | None = field(
        default_factory=lambda: MissionFacts("msn_moon_1", "SIM", "lunar_surface", 180, "node_base")
    )
    help: dict[str, HelpSessionSnapshot] = field(default_factory=dict)
    plans: dict[str, PlanRunSnapshot] = field(default_factory=dict)
    aboard: dict[str, bool] | None = None
    down: set[str] = field(default_factory=set)

    def check(self, port: str) -> None:
        if port in self.down:
            raise PortDown(port)

    def walk(self, person_id: str, *, until: datetime | None = WALK_END) -> None:
        fact = OverlayFact(
            f"ovl_{person_id}", person_id, "eva", "active", None, NOW - timedelta(hours=1), None, until
        )
        self.overlays[person_id] = [fact]

    def sleep(self, person_id: str) -> None:
        fact = OverlayFact(
            f"ovl_s_{person_id}", person_id, "sleep", "active", None, NOW - timedelta(hours=1), None
        )
        self.overlays[person_id] = [fact]


class Roster:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def crew(self, at: datetime) -> Sequence[RosterMember]:
        self.facts.check("roster")
        return list(self.facts.roster)


class Overlays:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]:
        self.facts.check("overlays")
        return {p: self.facts.overlays.get(p, []) for p in person_ids}


class Locations:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def location_of(self, person_id: str, at: datetime) -> str | None:
        self.facts.check("locations")
        return self.facts.locations.get(person_id)


class Mission:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def current(self, uow: object = None) -> MissionFacts | None:
        self.facts.check("mission")
        return self.facts.mission

    def day_end(self, instant: datetime) -> datetime:
        start = instant.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return start + timedelta(days=1)


class DayLabels:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def inline_label_at(self, instant: datetime) -> str:
        self.facts.check("day_labels")
        return f"day {(instant - MISSION_START).days + 1}"


class HelpReader:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def snapshot(self, help_session_id: str) -> HelpSessionSnapshot | None:
        self.facts.check("help")
        return self.facts.help.get(help_session_id)

    def readable_by(self, session: SessionContext, help_session_id: str) -> bool:
        snap = self.facts.help.get(help_session_id)
        if snap is None:
            return False
        if session.roles & {Role.STATION, Role.CREW_MEDICAL_OFFICER}:
            return True
        return session.person_id is not None  # E26 interim: every crew person sees an open help session


class PlanReader:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def state(self, plan_run_id: str) -> PlanRunSnapshot | None:
        self.facts.check("plans")
        return self.facts.plans.get(plan_run_id)


class Supplies:
    def __init__(self, facts: Facts) -> None:
        self.facts = facts

    def aboard(self, supply_ids: Collection[str], environment_id: str) -> Mapping[str, bool]:
        self.facts.check("supplies")
        return {s: bool(self.facts.aboard and self.facts.aboard.get(s)) for s in supply_ids}


class ByVersion:
    def __init__(self, docs: Mapping[ConfigName, ConfigDocumentView]) -> None:
        self.docs = docs

    def document(self, name: ConfigName, version: str | None = None) -> ConfigDocumentView:
        doc = self.docs[name]
        if version is not None and doc.version != version:
            raise KeyError(version)
        return doc


def help_session(
    session_id: str,
    *,
    subject: str | None,
    flow: str | None,
    opener: str = "mara",
    state: str = "open",
) -> HelpSessionSnapshot:
    node = NodeRef("flow", flow) if flow is not None else NodeRef("triage", "start")
    return HelpSessionSnapshot(
        session_id,
        "open" if state == "open" else "closed",
        1,
        subject,
        Actor("person", opener),
        (),
        node,
        None,
        NOW - timedelta(minutes=1),
        None,
        None,
    )


def help_payload(snap: HelpSessionSnapshot) -> dict[str, object]:
    return {
        "help_session_id": snap.id,
        "revision": snap.revision,
        "state": snap.state,
        "subject": {"kind": "crewmate", "person_id": snap.subject_person_id, "display_name": None},
    }


SESSIONS: dict[str, SessionContext] = {
    "mara": SessionContext("ses_mara", "crew", Actor("person", "mara"), "mara", frozenset({Role.CREW})),
    "tomas": SessionContext("ses_tomas", "crew", Actor("person", "tomas"), "tomas", frozenset({Role.CREW})),
    "sofia": SessionContext(
        "ses_sofia",
        "crew",
        Actor("person", "sofia"),
        "sofia",
        frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER}),
    ),
    "ilya": SessionContext(
        "ses_ilya", "crew", Actor("person", "ilya"), "ilya", frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER})
    ),
    "station": SessionContext(
        "ses_station", "station", Actor("station", "stn_1"), None, frozenset({Role.STATION})
    ),
    # an assistant actor carrying a crew role: the domain still refuses it (AGENTS rule 4)
    "assistant": SessionContext(
        "ses_asst", "crew", Actor("assistant", "asst_1"), "mara", frozenset({Role.CREW})
    ),
}


def authenticate(request: Request, operation_id: str) -> SessionContext:
    name = request.headers.get("X-Test-Session", "")
    session = SESSIONS.get(name)
    if session is None:
        raise ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)
    return session


class Harness:
    def __init__(self, tmp_path: Path, *, role_rules: bytes | None = None) -> None:
        self.facts = Facts()
        self.clock = MemoryClock(NOW, 1, "epoch_1")
        self.docs = documents(role_rules)
        self.release = MemoryRelease("rel_1", self.docs)
        self.uow = UowFactory(tmp_path / "hub.db", self.clock, self.release)
        self.ids = CountingIds()
        settings = RolesSettings.parse(RolesSettings.defaults())
        self.deps = Deps(
            uow=self.uow,
            repository=SqliteRolesRepository,
            clock=self.clock,
            ids=self.ids,
            settings=settings,
            idempotency=SqliteIdempotency(),
            roster=Roster(self.facts),
            overlays=Overlays(self.facts),
            mission=Mission(self.facts),
            day_labels=DayLabels(self.facts),
            help_sessions=HelpReader(self.facts),
            locations=Locations(self.facts),
            plan_runs=PlanReader(self.facts),
            supplies=Supplies(self.facts),
            config_reader=ByVersion(self.docs),
            configs=ConfigCache(),
        )
        self.proposals = Proposals(self.deps)
        self.actions = RoleActions(self.deps)
        self.skills = Skills(self.deps)
        self.provided = RolesProvided(self.deps)
        app = FastAPI()
        app.include_router(build_router(self.actions, self.skills, authenticate))
        self.client = TestClient(app)

    # ---------------------------------------------------------------------------------------------- driving
    def practice(self, person_id: str, skill_id: str, at: datetime = DAY_12, ref: str = "pre_1") -> None:
        with self.uow.begin(actor=Actor("person", person_id), correlation_id="cor_t") as uow:
            repo = SqliteRolesRepository(uow.connection)
            repo.add_practices(
                [
                    Practice(
                        self.ids.new("prc"), person_id, skill_id, "preflight", ref, at, "SIM", "msn_moon_1"
                    )
                ],
                recorded_by="test",
                release_id="rel_1",
                at=at,
            )

    def open_help(self, snap: HelpSessionSnapshot) -> None:
        self.facts.help[snap.id] = snap
        with self.uow.begin(actor=Actor("engine", "help"), correlation_id="cor_t") as uow:
            self.proposals.on_help_session_changed(uow, help_payload(snap))
        self.proposals.run_due()

    def open_plan(self, run: PlanRunSnapshot) -> None:
        self.facts.plans[run.plan_run_id] = run
        payload = {
            "plan_run_id": run.plan_run_id,
            "revision": 1,
            "state": "active",
            "subject": {"person_id": run.subject_person_id},
        }
        with self.uow.begin(actor=Actor("engine", "plans"), correlation_id="cor_t") as uow:
            self.proposals.on_plan_run_changed(uow, payload)
        self.proposals.run_due()

    def overlay_event(self, person_id: str, kind: str, state: str) -> None:
        payload = {"person_id": person_id, "overlay": kind, "state": state}
        with self.uow.begin(actor=Actor("adapter", "mission"), correlation_id="cor_t") as uow:
            self.proposals.on_overlay_changed(uow, payload)

    def plan_run(
        self, subject: str = "mara", skills: tuple[str, ...] = ("pulse_check_wrist",)
    ) -> PlanRunSnapshot:
        return PlanRunSnapshot(
            "prn_icl10_mara_1",
            subject,
            "open",
            tuple((f"step_{i}", s) for i, s in enumerate(skills)),
            self.docs[ConfigName.ROLE_RULES].version,
            self.docs[ConfigName.SKILLS_CATALOG].version,
        )

    def get(self, path: str, who: str) -> tuple[int, dict[str, object]]:
        response = self.client.get(path, headers={"X-Test-Session": who})
        return response.status_code, response.json()

    def post(
        self, path: str, who: str, body: Mapping[str, object], *, key: str, if_match: int | None = None
    ) -> tuple[int, dict[str, object]]:
        headers = {"X-Test-Session": who, "Idempotency-Key": key}
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        response = self.client.post(path, json=dict(body), headers=headers)
        return response.status_code, response.json()

    def roles(self, help_session_id: str, who: str = "station") -> dict[str, object]:
        status, body = self.get(f"/v1/help-sessions/{help_session_id}/roles", who)
        if status != 200:
            raise AssertionError(body)
        return body

    def advance(self, seconds: int) -> None:
        self.clock.at = self.clock.at + timedelta(seconds=seconds)


def assignments(view: Mapping[str, object]) -> list[dict[str, object]]:
    items = view["assignments"] if "assignments" in view else view["items"]
    if not isinstance(items, list):
        raise TypeError("assignments")
    return [i for i in items if isinstance(i, dict)]


def by_role(view: Mapping[str, object]) -> dict[str, dict[str, object]]:
    return {str(a["role_id"]): a for a in assignments(view)}


def person_of(item: Mapping[str, object]) -> str:
    person = item["person"]
    if not isinstance(person, dict):
        raise TypeError("person")
    return str(person["person_id"])


def unfilled(view: Mapping[str, object]) -> list[tuple[str, bool]]:
    rows = view["unfilled"]
    if not isinstance(rows, list):
        raise TypeError("unfilled")
    return [(str(r["role_id"]), bool(r["required"])) for r in rows if isinstance(r, dict)]


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("not an object")
    return {str(k): v for k, v in value.items()}


def objs(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("not a list")
    return [obj(v) for v in value]


def texts(value: object, key: str = "text") -> list[object]:
    return [item[key] for item in objs(value)]
