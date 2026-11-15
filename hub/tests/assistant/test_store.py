from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.assistant.adapters.store import Store
from crew_health.assistant.application.dto import Assessment, Conversation, RuntimeRecord, Work
from crew_health.assistant.domain.runtime import Runtime, RuntimeState
from crew_health.assistant.domain.turn import TurnState
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, SessionContext
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

AT = datetime(2026, 11, 15, tzinfo=UTC)
ACTOR = Actor("person", "person_own")
SESSION = SessionContext("session_own", "crew", ACTOR, ACTOR.id, frozenset())
STORE = Store(max_rows=10, max_body_bytes=8192)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[SQLiteDatabase]:
    value = database(tmp_path)
    migration = (
        Path(__file__).resolve().parents[2]
        / "src/crew_health/assistant/adapters/migrations/0001_assistant.sql"
    )
    if migration.exists():
        value.migrate(migration.read_text(encoding="utf-8"))
    try:
        yield value
    finally:
        value.close()


def conversation() -> Conversation:
    return Conversation("conversation_own", ACTOR.id, "general", None, "config_A", "model_A", "SIM", AT)


def work(ref: str = "turn_own") -> Work:
    return Work(
        ref,
        "task_" + ref,
        "conversation",
        "conversation_own",
        None,
        None,
        None,
        ACTOR.id,
        ACTOR,
        SESSION,
        "general",
        2,
        "config_A",
        "evidence_A",
        "model_A",
        1,
        AT,
    )


def message(ref: str, *, text: str = "configured fixture words") -> dict[str, object]:
    return {
        "message_id": ref,
        "conversation_id": "conversation_own",
        "turn_id": None,
        "role": "person",
        "created_at": AT.isoformat(),
        "text": text,
        "action": None,
        "status": "complete",
        "error_code": None,
        "evidence": [],
        "limitations": [],
    }


