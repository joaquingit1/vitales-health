"""Unit of work guards around transaction control, deadlines and connection lifetimes (SYSTEM 5.7, E01 11)."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.testing import MemoryClock
from crew_health.kernel.uow import Actor
from crew_health.platform import storage
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-02", "SYSTEM.5.7", "AGENTS.6")]
ENGINE = Actor("engine", "engine_1")


def _rows(db: storage.SQLiteDatabase) -> tuple[tuple[object, ...], ...]:
    with db.snapshot() as view:
        return view.connection.rows("SELECT id FROM guard_fact", (), limit=10)


@pytest.mark.parametrize(
    "statement",
    [
        "COMMIT",
        "/* application SQL */ COMMIT",
        "-- note\nCOMMIT",
        " /*a*/ /*b*/ END",
        "/* x */ ROLLBACK",
        "/* x */ BEGIN",
        "/* x */ SAVEPOINT escape",
        "/* x */ RELEASE escape",
    ],
)
def test_transaction_control_from_module_sql_is_refused_and_the_write_rolls_back(
    tmp_path: Path, statement: str
) -> None:
    db = database(tmp_path)
    db.migrate("CREATE TABLE guard_fact (id TEXT PRIMARY KEY) STRICT;")
    with (
        pytest.raises((ProblemError, StorageUnavailable)),
        db.begin(actor=ENGINE, correlation_id="c1") as uow,
    ):
        uow.connection.execute("INSERT INTO guard_fact VALUES (?)", ("kept_only_if_committed",))
        uow.connection.execute(statement)
    assert _rows(db) == ()
    with db.begin(actor=ENGINE, correlation_id="c2") as uow:
        with pytest.raises(ProblemError), uow.savepoint("inner"):
            uow.connection.execute("INSERT INTO guard_fact VALUES (?)", ("rolled_back_to_savepoint",))
            raise ProblemError(ErrorCode.INVALID_VALUE, "inner failure")
        uow.connection.execute("INSERT INTO guard_fact VALUES (?)", ("committed",))
    assert _rows(db) == (("committed",),)


def test_transaction_past_its_deadline_fails_loudly_and_rolls_back(tmp_path: Path) -> None:
    clock = MemoryClock(datetime(2026, 11, 14, tzinfo=UTC), 0, "epoch_1")
    db = database(tmp_path, clock=clock)
    db.migrate("CREATE TABLE guard_fact (id TEXT PRIMARY KEY) STRICT;")
    timeout_ns = db.settings.integer("storage.transaction_timeout_ms") * 1_000_000
    with pytest.raises(ProblemError) as caught, db.begin(actor=ENGINE, correlation_id="c1") as uow:
        uow.connection.execute("INSERT INTO guard_fact VALUES (?)", ("late",))
        clock.monotonic += timeout_ns + 1
    assert caught.value.retryable
    assert _rows(db) == ()
    with db.begin(actor=ENGINE, correlation_id="c2") as uow:
        uow.connection.execute("INSERT INTO guard_fact VALUES (?)", ("on_time",))
    assert _rows(db) == (("on_time",),)


def test_failed_connect_in_tracked_migration_releases_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = database(tmp_path)
    original = db._connect
    calls = {"n": 0}

    def failing_once() -> sqlite3.Connection:
        calls["n"] += 1
        if calls["n"] == 1:
            raise StorageUnavailable("io_error")
        return original()

    monkeypatch.setattr(db, "_connect", failing_once)
    sql = "CREATE TABLE probe_m (id TEXT PRIMARY KEY) STRICT;"
    with pytest.raises(StorageUnavailable):
        db.migrate_tracked("probe.0001", hashlib.sha256(sql.encode()).hexdigest(), sql)
    with db.begin(actor=ENGINE, correlation_id="after_failure") as uow:
        assert uow.connection.rows("SELECT 1", (), limit=1) == ((1,),)


def test_connection_is_closed_when_a_pragma_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = database(tmp_path)
    closed: list[bool] = []
    real_connect = sqlite3.connect

    class Wrapped:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self.inner = inner

        def execute(self, sql: str, *args: object) -> object:
            if sql.startswith("PRAGMA synchronous"):
                raise sqlite3.OperationalError("injected pragma failure")
            return self.inner.execute(sql, *args)  # type: ignore[arg-type]

        def close(self) -> None:
            closed.append(True)
            self.inner.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: Wrapped(real_connect(*a, **k)))
    with pytest.raises(StorageUnavailable):
        db._connect()
    assert closed == [True]


def test_backup_hash_matches_the_written_file(tmp_path: Path) -> None:
    db = database(tmp_path / "hub")
    target = tmp_path / "backup.sqlite"
    assert db.backup_to(target) == hashlib.sha256(target.read_bytes()).hexdigest()
