from pathlib import Path

import pytest

from crew_health.assistant.adapters.staging import PinnedWork, ReadStage
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.uow import Actor, UnitOfWork
from crew_health.platform.testing.sqlite import database

ACTOR = Actor("person", "person_own")


def entry() -> AuditEntry:
    return AuditEntry(
        "audit_one",
        "tools.denied",
        1,
        ACTOR,
        {"turn_id": "turn_own"},
        "correlation_one",
        None,
        {"status": "denied"},
    )


@pytest.mark.trace("T-E13-013", "INV-E13-05", "SYSTEM.5.10.1")
def test_preflight_uses_real_readonly_sqlite_and_never_a_writer(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        with db.snapshot() as view:
            stage = ReadStage(
                view,
                release=MemoryRelease("config_A", {}),
                actor=ACTOR,
                correlation_id="correlation_one",
                max_audits=2,
                max_entry_bytes=4096,
            )
            typed: UnitOfWork = stage
            assert typed.release.release_id == "config_A"
            assert stage.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((0,),)
            with pytest.raises(ProblemError):
                stage.connection.execute(
                    "INSERT INTO platform_evaluation_completed VALUES (?)", ("evaluation_forbidden",)
                )
            stage.audit(entry())
            assert stage.finish() == (entry(),)
            assert view.connection.rows("SELECT count(*) FROM platform_audit", (), limit=1) == ((0,),)
            with pytest.raises(ProblemError):
                stage.audit(entry())
    finally:
        db.close()


@pytest.mark.trace("T-E13-021", "INV-E13-11", "R17")
def test_staged_audits_are_bounded_and_discarded_without_storage_effect(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        with db.snapshot() as view:
            stage = ReadStage(
                view,
                release=view.release,
                actor=ACTOR,
                correlation_id="correlation_one",
                max_audits=1,
                max_entry_bytes=4096,
            )
            stage.audit(entry())
            with pytest.raises(ProblemError):
                stage.audit(entry())
            stage.discard()
            with pytest.raises(ProblemError):
                stage.finish()
            small = ReadStage(
                view,
                release=view.release,
                actor=ACTOR,
                correlation_id="correlation_one",
                max_audits=1,
                max_entry_bytes=1,
            )
            with pytest.raises(ProblemError):
                small.audit(entry())
    finally:
        db.close()


@pytest.mark.trace("T-E13-080", "V30", "SYSTEM.8.1")
def test_old_pin_is_preserved_inside_current_write_without_replacing_storage_or_actor(tmp_path: Path) -> None:
    db = database(tmp_path)
    try:
        with db.begin(actor=ACTOR, correlation_id="correlation_one") as work:
            old = MemoryRelease("config_A", {})
            typed: UnitOfWork = PinnedWork(work, old)
            assert work.release.release_id != "config_A"
            assert typed.release is old
            assert typed.connection is work.connection and typed.actor == ACTOR
            assert typed.now == work.now and typed.correlation_id == work.correlation_id
    finally:
        db.close()


@pytest.mark.trace("T-E13-021", "INV-E13-11")
def test_audit_limit_counts_serialized_structure_as_well_as_string_values(tmp_path: Path) -> None:
    from crew_health.kernel.commands import canonical_json

    e = entry()
    raw = canonical_json(
        {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "event_version": e.event_version,
            "actor": {"kind": e.actor.kind, "id": e.actor.id},
            "refs": dict(e.refs),
            "correlation_id": e.correlation_id,
            "causation_id": e.causation_id,
            "safe_metadata": dict(e.safe_metadata),
        }
    )
    db = database(tmp_path)
    try:
        with db.snapshot() as view:
            stage = ReadStage(
                view,
                release=view.release,
                actor=ACTOR,
                correlation_id="correlation_one",
                max_audits=1,
                max_entry_bytes=len(raw) - 1,
            )
            with pytest.raises(ProblemError):
                stage.audit(e)
    finally:
        db.close()
