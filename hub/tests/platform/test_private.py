from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import Role
from crew_health.kernel.commands import command_identity
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.private import PrivateIntent, PrivatePublicationRejected, SealedPrivateRef
from crew_health.kernel.testing import MemoryPrivateStore, MemoryVault
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork
from crew_health.platform.adapters.intents import IntentRepository
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.adapters.private import PrivateReferences
from crew_health.platform.application.health import ComponentHealthState
from crew_health.platform.application.private import (
    PENDING_DELETIONS,
    PrivateCoordinator,
    private_identity_bytes,
)
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-04", "T-E01-14", "R25")]


class Writer:
    def attach(self, uow: UnitOfWork, reference: SealedPrivateRef, intent: PrivateIntent) -> None:
        uow.connection.execute("INSERT INTO test_private_owner VALUES (?)", (reference.entry_id,))

    def tombstone(self, uow: UnitOfWork, *, entry_ids: tuple[str, ...], revocation_ref: str) -> None:
        for entry in entry_ids:
            uow.connection.execute("DELETE FROM test_private_owner WHERE id=?", (entry,))


class FaultStore(MemoryPrivateStore):
    fail_ack = False

    def acknowledge(self, reference, receipt):
        if self.fail_ack:
            raise StorageUnavailable("disk_full")
        return super().acknowledge(reference, receipt)


def station(tmp_path: Path):
    db = database(tmp_path)
    db.migrate("CREATE TABLE test_private_owner (id TEXT PRIMARY KEY) STRICT;")
    vault = MemoryVault(
        ids=db.ids,
        settings=db.settings,
        secrets={"person_1": b"test-private-key"},
        authorize=lambda session, ref: session.person_id == ref.owner_person_id,
    )
    store = FaultStore(vault=vault, settings=db.settings)
    ledger = CommandLedger(db.ids)
    coordinator = PrivateCoordinator(
        transactions=db,
        snapshots=db,
        store=store,
        vault=vault,
        writers={"journal": Writer()},
        ledger=ledger,
        intents=IntentRepository(ledger),
        references=PrivateReferences(),
        ids=db.ids,
        settings=db.settings,
        authorize=lambda s, owner: s.person_id == owner,
        health=ComponentHealthState(maximum=db.settings.integer("registry.max_hooks")),
        clock=db.clock,
    )
    session = SessionContext(
        "session_1", "crew", Actor("person", "person_1"), "person_1", frozenset({Role.CREW})
    )
    identity = command_identity(
        key="command_1",
        actor_ref="person_1",
        operation="writeJournal",
        target="/journal",
        parameters_json=b"{}",
        body_fingerprint="",
        fingerprint_kind="hmac_sha256",
    )
    intent = PrivateIntent(
        identity, "person_1", "entry_1", "journal", "version_1", None, b"SECRETCLINICALCANARY"
    )
    fingerprint = vault.fingerprint(
        owner_person_id="person_1", canonical_identity_and_body=private_identity_bytes(intent)
    )
    return (
        db,
        coordinator,
        vault,
        store,
        session,
        replace(intent, identity=replace(identity, body_fingerprint=fingerprint)),
    )


def test_private_repair_after_hub_commit_and_exact_retry(tmp_path: Path) -> None:
    db, coordinator, vault, store, session, intent = station(tmp_path)
    store.fail_ack = True
    with pytest.raises(StorageUnavailable):
        coordinator.write(session, intent)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((1,),)
    store.fail_ack = False
    assert coordinator.recover(limit=10) == 1
    receipt = coordinator.write(session, intent)
    assert receipt == store.receipts["entry_1"]
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM test_private_owner", (), limit=1) == ((1,),)
        for table in ("platform_private_reference", "platform_audit", "platform_command_ledger", "repl_log"):
            rows = view.connection.rows("SELECT * FROM " + table, (), limit=100)
            assert "SECRETCLINICALCANARY" not in str(rows)


