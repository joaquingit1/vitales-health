from pathlib import Path

import pytest

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor
from crew_health.platform.testing.sqlite import database

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-03", "R25")]


def test_commit_rollback_snapshot_and_reopen(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.migrate("CREATE TABLE test_fact (id TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;")
    with db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_1") as uow:
        uow.connection.execute("INSERT INTO test_fact VALUES (?, ?)", ("fact_1", "accepted"))
    with (
        pytest.raises(ValueError),
        db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_2") as uow,
    ):
        uow.connection.execute("INSERT INTO test_fact VALUES (?, ?)", ("fact_2", "unaccepted"))
        raise ValueError("injected crash before commit")
    with db.snapshot() as view:
        assert view.connection.rows("SELECT * FROM test_fact ORDER BY id", (), limit=10) == (
            ("fact_1", "accepted"),
        )
        with pytest.raises(ProblemError):
            view.connection.execute("DELETE FROM test_fact")
    db.close()
    reopened = database(tmp_path, initialize=False)
    with reopened.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM test_fact", (), limit=1) == ((1,),)


def test_savepoint_rolls_back_only_failed_work(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.migrate("CREATE TABLE test_fact (id TEXT PRIMARY KEY) STRICT;")
    with db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_1") as uow:
        uow.connection.execute("INSERT INTO test_fact VALUES (?)", ("kept",))
        with pytest.raises(ValueError), uow.savepoint("savepoint_1"):
            uow.connection.execute("INSERT INTO test_fact VALUES (?)", ("lost",))
            raise ValueError("computation")
    with db.snapshot() as view:
        assert view.connection.rows("SELECT id FROM test_fact", (), limit=10) == (("kept",),)


def test_rows_overflow_and_nested_write_fail(tmp_path: Path) -> None:
    db = database(tmp_path)
    with db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_1") as uow:
        with pytest.raises(ProblemError):
            uow.connection.rows("SELECT 1 UNION ALL SELECT 2", (), limit=1)
        with (
            pytest.raises(ProblemError),
            db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_2"),
        ):
            pass


def test_missing_established_store_never_recreated(tmp_path: Path) -> None:
    with pytest.raises(ProblemError):
        database(tmp_path, initialize=False)


def test_task_cancel_fences_late_completion_and_internal_jobs_stay_private(tmp_path):
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.commands import command_identity
    from crew_health.kernel.tasks import TaskInput, TaskRegistration, TaskResult
    from crew_health.kernel.testing import MemoryTaskHandler
    from crew_health.kernel.uow import Actor, Audience, AudienceProjectionKey, SessionContext
    from crew_health.platform.adapters.tasks import TaskRepository
    from crew_health.platform.application.tasks import TaskService
    from crew_health.platform.testing.sqlite import database

    db = database(tmp_path)
    actor = Actor("person", "person")
    session = SessionContext("session", "crew", actor, "person", frozenset({Role.CREW}))
    applied = []
    handler = MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "result", "0" * 64),
        lambda uow, lease, result: applied.append(result.result_ref),
        lambda result: None,
    )
    service = TaskService(
        settings=db.settings,
        repository=TaskRepository(),
        ids=db.ids,
        registrations=(TaskRegistration("internal", "version", handler, None, None),),
        authorize=lambda view, session, task: session.actor == task.actor,
        authorize_completion=lambda uow, task: True,
        capture=lambda at: TimeInput(at, "source", "segment", 1, "source_raw", "SIM"),
        elapsed=lambda view, a, b: b.original_at - a.original_at,
    )
    command = command_identity(
        key="key",
        actor_ref=actor.id,
        operation="internal.command",
        target="/internal",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    task = TaskInput(
        "task_1",
        "internal",
        "version",
        "input",
        "0" * 64,
        "release_1",
        None,
        actor,
        Audience("owner", "person", AudienceProjectionKey("test", "task", 1), "1"),
        command,
        None,
    )
    with db.begin(actor=actor, correlation_id="submit") as uow:
        assert service.submit(uow, task).state == "queued"
        assert service.submit(uow, task).state == "queued"
        (lease,) = service.claim(uow, worker_id="worker", handlers=frozenset({"internal"}), limit=1)
    with db.begin(actor=actor, correlation_id="cancel") as uow:
        assert service.cancel(uow, session, task.task_id).state == "cancelled"
        assert not service.complete(uow, lease, handler.execute(lease))
    assert not applied
    with db.snapshot() as view:
        with pytest.raises(ProblemError) as error:
            service.get(view, session, task.task_id)
        assert error.value.code.value == "NOT_FOUND"


def task_station(tmp_path, *, public=False, grant=None, elapsed=None):
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.commands import command_identity
    from crew_health.kernel.tasks import TaskInput, TaskRegistration, TaskResult
    from crew_health.kernel.testing import MemoryJobProjector, MemoryTaskHandler
    from crew_health.kernel.uow import Audience, AudienceProjectionKey, SessionContext
    from crew_health.platform.adapters.tasks import TaskRepository
    from crew_health.platform.api.jobs import job_body
    from crew_health.platform.application.tasks import TaskService

    db = database(tmp_path)
    db.migrate("CREATE TABLE task_effect (task_id TEXT PRIMARY KEY, result_ref TEXT NOT NULL) STRICT;")
    actor = Actor("person", "person")
    session = SessionContext("session", "crew", actor, "person", frozenset({Role.CREW}))
    handler = MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "result", "0" * 64),
        lambda uow, lease, result: uow.connection.execute(
            "INSERT INTO task_effect VALUES (?,?)", (result.task_id, result.result_ref)
        ),
        lambda result: None,
    )
    kind = "assessment" if public else None
    projector = MemoryJobProjector(lambda view, session, result: None) if public else None
    registration = TaskRegistration("handler", "version", handler, projector, kind)
    service = TaskService(
        repository=TaskRepository(),
        settings=db.settings,
        ids=db.ids,
        registrations=(registration,),
        authorize=lambda view, session, task: session.actor == task.actor,
        authorize_completion=(lambda uow, task: grant[0]) if grant is not None else lambda uow, task: True,
        capture=lambda at: TimeInput(at, "source", "segment", 1, "source_raw", "SIM"),
        elapsed=elapsed or (lambda view, start, end: end.original_at - start.original_at),
        public_payload=lambda status, correlation: job_body(status, None, correlation),
    )
    command = command_identity(
        key="key",
        actor_ref=actor.id,
        operation="internal.command",
        target="/internal",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    task = TaskInput(
        "task_1",
        "handler",
        "version",
        "input",
        "0" * 64,
        "release_1",
        None,
        actor,
        Audience("owner", "person", AudienceProjectionKey("test", "task", 1), "1"),
        command,
        kind,
    )
    if public:
        from crew_health.events.adapters.repository import EventRepository, EventValidator

        db.migrate(Path("hub/src/crew_health/events/adapters/migrations/0001_events.sql").read_text())
        events = EventRepository(
            settings=db.settings,
            ids=db.ids,
            validator=EventValidator(schema_directory=Path("contracts/api/schemas")),
        )
        db.event_sink = events.append
    return db, service, task, session, handler


def test_task_complete_is_atomic_and_retains_pins(tmp_path):
    from dataclasses import replace

    from crew_health.contracts_gen.enums import ErrorCode

    db, service, task, session, handler = task_station(tmp_path)
    with db.begin(actor=session.actor, correlation_id="submit") as uow:
        service.submit(uow, task)
        with pytest.raises(ProblemError) as error:
            service.submit(uow, replace(task, input_fingerprint="f" * 64))
        assert error.value.code == ErrorCode.DUPLICATE_COMMAND
        (lease,) = service.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)
    staged = handler.execute(lease)
    with pytest.raises(OSError):
        with db.begin(actor=session.actor, correlation_id="completion") as uow:
            assert service.complete(uow, lease, staged)
            raise OSError("crash before commit")
    with db.snapshot() as view:
        assert not view.connection.rows("SELECT task_id FROM task_effect", (), limit=1)
    with db.begin(actor=session.actor, correlation_id="completion_retry") as uow:
        assert service.complete(uow, lease, staged)
        assert not service.complete(uow, lease, staged)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT task_id,result_ref FROM task_effect", (), limit=1) == (
            (task.task_id, "result"),
        )
        row = service.repository.get(view, task.task_id)
        assert row.task.release_id == task.release_id
        assert row.task.handler_version == task.handler_version
        assert row.task.command == task.command


