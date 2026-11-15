from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid7

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock, HlcClock
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.ids import IdFactory, require_id
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.storage import SqlValue, StatementResult
from crew_health.kernel.uow import (
    Actor,
    DomainEvent,
    OutboxEvent,
    ReadSnapshot,
    ReplicationChange,
    ReplicationRef,
    StoredEvent,
    UnitOfWork,
)
from crew_health.platform.adapters.audit import AuditRepository
from crew_health.platform.application.scheduler import SchedulerService
from crew_health.platform.domain.audit import AuditPolicy, core_policies
from crew_health.platform.domain.errors import StorageConstraintViolated, fail

if TYPE_CHECKING:
    from crew_health.kernel.commands import CommandIdentity
    from crew_health.kernel.uow import OutboxEvent, StoredEvent


def storage_error(error: sqlite3.Error) -> StorageUnavailable | StorageConstraintViolated:
    code = getattr(error, "sqlite_errorcode", 0) & 255
    if code == sqlite3.SQLITE_FULL:
        return StorageUnavailable("disk_full")
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return StorageUnavailable("locked")
    if code == sqlite3.SQLITE_CONSTRAINT:
        return StorageConstraintViolated()
    if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return StorageUnavailable("corrupt")
    return StorageUnavailable("io_error")


class SQLiteSession:
    def __init__(
        self,
        connection: sqlite3.Connection,
        settings: HubSettings,
        *,
        readonly: bool,
        expired: Callable[[], bool] | None = None,
    ) -> None:
        self._connection, self._settings, self._readonly = connection, settings, readonly
        self._expired = expired
        self.active = True

    def _check(self, sql: str, parameters: tuple[SqlValue, ...]) -> None:
        if not self.active:
            raise fail(ErrorCode.INVALID_TRANSITION)
        if len(sql.encode()) + sum(
            len(v.encode()) if isinstance(v, str) else len(v) if isinstance(v, bytes) else 8
            for v in parameters
        ) > self._settings.integer("storage.max_statement_bytes"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        if _first_token(sql) in (
            "BEGIN",
            "COMMIT",
            "END",
            "ROLLBACK",
            "SAVEPOINT",
            "RELEASE",
            "ATTACH",
            "DETACH",
            "PRAGMA",
        ):
            raise fail(ErrorCode.INVALID_TRANSITION)

    def _budget(self) -> None:
        maximum = self._settings.integer("storage.max_vm_steps")
        stride = min(maximum, 1000)
        steps = 0

        expired = self._expired

        def progress() -> int:
            nonlocal steps
            steps += stride
            return int(steps >= maximum or (expired is not None and expired()))

        self._connection.set_progress_handler(progress, stride)

    def execute(self, sql: str, parameters: tuple[SqlValue, ...] = ()) -> StatementResult:
        self._check(sql, parameters)
        if self._readonly:
            raise fail(ErrorCode.INVALID_TRANSITION)
        try:
            self._budget()
            cursor = self._connection.execute(sql, parameters)
            return StatementResult(max(cursor.rowcount, 0), cursor.lastrowid)
        except sqlite3.Error as error:
            raise storage_error(error) from None
        finally:
            self._connection.set_progress_handler(None, 0)

    def rows(
        self, sql: str, parameters: tuple[SqlValue, ...], *, limit: int
    ) -> tuple[tuple[SqlValue, ...], ...]:
        self._check(sql, parameters)
        if not 1 <= limit <= self._settings.integer("storage.max_rows"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        # A SELECT-only authorizer also covers writable CTEs and RETURNING clauses.
        self._connection.set_authorizer(_read_authorizer)
        try:
            self._budget()
            cursor = self._connection.execute(sql, parameters)
            rows = cursor.fetchmany(limit + 1)
            if len(rows) > limit:
                raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
            result: list[tuple[SqlValue, ...]] = []
            for row in rows:
                if any(type(v) not in (str, int, bytes, type(None)) for v in row):
                    raise StorageUnavailable("corrupt")
                result.append(tuple(row))
            return tuple(result)
        except sqlite3.Error as error:
            raise storage_error(error) from None
        finally:
            self._connection.set_progress_handler(None, 0)
            self._connection.set_authorizer(_read_authorizer if self._readonly else _write_authorizer)


def _first_token(sql: str) -> str:
    """The first keyword after any leading whitespace and SQL comments, upper case."""
    text = sql
    for _ in range(len(sql) + 1):
        text = text.lstrip()
        if text.startswith("--"):
            end = text.find("\n")
            text = "" if end < 0 else text[end + 1 :]
        elif text.startswith("/*"):
            end = text.find("*/")
            text = "" if end < 0 else text[end + 2 :]
        else:
            break
    pieces = text.split(maxsplit=1)
    return pieces[0].upper() if pieces else ""


def _read_authorizer(
    action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None
) -> int:
    if action in (
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    ):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _write_authorizer(
    action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None
) -> int:
    if action in (
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        # SYSTEM 5.7: no manual commit or nested transaction from module SQL, comment-prefixed or not; the unit
        # of work clears the authorizer only around its own commit, rollback and savepoint statements.
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
    ):
        return sqlite3.SQLITE_DENY
    if arg1 == "platform_pending_replica" and action in (
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_DELETE,
    ):
        if action == sqlite3.SQLITE_INSERT and source is not None and source.startswith("_e01_repl_"):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if arg1 == "events_retention" and action in (
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_DELETE,
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class SQLiteView:
    def __init__(self, session: SQLiteSession, release: ReleaseHandle, now: datetime) -> None:
        self._session, self._release, self._now = session, release, now

    @property
    def connection(self) -> SQLiteSession:
        return self._session

    @property
    def release(self) -> ReleaseHandle:
        return self._release

    @property
    def now(self) -> datetime:
        return self._now


class SQLiteDatabase:
    def __init__(
        self,
        path: Path,
        *,
        clock: Clock,
        ids: IdFactory,
        release: ReleaseHandle,
        settings: HubSettings,
        origin_node_id: str,
        origin_incarnation: str,
        initialize: bool = False,
    ) -> None:
        self.path, self.clock, self.ids, self.settings = path, clock, ids, settings
        self.origin_node_id, self.origin_incarnation = (
            require_id(origin_node_id),
            require_id(origin_incarnation),
        )
        self._release_provider: Callable[[], ReleaseHandle] = lambda: release
        self._release_reader: Callable[[ReadSnapshot], ReleaseHandle] | None = None
        self._writer = threading.Lock()
        self._admission = threading.BoundedSemaphore(settings.integer("storage.writer_queue_max") + 1)
        self._local = threading.local()
        self._replicated_tables: list[tuple[str, str, str]] = [
            ("records_observation", "observation", "observation_id"),
            ("records_answer", "answer", "answer_id"),
            ("config_release", "config.release", "release_id"),
            ("platform_private_reference", "private_reference", "entry_id"),
            ("platform_task", "task", "task_id"),
            ("clock_correction", "clock.correction", "correction_id"),
            ("platform_evaluation_pending", "evaluation", "completion_id"),
            ("platform_evaluation_completed", "evaluation", "completion_id"),
        ]
        self.audit_policies = {policy.event_type: policy for policy in core_policies()}
        self._closed = False
        self._postcommit: list[Callable[[], None]] = []
        self._disk_full_handlers: list[Callable[[], None]] = []
        self._guards: dict[str, Callable[[UnitOfWork], None]] = {}
        self._required_guards: frozenset[str] = frozenset()
        self.hlc: HlcClock | None = None
        self.event_sink: Callable[[UnitOfWork, OutboxEvent], StoredEvent] | None = None
        from crew_health.platform.application.uow import EventBus

        self.bus = EventBus(settings)
        self.scheduler = SchedulerService(settings)
        if not path.exists() and not initialize:
            raise StorageUnavailable("corrupt")
        if initialize:
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            if initialize:
                self._migrate(
                    connection,
                    Path(__file__)
                    .parent.joinpath("adapters/migrations/0001_platform.sql")
                    .read_text(encoding="utf-8"),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO platform_identity VALUES (1, ?, ?)",
                    (origin_node_id, origin_incarnation),
                )
            identity = connection.execute(
                "SELECT node_id, incarnation FROM platform_identity WHERE singleton=1"
            ).fetchone()
            if identity != (origin_node_id, origin_incarnation) or connection.execute(
                "PRAGMA quick_check"
            ).fetchone() != ("ok",):
                raise StorageUnavailable("corrupt")
        except sqlite3.Error as error:
            raise storage_error(error) from None
        finally:
            connection.close()

    def register_replicated_table(self, table: str, *, record_type: str, key_column: str) -> None:
        if (table, record_type, key_column) in self._replicated_tables:
            return  # the same declaration from a second assembly over this database
        if (
            not table.isascii()
            or not table.isidentifier()
            or not key_column.isascii()
            or not key_column.isidentifier()
            or not record_type
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        if any(existing[0] == table for existing in self._replicated_tables) or len(
            self._replicated_tables
        ) >= self.settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.CONFIG_INVALID)
        self._replicated_tables.append((table, record_type, key_column))

    def _tracking(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TEMP TABLE platform_pending_replica(table_index INTEGER NOT NULL,record_id TEXT NOT NULL,PRIMARY KEY(table_index,record_id)) STRICT"
        )
        for index, (table, _, key) in enumerate(self._replicated_tables):
            if (
                connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name=?", (table,)
                ).fetchone()
                is None
            ):
                continue
            for operation, row in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
                # Identifiers are compiled registration values; SQL cannot bind identifiers.
                statement = f'CREATE TEMP TRIGGER _e01_repl_{index}_{operation} AFTER {operation} ON main."{table}" BEGIN INSERT OR IGNORE INTO platform_pending_replica VALUES ({index},CAST({row}."{key}" AS TEXT)); END;'
                connection.execute(statement)

    def _verify_declarations(self, connection: sqlite3.Connection, uow: Transaction) -> None:
        maximum = self.settings.integer("storage.max_rows")
        dirty = connection.execute(
            "SELECT table_index,record_id FROM platform_pending_replica ORDER BY table_index,record_id LIMIT ?",
            (maximum + 1,),
        ).fetchmany(maximum + 1)
        if len(dirty) > maximum:
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        for index, reference in dirty:
            expected = (self._replicated_tables[index][1], reference)
            if expected not in uow.declared_replication:
                raise fail(ErrorCode.INTERNAL_ERROR)

    def register_audit_policy(self, policy: AuditPolicy) -> None:
        if self.audit_policies.get(policy.event_type) == policy:
            return  # the same declaration from a second assembly over this database
        if policy.event_type in self.audit_policies or len(self.audit_policies) >= self.settings.integer(
            "registry.max_hooks"
        ):
            raise fail(ErrorCode.CONFIG_INVALID)
        self.audit_policies[policy.event_type] = policy

    def add_disk_full_handler(self, callback: Callable[[], None]) -> None:
        if len(self._disk_full_handlers) >= self.settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.CONFIG_INVALID)
        self._disk_full_handlers.append(callback)

    def set_transaction_guard(self, name: str, guard: Callable[[UnitOfWork], None]) -> None:
        """Run `guard` inside every write transaction before its work (SYSTEM 5.2: the clock regression check
        against the durable last-committed high water). A guard failure rolls the write back. Keyed by name, so
        assembling an app twice over one database replaces the guard instead of running it twice."""
        require_id(name)
        if name not in self._guards and len(self._guards) >= self.settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.CONFIG_INVALID)
        self._guards[name] = guard

    def require_guards(self, names: frozenset[str]) -> None:
        """From now on no write transaction opens unless every named guard is installed (SYSTEM 5.2: the clock
        high water loads before any write). Assembly calls it before any provider or binding can write."""
        self._required_guards = self._required_guards | names

    def add_postcommit(self, callback: Callable[[], None]) -> None:
        if len(self._postcommit) >= self.settings.integer("registry.max_hooks"):
            raise fail(ErrorCode.CONFIG_INVALID)
        self._postcommit.append(callback)

    def set_release_provider(self, provider: Callable[[], ReleaseHandle]) -> None:
        self._release_provider = provider

    def set_release_reader(self, reader: Callable[[ReadSnapshot], ReleaseHandle]) -> None:
        self._release_reader = reader

    def _pin(self, session: SQLiteSession, now: datetime) -> ReleaseHandle:
        base = self._release_provider()
        return self._release_reader(SQLiteView(session, base, now)) if self._release_reader else base

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if getattr(self._local, "writing", False):
            raise fail(ErrorCode.INVALID_TRANSITION)
        if not self._writer.acquire(timeout=self.settings.integer("storage.busy_timeout_ms") / 1000):
            raise StorageUnavailable("locked")
        try:
            yield
        finally:
            self._writer.release()

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise StorageUnavailable("io_error")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.settings.integer("storage.busy_timeout_ms") / 1000,
                isolation_level=None,
            )
            # Every unit of work and read snapshot opens its own connection. By default the last one to close
            # checkpoints the WAL and deletes it under an exclusive lock, and a reader opening meanwhile waits on
            # that lock: with synchronous=FULL on a slow disk the wait outlasts the busy timeout and a plain read
            # fails "locked" while writes run (E32 load test, CI). Checkpoints still run on commit (autocheckpoint).
            connection.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA recursive_triggers=ON")
            connection.setlimit(
                sqlite3.SQLITE_LIMIT_SQL_LENGTH, self.settings.integer("storage.max_statement_bytes")
            )
            return connection
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise storage_error(error) from None

    @staticmethod
    def _statements(sql: str) -> tuple[str, ...]:
        statements: list[str] = []
        pending = ""
        for character in sql:
            pending += character
            if character == ";" and sqlite3.complete_statement(pending):
                statements.append(pending)
                pending = ""
        if pending.strip():
            raise StorageUnavailable("corrupt")
        return tuple(statements)

    @classmethod
    def _migrate(cls, connection: sqlite3.Connection, sql: str) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in cls._statements(sql):
                connection.execute(statement)
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()

    def migrate_tracked(self, name: str, sha256: str, sql: str) -> None:
        if (
            hashlib.sha256(sql.encode()).hexdigest() != sha256
            or not name
            or len(sql.encode()) > self.settings.integer("storage.max_statement_bytes")
        ):
            raise StorageUnavailable("corrupt")
        if not self._writer.acquire(timeout=self.settings.integer("storage.busy_timeout_ms") / 1000):
            raise StorageUnavailable("locked")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT sha256 FROM platform_migration WHERE name=?", (name,)
            ).fetchone()
            if prior is not None:
                if prior != (sha256,):
                    raise StorageUnavailable("corrupt")
            else:
                for statement in self._statements(sql):
                    connection.execute(statement)
                connection.execute("INSERT INTO platform_migration VALUES (?,?)", (name, sha256))
            connection.commit()
        except sqlite3.Error as error:
            raise storage_error(error) from None
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()
            self._writer.release()

    def migrate(self, sql: str) -> None:
        if getattr(self._local, "writing", False):
            raise fail(ErrorCode.INVALID_TRANSITION)
        with self._exclusive():
            connection = self._connect()
            try:
                self._migrate(connection, sql)
            except sqlite3.Error as error:
                raise storage_error(error) from None
            finally:
                connection.close()

    @contextmanager
    def begin(
        self, *, actor: Actor, command: CommandIdentity | None = None, correlation_id: str
    ) -> Iterator[UnitOfWork]:
        if getattr(self._local, "writing", False):
            raise fail(ErrorCode.INVALID_TRANSITION)
        if not self._required_guards <= self._guards.keys():
            raise fail(ErrorCode.CAPABILITY_UNAVAILABLE)
        if not self._admission.acquire(blocking=False):
            raise StorageUnavailable("locked")
        if not self._writer.acquire(timeout=self.settings.integer("storage.busy_timeout_ms") / 1000):
            self._admission.release()
            raise StorageUnavailable("locked")
        connection: sqlite3.Connection | None = None
        session: SQLiteSession | None = None
        self._local.writing = True
        committed = False
        disk_full = False
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            self._tracking(connection)
            deadline = (
                self.clock.monotonic_ns()
                + self.settings.integer("storage.transaction_timeout_ms") * 1_000_000
            )

            def expired() -> bool:
                return self.clock.monotonic_ns() > deadline

            session = SQLiteSession(connection, self.settings, readonly=False, expired=expired)
            connection.set_authorizer(_write_authorizer)

            now = self.clock.now()
            uow = Transaction(self, session, self._pin(session, now), now, actor, correlation_id)
            for name in sorted(self._guards):
                self._guards[name](uow)
            yield uow
            self._verify_declarations(connection, uow)
            if expired():
                # storage.transaction_timeout_ms (E01 11): a callback that held the writer too long fails loudly.
                raise ProblemError(ErrorCode.INTERNAL_ERROR, "transaction deadline exceeded", retryable=True)
            connection.set_authorizer(None)
            connection.commit()
            committed = True
        except StorageUnavailable as error:
            disk_full = error.cause == "disk_full"
            raise
        except sqlite3.Error as error:
            fault = storage_error(error)
            disk_full = isinstance(fault, StorageUnavailable) and fault.cause == "disk_full"
            raise fault from None
        finally:
            if session is not None:
                session.active = False
            if connection is not None:
                connection.set_authorizer(None)
                if connection.in_transaction:
                    connection.rollback()
                connection.close()
            self._local.writing = False
            self._writer.release()
            self._admission.release()
            if disk_full:
                for callback in tuple(self._disk_full_handlers):
                    callback()
        if committed:
            for callback in tuple(self._postcommit):
                callback()

    @contextmanager
    def snapshot(self) -> Iterator[ReadSnapshot]:
        connection = self._connect()
        session = SQLiteSession(connection, self.settings, readonly=True)
        try:
            connection.execute("BEGIN")
            connection.execute("SELECT singleton FROM platform_identity").fetchone()
            now = self.clock.now()
            yield SQLiteView(session, self._pin(session, now), now)
        finally:
            session.active = False
            connection.set_authorizer(None)
            connection.rollback()
            connection.close()

    def savepoint(self, session: SQLiteSession, name: str) -> Iterator[None]:
        require_id(name)
        connection = session._connection

        def internal(statement: str) -> None:
            connection.set_authorizer(None)
            try:
                connection.execute(statement)
            finally:
                connection.set_authorizer(_write_authorizer)

        internal('SAVEPOINT "' + name + '"')
        try:
            yield
        except BaseException:
            internal('ROLLBACK TO "' + name + '"')
            raise
        finally:
            internal('RELEASE "' + name + '"')

    def retention_advance(self, uow: UnitOfWork, through: int) -> None:

        if not isinstance(uow, Transaction) or uow.db is not self or through < 0:
            raise fail(ErrorCode.INVALID_TRANSITION)
        connection = uow.connection._connection
        connection.set_authorizer(None)
        try:
            connection.execute("UPDATE events_retention SET watermark=? WHERE singleton=1", (through,))
        except sqlite3.Error as error:
            raise storage_error(error) from None
        finally:
            connection.set_authorizer(_write_authorizer)

    def close(self) -> None:
        self._closed = True

    def backup_to(self, destination: Path) -> str:
        with self._exclusive():
            source = self._connect()
            target: sqlite3.Connection | None = None
            try:
                target = sqlite3.connect(destination)
                source.backup(target, pages=self.settings.integer("storage.max_rows"), sleep=0)
            except sqlite3.Error as error:
                raise storage_error(error) from None
            finally:
                if target is not None:
                    target.close()
                source.close()
        digest = hashlib.sha256()
        with destination.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()


