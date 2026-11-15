"""T-E01-10: producer durability and bounded evaluator failure isolation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.errors import ProblemError, StorageUnavailable
from crew_health.kernel.evaluation import (
    EvaluationInput,
    EvaluatorComputationFailure,
    EvaluatorRegistration,
    PendingEvaluation,
)
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.testing import MemoryClock, MemoryRelease
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshot,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.platform.adapters import codec
from crew_health.platform.adapters.evaluation import EvaluationRepository
from crew_health.platform.application.evaluation import EvaluationService
from crew_health.platform.domain.errors import StorageConstraintViolated
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-10", "D13", "SYSTEM.5.10.2")]


def change(kind: str, reference: str, release: str) -> ReplicationChange:
    return ReplicationChange(
        None,
        kind,
        reference,
        "append",
        b"{}",
        (),
        release,
        Audience("test", reference, AudienceProjectionKey("tests", kind, 1), "1"),
    )


@dataclass
class WritingEvaluator:
    failure: Exception | None
    calls: list[EvaluationInput] = field(default_factory=list)

    def evaluate(self, uow: UnitOfWork, value: EvaluationInput) -> None:
        self.calls.append(value)
        uow.connection.execute("INSERT INTO isolation_effect VALUES (?)", (value.completion_id,))
        uow.replicate(change("test_effect", value.completion_id, value.release_id))
        if self.failure is not None:
            raise self.failure


class UnwritableMarker(EvaluationRepository):
    def failed(self, uow: UnitOfWork, value: EvaluationInput, code: str) -> PendingEvaluation:
        super().failed(uow, value, code)
        raise StorageUnavailable("disk_full")


@dataclass
class Station:
    db: SQLiteDatabase
    evaluator: WritingEvaluator
    service: EvaluationService
    value: EvaluationInput

    def answer(self, uow: UnitOfWork) -> None:
        uow.connection.execute("INSERT INTO isolation_answer VALUES (?)", (self.value.input_ref,))
        uow.replicate(change("answer", self.value.input_ref, self.value.release_id))


def station(tmp_path: Path, failure: Exception, *, marker_failure: bool = False) -> Station:
    db = database(tmp_path)
    db.migrate(
        "CREATE TABLE isolation_answer(id TEXT PRIMARY KEY) STRICT;"
        "CREATE TABLE isolation_effect(id TEXT PRIMARY KEY) STRICT;"
    )
    db.register_replicated_table("isolation_answer", record_type="answer", key_column="id")
    db.register_replicated_table("isolation_effect", record_type="test_effect", key_column="id")
    evaluator = WritingEvaluator(failure)
    value = EvaluationInput(
        "rules",
        "immutable_v1",
        "answer_1",
        TimeInput(db.clock.now() - timedelta(minutes=1), "source_1", "segment_1", 1, "source_raw", "SIM"),
        "release_1",
        "partition_1",
        2,
        db.ids.derived("completion", "rules", "immutable_v1", "answer_1", "2", "release_1"),
    )
    service = EvaluationService(
        (EvaluatorRegistration(value.evaluator, value.evaluator_version, evaluator),),
        db.settings,
        db.ids,
        repository=UnwritableMarker() if marker_failure else EvaluationRepository(),
        capture=lambda uow: TimeInput(uow.now, "source_1", "segment_1", 1, "source_raw", "SIM"),
        fault_codes=frozenset({"bounded_failure"}),
    )
    return Station(db, evaluator, service, value)


def count(view: ReadSnapshot, table: str) -> int:
    ((value,),) = view.connection.rows("SELECT count(*) FROM " + table, (), limit=1)
    assert isinstance(value, int)
    return value


def assert_empty(db: SQLiteDatabase) -> None:
    with db.snapshot() as view:
        for table in (
            "isolation_answer",
            "isolation_effect",
            "platform_evaluation_pending",
            "platform_evaluation_completed",
            "platform_schedule",
            "platform_audit",
            "repl_log",
            "platform_reference",
        ):
            assert count(view, table) == 0, table


def test_allowlisted_computation_failure_preserves_answer_and_retry_but_rolls_back_all_effects(
    tmp_path: Path,
) -> None:
    s = station(tmp_path, EvaluatorComputationFailure("bounded_failure"))
    with s.db.begin(actor=Actor("person", "person_1"), correlation_id="answer_1") as uow:
        s.answer(uow)
        assert s.service.run(uow, s.value) == "pending"
    with s.db.snapshot() as view:
        assert count(view, "isolation_answer") == 1
        assert count(view, "isolation_effect") == 0
        assert count(view, "platform_evaluation_completed") == 0
        (marker,) = s.service.pending(view, after_id=None, limit=1)
        assert marker.input == s.value
        assert marker.fault_code == "bounded_failure" and marker.attempts == 1
        ((raw_job,),) = view.connection.rows("SELECT job FROM platform_schedule", (), limit=1)
        assert isinstance(raw_job, bytes)
        job = codec(ScheduledJob).validate_json(raw_job)
        assert job.key == s.db.ids.derived("retry", s.value.completion_id)
        assert job.handler == "platform.evaluation.retry"
        assert dict(job.payload) == {"completion_id": s.value.completion_id}
        ((raw_audit,),) = view.connection.rows("SELECT envelope FROM platform_audit", (), limit=1)
        assert isinstance(raw_audit, bytes)
        audit = json.loads(raw_audit)["entry"]
        assert audit["event_type"] == "evaluation.pending"
        assert audit["refs"] == {"input": s.value.input_ref, "completion": s.value.completion_id}
        assert audit["safe_metadata"] == {}
        assert count(view, "repl_log") == 2
        assert view.connection.rows(
            "SELECT record_type, record_id FROM platform_reference ORDER BY record_type", (), limit=2
        ) == (("answer", s.value.input_ref), ("evaluation", s.value.completion_id))


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(StorageUnavailable("io_error"), id="storage"),
        pytest.param(StorageConstraintViolated(), id="integrity"),
        pytest.param(OSError("I/O failure"), id="io"),
        pytest.param(ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "denied"), id="authorization"),
        pytest.param(ProblemError(ErrorCode.CONFIG_INVALID, "invalid"), id="configuration"),
        pytest.param(ProblemError(ErrorCode.INVALID_VALUE, "invalid"), id="invalid_value"),
        pytest.param(ProblemError(ErrorCode.MALFORMED_REQUEST, "invalid"), id="malformed_request"),
        pytest.param(EvaluatorComputationFailure("unregistered_failure"), id="unregistered_code"),
    ],
)
def test_forbidden_failures_abort_the_entire_answer_without_marker_or_retry(
    tmp_path: Path,
    failure: Exception,
) -> None:
    s = station(tmp_path, failure)
    with pytest.raises(type(failure)) as raised:
        with s.db.begin(actor=Actor("person", "person_1"), correlation_id="answer_1") as uow:
            s.answer(uow)
            s.service.run(uow, s.value)
    assert raised.value is failure
    assert s.evaluator.calls == [s.value]
    assert_empty(s.db)


def test_marker_storage_failure_aborts_answer_and_partially_written_marker(tmp_path: Path) -> None:
    s = station(tmp_path, EvaluatorComputationFailure("bounded_failure"), marker_failure=True)
    with pytest.raises(StorageUnavailable) as raised:
        with s.db.begin(actor=Actor("person", "person_1"), correlation_id="answer_1") as uow:
            s.answer(uow)
            s.service.run(uow, s.value)
    assert raised.value.cause == "disk_full"
    assert_empty(s.db)


def test_retry_preserves_original_identity_and_pins_and_completed_input_has_no_duplicate_effects(
    tmp_path: Path,
) -> None:
    s = station(tmp_path, EvaluatorComputationFailure("bounded_failure"))
    actor = Actor("person", "person_1")
    with s.db.begin(actor=actor, correlation_id="answer_1") as uow:
        s.answer(uow)
        assert s.service.run(uow, s.value) == "pending"
    s.evaluator.failure = None
    assert isinstance(s.db.clock, MemoryClock)
    s.db.clock.at += timedelta(hours=1)
    s.db.set_release_provider(lambda: MemoryRelease("release_2", {}))
    with s.db.begin(actor=actor, correlation_id="retry_1") as uow:
        assert s.service.retry(uow, completion_id=s.value.completion_id) == "completed"
    with s.db.snapshot() as view:
        before = view.connection.rows("SELECT envelope FROM platform_audit ORDER BY seq", (), limit=2)
        assert count(view, "platform_evaluation_completed") == 1
        assert count(view, "isolation_effect") == 1
        assert count(view, "platform_evaluation_pending") == 0
        assert count(view, "repl_log") == 4
    with s.db.begin(actor=actor, correlation_id="retry_2") as uow:
        assert s.service.retry(uow, completion_id=s.value.completion_id) == "completed"
        assert s.service.run(uow, s.value) == "completed"
    assert s.evaluator.calls == [s.value, s.value]
    with s.db.snapshot() as view:
        assert view.connection.rows("SELECT envelope FROM platform_audit ORDER BY seq", (), limit=2) == before
        assert count(view, "platform_evaluation_completed") == 1
        assert count(view, "isolation_effect") == 1
        assert count(view, "platform_evaluation_pending") == 0
        assert count(view, "repl_log") == 4