def test_task_expiration_recovery_and_stale_worker_generation(tmp_path):
    from dataclasses import replace
    from datetime import timedelta

    from crew_health.contracts_gen.enums import ErrorCode

    db, service, task, session, handler = task_station(tmp_path)
    with db.begin(actor=session.actor, correlation_id="submit") as uow:
        service.submit(uow, task)
        (first,) = service.claim(uow, worker_id="first_worker", handlers=frozenset({"handler"}), limit=1)
    staged = handler.execute(first)
    db.clock.at += timedelta(milliseconds=db.settings.integer("tasks.lease_ms"))
    with db.begin(actor=session.actor, correlation_id="recover") as uow:
        assert not service.complete(uow, first, staged)
        assert service.recover(uow, limit=1) == 1
        assert not service.claim(uow, worker_id="second_worker", handlers=frozenset({"handler"}), limit=1)
    db.clock.at += timedelta(milliseconds=db.settings.integer("tasks.retry_backoff_ms"))
    with db.begin(actor=session.actor, correlation_id="retry") as uow:
        (second,) = service.claim(uow, worker_id="second_worker", handlers=frozenset({"handler"}), limit=1)
        assert second.generation > first.generation
        assert not service.complete(uow, first, staged)
        assert not service.complete(uow, replace(second, worker_id="imposter"), handler.execute(second))
        assert service.fail(uow, second, code=ErrorCode.INTERNAL_ERROR, retryable=False)
        assert service.repository.get(uow, task.task_id).status.state == "failed"