class Transaction(SQLiteView):
    def __init__(
        self,
        db: SQLiteDatabase,
        session: SQLiteSession,
        release: ReleaseHandle,
        now: datetime,
        actor: Actor,
        correlation_id: str,
    ) -> None:
        super().__init__(session, release, now)
        self.db, self._actor, self._correlation_id = db, actor, correlation_id
        self.declared_replication: set[tuple[str, str]] = set()

    @property
    def actor(self) -> Actor:
        return self._actor

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    def publish(self, event: DomainEvent) -> None:
        self.db.bus.publish(self, event)

    def emit(self, event: OutboxEvent) -> StoredEvent:
        if self.db.event_sink is None:
            raise fail(ErrorCode.CONFIG_INVALID)
        return self.db.event_sink(self, event)

    def audit(self, entry: AuditEntry) -> None:
        AuditRepository(
            node_id=self.db.origin_node_id,
            incarnation=self.db.origin_incarnation,
            policies=self.db.audit_policies,
        ).append(self, entry)

    def schedule(self, job: ScheduledJob, *, expected_generation: int | None) -> int:
        return self.db.scheduler.replace(self, job, expected_generation=expected_generation)

    def cancel_schedule(self, key: str, *, expected_generation: int) -> bool:
        return self.db.scheduler.cancel(self, key, expected_generation=expected_generation)

    def replicate(self, change: ReplicationChange) -> ReplicationRef:
        from crew_health.platform.adapters.replication import append_change

        if len(self.declared_replication) >= self.db.settings.integer("storage.max_rows"):
            raise fail(ErrorCode.PAYLOAD_TOO_LARGE)
        self.declared_replication.add((change.record_type, change.record_id))
        stamp = None if self.db.hlc is None else self.db.hlc.tick(self)
        return append_change(
            self, change, node_id=self.db.origin_node_id, incarnation=self.db.origin_incarnation, hlc=stamp
        )

    @contextmanager
    def savepoint(self, name: str) -> Iterator[None]:
        queue = tuple(self.db.bus.queue)
        declared = set(self.declared_replication)
        try:
            with contextmanager(self.db.savepoint)(self.connection, name):
                yield
        except BaseException:
            self.db.bus.queue[:] = queue
            self.declared_replication = declared
            raise


