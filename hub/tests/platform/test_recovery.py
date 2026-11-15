"""T-E01-17 R25: staged capture, trusted checksums and expand-only migrations."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace

import pytest

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.uow import Actor
from crew_health.platform.adapters.migrations import MigrationAuthorization, MigrationRunner, MigrationSpec
from crew_health.platform.adapters.recovery import RecoveryRepository
from crew_health.platform.application.recovery import CaptureService, RecoveryService
from crew_health.platform.storage import inspect_sqlite
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-17", "R25")]


class Authorization:
    def __init__(self, migrations):
        self.migrations = {value.name: value.sha256 for value in migrations}

    def verify(self, proof):
        if proof != b"trusted-installation-proof":
            raise ProblemError(ErrorCode.CONFIG_INVALID, "CONFIG_INVALID")
        return MigrationAuthorization(
            frozenset({"one", "two"}), frozenset({"one", "two"}), self.migrations, "expand"
        )


def migration(name, sql):
    return MigrationSpec(name, sql, hashlib.sha256(sql.encode()).hexdigest())


def test_checksum_atomic_expansion_and_previous_reader_preserve_rows(tmp_path):
    db = database(tmp_path)
    first = migration("example_0001", "CREATE TABLE example_record(id TEXT PRIMARY KEY) STRICT;")
    second = migration("example_0002", "CREATE TABLE example_extra(id TEXT PRIMARY KEY) STRICT;")
    auth = Authorization((first, second))
    runner = MigrationRunner(database=db, settings=db.settings, verifier=auth)
    runner.apply((first,), proof=b"trusted-installation-proof", executable_version="one")
    with db.begin(actor=Actor("operator", "operator_1"), correlation_id="correlation_1") as uow:
        uow.connection.execute("INSERT INTO example_record VALUES(?)", ("accepted_1",))
    runner.apply((first, second), proof=b"trusted-installation-proof", executable_version="two")
    runner.require_compatible(proof=b"trusted-installation-proof", executable_version="one", write=False)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT id FROM example_record", (), limit=1) == (("accepted_1",),)
        assert view.connection.rows("SELECT id FROM example_extra", (), limit=1) == ()
    runner.apply((first, second), proof=b"trusted-installation-proof", executable_version="two")
    with pytest.raises(ProblemError):
        runner.apply(
            (replace(first, sql=first.sql + " "),),
            proof=b"trusted-installation-proof",
            executable_version="one",
        )
    with pytest.raises(ProblemError):
        runner.require_compatible(
            proof=b"trusted-installation-proof", executable_version="unsupported", write=False
        )


def test_missing_verifier_and_destructive_or_failed_migration_do_not_change_data(tmp_path):
    db = database(tmp_path)
    first = migration("safe_0001", "CREATE TABLE safe_record(id TEXT PRIMARY KEY) STRICT;")
    destructive = migration("bad_0002", "DROP TABLE safe_record;")
    runner = MigrationRunner(database=db, settings=db.settings, verifier=Authorization((first, destructive)))
    with pytest.raises(ProblemError):
        MigrationRunner(database=db, settings=db.settings, verifier=None).apply(
            (first,), proof=b"", executable_version="one"
        )
    runner.apply((first,), proof=b"trusted-installation-proof", executable_version="one")
    with pytest.raises(ProblemError):
        runner.apply((destructive,), proof=b"trusted-installation-proof", executable_version="one")
    failed = migration(
        "bad_0003",
        "CREATE TABLE temporary_expansion(id TEXT PRIMARY KEY) STRICT; CREATE TABLE safe_record(id TEXT PRIMARY KEY) STRICT;",
    )
    with pytest.raises(StorageUnavailable):
        MigrationRunner(database=db, settings=db.settings, verifier=Authorization((first, failed))).apply(
            (failed,), proof=b"trusted-installation-proof", executable_version="one"
        )
    with db.snapshot() as view:
        assert (
            view.connection.rows(
                "SELECT name FROM sqlite_master WHERE name=?", ("temporary_expansion",), limit=1
            )
            == ()
        )
        assert (
            view.connection.rows("SELECT name FROM platform_migration WHERE name=?", ("bad_0003",), limit=1)
            == ()
        )


class Barrier:
    def __init__(self):
        self.entered = False

    @contextmanager
    def backup_barrier(self):
        assert not self.entered
        self.entered = True
        try:
            yield
        finally:
            self.entered = False

    def verify_live_references(self):
        assert self.entered
        return 0


class PrivateCapture:
    def __init__(self, db, barrier):
        self.db, self.barrier = db, barrier

    def capture(self, destination):
        assert self.barrier.entered
        return self.db.backup_to(destination)

    def verify(self, path, watermark):
        inspect_sqlite(path)
        assert watermark == 0


class Vault:
    def generation(self):
        return 1

    def require_current(self, minimum_generation):
        assert minimum_generation <= 1


def test_paired_staging_is_wal_consistent_and_is_never_an_installation(tmp_path):
    live = tmp_path / "live"
    private = tmp_path / "private"
    db = database(live)
    private_db = database(private)
    barrier = Barrier()
    repository = RecoveryRepository(database=db)
    service = CaptureService(
        hub=db,
        coordinator=barrier,
        private=PrivateCapture(private_db, barrier),
        repository=repository,
        vault=Vault(),
        settings=db.settings,
    )
    staged = service.stage(tmp_path / "stage")
    assert staged.state == "staged_unverified"
    assert not barrier.entered
    inspect_sqlite(staged.hub_path)
    inspect_sqlite(staged.private_path)
    assert staged.facts.reference_watermark == 0
    assert staged.facts.audit_head_hash == "0" * 64
    assert staged.facts.hub_sha256 == hashlib.sha256(staged.hub_path.read_bytes()).hexdigest()
    recovery = RecoveryService(verifier=None, private=PrivateCapture(private_db, barrier), vault=Vault())
    with pytest.raises(ProblemError):
        recovery.verify(staged.directory)
    with pytest.raises(ProblemError):
        recovery.install(staged)


def test_stage_does_not_overwrite_existing_destination(tmp_path):
    db = database(tmp_path / "live")
    private_db = database(tmp_path / "private")
    barrier = Barrier()
    service = CaptureService(
        hub=db,
        coordinator=barrier,
        private=PrivateCapture(private_db, barrier),
        repository=RecoveryRepository(database=db),
        vault=Vault(),
        settings=db.settings,
    )
    target = tmp_path / "stage"
    target.mkdir()
    (target / "accepted").write_bytes(b"retained")
    with pytest.raises(ProblemError):
        service.stage(target)
    assert (target / "accepted").read_bytes() == b"retained"


def test_cli_never_starts_with_sample_configuration_or_exposes_input(tmp_path, capsys):
    from crew_health.platform.cli import main

    db = database(tmp_path)
    assert main(["serve"]) == 2
    assert "CAPABILITY_UNAVAILABLE" in capsys.readouterr().out
    assert main(["doctor", "--database", str(db.path)]) == 0
    assert "trusted_backup" in capsys.readouterr().out
    assert main(["doctor", "PRIVATECANARYSECRET"]) == 2
    output = capsys.readouterr()
    assert "PRIVATECANARYSECRET" not in output.out + output.err
    assert "MALFORMED_REQUEST" in output.out