def test_unknown_elapsed_time_keeps_running_lease_pending(tmp_path):
    db, service, task, session, handler = task_station(tmp_path, elapsed=lambda view, start, end: None)
    with db.begin(actor=session.actor, correlation_id="submit") as uow:
        service.submit(uow, task)
        (lease,) = service.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)
        assert not service.complete(uow, lease, handler.execute(lease))
        assert service.recover(uow, limit=1) == 0
        assert service.repository.get(uow, task.task_id).status.state == "running"


def test_revoked_task_grant_cancels_before_publishing_result(tmp_path):
    grant = [True]
    db, service, task, session, handler = task_station(tmp_path, grant=grant)
    with db.begin(actor=session.actor, correlation_id="submit") as uow:
        service.submit(uow, task)
        (lease,) = service.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)
    result = handler.execute(lease)
    grant[0] = False
    with db.begin(actor=session.actor, correlation_id="revoked") as uow:
        assert not service.complete(uow, lease, result)
        assert service.repository.get(uow, task.task_id).status.state == "cancelled"
    with db.snapshot() as view:
        assert not view.connection.rows("SELECT task_id FROM task_effect", (), limit=1)


def test_public_jobs_use_real_outbox_and_legal_null_result_projection(tmp_path):
    from dataclasses import replace
    from decimal import Decimal

    from crew_health.contracts_gen.schemas.job_schema import Job
    from crew_health.kernel.units import Quantity, Unit
    from crew_health.platform.api.jobs import job_body

    db, service, task, session, handler = task_station(tmp_path, public=True)
    with db.begin(actor=session.actor, correlation_id="submit") as uow:
        service.submit(uow, task)
        (lease,) = service.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)
    staged = handler.execute(lease)
    with db.begin(actor=session.actor, correlation_id="complete") as uow:
        assert service.progress(uow, lease, Quantity(Decimal("0.5"), Unit("1")))
        with pytest.raises(ProblemError):
            service.progress(uow, lease, Quantity(Decimal(1), Unit("h")))
        assert service.complete(uow, lease, staged)
    with db.snapshot() as view:
        status = service.get(view, session, task.task_id)
        assert status.job_id == task.task_id and status.state == "succeeded"
        assert service.result(view, session, task.task_id) is None
        assert Job.model_validate_json(job_body(status, None, "query")).result_ref is None
        assert view.connection.rows("SELECT COUNT(*) FROM events_outbox", (), limit=1) == ((4,),)
        with pytest.raises(ProblemError):
            service.get(view, replace(session, actor=Actor("person", "another")), task.task_id)