def inspect_sqlite(path: Path) -> None:
    if not path.is_file():
        raise StorageUnavailable("corrupt")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        connection.execute("PRAGMA trusted_schema=OFF")
        if (
            connection.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",)
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise StorageUnavailable("corrupt")
    except sqlite3.Error as error:
        raise storage_error(error) from None
    finally:
        if connection is not None:
            connection.close()


def stored_identity(path: Path) -> tuple[str, str] | None:
    """The node and incarnation an existing hub database was initialized with, read without writing; None when
    the file does not exist yet. A file without exactly one identity row is corrupt, never re-identified."""
    if not path.exists():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        connection.execute("PRAGMA trusted_schema=OFF")
        row = connection.execute(
            "SELECT node_id, incarnation FROM platform_identity WHERE singleton=1"
        ).fetchone()
    except sqlite3.Error as error:
        raise storage_error(error) from None
    finally:
        if connection is not None:
            connection.close()
    if not isinstance(row, tuple) or not all(isinstance(value, str) for value in row):
        raise StorageUnavailable("corrupt")
    return require_id(row[0]), require_id(row[1])


@contextmanager
def snapshot_file(
    path: Path, *, settings: HubSettings, release: ReleaseHandle, clock: Clock
) -> Iterator[ReadSnapshot]:
    if not path.is_file():
        raise StorageUnavailable("corrupt")
    connection: sqlite3.Connection | None = None
    session: SQLiteSession | None = None
    try:
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, timeout=settings.integer("storage.busy_timeout_ms") / 1000
        )
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        session = SQLiteSession(connection, settings, readonly=True)
        yield SQLiteView(session, release, clock.now())
    except sqlite3.Error as error:
        raise storage_error(error) from None
    finally:
        if session is not None:
            session.active = False
        if connection is not None:
            connection.set_authorizer(None)
            connection.rollback()
            connection.close()


class SystemIds:
    def __init__(self, *, new_uuid: Callable[[], UUID] = uuid7) -> None:
        self._new_uuid = new_uuid

    def new(self, prefix: str) -> str:
        return require_id(prefix + "_" + self._new_uuid().hex)

    def derived(self, prefix: str, *parts: str) -> str:
        from crew_health.kernel.commands import canonical_json

        digest = hashlib.sha256(canonical_json([prefix, *parts])).hexdigest()
        return require_id(prefix + "_" + digest[:32])
