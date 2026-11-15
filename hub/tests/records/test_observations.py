"""T-E01-05/T-E01-06 R01/R06: source, partition, quantity and quality invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity, Unit, WireQuantity
from crew_health.records.domain.observations import ObservationPolicy, assess_quality

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-05", "T-E01-06", "R01", "R06")]


def policy():
    # Synthetic parser/quality golden, no clinical threshold or deployment policy.
    return ObservationPolicy(
        "indicator",
        "quality-version",
        "source-unit-test",
        Unit("1"),
        frozenset({"1"}),
        frozenset({"self_report"}),
        Decimal(0),
        Decimal(100),
        Decimal(0),
        Decimal(100),
        3,
        Decimal(10),
        True,
    )


@given(st.decimals(allow_nan=False, allow_infinity=False, min_value=-100, max_value=200))
def test_range_check_is_total(value):
    result = assess_quality(policy(), Quantity(value, Unit("1")), datetime(2026, 1, 1, tzinfo=UTC), ())
    assert result.status == ("accepted" if 0 < value < 100 else "rejected")


def test_stuck_saturated_and_rate_changes_are_kept_as_rejected_facts():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert assess_quality(
        policy(), Quantity(Decimal(50), Unit("1")), now, ((now, Quantity(Decimal(50), Unit("1"))),) * 2
    ).reasons == ("stuck_value",)
    assert assess_quality(policy(), Quantity(Decimal(100), Unit("1")), now, ()).status == "rejected"
    previous = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert (
        "rate_of_change"
        in assess_quality(
            policy(), Quantity(Decimal(50), Unit("1")), now, ((previous, Quantity(Decimal(1), Unit("1"))),)
        ).reasons
    )


def test_missing_manual_checks_or_unsourced_policy_fails_closed():
    from dataclasses import replace

    for invalid in (replace(policy(), manual_checks=False), replace(policy(), source_ref="")):
        with pytest.raises(ProblemError):
            invalid.validate()


def station(tmp_path):
    from crew_health.clock.adapters.repository import SqlClockRepository
    from crew_health.clock.application.clock import PersistentClocks
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
    from crew_health.kernel.testing import MemoryUnits, MemoryWireConverter
    from crew_health.kernel.units import ConvertedQuantity
    from crew_health.kernel.uow import Actor, SessionContext
    from crew_health.platform.testing.sqlite import database
    from crew_health.records.adapters.repository import RecordRepository
    from crew_health.records.application.commands import RecordsCommands
    from crew_health.records.application.visibility import OwnerAuthorization
    from crew_health.records.domain.observations import RecordContext

    db = database(tmp_path)
    for migration in sorted(Path("hub/src/crew_health/records/adapters/migrations").glob("*_records.sql")):
        db.migrate(migration.read_text(encoding="utf-8"))
    db.migrate(Path("hub/src/crew_health/clock/adapters/migrations/0001_clock.sql").read_text())
    clocks = PersistentClocks(
        repository=SqlClockRepository(
            settings=db.settings, node_id=db.origin_node_id, incarnation_id=db.origin_incarnation
        ),
        settings=db.settings,
        ids=db.ids,
    )
    with db.begin(actor=Actor("operator", "operator"), correlation_id="setup") as uow:
        clocks.open(uow, ClockSegment("source", "oscillator", "segment", None, 1, 1))
        clocks.apply(
            uow,
            ClockCorrection(
                "correction",
                "source",
                "segment",
                1,
                datetime(2026, 1, 1, tzinfo=UTC),
                None,
                0,
                0,
                uow.now,
                "operator",
            ),
        )
    session = SessionContext("session", "crew", Actor("person", "person"), "person", frozenset({Role.CREW}))

    class Binding:
        def bind(self, uow, session, value):
            return RecordContext(
                session.person_id,
                session.person_id,
                "mission",
                "SIM",
                TimeInput(value.observed_at, "source", "segment", 1, "source_raw", "SIM"),
                "self_capture",
            )

    class Policies:
        def for_code(self, release, code):
            return policy()

    class Workflow:
        def require(self, uow, session, answer):
            if session.person_id != answer.patient_id:
                raise ProblemError(
                    __import__(
                        "crew_health.contracts_gen.enums", fromlist=["ErrorCode"]
                    ).ErrorCode.UNAUTHORIZED_SCOPE,
                    "denied",
                )

    repo = RecordRepository()
    service = RecordsCommands(
        repository=repo,
        ids=db.ids,
        units=MemoryUnits({"1": ("scalar", Decimal(1), Decimal(0))}),
        converter=MemoryWireConverter(
            lambda value, code, release, to, pressure: ConvertedQuantity(
                value, Quantity(value.value, to), "identity", "mapping", None, "exact"
            )
        ),
        authorization=OwnerAuthorization(),
        binding=Binding(),
        policies=Policies(),
        clocks=clocks,
        workflow=Workflow(),
    )
    return db, repo, service, session


def observation_input(db, **updates):
    from dataclasses import replace

    from crew_health.kernel.units import WireQuantity
    from crew_health.records.domain.observations import ObservationInput

    value = ObservationInput(
        "person",
        None,
        "indicator",
        WireQuantity(Decimal(50), "1"),
        db.clock.now(),
        "SIM",
        "self_report",
        None,
        (),
        None,
        None,
        None,
    )
    return replace(value, **updates)


def test_real_sqlite_accepts_after_commit_and_preserves_rejected_reading(tmp_path):
    from crew_health.contracts_gen.schemas.observation_schema import Observation
    from crew_health.records.api.router import observation_wire

    db, repo, service, session = station(tmp_path)
    with db.begin(actor=session.actor, correlation_id="capture") as uow:
        first = service.record_observation(uow, session, observation_input(db))
        rejected = service.record_observation(
            uow, session, observation_input(db, original=WireQuantity(Decimal(100), "1"))
        )
    with db.snapshot() as view:
        assert repo.observation(view, first.observation_id) == first
        assert repo.observation(view, rejected.observation_id).quality_status == "rejected"
        accepted = repo.observations(
            view, person_id="person", mission_id="mission", origin_mode="SIM", code="indicator", limit=10
        )
        assert tuple(r.observation_id for r in accepted) == (first.observation_id,)
        assert not repo.observations(
            view, person_id="person", mission_id="mission", origin_mode="LIVE", code="indicator", limit=10
        )
        assert Observation.model_validate_json(observation_wire(first)).original_value == 50


def test_failure_in_outer_command_rolls_back_record_audit_and_replication(tmp_path):
    db, repo, service, session = station(tmp_path)
    with db.snapshot() as view:
        before_audit = view.connection.rows("SELECT COUNT(*) FROM platform_audit", (), limit=1)
        before_repl = view.connection.rows("SELECT COUNT(*) FROM repl_log", (), limit=1)
    with pytest.raises(OSError):
        with db.begin(actor=session.actor, correlation_id="capture") as uow:
            record = service.record_observation(uow, session, observation_input(db))
            raise OSError("injected storage fault")
    with db.snapshot() as view:
        assert repo.observation(view, record.observation_id) is None
        assert view.connection.rows("SELECT COUNT(*) FROM platform_audit", (), limit=1) == before_audit
        assert view.connection.rows("SELECT COUNT(*) FROM repl_log", (), limit=1) == before_repl
        assert not view.connection.rows("SELECT observation_id FROM records_observation", (), limit=10)


def test_unsupported_device_and_bad_lineage_never_insert(tmp_path):
    db, repo, service, session = station(tmp_path)
    for update in (
        {"acquisition_method": "device"},
        {"supersedes": "missing"},
        {"patient_id": "another"},
        {"episode_id": "missing"},
    ):
        with pytest.raises(ProblemError):
            with db.begin(actor=session.actor, correlation_id="capture") as uow:
                service.record_observation(uow, session, observation_input(db, **update))
    with db.snapshot() as view:
        assert not view.connection.rows("SELECT observation_id FROM records_observation", (), limit=10)


def test_observation_append_only_and_cross_crew_privacy(tmp_path):
    from dataclasses import replace

    from crew_health.records.application.queries import RecordsQueries
    from crew_health.records.application.visibility import OwnerAuthorization

    db, repo, service, session = station(tmp_path)
    with db.begin(actor=session.actor, correlation_id="capture") as uow:
        record = service.record_observation(uow, session, observation_input(db))
    with pytest.raises(ProblemError):
        with db.begin(actor=session.actor, correlation_id="tamper") as uow:
            uow.connection.execute(
                "DELETE FROM records_observation WHERE observation_id=?", (record.observation_id,)
            )
    with db.snapshot() as view:
        queries = RecordsQueries(repo, OwnerAuthorization(), db.settings)
        assert queries.observation(view, session, record.observation_id) == record
        with pytest.raises(ProblemError):
            queries.observation(view, replace(session, person_id="another"), record.observation_id)


def test_real_answer_append_publishes_once_and_rollback_shares_outer_uow(tmp_path):
    import hashlib
    import json
    from dataclasses import replace

    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.config import ConfigDocumentView
    from crew_health.kernel.testing import MemoryRelease
    from crew_health.records.domain.answers import AnswerAccepted, AnswerRecord

    raw = json.dumps(
        json.loads(Path("contracts/api/examples/config/questionnaires.example.json").read_text())["value"]
    ).encode()
    document = ConfigDocumentView(ConfigName.QUESTIONNAIRES, hashlib.sha256(raw).hexdigest(), raw)
    db, repo, service, session = station(tmp_path)
    pinned = MemoryRelease("release", {document.name: document})
    db.set_release_provider(lambda: pinned)
    answer = AnswerRecord(
        "ans_first",
        "mood",
        pinned.version_of(ConfigName.QUESTIONNAIRES),
        "present",
        Quantity(Decimal(3), Unit("1")),
        None,
        None,
        session.actor,
        db.clock.now(),
        "SIM",
        None,
        "person",
        "mission",
        None,
        pinned.release_id,
        TimeInput(db.clock.now(), "source", "segment", 1, "source_raw", "SIM"),
        db.clock.now(),
        None,
    )
    delivered = []
    db.bus.subscribe(
        AnswerAccepted, lambda uow, event: delivered.append(event.answer.answer_id), name="capture"
    )
    with db.begin(actor=session.actor, correlation_id="answer") as uow:
        assert service.append_answer(uow, session, answer) == answer
        assert service.append_answer(uow, session, answer) == answer
    assert delivered == [answer.answer_id]
    with db.snapshot() as view:
        assert repo.answer(view, answer.answer_id) == answer
    corrected = replace(
        answer, answer_id="ans_next", supersedes=answer.answer_id, presence="declined", value=None
    )

    def fail(uow, event):
        raise OSError("storage failure")

    db.bus.subscribe(AnswerAccepted, fail, name="failure")
    with pytest.raises(OSError):
        with db.begin(actor=session.actor, correlation_id="answer") as uow:
            service.append_answer(uow, session, corrected)
    with db.snapshot() as view:
        assert repo.answer(view, corrected.answer_id) is None
        assert repo.answer(view, answer.answer_id) == answer


def test_observation_evaluator_computation_failure_commits_pending_and_pinned_input(tmp_path):
    from datetime import timedelta

    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.evaluation import EvaluatorComputationFailure, EvaluatorRegistration
    from crew_health.kernel.scheduler import ElapsedDeadline, ScheduledJob
    from crew_health.kernel.testing import MemoryEvaluator
    from crew_health.platform.adapters import codec
    from crew_health.platform.adapters.evaluation import EvaluationRepository
    from crew_health.platform.application.evaluation import EvaluationService
    from crew_health.records.application.evaluation import RecordEvaluation

    db, repo, service, session = station(tmp_path)

    def fail(uow, value):
        raise EvaluatorComputationFailure("numerical_failure")

    isolated = EvaluationService(
        (EvaluatorRegistration("rules", "version", MemoryEvaluator(fail)),),
        db.settings,
        db.ids,
        repository=EvaluationRepository(),
        capture=lambda uow: TimeInput(uow.now, "source", "segment", 1, "source_raw", "SIM"),
        fault_codes=frozenset({"numerical_failure"}),
    )
    service.evaluations = (RecordEvaluation(isolated, db.ids, "rules", "version"),)
    with db.begin(actor=session.actor, correlation_id="capture") as uow:
        record = service.record_observation(
            uow, session, observation_input(db, observed_at=db.clock.now() - timedelta(minutes=1))
        )
    with db.snapshot() as view:
        assert repo.observation(view, record.observation_id) == record
        (pending,) = isolated.pending(view, after_id=None, limit=10)
        assert pending.input.input_ref == record.observation_id
        assert pending.input.observed == record.observed
        assert pending.input.release_id == record.release_id

        (stored,) = view.connection.rows("SELECT job FROM platform_schedule", (), limit=1)
        retry = codec(ScheduledJob).validate_json(stored[0])
        assert isinstance(retry.deadline, ElapsedDeadline)
        assert retry.deadline.anchor.original_at == record.received_at
        assert retry.deadline.anchor.original_at != record.observed.original_at
        assert retry.deadline.anchor.source_clock_id == "source"
        assert retry.deadline.anchor.segment_id == "segment"
        assert retry.deadline.anchor.origin_mode == "SIM"
        assert retry.deadline.duration == timedelta(
            milliseconds=db.settings.integer("evaluation.retry_backoff_ms")
        )


def test_observation_evaluator_storage_failure_rolls_back_input(tmp_path):
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.evaluation import EvaluatorRegistration
    from crew_health.kernel.testing import MemoryEvaluator
    from crew_health.platform.adapters.evaluation import EvaluationRepository
    from crew_health.platform.application.evaluation import EvaluationService
    from crew_health.records.application.evaluation import RecordEvaluation

    db, repo, service, session = station(tmp_path)

    def fail(uow, value):
        raise OSError("disk I/O failure")

    isolated = EvaluationService(
        (EvaluatorRegistration("rules", "version", MemoryEvaluator(fail)),),
        db.settings,
        db.ids,
        repository=EvaluationRepository(),
        capture=lambda uow: TimeInput(uow.now, "source", "segment", 1, "source_raw", "SIM"),
    )
    service.evaluations = (RecordEvaluation(isolated, db.ids, "rules", "version"),)
    with pytest.raises(OSError):
        with db.begin(actor=session.actor, correlation_id="capture") as uow:
            service.record_observation(uow, session, observation_input(db))
    with db.snapshot() as view:
        assert not view.connection.rows("SELECT observation_id FROM records_observation", (), limit=10)
        assert not isolated.pending(view, after_id=None, limit=10)


def test_http_observation_parser_rejects_duplicate_keys_and_preserves_decimal():
    from crew_health.records.api.router import observation_input

    raw = b'{"patient_id":"person","code":"indicator","value":0.1234567890123456789,"unit":"1","observed_at":"2026-11-14T00:00:00Z","origin_mode":"SIM","acquisition_method":"self_report"}'
    assert observation_input(raw).original.value == Decimal("0.1234567890123456789")
    with pytest.raises(ProblemError):
        observation_input(raw.replace(b'"value":', b'"value":99,"value":'))


@pytest.mark.trace("T-E01-05", "R06")
@pytest.mark.parametrize(
    "other_source", [{"device_id": "device_b"}, {"acquisition_method": "manual_instrument"}]
)
def test_interleaved_sources_cannot_hide_stuck_value(tmp_path, other_source):
    from dataclasses import replace
    from datetime import timedelta

    db, repo, service, session = station(tmp_path)

    class Policies:
        def for_code(self, release, code):
            return replace(policy(), methods=frozenset({"self_report", "manual_instrument"}))

    service.policies = Policies()
    values = [({}, 50), ({}, 50), (other_source, 10), (other_source, 20), (other_source, 30), ({}, 50)]
    with db.begin(actor=session.actor, correlation_id="capture") as uow:
        for index, (source, value) in enumerate(values):
            reading = service.record_observation(
                uow,
                session,
                observation_input(
                    db,
                    original=WireQuantity(Decimal(value), "1"),
                    observed_at=db.clock.now() - timedelta(minutes=len(values) - index),
                    **({"device_id": "device_a"} | source),
                ),
            )
    assert reading.quality_status == "rejected"
    assert reading.quality_reasons == ("stuck_value",)
    with db.snapshot() as view:
        assert repo.observation(view, reading.observation_id) == reading


@pytest.mark.trace("T-E01-05", "R06")
def test_changed_canonical_unit_between_releases_is_rejected_and_retains_both_readings(tmp_path):
    from dataclasses import replace
    from datetime import timedelta

    from crew_health.kernel.testing import MemoryRelease

    db, repo, service, session = station(tmp_path)
    with db.begin(actor=session.actor, correlation_id="old_release") as uow:
        old = service.record_observation(
            uow, session, observation_input(db, observed_at=db.clock.now() - timedelta(seconds=1))
        )

    class ChangedPolicy:
        def for_code(self, release, code):
            return replace(policy(), version="changed", canonical_unit=Unit("h"))

    service.policies = ChangedPolicy()
    db.set_release_provider(lambda: MemoryRelease("release_changed", {}))
    with db.begin(actor=session.actor, correlation_id="new_release") as uow:
        new = service.record_observation(
            uow, session, observation_input(db, original=WireQuantity(Decimal(1), "1"))
        )
    assert new.quality_status == "rejected"
    assert "history_unit_mismatch" in new.quality_reasons
    assert "rate_of_change" not in new.quality_reasons
    assert old.release_id != new.release_id
    with db.snapshot() as view:
        assert repo.observation(view, old.observation_id) == old
        assert repo.observation(view, new.observation_id) == new


@pytest.mark.trace("T-E01-05", "R06")
def test_quality_never_subtracts_mixed_historical_units():
    from datetime import timedelta

    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = assess_quality(
        policy(),
        Quantity(Decimal(50), Unit("1")),
        now,
        ((now - timedelta(seconds=1), Quantity(Decimal(1), Unit("h"))),),
    )
    assert result.status == "rejected"
    assert result.reasons == ("history_unit_mismatch",)


@pytest.mark.trace("T-E01-05", "R06")
def test_unit_mismatch_never_downgrades_a_saturated_reading():
    """Review N9: a saturated reading after a unit change stays rejected with its saturation reason."""
    from datetime import timedelta

    now = datetime(2026, 1, 1, tzinfo=UTC)
    history = ((now - timedelta(seconds=5), Quantity(Decimal(1), Unit("h"))),)
    result = assess_quality(policy(), Quantity(Decimal(100), Unit("1")), now, history)
    assert result.status == "rejected"
    assert "saturation" in result.reasons and "history_unit_mismatch" in result.reasons


@pytest.mark.trace("T-E01-05", "R06")
def test_interleaved_sources_do_not_hide_rate_change_or_use_later_readings(tmp_path):
    from datetime import timedelta

    db, _, service, session = station(tmp_path)
    now = db.clock.now()
    with db.begin(actor=session.actor, correlation_id="capture") as uow:
        for device, offset, value in [
            (None, -2, 1),
            ("other", -1, 30),
            ("other", -1, 40),
            ("other", -1, 50),
            (None, 0, 90),
            (None, -1, 50),
        ]:
            record = service.record_observation(
                uow,
                session,
                observation_input(
                    db,
                    device_id=device,
                    observed_at=now + timedelta(seconds=offset),
                    original=WireQuantity(Decimal(value), "1"),
                ),
            )
    assert record.quality_status == "rejected"
    assert record.quality_reasons == ("rate_of_change",)


def test_history_filters_before_the_limit_and_signals_more(tmp_path):
    """R-E14-2: rows the history excludes (an unknown time) never use up the page, and has_more says whether
    older matching history exists, so a short page is never mistaken for complete history."""
    from dataclasses import replace
    from datetime import timedelta

    from crew_health.records.application.queries import RecordsQueries
    from crew_health.records.application.visibility import OwnerAuthorization

    db, repo, service, session = station(tmp_path)
    with db.begin(actor=session.actor, correlation_id="capture") as uow:
        known = service.record_observation(uow, session, observation_input(db))
        assert known.normalized.state == "known" and known.quality_status == "accepted"
        for index in range(5):  # newer than `known` and more of them than the page holds
            later = known.normalized.corrected_at + timedelta(minutes=index + 1)
            repo.add_observation(
                uow,
                replace(
                    known,
                    observation_id=f"obs_unknown_{index}",
                    normalized=replace(known.normalized, corrected_at=later, state="unknown"),
                ),
            )
    queries = RecordsQueries(repo, OwnerAuthorization(), db.settings)
    scope = {"mission_id": known.mission_id, "origin_mode": known.origin_mode}
    with db.snapshot() as view:
        page = queries.accepted(view, session, known.patient_id, known.code, limit=2, **scope)
    assert page.items == (known,) and page.has_more is False
    with db.begin(actor=session.actor, correlation_id="more") as uow:
        for index in range(2):
            earlier = known.normalized.corrected_at - timedelta(minutes=index + 1)
            repo.add_observation(
                uow,
                replace(
                    known,
                    observation_id=f"obs_known_{index}",
                    normalized=replace(known.normalized, corrected_at=earlier),
                ),
            )
    with db.snapshot() as view:
        first = queries.accepted(view, session, known.patient_id, known.code, limit=2, **scope)
        whole = queries.accepted(view, session, known.patient_id, known.code, limit=3, **scope)
    assert [r.observation_id for r in first.items] == [known.observation_id, "obs_known_0"]
    assert first.has_more is True
    assert len(whole.items) == 3 and whole.has_more is False


@pytest.mark.trace("T-E01-05", "R06")
def test_rare_source_stuck_history_is_indexed_and_bounded_by_its_limit(tmp_path):
    """AGENTS 1.3 and 2: the stuck value history of a rare source (a manual reading after a long 1 Hz device run in
    the same partition) reads only its own last rows through records_observation_source_history, so a large
    partition never exhausts storage.max_vm_steps and the reading is still recorded."""
    import hashlib
    import json
    import sqlite3
    from datetime import timedelta

    db, repo, service, session = station(tmp_path)
    start = db.clock.now() - timedelta(days=4)
    rows = []
    for index in range(250_000):  # under three days of one 1 Hz device, all corrected and in the source index
        at = (start + timedelta(seconds=index)).isoformat()
        payload = json.dumps(
            {
                "acquisition_method": "self_report",
                "device_id": "device_bulk",
                "normalized": {"corrected_at": at},
            }
        ).encode()
        rows.append(
            (
                f"obs_bulk_{index}",
                "person",
                "mission",
                "SIM",
                "indicator",
                at,
                "accepted",
                payload,
                hashlib.sha256(payload).hexdigest(),
            )
        )
    raw = sqlite3.connect(db.path)
    raw.executemany(
        "INSERT INTO records_observation(observation_id,patient_id,mission_id,origin_mode,code,observed_at,"
        "quality_status,payload,digest) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    raw.commit()
    raw.close()
    readings = []
    with db.begin(actor=session.actor, correlation_id="rare") as uow:
        for minutes in (3, 2, 1):
            readings.append(
                service.record_observation(
                    uow,
                    session,
                    observation_input(
                        db,
                        observed_at=db.clock.now() - timedelta(minutes=minutes),
                        device_id="device_rare",
                    ),
                )
            )
    assert readings[-1].quality_reasons == ("stuck_value",)
    with db.snapshot() as view:
        history = repo.observations(
            view,
            person_id="person",
            mission_id="mission",
            origin_mode="SIM",
            code="indicator",
            limit=3,
            accepted_only=False,
            before=db.clock.now(),
            source=("self_report", "device_rare"),
            corrected_only=True,
        )
        assert [r.observation_id for r in history] == [r.observation_id for r in reversed(readings)]
        for unbounded in ({"corrected_only": False}, {"corrected_only": True, "accepted_only": True}):
            with pytest.raises(ProblemError):
                repo.observations(
                    view,
                    person_id="person",
                    mission_id="mission",
                    origin_mode="SIM",
                    code="indicator",
                    limit=3,
                    source=("self_report", "device_rare"),
                    **({"accepted_only": False} | unbounded),
                )