class Visibility:
    allowed = True

    def authorize(self, view, session, identity, result):
        if not self.allowed:
            from crew_health.contracts_gen.enums import ErrorCode
            from crew_health.kernel.errors import ProblemError

            raise ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "private text must not escape")


def command_station(tmp_path):
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.commands import command_identity
    from crew_health.kernel.uow import SessionContext
    from crew_health.platform.adapters.ledger import CommandLedger
    from crew_health.platform.application.commands import CommandExecutor

    db = database(tmp_path)
    db.migrate("CREATE TABLE test_command_effect(id TEXT PRIMARY KEY) STRICT;")
    session = SessionContext(
        "session_1", "crew", Actor("person", "person_1"), "person_1", frozenset({Role.CREW})
    )
    identity = command_identity(
        key="command_1",
        actor_ref="person_1",
        operation="recordObservation",
        target="/v1/observations",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )
    ledger = CommandLedger(db.ids, settings=db.settings)
    visibility = Visibility()
    executor = CommandExecutor(
        transactions=db, ledger=ledger, visibility=visibility, authorize=lambda session, operation: None
    )
    return db, session, identity, ledger, executor, visibility


def test_durable_receipt_replay_after_cache_window_and_reauthorization(tmp_path):
    from datetime import timedelta

    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.commands import StoredResult

    db, session, identity, ledger, executor, visibility = command_station(tmp_path)

    def apply(uow):
        uow.connection.execute("INSERT INTO test_command_effect VALUES (?)", ("effect_1",))
        return StoredResult(ledger.accepted(uow, identity), 201, "obs_1", "accepted", None)

    accepted = executor.execute(session, identity, apply)
    db.clock.at += timedelta(days=2)
    with pytest.raises(ProblemError) as retry:
        executor.execute(session, identity, apply)
    assert retry.value.code == ErrorCode.DUPLICATE_COMMAND
    assert retry.value.current_state["receipt_id"] == accepted.receipt.receipt_id
    assert set(retry.value.current_state) == {
        "kind",
        "command_key",
        "operation_id",
        "target",
        "receipt_id",
        "accepted_at",
    }
    visibility.allowed = False
    with pytest.raises(ProblemError) as revoked:
        executor.execute(session, identity, apply)
    assert revoked.value.code == ErrorCode.UNAUTHORIZED_SCOPE
    assert revoked.value.current_state is None
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM test_command_effect", (), limit=1) == ((1,),)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "/v1/observations/other"),
        ("operation", "cancelJob"),
        ("parameters_sha256", "1" * 64),
        ("body_fingerprint", "2" * 64),
    ],
)
def test_command_key_reuse_compares_complete_identity(tmp_path, field, value):
    from dataclasses import replace

    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.commands import StoredResult

    db, session, identity, ledger, executor, _ = command_station(tmp_path)
    executor.execute(
        session,
        identity,
        lambda uow: StoredResult(ledger.accepted(uow, identity), 201, "obs_1", "accepted", None),
    )
    with pytest.raises(ProblemError) as conflict:
        executor.execute(
            session,
            replace(identity, **{field: value}),
            lambda uow: pytest.fail("identity conflict executed"),
        )
    assert conflict.value.code == ErrorCode.DUPLICATE_COMMAND and conflict.value.current_state is None


def test_nonpublic_body_is_never_cached_and_unsafe_code_rejected(tmp_path):
    from crew_health.kernel.commands import StoredResult

    db, session, identity, ledger, _, _ = command_station(tmp_path)
    for code, body in [("accepted", b"SECRETCLINICALCANARY"), ("SECRETCLINICALCANARY", None)]:
        with (
            pytest.raises(ProblemError),
            db.begin(actor=session.actor, command=identity, correlation_id=identity.key) as uow,
        ):
            ledger.record(
                uow, identity, StoredResult(ledger.accepted(uow, identity), 201, "obs_1", code, body)
            )
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM platform_command_ledger", (), limit=1) == ((0,),)