def test_private_bad_hmac_other_owner_and_late_publication(tmp_path: Path) -> None:
    db, coordinator, vault, store, session, intent = station(tmp_path)
    other = replace(session, person_id="person_2", actor=Actor("person", "person_2"))
    with pytest.raises(ProblemError):
        coordinator.write(other, intent)
    with pytest.raises(ProblemError):
        coordinator.write(session, replace(intent, plaintext=b"changed"))
    sealed = store.seal(intent)
    with pytest.raises(PrivatePublicationRejected):
        coordinator.publish(session, intent, sealed, lambda uow: False)
    assert vault.is_revoked(entry_id="entry_1")
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_private_reference", (), limit=1) == ((0,),)


def test_orphan_recovery_and_barrier(tmp_path: Path) -> None:
    db, coordinator, vault, store, session, intent = station(tmp_path)
    store.seal(intent)
    assert coordinator.recover(limit=1) == 1
    assert vault.is_revoked(entry_id=intent.entry_id)
    with coordinator.backup_barrier(), pytest.raises(ProblemError):
        coordinator.write(session, intent)


def test_private_deletion_retries_and_blocks_republication(tmp_path: Path) -> None:
    db, coordinator, vault, store, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    deletion = replace(intent.identity, key="command_2", operation="deleteJournal")
    result = coordinator.delete(
        session, identity=deletion, owner_person_id="person_1", entry_ids=("entry_1",)
    )
    assert result.revocation.complete and result.hub_reference_seq == 1
    assert (
        coordinator.delete(session, identity=deletion, owner_person_id="person_1", entry_ids=("entry_1",))
        == result
    )
    with pytest.raises(ProblemError):
        coordinator.write(session, intent)


def test_missing_acknowledged_ciphertext_refuses_recovery_and_backup(tmp_path: Path) -> None:
    db, coordinator, _, store, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    del store.entries[intent.entry_id]
    with pytest.raises(StorageUnavailable):
        coordinator.recover(limit=10)
    with pytest.raises(StorageUnavailable), coordinator.backup_barrier():
        pytest.fail("missing ciphertext entered backup barrier")
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_private_reference", (), limit=1) == ((1,),)
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((1,),)


def test_seal_failure_has_no_hub_acceptance_and_retry_is_once(tmp_path: Path, monkeypatch) -> None:
    db, coordinator, _, store, session, intent = station(tmp_path)
    original = store.seal

    def failed_seal(value):
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(store, "seal", failed_seal)
    with pytest.raises(StorageUnavailable):
        coordinator.write(session, intent)
    with db.snapshot() as view:
        for table in (
            "platform_private_reference",
            "platform_command_ledger",
            "platform_audit",
            "test_private_owner",
        ):
            assert view.connection.rows("SELECT count(*) FROM " + table, (), limit=1) == ((0,),)
    monkeypatch.setattr(store, "seal", original)
    first = coordinator.write(session, intent)
    assert coordinator.write(session, intent) == first
    assert len(store.entries) == 1


def test_hub_reference_fault_rolls_back_every_hub_effect(tmp_path: Path, monkeypatch) -> None:
    db, coordinator, _, store, session, intent = station(tmp_path)
    writer = coordinator.writers["journal"]
    original = writer.attach

    def failed_attach(uow, reference, private_intent):
        original(uow, reference, private_intent)
        raise StorageUnavailable("disk_full")

    monkeypatch.setattr(writer, "attach", failed_attach)
    with pytest.raises(StorageUnavailable):
        coordinator.write(session, intent)
    assert intent.entry_id in store.entries
    assert intent.entry_id not in store.receipts
    with db.snapshot() as view:
        for table in (
            "platform_private_reference",
            "platform_command_ledger",
            "platform_audit",
            "test_private_owner",
        ):
            assert view.connection.rows("SELECT count(*) FROM " + table, (), limit=1) == ((0,),)
    monkeypatch.setattr(writer, "attach", original)
    receipt = coordinator.write(session, intent)
    assert receipt == store.receipts[intent.entry_id]


