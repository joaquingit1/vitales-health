from pathlib import Path

import pytest

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, ReplicationChange
from crew_health.platform.adapters.audit import SQLiteAuditVerifier
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-14", "R25")]


def _register_records(db):
    db.register_audit_policy(
        AuditPolicy("records.accepted", {}, frozenset({"record"}), {"record": frozenset({"test_record"})})
    )


def _record_reference(uow, record_id):
    uow.replicate(
        ReplicationChange(
            None,
            "test_record",
            record_id,
            "append",
            b"{}",
            (),
            uow.release.release_id,
            Audience("operator", record_id, AudienceProjectionKey("tests", "record", 1), "1"),
        )
    )


def test_audit_chain_and_trusted_suffix(tmp_path: Path) -> None:
    db = database(tmp_path)
    _register_records(db)
    actor = Actor("person", "person_1")
    with db.begin(actor=actor, correlation_id="correlation_1") as uow:
        _record_reference(uow, "record_1")
        uow.audit(
            AuditEntry(
                "audit_1", "records.accepted", 1, actor, {"record": "record_1"}, "correlation_1", None, {}
            )
        )
        _record_reference(uow, "record_2")
        uow.audit(
            AuditEntry(
                "audit_2",
                "records.accepted",
                1,
                actor,
                {"record": "record_2"},
                "correlation_1",
                "audit_1",
                {},
            )
        )
    with db.snapshot() as view:
        rows = view.connection.rows("SELECT seq, entry_hash FROM platform_audit ORDER BY seq", (), limit=2)
    verifier = SQLiteAuditVerifier(db, page_size=1)
    assert verifier.verify(expected_head_seq=2, expected_head_hash=str(rows[1][1])).ok
    assert not verifier.verify(expected_head_seq=1, expected_head_hash=str(rows[0][1])).ok
    assert not verifier.verify(expected_head_seq=3, expected_head_hash="0" * 64).ok
    with pytest.raises(ProblemError), db.begin(actor=actor, correlation_id="correlation_2") as uow:
        uow.connection.execute("UPDATE platform_audit SET at=? WHERE seq=?", ("bad", 1))


def test_private_canary_cannot_be_metadata(tmp_path: Path) -> None:
    db = database(tmp_path)
    actor = Actor("person", "person_1")
    with pytest.raises(ProblemError), db.begin(actor=actor, correlation_id="correlation_1") as uow:
        uow.audit(
            AuditEntry(
                "audit_1",
                "private.write",
                1,
                actor,
                {},
                "correlation_1",
                None,
                {"note": "SECRETCLINICALCANARY"},
            )
        )


def _accepted_chain(tmp_path):
    db = database(tmp_path)
    _register_records(db)
    actor = Actor("person", "person_1")
    with db.begin(actor=actor, correlation_id="correlation_1") as uow:
        for seq in range(1, 3):
            _record_reference(uow, f"record_{seq}")
            uow.audit(
                AuditEntry(
                    f"audit_{seq}",
                    "records.accepted",
                    1,
                    actor,
                    {"record": f"record_{seq}"},
                    "correlation_1",
                    None,
                    {},
                )
            )
    with db.snapshot() as view:
        head = view.connection.rows(
            "SELECT seq,entry_hash FROM platform_audit ORDER BY seq DESC LIMIT 1", (), limit=1
        )[0]
    return db, head


@pytest.mark.parametrize(
    "column,value",
    [
        ("origin_node_id", "node_changed"),
        ("origin_incarnation", "incarnation_changed"),
        ("at", "2020-01-01T00:00:00+00:00"),
        ("event_id", "audit_changed"),
        ("seq", 3),
        ("entry_hash", "f" * 64),
        ("envelope", b"{}"),
    ],
)
def test_audit_detects_indexed_copy_and_envelope_corruption(tmp_path, column, value):
    import sqlite3

    db, head = _accepted_chain(tmp_path)
    # This models offline tampering. The production connection refuses these writes.
    with sqlite3.connect(db.path) as offline:
        offline.execute("DROP TRIGGER platform_audit_no_update")
        offline.execute("UPDATE platform_audit SET " + column + "=? WHERE seq=1", (value,))
    result = SQLiteAuditVerifier(db, page_size=1).verify(
        expected_head_seq=head[0], expected_head_hash=head[1]
    )
    assert not result.ok
    assert result.first_bad_seq == 1


def test_audit_suffix_deletion_needs_external_trusted_checkpoint(tmp_path):
    import sqlite3

    db, head = _accepted_chain(tmp_path)
    with sqlite3.connect(db.path) as offline:
        offline.execute("DROP TRIGGER platform_audit_no_delete")
        offline.execute("DELETE FROM platform_audit WHERE seq=2")
    result = SQLiteAuditVerifier(db, page_size=1).verify(
        expected_head_seq=head[0], expected_head_hash=head[1]
    )
    assert not result.ok
    assert result.first_bad_seq == 2


@pytest.mark.parametrize(
    "path,value",
    [
        ("origin_node_id", "node_changed"),
        ("origin_incarnation", "incarnation_changed"),
        ("audit_seq", 3),
        ("at", "2020-01-01T00:00:00.000000Z"),
        ("entry.event_id", "audit_changed"),
        ("entry.event_type", "other.type"),
        ("entry.event_version", 2),
        ("entry.actor.kind", "operator"),
        ("entry.actor.id", "person_other"),
        ("entry.refs.record", "record_changed"),
        ("entry.correlation_id", "correlation_changed"),
        ("entry.causation_id", "cause_changed"),
        ("entry.safe_metadata", {"safe": True}),
        ("prev_hash", "f" * 64),
    ],
)
def test_audit_hash_covers_every_envelope_field(tmp_path, path, value):
    import json
    import sqlite3

    from crew_health.kernel.commands import canonical_json

    db, head = _accepted_chain(tmp_path)
    with db.snapshot() as view:
        raw = view.connection.rows("SELECT envelope FROM platform_audit WHERE seq=1", (), limit=1)[0][0]
    payload = json.loads(raw)
    target = payload
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    with sqlite3.connect(db.path) as offline:
        offline.execute("DROP TRIGGER platform_audit_no_update")
        offline.execute("UPDATE platform_audit SET envelope=? WHERE seq=1", (canonical_json(payload),))
    assert (
        not SQLiteAuditVerifier(db, page_size=1)
        .verify(expected_head_seq=head[0], expected_head_hash=head[1])
        .ok
    )


def test_audit_rejects_noncanonical_bytes_even_with_same_semantics(tmp_path):
    import sqlite3

    db, head = _accepted_chain(tmp_path)
    with sqlite3.connect(db.path) as offline:
        offline.execute("DROP TRIGGER platform_audit_no_update")
        raw = offline.execute("SELECT envelope FROM platform_audit WHERE seq=1").fetchone()[0]
        offline.execute("UPDATE platform_audit SET envelope=? WHERE seq=1", (b" " + raw,))
    assert (
        not SQLiteAuditVerifier(db, page_size=1)
        .verify(expected_head_seq=head[0], expected_head_hash=head[1])
        .ok
    )


def test_unknown_audit_reference_cannot_smuggle_space_free_private_text(tmp_path):
    db = database(tmp_path)
    actor = Actor("person", "person_1")
    with pytest.raises(ProblemError), db.begin(actor=actor, correlation_id="correlation_1") as uow:
        uow.audit(
            AuditEntry(
                "audit_1",
                "unknown.private_event",
                1,
                actor,
                {"record": "SECRETCLINICALCANARY"},
                "correlation_1",
                None,
                {},
            )
        )
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((0,),)