def test_pending_parent_resumes_outside_writer_and_only_parent_receipt_accepts(tmp_path):
    from crew_health.kernel.commands import StoredResult
    from crew_health.platform.adapters.intents import IntentRepository
    from crew_health.platform.application.commands import CommandExecutor

    db, session, identity, ledger, _, visibility = command_station(tmp_path)
    intents = IntentRepository(ledger)
    with db.begin(actor=session.actor, correlation_id=identity.key) as uow:
        intent = intents.reserve(uow, identity, resume_handler="resume.test", input_ref="input_1")

    class Resumer:
        def resume(self, session, pending):
            assert pending == intent
            with db.begin(actor=session.actor, correlation_id=identity.key) as uow:
                result = StoredResult(ledger.accepted(uow, identity), 201, "obs_1", "accepted", None)
                uow.connection.execute("INSERT INTO test_command_effect VALUES (?)", ("effect_1",))
                intents.complete(uow, pending, result)
            return result

    executor = CommandExecutor(
        transactions=db,
        ledger=ledger,
        visibility=visibility,
        authorize=lambda s, o: None,
        resumers={"resume.test": Resumer()},
    )
    with pytest.raises(ProblemError) as completed:
        executor.execute(session, identity, lambda uow: pytest.fail("pending parent started as new"))
    assert completed.value.current_state["kind"] == "accepted_command"
    with db.snapshot() as view:
        assert view.connection.rows("SELECT count(*) FROM test_command_effect", (), limit=1) == ((1,),)


def test_scheduler_generation_due_and_atomic_effect(tmp_path):
    from dataclasses import replace
    from datetime import timedelta

    from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
    from crew_health.platform.application.scheduler import SchedulerService

    db, session, _, _, _, _ = command_station(tmp_path)
    scheduler = SchedulerService(db.settings)
    job = ScheduledJob(
        "schedule_1", "test.run", AbsoluteDeadline(db.clock.now()), {"record_id": "record_1"}, None
    )
    with db.begin(actor=session.actor, correlation_id="correlation_1") as uow:
        assert scheduler.replace(uow, job, expected_generation=None) == 1
        with pytest.raises(ProblemError):
            scheduler.replace(uow, job, expected_generation=None)
        assert (
            scheduler.replace(
                uow,
                replace(job, deadline=AbsoluteDeadline(db.clock.now() + timedelta(seconds=1))),
                expected_generation=1,
            )
            == 2
        )
    scheduler.register(
        "test.run",
        lambda uow, job: uow.connection.execute("INSERT INTO test_command_effect VALUES (?)", ("effect_1",)),
    )
    with db.begin(actor=session.actor, correlation_id="correlation_2") as uow:
        assert scheduler.dispatch(uow, limit=1) == 0
    db.clock.at += timedelta(seconds=1)
    with db.begin(actor=session.actor, correlation_id="correlation_3") as uow:
        assert scheduler.dispatch(uow, limit=1) == 1
        assert scheduler.dispatch(uow, limit=1) == 0
        assert scheduler.cancel(uow, job.key, expected_generation=2) is False


