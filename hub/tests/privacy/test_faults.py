"""T-E02-012: a storage failure at every step of every E02 write command accepts nothing and is never swallowed.

Each command runs on a real SQLite station through a unit of work factory that fails its Nth step with
`StorageUnavailable("disk_full")`, for every N until the command completes. A step is a storage statement, an
audit append, an event, a replication reference, a schedule change or the commit itself; the failure comes
either before the step or after it has written inside the transaction. Every failed attempt must surface the
typed, retryable storage error and leave every table as it was. The one exception is the sign-in attempt
table for commands that prove a credential: a reservation committed before a later failure stays counted,
which is the attempt limit failing closed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from e02_station import (
    OPERATOR,
    START,
    Light,
    Ports,
    credential,
    light,
    mission_id,
    seed_roster,
    sim_bootstrap,
    sim_members,
    sim_station_principal,
)

from crew_health.consent.application.records import RECORD_PURPOSE
from crew_health.consent.application.service import ConsentService
from crew_health.consent.domain.grants import Grant, GrantRequest
from crew_health.consent.module import build_deps as consent_deps
from crew_health.identity.application.admin import IdentityAdmin
from crew_health.identity.application.ports import IdentityPorts
from crew_health.identity.application.service import IdentityService
from crew_health.identity.application.support import GroundSignIn
from crew_health.identity.domain.roster import ProvisionedPrincipal
from crew_health.identity.domain.session import SignInRequest
from crew_health.identity.module import build_deps as identity_deps
from crew_health.identity.module import consumer_ports
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import CommandIdentity, CommandReceipts, IdempotencyStore
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StatementResult, StorageSession
from crew_health.kernel.uow import (
    Actor,
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
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.storage import SQLiteDatabase

LISTENER = "127.0.0.1:8000"
CLIENT = "10.0.0.2"
BUCKETS = "identity_auth_bucket"
STEP_CEILING = 400
WINDOW = timedelta(minutes=5)


class Faults:
    """Fails the armed step once; disarmed, every step passes."""

    def __init__(self) -> None:
        self.at = 0
        self.after = False
        self.steps = 0
        self.fired = False
        self.phases: list[str] = []

    def arm(self, at: int, *, after: bool) -> None:
        self.at, self.after, self.steps, self.fired, self.phases = at, after, 0, False, []

    def disarm(self) -> None:
        self.arm(0, after=False)

    def before(self, phase: str) -> None:
        self.steps += 1
        if self.steps > STEP_CEILING:
            raise AssertionError("a command took more steps than the ceiling")
        self.phases.append(phase)
        if not self.after:
            self._fire()

    def done(self) -> None:
        if self.after:
            self._fire()

    def commit(self) -> None:
        self.before("commit")
        self._fire()

    def _fire(self) -> None:
        if self.steps == self.at:
            self.fired = True
            raise StorageUnavailable("disk_full")


class FaultySession:
    def __init__(self, inner: StorageSession, faults: Faults) -> None:
        self.inner, self.faults = inner, faults

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        self.faults.before("execute")
        result = self.inner.execute(sql, parameters)
        self.faults.done()
        return result

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        self.faults.before("rows")
        result = self.inner.rows(sql, parameters, limit=limit)
        self.faults.done()
        return result


class FaultyWork:
    def __init__(self, inner: UnitOfWork, faults: Faults) -> None:
        self.inner, self.faults = inner, faults
        self.session = FaultySession(inner.connection, faults)

    @property
    def connection(self) -> StorageSession:
        return self.session

    @property
    def now(self) -> datetime:
        return self.inner.now

    @property
    def actor(self) -> Actor:
        return self.inner.actor

    @property
    def release(self) -> ReleaseHandle:
        return self.inner.release

    @property
    def correlation_id(self) -> str:
        return self.inner.correlation_id

    def publish(self, event: DomainEvent) -> None:
        self.faults.before("publish")
        self.inner.publish(event)
        self.faults.done()

    def emit(self, event: OutboxEvent) -> StoredEvent:
        self.faults.before("emit")
        stored = self.inner.emit(event)
        self.faults.done()
        return stored

    def audit(self, entry: AuditEntry) -> None:
        self.faults.before("audit")
        self.inner.audit(entry)
        self.faults.done()

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        self.faults.before("schedule")
        generation = self.inner.schedule(job, expected_generation=expected_generation)
        self.faults.done()
        return generation

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        self.faults.before("cancel_schedule")
        cancelled = self.inner.cancel_schedule(key, expected_generation=expected_generation)
        self.faults.done()
        return cancelled

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        self.faults.before("replicate")
        reference = self.inner.replicate(change)
        self.faults.done()
        return reference

    def savepoint(self, name: str) -> AbstractContextManager[None]:
        return self.inner.savepoint(name)


class FaultyUnits:
    def __init__(self, database: SQLiteDatabase, faults: Faults) -> None:
        self.database, self.faults = database, faults

    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> AbstractContextManager[UnitOfWork]:
        return self._begin(actor, command, correlation_id)

    @contextmanager
    def _begin(
        self, actor: Actor, command: CommandIdentity | None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        with self.database.begin(actor=actor, command=command, correlation_id=correlation_id) as uow:
            yield FaultyWork(uow, self.faults)
            self.faults.commit()


@dataclass(frozen=True, slots=True)
class Completed[T]:
    """A command that completed after every earlier step failed once, with the phases it went through."""

    result: T
    phases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Faulty:
    """E02 services over the same station whose every write goes through the failing factory."""

    base: Light
    faults: Faults
    identity: IdentityService
    admin: IdentityAdmin
    consent: ConsentService


def faulty(base: Light) -> Faulty:
    db, faults = base.database, Faults()
    ledger = CommandLedger(db.ids, settings=db.settings)
    units: UnitOfWorkFactory = FaultyUnits(db, faults)
    ports = Ports(
        {
            UnitOfWorkFactory: units,
            ReadSnapshotFactory: db,
            Clock: db.clock,
            IdFactory: db.ids,
            HubSettings: db.settings,
            IdempotencyStore: ledger,
            CommandReceipts: ledger,
        }
    )
    identity = IdentityService(identity_deps(ports, GroundSignIn(False)))
    ports.values[IdentityPorts] = consumer_ports(identity)
    return Faulty(
        base, faults, identity, IdentityAdmin(identity.deps, identity), ConsentService(consent_deps(ports))
    )


def tables(db: SQLiteDatabase, *, ignoring: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    ceiling = db.settings.integer("storage.max_rows")
    with db.snapshot() as view:
        names = [
            str(row[0])
            for row in view.connection.rows(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
                (),
                limit=ceiling,
            )
        ]
        state: dict[str, list[str]] = {}
        for name in names:
            if name in ignoring:
                continue
            rows = view.connection.rows(f'SELECT * FROM "{name}"', (), limit=ceiling)
            assert len(rows) < ceiling
            state[name] = sorted(repr(row) for row in rows)
    return state


def next_window(base: Light) -> None:
    """Moves past the sign-in window so earlier reservations never lock the next attempt."""
    base.clock.at += WINDOW
    base.clock.monotonic += int(WINDOW.total_seconds()) * 1_000_000_000


def every_step[T](
    hub: Faulty,
    prepare: Callable[[], Callable[[], T]],
    *,
    ignoring: frozenset[str] = frozenset(),
    proves: bool = False,
) -> list[Completed[T]]:
    """Fails each step of the prepared command in turn, then lets it complete; once per failure mode.

    A command that proves a credential gets a fresh sign-in window per attempt, so the reservations its
    failed attempts keep never lock the next one.
    """
    completed: list[Completed[T]] = []
    for after in (False, True):
        command = prepare()
        for at in range(1, STEP_CEILING + 1):
            if proves:
                next_window(hub.base)
            before = tables(hub.base.database, ignoring=ignoring)
            hub.faults.arm(at, after=after)
            try:
                result = command()
            except StorageUnavailable as error:
                assert hub.faults.fired, f"a storage error that was not injected at step {at}"
                assert error.cause == "disk_full"
                assert error.retryable
                assert tables(hub.base.database, ignoring=ignoring) == before, f"step {at} left a change"
                hub.faults.disarm()
                continue
            phases = tuple(hub.faults.phases)
            fired = hub.faults.fired
            hub.faults.disarm()
            assert not fired, f"the failure at step {at} was swallowed"
            assert len(phases) == at - 1, "the command completed before reaching every failure point"
            assert tables(hub.base.database, ignoring=ignoring) != before
            completed.append(Completed(result, phases))
            break
        else:
            pytest.fail("the command never completed within the step ceiling")
    return completed


def unended_sessions(db: SQLiteDatabase) -> int:
    with db.snapshot() as view:
        rows = view.connection.rows(
            "SELECT count(*) FROM identity_session WHERE ended_at IS NULL", (), limit=1
        )
    value = rows[0][0]
    assert isinstance(value, int)
    return value


def owner(hub: Faulty, person_id: str, secret: str | None = None) -> SessionContext:
    return hub.base.identity.verify_owner(
        person_id, secret or credential(person_id), client="console", correlation_id="c"
    )


def grant_request(recipient: str, record_id: str, until: datetime | None) -> GrantRequest:
    return GrantRequest(
        recipient,
        mission_id(),
        "SIM",
        record_id,
        RECORD_PURPOSE,
        frozenset({"read"}),
        frozenset({"observation"}),
        START,
        until,
    )


@pytest.fixture
def hub(tmp_path: Path) -> Faulty:
    return faulty(light(tmp_path))


@pytest.mark.trace("T-E02-012", "T-E02-009")
def test_a_failed_sign_in_issues_no_session_and_no_token(hub: Faulty) -> None:
    def prepare() -> Callable[[], str | None]:
        def command() -> str | None:
            view = hub.identity.sign_in(
                SignInRequest("crew", "mara"),
                credential("mara"),
                client=CLIENT,
                listener=LISTENER,
                correlation_id="c",
            )
            return view.token

        return command

    runs = every_step(hub, prepare, ignoring=frozenset({BUCKETS}), proves=True)
    assert all(run.result is not None for run in runs)
    assert unended_sessions(hub.base.database) == 2
    for run in runs:
        assert run.phases.count("commit") == 2
        assert {"rows", "execute", "audit", "replicate"} <= set(run.phases)


@pytest.mark.trace("T-E02-012", "T-E02-013")
def test_a_failed_sign_out_keeps_the_session_and_its_retry_ends_it(hub: Faulty) -> None:
    keys = iter(("signout_key_1", "signout_key_2"))

    def prepare() -> Callable[[], None]:
        token = hub.base.identity.sign_in(
            SignInRequest("crew", "ilya"),
            credential("ilya"),
            client=CLIENT,
            listener=LISTENER,
            correlation_id="c",
        ).token
        assert token is not None
        session = hub.base.identity.authenticate(token, listener=LISTENER)
        key = next(keys)

        def command() -> None:
            hub.identity.sign_out(session, key=key, correlation_id="c")

        return command

    runs = every_step(hub, prepare)
    assert unended_sessions(hub.base.database) == 0
    for run in runs:
        assert run.phases.count("commit") == 1
        assert {"rows", "execute", "audit", "replicate"} <= set(run.phases)


@pytest.mark.trace("T-E02-012", "T-E02-015")
def test_a_failed_credential_change_keeps_the_old_credential_and_its_generation(hub: Faulty) -> None:
    secrets = iter(("sofia changed passphrase one", "sofia changed passphrase two"))
    current = [credential("sofia")]

    def prepare() -> Callable[[], int]:
        old, new = current[0], next(secrets)

        def command() -> int:
            generation = hub.admin.set_credential(
                "sofia", new, current_credential=old, operator=None, client="console", correlation_id="c"
            )
            current[0] = new
            return generation

        return command

    runs = every_step(hub, prepare, ignoring=frozenset({BUCKETS}), proves=True)
    first, second = (run.result for run in runs)
    assert second == first + 1
    owner(hub, "sofia", current[0])
    for run in runs:
        assert run.phases.count("commit") == 3
        assert "audit" in run.phases


@pytest.mark.trace("T-E02-012", "T-E02-009")
def test_a_failed_roster_change_removes_nobody_and_ends_no_session(hub: Faulty) -> None:
    removals = iter(("tomas", "sofia"))
    remaining = list(sim_members())
    revision = [1]

    def prepare() -> Callable[[], tuple[str, ...]]:
        removed = next(removals)
        for person_id in (removed, "mara"):
            hub.base.identity.sign_in(
                SignInRequest("crew", person_id),
                credential(person_id),
                client=CLIENT,
                listener=LISTENER,
                correlation_id="c",
            )
        members = tuple(member for member in remaining if member.person_id != removed)

        def command() -> tuple[str, ...]:
            result = hub.admin.apply_roster(
                sim_bootstrap(members=members),
                expected_revision=revision[0],
                operator=OPERATOR,
                correlation_id="c",
            )
            revision[0] = result.revision
            remaining[:] = members
            return result.removed

        return command

    runs = every_step(hub, prepare)
    assert [run.result for run in runs] == [("tomas",), ("sofia",)]
    assert unended_sessions(hub.base.database) == 2
    for run in runs:
        assert run.phases.count("commit") == 1
        assert {"rows", "execute", "audit", "replicate"} <= set(run.phases)


@pytest.mark.trace("T-E02-012", "T-E02-009")
def test_a_failed_adapter_token_issue_leaves_no_usable_token(tmp_path: Path) -> None:
    camera = ProvisionedPrincipal("camera_1", "adapter", "Camera one")
    base = light(tmp_path, seed=False)
    seed_roster(base, sim_bootstrap(principals=(sim_station_principal(), camera)))
    hub = faulty(base)

    def prepare() -> Callable[[], str | None]:
        def command() -> str | None:
            return hub.admin.issue_adapter_token(
                "camera_1", listener=LISTENER, operator=OPERATOR, correlation_id="c"
            ).token

        return command

    runs = every_step(hub, prepare)
    kinds = [hub.base.identity.authenticate(run.result, listener=LISTENER).kind for run in runs if run.result]
    assert kinds == ["adapter", "adapter"]
    for run in runs:
        assert run.phases.count("commit") == 1
        assert "audit" in run.phases


@pytest.mark.trace("T-E02-012", "T-E02-007", "T-E02-010")
def test_a_failed_grant_releases_nothing_and_schedules_no_expiry(hub: Faulty) -> None:
    records = iter(("obs_1", "obs_2"))

    def prepare() -> Callable[[], Grant]:
        subject = owner(hub, "mara")
        record_id = next(records)
        until = hub.base.clock.at + timedelta(days=1)

        def command() -> Grant:
            return hub.consent.grant(subject, grant_request("ilya", record_id, until), correlation_id="c")

        return command

    runs = every_step(hub, prepare)
    assert [(run.result.state, run.result.request.object_ref) for run in runs] == [
        ("active", "obs_1"),
        ("active", "obs_2"),
    ]
    for run in runs:
        assert run.phases.count("commit") == 1
        assert {"schedule", "audit", "replicate"} <= set(run.phases)


@pytest.mark.trace("T-E02-012", "T-E02-007", "T-E02-010")
def test_a_failed_revocation_changes_nothing_and_its_retry_is_final(hub: Faulty) -> None:
    records = iter(("obs_1", "obs_2"))

    def prepare() -> Callable[[], Grant]:
        subject = owner(hub, "mara")
        until = hub.base.clock.at + timedelta(days=1)
        created = hub.base.consent.grant(
            subject, grant_request("ilya", next(records), until), correlation_id="c"
        )

        def command() -> Grant:
            return hub.consent.revoke(
                subject, created.grant_id, expected_revision=created.revision, correlation_id="c"
            )

        return command

    runs = every_step(hub, prepare)
    assert [run.result.state for run in runs] == ["revoked", "revoked"]
    for run in runs:
        assert run.phases.count("commit") == 1
        assert {"cancel_schedule", "audit", "replicate"} <= set(run.phases)