@pytest.mark.trace("T-E13-020", "INV-E13-06", "SYSTEM.8.6")
def test_open_conversation_identity_pins_and_database_uniqueness(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        assert STORE.conversation(uow, "conversation_own") == conversation()
        assert STORE.open_conversation(uow, ACTOR.id, "general", None) == conversation()
    with pytest.raises(ProblemError):
        with db.begin(actor=ACTOR, correlation_id="correlation_two") as uow:
            STORE.create_conversation(uow, replace(conversation(), conversation_id="conversation_duplicate"))
    with db.snapshot() as view:
        assert STORE.conversation(view, "absent") is None
        assert STORE.open_conversation(view, "person_foreign", "general", None) is None


@pytest.mark.trace("T-E13-050", "INV-E13-07", "SYSTEM.8.6")
def test_messages_are_durable_ordered_and_immutable(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        STORE.append_message(uow, message("message_one"))
        STORE.append_message(uow, message("message_two"))
    with db.snapshot() as view:
        assert STORE.messages(view, "conversation_own") == (message("message_one"), message("message_two"))
        current = STORE.conversation(view, "conversation_own")
        assert current is not None and current.revision == 3
    for sql in ("DELETE FROM assistant_message", "UPDATE assistant_message SET body=body"):
        with pytest.raises(ProblemError):
            with db.begin(actor=ACTOR, correlation_id="correlation_two") as uow:
                uow.connection.execute(sql)


@pytest.mark.trace("T-E13-020", "INV-E13-06")
def test_active_turn_is_unique_and_terminal_turn_does_not_block_next(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        STORE.create_work(uow, work())
        assert STORE.active(uow, "conversation_own") == work()
    with pytest.raises(ProblemError):
        with db.begin(actor=ACTOR, correlation_id="correlation_two") as uow:
            STORE.create_work(uow, work("turn_duplicate"))
    with db.begin(actor=ACTOR, correlation_id="correlation_three") as uow:
        STORE.update_work(uow, work(), replace(work(), state=TurnState.CANCELLED))
        STORE.create_work(uow, work("turn_next"))
        assert STORE.active(uow, "conversation_own") == work("turn_next")


@pytest.mark.trace("T-E13-025", "T-E13-060", "SYSTEM.8.6")
def test_preemption_history_appends_both_occurrences_and_stale_writer_is_rejected(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        before = work()
        running = replace(before, state=TurnState.RUNNING)
        STORE.create_work(uow, before)
        STORE.update_work(uow, before, running)
        requeued = replace(before, preemptions=1)
        STORE.update_work(uow, running, requeued)
        STORE.update_work(uow, requeued, replace(requeued, state=TurnState.RUNNING))
        with pytest.raises(ProblemError):
            STORE.update_work(uow, before, running)
        states = uow.connection.rows(
            "SELECT state FROM assistant_turn_history WHERE turn_id=? ORDER BY seq", ("turn_own",), limit=10
        )
        assert states == (("queued",), ("running",), ("queued",), ("running",))
        assert STORE.work(uow, "turn_own") == replace(requeued, state=TurnState.RUNNING)


@pytest.mark.trace("T-E13-021", "T-E13-022", "INV-E13-11")
def test_pending_is_bounded_and_orders_help_before_episode_before_other(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        for ref, priority in [("turn_other", 2), ("turn_episode", 1), ("turn_help", 0)]:
            STORE.create_work(uow, replace(work(ref), conversation_id=None, priority=priority))
        assert tuple(w.priority for w in STORE.pending(uow)) == (0, 1, 2)
        with pytest.raises(ProblemError):
            Store(max_rows=2, max_body_bytes=8192).pending(uow)


@pytest.mark.trace("T-E13-075", "T-E13-080", "SYSTEM.8.6")
def test_runtime_desire_and_each_transition_survive_independently(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        assert STORE.runtime(uow) is None
        loading = RuntimeRecord(Runtime(True, RuntimeState.LOADING), "model_A", "stub", AT)
        running = replace(loading, runtime=Runtime(True, RuntimeState.RUNNING))
        STORE.set_runtime(uow, loading)
        STORE.set_runtime(uow, running)
        STORE.set_runtime(uow, loading)
    with db.snapshot() as view:
        assert STORE.runtime(view) == loading
        assert view.connection.rows(
            "SELECT state FROM assistant_runtime_history ORDER BY seq", (), limit=10
        ) == (("loading",), ("running",), ("loading",))


def assessment() -> Assessment:
    return Assessment(
        "assessment_own",
        "episode_own",
        ACTOR.id,
        canonical_json({"assessment_id": "assessment_own", "validation_status": "draft"}),
        "incomplete",
        "MODEL_TIMEOUT",
        "config_A",
        "turn_own",
        "job_own",
        AT,
    )


@pytest.mark.trace("T-E13-033", "T-E13-040", "T-E13-049", "SYSTEM.8.6")
def test_assessment_support_queries_status_and_history_keep_original_claims(db: SQLiteDatabase) -> None:
    value = assessment()
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_assessment(
            uow,
            value,
            (
                ("finding", 0, "observation", "observation_own"),
                ("hypothesis", 0, "passage", "passage_pinned"),
            ),
        )
        assert STORE.assessment(uow, value.assessment_id) == value
        assert STORE.citing(uow, "episode_own", ("passage_pinned",)) == ("assessment_own",)
        assert STORE.citing(uow, "episode_foreign", ("passage_pinned",)) == ()
        assert STORE.assessment_ids(uow, "episode_own") == ("assessment_own",)
        STORE.mark_assessment(uow, value.assessment_id, state="reviewed", review="review_own")
        STORE.mark_assessment(uow, value.assessment_id, state="stale", cause="correction_own")
        stale = STORE.assessment(uow, value.assessment_id)
        assert (
            stale is not None
            and stale.validation_status == "stale"
            and stale.body == value.body
            and stale.review_id == "review_own"
        )
        history = uow.connection.rows(
            "SELECT state FROM assistant_assessment_history ORDER BY seq", (), limit=10
        )
        assert history == (("draft",), ("reviewed",), ("stale",))
        with pytest.raises(ProblemError):
            STORE.mark_assessment(uow, value.assessment_id, state="reviewed", review="review_duplicate")


@pytest.mark.trace("T-E13-013", "T-E13-049", "INV-E13-05")
def test_transaction_rollback_discards_message_turn_and_assessment_together(db: SQLiteDatabase) -> None:
    with pytest.raises(RuntimeError):
        with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
            STORE.create_conversation(uow, conversation())
            STORE.create_work(uow, work())
            STORE.append_message(uow, message("message_one"))
            STORE.create_assessment(uow, assessment(), ())
            raise RuntimeError("controlled fault before commit")
    with db.snapshot() as view:
        assert STORE.conversation(view, "conversation_own") is None
        assert STORE.work(view, "turn_own") is None
        assert STORE.assessment(view, "assessment_own") is None


@pytest.mark.trace("T-E13-021", "INV-E13-11")
def test_row_and_payload_caps_fail_loudly_instead_of_truncating(db: SQLiteDatabase) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        for ref in ("message_one", "message_two", "message_three"):
            STORE.append_message(uow, message(ref))
        with pytest.raises(ProblemError) as error:
            Store(max_rows=2, max_body_bytes=8192).messages(uow, "conversation_own")
        assert error.value.code == ErrorCode.PAYLOAD_TOO_LARGE
        with pytest.raises(ProblemError):
            Store(max_rows=10, max_body_bytes=4).append_message(uow, message("message_overflow"))


@pytest.mark.trace("T-E13-080", "INV-E13-15", "SYSTEM.8.6")
@pytest.mark.parametrize(
    "change",
    [
        {"config_release_id": "config_B"},
        {"model_release_id": "model_B"},
        {"subject_person_id": "person_foreign"},
        {"actor": Actor("person", "person_foreign")},
        {"session": replace(SESSION, session_id="session_foreign")},
    ],
)
def test_work_updates_cannot_rewrite_captured_identity_or_release_pins(
    db: SQLiteDatabase, change: dict[str, object]
) -> None:
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        STORE.create_work(uow, work())
        with pytest.raises(ProblemError):
            STORE.update_work(uow, work(), replace(work(), **change))  # type: ignore[arg-type]
        assert STORE.work(uow, "turn_own") == work()


@pytest.mark.trace("T-E13-052", "SYSTEM.8.6")
def test_migration_is_additive_and_all_history_and_claim_rows_refuse_rewrite(db: SQLiteDatabase) -> None:
    from crew_health.platform.adapters.migrations import require_expansion

    path = (
        Path(__file__).resolve().parents[2]
        / "src/crew_health/assistant/adapters/migrations/0001_assistant.sql"
    )
    require_expansion(path.read_text(encoding="utf-8"), module="assistant")
    with db.begin(actor=ACTOR, correlation_id="correlation_one") as uow:
        STORE.create_conversation(uow, conversation())
        STORE.create_work(uow, work())
        STORE.create_assessment(uow, assessment(), (("finding", 0, "observation", "observation_own"),))
        STORE.set_runtime(uow, RuntimeRecord(Runtime(True, RuntimeState.LOADING), "model_A", "stub", AT))
        for table in (
            "assistant_turn_history",
            "assistant_runtime_history",
            "assistant_assessment_history",
            "assistant_claim_ref",
        ):
            for sql in (
                f"DELETE FROM {table}",
                f"UPDATE {table} SET "
                + ("ref_id=ref_id" if table == "assistant_claim_ref" else "state=state"),
            ):
                with pytest.raises(ProblemError):
                    uow.connection.execute(sql)