def test_evaluation_computation_rolls_back_only_effect_and_retry_keeps_input(tmp_path):
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.evaluation import (
        EvaluationInput,
        EvaluatorComputationFailure,
        EvaluatorRegistration,
    )
    from crew_health.kernel.uow import Audience, AudienceProjectionKey, ReplicationChange
    from crew_health.platform.adapters.evaluation import EvaluationRepository
    from crew_health.platform.application.evaluation import EvaluationService

    db, session, _, _, _, _ = command_station(tmp_path)

    class Evaluator:
        fails = True
        calls = []

        def evaluate(self, uow, value):
            self.calls.append(value)
            uow.connection.execute("INSERT INTO test_command_effect VALUES (?)", ("evaluation_effect",))
            if self.fails:
                raise EvaluatorComputationFailure("bounded_failure")

    evaluator = Evaluator()
    service = EvaluationService(
        (EvaluatorRegistration("rules", "version1", evaluator),),
        db.settings,
        db.ids,
        repository=EvaluationRepository(),
        capture=lambda uow: TimeInput(uow.now, "source_1", "segment_1", 1, "hub_corrected", "SIM"),
        fault_codes=frozenset({"bounded_failure"}),
    )
    observed = TimeInput(db.clock.now(), "source_1", "segment_1", 1, "hub_corrected", "SIM")
    value = EvaluationInput(
        "rules", "version1", "obs_1", observed, "release_1", "partition_1", 1, "completion_1"
    )
    with db.begin(actor=session.actor, correlation_id="correlation_1") as uow:
        uow.replicate(
            ReplicationChange(
                None,
                "observation",
                "obs_1",
                "create",
                b"{}",
                (),
                "release_1",
                Audience("test", "obs_1", AudienceProjectionKey("records", "observation", 1), "1"),
            )
        )
        assert service.run(uow, value) == "pending"
        assert uow.connection.rows("SELECT count(*) FROM test_command_effect", (), limit=1) == ((0,),)
    evaluator.fails = False
    with db.begin(actor=session.actor, correlation_id="correlation_2") as uow:
        assert service.retry(uow, completion_id="completion_1") == "completed"
        assert service.retry(uow, completion_id="completion_1") == "completed"
        assert evaluator.calls == [value, value]
    with db.snapshot() as view:
        assert service.pending(view, after_id=None, limit=10) == ()
        assert view.connection.rows("SELECT count(*) FROM test_command_effect", (), limit=1) == ((1,),)


def test_commit_refuses_undeclared_replicated_mutation_and_savepoint_restores_accounting(tmp_path):
    from crew_health.kernel.uow import Audience, AudienceProjectionKey, ReplicationChange

    db = database(tmp_path)
    db.migrate("CREATE TABLE test_replicated(id TEXT PRIMARY KEY) STRICT;")
    db.register_replicated_table("test_replicated", record_type="test_record", key_column="id")
    change = ReplicationChange(
        None,
        "test_record",
        "record_1",
        "create",
        b"{}",
        (),
        "release_1",
        Audience("test", "record_1", AudienceProjectionKey("test", "record", 1), "1"),
    )
    with (
        pytest.raises(ProblemError),
        db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_1") as uow,
    ):
        uow.connection.execute("INSERT INTO test_replicated VALUES (?)", ("record_1",))
    with db.begin(actor=Actor("person", "person_1"), correlation_id="correlation_2") as uow:
        with pytest.raises(ValueError), uow.savepoint("savepoint_1"):
            uow.connection.execute("INSERT INTO test_replicated VALUES (?)", ("record_1",))
            uow.replicate(change)
            raise ValueError("before savepoint release")
        uow.connection.execute("INSERT INTO test_replicated VALUES (?)", ("record_1",))
        uow.replicate(change)
    with db.snapshot() as view:
        assert view.connection.rows("SELECT * FROM test_replicated", (), limit=1) == (("record_1",),)
        assert view.connection.rows("SELECT seq FROM repl_log", (), limit=1) == ((1,),)


def test_production_identifier_adapter_uses_injected_uuid_and_stable_derivation():
    from uuid import UUID

    from crew_health.platform.storage import SystemIds

    ids = SystemIds(new_uuid=lambda: UUID("018f0000-0000-7000-8000-000000000001"))
    assert ids.new("obs") == "obs_018f0000000070008000000000000001"
    assert ids.derived("record", "a", "b") == ids.derived("record", "a", "b")
    assert ids.derived("record", "a", "b") != ids.derived("record", "ab")