def test_pending_vault_revocation_never_accepts_parent_deletion(tmp_path: Path, monkeypatch) -> None:
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    original = vault.revoke

    def pending_revoke(**kwargs):
        receipt = original(**kwargs)
        return replace(receipt, complete=False, outstanding_replica_ids=("replica_1",))

    monkeypatch.setattr(vault, "revoke", pending_revoke)
    deletion = replace(intent.identity, key="command_delete", operation="deleteJournal")
    pending = coordinator.delete(
        session, identity=deletion, owner_person_id="person_1", entry_ids=(intent.entry_id,)
    )
    assert not pending.revocation.complete
    assert pending.hub_reference_seq is None
    assert vault.is_revoked(entry_id=intent.entry_id)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT completed FROM platform_private_deletion", (), limit=1) == ((0,),)
        assert view.connection.rows(
            "SELECT count(*) FROM platform_command_ledger WHERE command_key=?", ("command_delete",), limit=1
        ) == ((0,),)
        assert view.connection.rows("SELECT deleted FROM platform_private_reference", (), limit=1) == ((0,),)
    monkeypatch.setattr(vault, "revoke", original)
    assert coordinator.delete(
        session, identity=deletion, owner_person_id="person_1", entry_ids=(intent.entry_id,)
    ).revocation.complete


def test_rejected_late_publication_rolls_back_callback_effects(tmp_path: Path) -> None:
    db, coordinator, vault, store, session, intent = station(tmp_path)
    sealed = store.seal(intent)

    def rejected(uow):
        uow.connection.execute("INSERT INTO test_private_owner VALUES(?)", ("late_effect",))
        return False

    with pytest.raises(PrivatePublicationRejected):
        coordinator.publish(session, intent, sealed, rejected)
    assert vault.is_revoked(entry_id=intent.entry_id)
    with db.snapshot() as view:
        for table in (
            "platform_private_reference",
            "platform_command_ledger",
            "platform_audit",
            "test_private_owner",
        ):
            assert view.connection.rows("SELECT count(*) FROM " + table, (), limit=1) == ((0,),)


