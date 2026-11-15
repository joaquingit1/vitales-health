"""T-E01-16 R25: real SQLite capacity rollback and explicit ENOSPC adapter behavior."""

import errno
from pathlib import Path

import pytest

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.uow import Actor
from crew_health.platform.adapters.recovery import DiskReserve, RecoveryRepository
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-16", "R25")]


def test_actual_sqlite_page_limit_aborts_whole_command_and_reserve_is_independent(tmp_path, monkeypatch):
    db = database(tmp_path)
    db.migrate("CREATE TABLE capacity_fact(id TEXT PRIMARY KEY, payload BLOB NOT NULL) STRICT;")
    reserve = DiskReserve(tmp_path / "reserve.bin", settings=db.settings, clock=db.clock)
    reserve.allocate()
    db.add_disk_full_handler(reserve.release)
    original = db._connect

    def limited():
        connection = original()
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        connection.execute(f"PRAGMA max_page_count={pages}")
        return connection

    monkeypatch.setattr(db, "_connect", limited)
    with (
        pytest.raises(StorageUnavailable) as error,
        db.begin(actor=Actor("operator", "operator_1"), correlation_id="correlation_1") as uow,
    ):
        uow.connection.execute("INSERT INTO capacity_fact VALUES(?,?)", ("accepted_1", bytes(65536)))
        uow.audit(AuditEntry("audit_full", "storage.fault", 1, uow.actor, {}, uow.correlation_id, None, {}))
    assert error.value.cause == "disk_full"
    assert not reserve.path.exists()
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM capacity_fact", (), limit=1) == ((0,),)
    with (
        pytest.raises(StorageUnavailable),
        db.begin(actor=Actor("operator", "operator_1"), correlation_id="correlation_1") as uow,
    ):
        uow.connection.execute("INSERT INTO capacity_fact VALUES(?,?)", ("accepted_1", bytes(65536)))
    monkeypatch.setattr(db, "_connect", original)
    with db.begin(actor=Actor("operator", "operator_1"), correlation_id="correlation_1") as uow:
        uow.connection.execute("INSERT INTO capacity_fact VALUES(?,?)", ("accepted_1", bytes(65536)))
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM capacity_fact", (), limit=1) == ((1,),)


def test_enospc_directory_error_is_typed_and_never_an_accepted_stage(tmp_path, monkeypatch):
    target = tmp_path / "stage"
    original = Path.mkdir

    def full(path, *args, **kwargs):
        if path == target:
            raise OSError(errno.ENOSPC, "PRIVATECANARY")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", full)
    with pytest.raises(StorageUnavailable) as error:
        RecoveryRepository.prepare(target)
    assert error.value.cause == "disk_full"
    assert str(error.value) == "disk_full"
    assert not target.exists()


def test_still_full_failure_cannot_claim_fault_audit_was_persisted(tmp_path, monkeypatch):
    db = database(tmp_path)
    db.register_audit_policy(AuditPolicy("storage.fault", {}, frozenset(), {}))
    original = db._connect

    def limited():
        connection = original()
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        connection.execute(f"PRAGMA max_page_count={pages}")
        return connection

    monkeypatch.setattr(db, "_connect", limited)
    with (
        pytest.raises(StorageUnavailable),
        db.begin(actor=Actor("operator", "operator_1"), correlation_id="correlation_1") as uow,
    ):
        for attempt in range(db.settings.integer("storage.max_rows")):
            uow.audit(
                AuditEntry(
                    f"audit_full_{attempt}", "storage.fault", 1, uow.actor, {}, uow.correlation_id, None, {}
                )
            )

    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((0,),)