def test_settings_document_cannot_weaken_registered_bound_or_hide_duplicate_key():
    from decimal import Decimal

    from crew_health.kernel.settings import SettingSpec
    from crew_health.platform.settings import ValidatedSettings

    spec = SettingSpec("storage.max_rows", "integer", None, Decimal(1), Decimal(1000), None, "SYSTEM 5.11")
    raw = b'{"settings":[{"key":"storage.max_rows","kind":"integer","unit":null,"minimum":"1","maximum":"200","basis":"explicit installation bound","value":120}]}'
    settings = ValidatedSettings.from_json(raw, specs=(spec,), max_bytes=1000)
    assert settings.integer("storage.max_rows") == 120
    for invalid in (
        raw.replace(b'"maximum":"200"', b'"maximum":"2000"'),
        raw.replace(b'"value":120', b'"value":120,"value":121'),
        raw.replace(b'"value":120', b'"value":NaN'),
    ):
        with pytest.raises(ProblemError):
            ValidatedSettings.from_json(invalid, specs=(spec,), max_bytes=1000)


def test_append_only_update_is_nonretryable_constraint_and_rolls_back(tmp_path: Path) -> None:
    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.audit import AuditEntry
    from crew_health.platform.domain.errors import StorageConstraintViolated

    db = database(tmp_path)
    db.migrate("CREATE TABLE test_fact (id TEXT PRIMARY KEY) STRICT;")
    actor = Actor("person", "person_1")
    with db.begin(actor=actor, correlation_id="audit_1") as uow:
        uow.connection.execute(
            "INSERT INTO platform_reference VALUES (?,?)", ("private_reference", "entry_1")
        )
        uow.audit(
            AuditEntry("audit_1", "private.accepted", 1, actor, {"entry": "entry_1"}, "audit_1", None, {})
        )
    with db.snapshot() as view:
        original = view.connection.rows("SELECT * FROM platform_audit", (), limit=1)
    with (
        pytest.raises(StorageConstraintViolated) as error,
        db.begin(actor=actor, correlation_id="forbidden_update") as uow,
    ):
        uow.connection.execute("INSERT INTO test_fact VALUES (?)", ("rolled_back",))
        uow.connection.execute("UPDATE platform_audit SET event_id=?", ("changed",))
    assert error.value.cause == "constraint"
    assert error.value.retryable is False
    assert error.value.code == ErrorCode.INTERNAL_ERROR
    with db.snapshot() as view:
        assert view.connection.rows("SELECT * FROM platform_audit", (), limit=1) == original
        assert view.connection.rows("SELECT * FROM test_fact", (), limit=1) == ()


def test_revision_reference_is_kept_durably_and_only_that_shape_bypasses_the_body_policy(tmp_path):
    """E05 records `{"revision": n}` so a replay rebuilds the response from its own history (SYSTEM 5.8). The
    ledger keeps that reference with the row, past the 24 h body cache, for sha256 and hmac identities alike;
    any other body still needs the explicit public projection."""
    from dataclasses import replace
    from datetime import timedelta

    from crew_health.kernel.commands import StoredResult

    db, session, identity, ledger, executor, visibility = command_station(tmp_path)
    private = replace(identity, key="command_2", fingerprint_kind="hmac_sha256")
    with db.begin(actor=session.actor, correlation_id="record") as uow:
        for current in (identity, private):
            ledger.record(
                uow,
                current,
                StoredResult(ledger.accepted(uow, current), 200, "msr_1", "created", b'{"revision":5}'),
            )
    db.clock.at += timedelta(days=2)
    with db.begin(actor=session.actor, correlation_id="replay") as uow:
        for current in (identity, private):
            stored = ledger.lookup(uow, current)
            assert stored is not None and stored.safe_body == b'{"revision":5}'
    for index, (resource, code, body) in enumerate(
        (
            ("msr_1", "created", b'{"revision":0}'),
            ("msr_1", "created", b'{"revision": 5}'),
            ("msr_1", "created", b'{"revision":5,"note":"private text"}'),
            (None, "created", b'{"revision":5}'),
            ("msr_1", "created; drop", b'{"revision":5}'),
            ("msr_1", "created", b'{"text":"private"}'),
        )
    ):
        current = replace(private, key=f"command_bad_{index}")
        with pytest.raises(ProblemError), db.begin(actor=session.actor, correlation_id="bad") as uow:
            ledger.record(
                uow, current, StoredResult(ledger.accepted(uow, current), 200, resource, code, body)
            )