def test_shared_barrier_serializes_journal_and_report_writers(tmp_path: Path) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    db, coordinator, vault, store, session, intent = station(tmp_path)
    coordinator.writers["report"] = Writer()
    report = replace(
        intent,
        identity=replace(intent.identity, key="command_report", target="/report"),
        entry_id="entry_report",
        version_id="version_report",
        record_kind="report",
        plaintext=b"PRIVATE_REPORT_TEST",
    )
    report = replace(
        report,
        identity=replace(
            report.identity,
            body_fingerprint=vault.fingerprint(
                owner_person_id="person_1", canonical_identity_and_body=private_identity_bytes(report)
            ),
        ),
    )
    started = [threading.Event(), threading.Event()]

    def write(index, value):
        started[index].set()
        return coordinator.write(session, value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with coordinator.backup_barrier():
            first = pool.submit(write, 0, intent)
            second = pool.submit(write, 1, report)
            assert all(event.wait(timeout=1) for event in started)
            assert not first.done() and not second.done()
            assert not store.entries
        assert first.result(timeout=5).entry_id == intent.entry_id
        assert second.result(timeout=5).entry_id == report.entry_id
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_private_reference", (), limit=1) == ((2,),)
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((2,),)


def test_capture_joins_real_coordinator_barrier_without_nested_entry(tmp_path: Path) -> None:
    from crew_health.platform.adapters.recovery import RecoveryRepository
    from crew_health.platform.application.recovery import CaptureService
    from crew_health.platform.storage import inspect_sqlite

    db, coordinator, _, _, _, _ = station(tmp_path)
    private_db = database(tmp_path / "private")

    class PrivateSnapshot:
        def capture(self, destination):
            return private_db.backup_to(destination)

        def verify(self, path, watermark):
            inspect_sqlite(path)
            assert watermark == 0

    class CurrentVault:
        def generation(self):
            return 0

        def require_current(self, minimum_generation):
            assert minimum_generation == 0

    service = CaptureService(
        hub=db,
        coordinator=coordinator,
        private=PrivateSnapshot(),
        repository=RecoveryRepository(database=db),
        vault=CurrentVault(),
        settings=db.settings,
    )
    assert service.stage(tmp_path / "stage").state == "staged_unverified"


def test_deleted_source_version_cannot_publish_an_unreferenced_extraction(tmp_path):
    from crew_health.platform.application.private import private_identity_bytes

    db, coordinator, vault, store, session, source = station(tmp_path)
    coordinator.write(session, source)
    identity = replace(source.identity, key="command_extract", operation="publishExtraction")
    draft = replace(
        source,
        identity=identity,
        entry_id="entry_draft",
        version_id="version_draft",
        source_version_id=source.version_id,
    )
    draft = replace(
        draft,
        identity=replace(
            identity,
            body_fingerprint=vault.fingerprint(
                owner_person_id=draft.owner_person_id,
                canonical_identity_and_body=private_identity_bytes(draft),
            ),
        ),
    )
    staged = store.seal(draft)
    coordinator.delete(
        session,
        identity=replace(source.identity, key="command_delete", operation="deleteJournal"),
        owner_person_id=source.owner_person_id,
        entry_ids=(source.entry_id,),
    )
    with pytest.raises(PrivatePublicationRejected):
        coordinator.publish(session, draft, staged, lambda uow: True)
    assert vault.is_revoked(entry_id=draft.entry_id)
    with db.snapshot() as view:
        assert view.connection.rows(
            "SELECT count(*) FROM platform_private_reference WHERE entry_id=?", (draft.entry_id,), limit=1
        ) == ((0,),)


@pytest.mark.trace("T-E01-04", "R25")
@pytest.mark.parametrize("field", ["operation", "target", "body_fingerprint", "parameters_sha256"])
def test_conflicting_deletion_key_never_calls_vault(tmp_path, monkeypatch, field):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    changes = {field: "different"}
    identity = replace(intent.identity, **changes)
    calls = []
    original = vault.revoke

    def revoke(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(vault, "revoke", revoke)
    with pytest.raises(ProblemError) as caught:
        coordinator.delete(
            session, identity=identity, owner_person_id="person_1", entry_ids=(intent.entry_id,)
        )
    assert caught.value.code.value == "DUPLICATE_COMMAND"
    assert calls == []
    assert not vault.is_revoked(entry_id=intent.entry_id)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT deleted FROM platform_private_reference", (), limit=1) == ((0,),)
        assert not view.connection.rows("SELECT completed FROM platform_private_deletion", (), limit=1)


@pytest.mark.trace("T-E01-04", "R25")
def test_deletion_reserves_shared_intent_before_vault_and_replays_without_revoke(tmp_path, monkeypatch):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    deletion = replace(intent.identity, key="command_delete", operation="deleteJournal")
    original = vault.revoke

    def revoke(**kwargs):
        with db.snapshot() as view:
            assert CommandLedger(db.ids).pending(view, deletion) is not None
        return original(**kwargs)

    monkeypatch.setattr(vault, "revoke", revoke)
    first = coordinator.delete(
        session, identity=deletion, owner_person_id="person_1", entry_ids=(intent.entry_id,)
    )
    monkeypatch.setattr(vault, "revoke", lambda **kwargs: pytest.fail("completed deletion repeated revoke"))
    assert (
        coordinator.delete(
            session, identity=deletion, owner_person_id="person_1", entry_ids=(intent.entry_id,)
        )
        == first
    )
    with db.snapshot() as view:
        assert CommandLedger(db.ids).pending(view, deletion) is None


@pytest.mark.trace("T-E01-04", "R25")
def test_recovery_finishes_revoked_deletion_once_after_tombstone_failure(tmp_path, monkeypatch):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    deletion = replace(intent.identity, key="command_delete", operation="deleteJournal")
    writer = coordinator.writers["journal"]
    original = writer.tombstone

    def fail_tombstone(*args, **kwargs):
        original(*args, **kwargs)
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(writer, "tombstone", fail_tombstone)
    with pytest.raises(StorageUnavailable):
        coordinator.delete(
            session, identity=deletion, owner_person_id="person_1", entry_ids=(intent.entry_id,)
        )
    assert vault.is_revoked(entry_id=intent.entry_id)
    monkeypatch.setattr(writer, "tombstone", original)
    assert coordinator.recover(limit=1) == 1
    assert coordinator.recover(limit=1) == 0
    with coordinator.backup_barrier():
        pass
    with db.snapshot() as view:
        assert view.connection.rows("SELECT completed FROM platform_private_deletion", (), limit=1) == ((1,),)
        assert view.connection.rows("SELECT deleted FROM platform_private_reference", (), limit=1) == ((1,),)
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((2,),)
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((2,),)
        assert view.connection.rows("SELECT count(*) FROM repl_log", (), limit=1) == ((3,),)
        assert not view.connection.rows("SELECT id FROM test_private_owner", (), limit=1)


@pytest.mark.trace("T-E01-04", "R25")
def test_backup_resolves_pending_seal_of_deleted_entry_without_new_key(tmp_path):
    _, coordinator, vault, store, session, intent = station(tmp_path)
    store.fail_ack = True
    with pytest.raises(StorageUnavailable):
        coordinator.write(session, intent)
    store.fail_ack = False
    coordinator.delete(
        session,
        identity=replace(intent.identity, key="command_delete", operation="deleteJournal"),
        owner_person_id="person_1",
        entry_ids=(intent.entry_id,),
    )
    keys = dict(vault.keys)
    with coordinator.backup_barrier():
        assert store.pending(after_entry_id=None, limit=1) == ()
    assert vault.keys == keys
    assert vault.is_revoked(entry_id=intent.entry_id)


@pytest.mark.trace("T-E01-04", "R25")
def test_backup_drains_more_than_one_pending_seal_page(tmp_path):
    _, coordinator, vault, store, _, intent = station(tmp_path)
    count = coordinator.settings.integer("private.recovery_batch") + 1
    for index in range(count):
        staged = replace(
            intent,
            entry_id=f"entry_{index}",
            version_id=f"version_{index}",
            identity=replace(intent.identity, key=f"command_{index}"),
        )
        store.seal(staged)
    with coordinator.backup_barrier():
        assert store.pending(after_entry_id=None, limit=1) == ()
    assert len(vault.revoked) == count


@pytest.mark.trace("T-E01-04", "R25")
def test_pending_other_command_key_cannot_revoke_private_entry(tmp_path, monkeypatch):
    from crew_health.platform.adapters.intents import IntentRepository

    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    key = "command_pending"
    with db.begin(actor=session.actor, correlation_id=key) as uow:
        IntentRepository(CommandLedger(db.ids)).reserve(
            uow, replace(intent.identity, key=key), resume_handler="other", input_ref=intent.entry_id
        )
    monkeypatch.setattr(vault, "revoke", lambda **kwargs: pytest.fail("conflict reached vault"))
    with pytest.raises(ProblemError) as caught:
        coordinator.delete(
            session,
            identity=replace(intent.identity, key=key, operation="deleteJournal"),
            owner_person_id="person_1",
            entry_ids=(intent.entry_id,),
        )
    assert caught.value.code.value == "DUPLICATE_COMMAND"
    assert not vault.is_revoked(entry_id=intent.entry_id)


@pytest.mark.trace("T-E01-04", "R25")
@pytest.mark.parametrize("changed", ["owner", "entry"])
def test_deletion_retry_with_changed_owner_or_entry_conflicts_before_vault(tmp_path, monkeypatch, changed):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    deletion = replace(intent.identity, key="command_delete", operation="deleteJournal")
    coordinator.delete(session, identity=deletion, owner_person_id="person_1", entry_ids=(intent.entry_id,))
    coordinator.authorize = lambda session, owner: True
    monkeypatch.setattr(vault, "revoke", lambda **kwargs: pytest.fail("conflict reached vault"))
    with pytest.raises(ProblemError) as caught:
        coordinator.delete(
            session,
            identity=deletion,
            owner_person_id="person_2" if changed == "owner" else "person_1",
            entry_ids=("entry_other",) if changed == "entry" else (intent.entry_id,),
        )
    assert caught.value.code.value == "DUPLICATE_COMMAND"
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((2,),)


@pytest.mark.trace("T-E01-04", "R25")
def test_recovery_resumes_deletion_after_vault_call_was_interrupted(tmp_path, monkeypatch):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    original = vault.revoke

    def interrupted(**kwargs):
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(vault, "revoke", interrupted)
    identity = replace(intent.identity, key="command_delete", operation="deleteJournal")
    with pytest.raises(StorageUnavailable):
        coordinator.delete(
            session, identity=identity, owner_person_id="person_1", entry_ids=(intent.entry_id,)
        )
    assert not vault.is_revoked(entry_id=intent.entry_id)
    monkeypatch.setattr(vault, "revoke", original)
    assert coordinator.recover(limit=1) == 1
    assert vault.is_revoked(entry_id=intent.entry_id)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT completed FROM platform_command_intent", (), limit=1) == ((1,),)


@pytest.mark.trace("T-E01-04", "R25")
def test_backup_waits_for_replica_revocation_then_recovers_same_intent(tmp_path, monkeypatch):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    original = vault.revoke

    def pending(**kwargs):
        return replace(original(**kwargs), complete=False, outstanding_replica_ids=("replica",))

    monkeypatch.setattr(vault, "revoke", pending)
    identity = replace(intent.identity, key="command_delete", operation="deleteJournal")
    result = coordinator.delete(
        session, identity=identity, owner_person_id="person_1", entry_ids=(intent.entry_id,)
    )
    assert not result.revocation.complete and result.hub_reference_seq is None
    with pytest.raises(ProblemError), coordinator.backup_barrier():
        pytest.fail("pending replica entered backup")
    with db.snapshot() as view:
        assert view.connection.rows("SELECT completed FROM platform_private_deletion", (), limit=1) == ((0,),)
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((1,),)
    monkeypatch.setattr(vault, "revoke", original)
    with coordinator.backup_barrier():
        pass
    assert coordinator.delete(
        session, identity=identity, owner_person_id="person_1", entry_ids=(intent.entry_id,)
    ).revocation == original(
        owner_person_id="person_1", entry_ids=(intent.entry_id,), command_key=identity.key
    )


@pytest.mark.trace("T-E01-04", "R25")
def test_recovery_drains_multiple_pages_of_interrupted_deletions(tmp_path, monkeypatch):
    db, coordinator, vault, _, session, intent = station(tmp_path)
    writer = coordinator.writers["journal"]
    original = writer.tombstone

    def interrupted(*args, **kwargs):
        raise StorageUnavailable("io_error")

    monkeypatch.setattr(writer, "tombstone", interrupted)
    count = coordinator.settings.integer("private.recovery_batch") + 1
    for index in range(count):
        entry = replace(
            intent,
            entry_id=f"entry_{index}",
            version_id=f"version_{index}",
            identity=replace(intent.identity, key=f"command_write_{index}"),
        )
        entry = replace(
            entry,
            identity=replace(
                entry.identity,
                body_fingerprint=vault.fingerprint(
                    owner_person_id=entry.owner_person_id,
                    canonical_identity_and_body=private_identity_bytes(entry),
                ),
            ),
        )
        coordinator.write(session, entry)
        with pytest.raises(StorageUnavailable):
            coordinator.delete(
                session,
                identity=replace(entry.identity, key=f"command_delete_{index}", operation="deleteJournal"),
                owner_person_id=entry.owner_person_id,
                entry_ids=(entry.entry_id,),
            )
    monkeypatch.setattr(writer, "tombstone", original)
    with coordinator.backup_barrier():
        pass
    with db.snapshot() as view:
        assert view.connection.rows(
            "SELECT count(*) FROM platform_private_deletion WHERE completed=1", (), limit=1
        ) == ((count,),)
        assert view.connection.rows(
            "SELECT count(*) FROM platform_private_reference WHERE deleted=0", (), limit=1
        ) == ((0,),)


@pytest.mark.trace("T-E01-04", "R25")
def test_concurrent_deletion_retries_have_one_receipt_and_tombstone(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db, coordinator, _, _, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    identity = replace(intent.identity, key="command_delete", operation="deleteJournal")

    def delete():
        return coordinator.delete(
            session, identity=identity, owner_person_id="person_1", entry_ids=(intent.entry_id,)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(delete), pool.submit(delete)
        assert first.result(timeout=5) == second.result(timeout=5)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((2,),)
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((2,),)


@pytest.mark.trace("T-E01-04", "R25")
def test_offline_replica_degrades_recovery_and_seal_repair_still_runs(tmp_path, monkeypatch):
    """SYSTEM 7.4 "Offline replicas leave deletion pending, visibly" and AGENTS 2 "Contain failures": startup
    recovery (the assembly STORAGE phase) with one vault replica offline keeps the deletion pending, reports it on
    health and still repairs an unrelated pending seal; once the replica is back, recovery completes and clears it."""
    db, coordinator, vault, store, session, intent = station(tmp_path)
    coordinator.write(session, intent)
    original = vault.revoke

    def offline(**kwargs):
        return replace(original(**kwargs), complete=False, outstanding_replica_ids=("replica",))

    monkeypatch.setattr(vault, "revoke", offline)
    identity = replace(intent.identity, key="command_delete", operation="deleteJournal")
    result = coordinator.delete(
        session, identity=identity, owner_person_id="person_1", entry_ids=(intent.entry_id,)
    )
    assert not result.revocation.complete
    second = replace(
        intent, entry_id="entry_2", version_id="version_2", identity=replace(intent.identity, key="command_2")
    )
    fingerprint = vault.fingerprint(
        owner_person_id="person_1", canonical_identity_and_body=private_identity_bytes(second)
    )
    second = replace(second, identity=replace(second.identity, body_fingerprint=fingerprint))
    store.fail_ack = True
    with pytest.raises(StorageUnavailable):
        coordinator.write(session, second)
    store.fail_ack = False
    assert store.pending(after_entry_id=None, limit=10)

    assert coordinator.recover(limit=db.settings.integer("private.recovery_batch")) == 1
    assert not store.pending(after_entry_id=None, limit=10)
    health = coordinator.health.snapshot()
    assert health.status == "degraded"
    assert [(c.component, c.code) for c in health.components] == [
        (PENDING_DELETIONS, "CAPABILITY_UNAVAILABLE")
    ]
    with db.snapshot() as view:
        assert view.connection.rows("SELECT completed FROM platform_private_deletion", (), limit=1) == ((0,),)
        assert view.connection.rows(
            "SELECT deleted FROM platform_private_reference WHERE entry_id=?", (intent.entry_id,), limit=1
        ) == ((0,),)
    assert vault.is_revoked(entry_id=intent.entry_id)
    with pytest.raises(ProblemError), coordinator.backup_barrier():
        pytest.fail("pending replica entered backup")

    monkeypatch.setattr(vault, "revoke", original)
    assert coordinator.recover(limit=db.settings.integer("private.recovery_batch")) == 1
    assert coordinator.health.snapshot().status == "ok"
    with db.snapshot() as view:
        assert view.connection.rows("SELECT completed FROM platform_private_deletion", (), limit=1) == ((1,),)
