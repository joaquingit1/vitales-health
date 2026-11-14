"""T-E01-01 R01/R06 independently checked canonical and finite-value cases."""

from __future__ import annotations

import operator
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import CommandIdentity, command_identity
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.evaluation import EvaluationInput, PendingEvaluation
from crew_health.kernel.ids import require_id
from crew_health.kernel.private import PrivateIntent
from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.testing import (
    MemoryClock,
    MemoryIds,
    MemoryRelease,
    MemoryUow,
    MemoryUowFactory,
    MemoryView,
)
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-01", "R01", "R06")]


@given(st.text())
def test_identifier_parser_is_total(value: str) -> None:
    import re

    from crew_health.kernel.ids import ID_PATTERN

    if re.fullmatch(ID_PATTERN, value):
        assert require_id(value) == value
    else:
        with pytest.raises(ProblemError):
            require_id(value)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_nonfinite_quantity_rejected(value: str) -> None:
    with pytest.raises(ProblemError):
        Quantity(Decimal(value), Unit("1"))


@given(st.decimals(allow_nan=False, allow_infinity=False))
def test_quantity_preserves_decimal(value: Decimal) -> None:
    assert Quantity(value, Unit("1")).value == value


def test_derived_id_avoids_separator_collisions() -> None:
    ids = MemoryIds(identifiers=("one",))
    assert ids.derived("p", "a:b", "c") != ids.derived("p", "a", "b:c")
    assert ids.new("p") == "p_one"
    assert ids.derived("p", "a", "b") == "p_2ec91f196666a2b4bd3bc12cd835d4c3"


def test_command_parameters_canonicalize_and_reject_duplicates() -> None:
    def identity(value: bytes) -> CommandIdentity:
        return command_identity(
            key="k",
            actor_ref="a",
            operation="op",
            target="/x",
            parameters_json=value,
            body_fingerprint="0" * 64,
            fingerprint_kind="sha256",
        )

    assert identity(b'{"z":1,"a":null}') == identity(b'{"a":null,"z":1}')
    with pytest.raises(ProblemError):
        identity(b'{"a":1,"a":2}')
    with pytest.raises(ProblemError):
        identity(b'{"a":NaN}')


def test_utc_construction() -> None:
    from crew_health.kernel.clock import TimeInput

    with pytest.raises(ProblemError):
        TimeInput(datetime(2026, 1, 1), "c", "s", 1, "source_raw", "LIVE")
    assert TimeInput(datetime(2026, 1, 1, tzinfo=UTC), "c", "s", 1, "source_raw", "SIM").origin_mode == "SIM"


@pytest.fixture
def station() -> MemoryUowFactory:
    import hashlib

    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.config import ConfigDocumentView
    from crew_health.kernel.settings import SettingSpec
    from crew_health.kernel.testing import (
        MemoryClock,
        MemoryRelease,
        MemorySettings,
        MemoryStorage,
        MemoryUowFactory,
    )

    values = {
        "storage.max_rows": 40,
        "storage.max_statement_bytes": 1000,
        "event_bus.max_events": 4,
        "events.max_subscribers": 4,
        "scheduler.max_jobs": 4,
        "registry.max_hooks": 8,
        "commands.cache_entries": 8,
        "commands.max_bytes": 1024,
        "clock.max_uncertainty_ms": 100,
        "clock.history_page_size": 10,
        "clock.future_tolerance_ms": 100,
        "clock.hlc_future_ms": 100,
        "clock.hlc_logical_max": 100,
        "tasks.max_pending": 4,
        "tasks.claim_batch": 2,
        "tasks.max_attempts": 2,
        "tasks.lease_ms": 1000,
        "tasks.max_input_bytes": 1000,
        "tasks.max_result_bytes": 1000,
        "evaluation.page_size": 4,
        "evaluation.retry_max": 2,
        "sync.pending_max": 8,
        "sync.page_entries": 4,
        "private.max_versions": 8,
        "private.max_entry_bytes": 20000000,
        "private.recovery_batch": 4,
    }
    specs = [
        SettingSpec(
            k,
            "integer",
            None,
            Decimal(1),
            Decimal(v),
            None,
            "docs/tdd/E01.md section 11 explicit test profile",
        )
        for k, v in values.items()
    ]
    settings = MemorySettings(specs, values)
    raw = ui_document()
    doc = ConfigDocumentView(ConfigName.UI_COPY, hashlib.sha256(raw).hexdigest(), raw)
    release = MemoryRelease("release", {doc.name: doc})
    clock = MemoryClock(datetime(2026, 1, 2, 12, tzinfo=UTC), 0, "epoch")
    storage = MemoryStorage(settings=settings, responses={("query", ()): (("value",),)}, results={})
    factory = MemoryUowFactory(
        clock=clock,
        ids=MemoryIds(identifiers=tuple(str(i) for i in range(100))),
        release=release,
        settings=settings,
        connection=storage,
        node_id="node",
        incarnation="incarnation",
    )
    return factory


def actor() -> Actor:
    from crew_health.kernel.uow import Actor

    return Actor("person", "owner")


def session() -> SessionContext:
    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.uow import SessionContext

    return SessionContext("session", "crew", actor(), "owner", frozenset({Role.CREW}))


def identity() -> CommandIdentity:
    return command_identity(
        key="command",
        actor_ref="owner",
        operation="operation",
        target="/target",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="sha256",
    )


def test_uow_rollback_savepoint_snapshot_and_bounds(station: MemoryUowFactory) -> None:
    from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob

    job = ScheduledJob("job", "handler", AbsoluteDeadline(station.clock.now()), {}, None)
    with station.snapshot() as snapshot:
        with station.begin(actor=actor(), correlation_id="correlation") as uow:
            assert uow.now == station.clock.now()
            with pytest.raises(ValueError), uow.savepoint("nested"):
                uow.schedule(job, expected_generation=None)
                raise ValueError("injected savepoint rollback")
            assert uow.schedule(job, expected_generation=None) == 1
        assert isinstance(snapshot, MemoryView)
        assert not snapshot.state.schedules
        with pytest.raises(ProblemError):
            snapshot.connection.execute("query")
    with pytest.raises(ValueError), station.begin(actor=actor(), correlation_id="rollback") as uow:
        uow.cancel_schedule("job", expected_generation=1)
        raise ValueError("injected crash")
    assert not station.state.schedules["job"][2]
    with station.begin(actor=actor(), correlation_id="retry") as uow:
        assert uow.cancel_schedule("job", expected_generation=1)
        assert not uow.cancel_schedule("job", expected_generation=1)
        with pytest.raises(ProblemError):
            uow.schedule(job, expected_generation=0)
        with pytest.raises(ProblemError), station.begin(actor=actor(), correlation_id="concurrent"):
            pass
    with station.snapshot() as view:
        assert view.connection.rows("query", (), limit=1) == (("value",),)
        with pytest.raises(ProblemError):
            view.connection.rows("query", (), limit=0)
        with pytest.raises(ProblemError):
            view.connection.rows("query", (), limit=41)


def test_buffered_events_replicas_and_audit_roll_back(station: MemoryUowFactory) -> None:
    from crew_health.contracts_gen.enums import EventType
    from crew_health.kernel.audit import AuditEntry
    from crew_health.kernel.uow import (
        Audience,
        AudienceProjectionKey,
        EventScope,
        OutboxEvent,
        ReplicationChange,
    )

    audience = Audience("owner", "object", AudienceProjectionKey("owner", "event", 1), "policy")
    with station.begin(actor=actor(), correlation_id="correlation") as uow:
        with pytest.raises(ValueError), uow.savepoint("events"):
            uow.emit(
                OutboxEvent(
                    EventType.CONFIG_RELEASE_ACTIVATED,
                    uow.now,
                    EventScope("operator", None, None),
                    "release",
                    None,
                    b"{}",
                    audience,
                )
            )
            uow.replicate(
                ReplicationChange(None, "reference", "id", "create", b"{}", (), "release", audience)
            )
            uow.audit(AuditEntry("audit", "event", 1, actor(), {}, "correlation", None, {}))
            raise ValueError("crash")
    assert station.state.events == []
    assert station.state.audit == []
    assert station.state.replication == []


def test_fifo_bus_sorted_handlers_and_fanout_bound(station: MemoryUowFactory) -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Event:
        event_type: ClassVar[str] = "test"
        value: int

    order = []
    station.bus.subscribe(Event, lambda uow, e: order.append(("b", e.value)), name="b")
    station.bus.subscribe(Event, lambda uow, e: order.append(("a", e.value)), name="a")
    with station.begin(actor=actor(), correlation_id="bus") as uow:
        uow.publish(Event(1))
    assert order == [("a", 1), ("b", 1)]
    station.bus.subscribe(Event, lambda uow, e: uow.publish(Event(e.value + 1)), name="loop")
    with pytest.raises(ProblemError), station.begin(actor=actor(), correlation_id="bounded") as uow:
        uow.publish(Event(1))
    assert station.bus.queue == []


def test_commands_full_identity_intents_receipts_and_denial(station: MemoryUowFactory) -> None:
    from dataclasses import replace

    from crew_health.kernel.commands import StoredResult
    from crew_health.kernel.testing import MemoryCommands

    commands = MemoryCommands(
        ids=station.ids,
        settings=station.settings,
        authorize=lambda *args: True,
        operations={"operation": True},
    )
    with station.begin(actor=actor(), correlation_id="command") as uow:
        pending = commands.reserve(uow, identity(), resume_handler="handler", input_ref="input")
        assert commands.reserve(uow, identity(), resume_handler="handler", input_ref="input") == pending
        assert commands.lookup(uow, identity()) is None
        receipt = commands.accepted(uow, identity())
        result = StoredResult(receipt, 201, "record", "accepted", None)
        commands.complete(uow, pending, result)
        assert commands.lookup(uow, identity()) == result
        assert commands.pending(uow, identity()) is None
        for changed in [
            replace(identity(), target="/other"),
            replace(identity(), operation="other"),
            replace(identity(), parameters_sha256="1" * 64),
            replace(identity(), body_fingerprint="2" * 64),
        ]:
            with pytest.raises(ProblemError):
                commands.lookup(uow, changed)
    with station.snapshot() as snapshot:
        assert commands.read(snapshot, session(), identity()) == receipt
        denied = MemoryCommands(
            ids=station.ids, settings=station.settings, authorize=lambda *args: False, operations={}
        )
        with pytest.raises(ProblemError):
            denied.read(snapshot, session(), identity())
    assert commands.requires_ledger("operation")
    with pytest.raises(ProblemError):
        commands.requires_ledger("unknown")


def test_config_bytes_are_pinned_and_scratch_label_changes(station: MemoryUowFactory) -> None:
    import hashlib
    import json

    from crew_health.contracts_gen.enums import ConfigName
    from crew_health.kernel.config import ConfigDocumentView
    from crew_health.kernel.testing import MemoryConfig, MemoryRelease

    old = memory_release(station)
    content = json.loads(old.document(ConfigName.UI_COPY).raw)
    original_label = content["body_region_labels"]["head"]
    content["body_region_labels"]["head"] = original_label.upper()
    raw = json.dumps(content).encode()
    validate_ui(content)
    document = ConfigDocumentView(ConfigName.UI_COPY, hashlib.sha256(raw).hexdigest(), raw)
    new = MemoryRelease("new", {document.name: document})
    reader = MemoryConfig([old, new], active_id="new")
    assert (
        json.loads(reader.document(ConfigName.UI_COPY).raw)["body_region_labels"]["head"]
        == original_label.upper()
    )
    assert json.loads(old.document(ConfigName.UI_COPY).raw)["body_region_labels"]["head"] == original_label
    assert reader.document(ConfigName.UI_COPY, old.version_of(ConfigName.UI_COPY)).raw != raw
    with pytest.raises(ProblemError):
        reader.release("missing")
    with pytest.raises(ProblemError):
        MemoryRelease("invalid", {document.name: ConfigDocumentView(document.name, "0" * 64, raw)})
    with pytest.raises(TypeError):
        apply_operator("setitem", new.documents, ConfigName.UI_COPY, document)


def test_settings_missing_wrong_type_and_bounds(station: MemoryUowFactory) -> None:
    from crew_health.kernel.testing import MemorySettings

    with pytest.raises(ProblemError):
        station.settings.integer("absent")
    values = dict(station.settings.values)
    values["storage.max_rows"] = True
    with pytest.raises(ProblemError):
        MemorySettings(tuple(station.settings.specs.values()), values)
    values["storage.max_rows"] = 41
    with pytest.raises(ProblemError):
        MemorySettings(tuple(station.settings.specs.values()), values)


def test_unit_independent_goldens_and_case_sensitive_parser() -> None:
    from crew_health.kernel.testing import MemoryUnits

    # SI exact definitions: metre, centimetre; Celsius offset 273.15 K.
    units = MemoryUnits(
        {
            "m": ("length", Decimal(1), Decimal(0)),
            "cm": ("length", Decimal("0.01"), Decimal(0)),
            "K": ("temperature", Decimal(1), Decimal(0)),
            "Cel": ("temperature", Decimal(1), Decimal("273.15")),
        }
    )
    assert units.convert(Quantity(Decimal(125), units.parse("cm")), units.parse("m")).value == Decimal("1.25")
    assert units.convert(Quantity(Decimal(0), units.parse("Cel")), units.parse("K")).value == Decimal(
        "273.15"
    )
    with pytest.raises(ProblemError):
        units.parse("M")
    with pytest.raises(ProblemError):
        units.convert(Quantity(Decimal(1), units.parse("m")), units.parse("K"))


def test_clock_segments_keep_equal_raw_times_separate(station: MemoryUowFactory) -> None:
    from datetime import timedelta

    from crew_health.kernel.clock import ClockCorrection, ClockSegment, TimeInput
    from crew_health.kernel.testing import MemoryClocks

    clocks = MemoryClocks(settings=station.settings, clock=station.clock, node_id="node", incarnation="inc")
    original = datetime(2026, 1, 2, 10, 5, tzinfo=UTC)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with station.begin(actor=actor(), correlation_id="clock") as uow:
        clocks.open(uow, ClockSegment("source", "physical", "a", None, 0, 1))
        clocks.open(uow, ClockSegment("source", "physical", "b", "a", 100, 2))
        clocks.apply(uow, ClockCorrection("ca", "source", "a", 1, start, None, 0, 0, uow.now, "operator"))
        clocks.apply(
            uow, ClockCorrection("cb", "source", "b", 2, start, None, 3600000, 0, uow.now, "operator")
        )
        assert clocks.tick(uow).logical == 0
        assert clocks.tick(uow).logical == 1
    with station.snapshot() as view:
        a = TimeInput(original, "source", "a", 1, "source_raw", "LIVE")
        b = TimeInput(original, "source", "b", 2, "source_raw", "LIVE")
        assert clocks.normalize(view, a).corrected_at == original
        assert clocks.normalize(view, b).corrected_at == original + timedelta(hours=1)
        segment_a = clocks.for_sequence(view, source_clock_id="source", sequence=99)
        segment_b = clocks.for_sequence(view, source_clock_id="source", sequence=100)
        assert segment_a is not None and segment_a.segment_id == "a"
        assert segment_b is not None and segment_b.segment_id == "b"
        assert clocks.elapsed(view, a, b) == timedelta(hours=1)
        assert (
            clocks.normalize(view, TimeInput(original, "source", "unknown", 1, "source_raw", "LIVE")).state
            == "unknown"
        )
        assert len(clocks.history(view, after_version=1, limit=10)) == 1


def test_mission_clock_opaque_versions_and_half_open_days() -> None:
    from datetime import timedelta

    from crew_health.kernel.met import MissionClockVersion
    from crew_health.kernel.testing import MemoryMissionClock

    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    clock = MemoryMissionClock([MissionClockVersion("opaque-z", 1, epoch, epoch, None, mission_template())])
    assert clock.met(epoch).met == "000/00:00:00"
    assert clock.mission_day(epoch + timedelta(days=1)) == 2
    assert clock.label(epoch, version="opaque-z") == (mission_template().format(day=1), 1)
    assert clock.day_bounds(2, version="opaque-z") == (epoch + timedelta(days=1), epoch + timedelta(days=2))
    with pytest.raises(ProblemError):
        clock.met(epoch - timedelta(seconds=1), version="opaque-z")
    with pytest.raises(ProblemError):
        clock.met(epoch, version="missing")


def test_task_generation_cancel_and_late_completion(station: MemoryUowFactory) -> None:
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.tasks import TaskInput, TaskRegistration, TaskResult
    from crew_health.kernel.testing import MemoryTaskHandler, MemoryTasks
    from crew_health.kernel.uow import Audience, AudienceProjectionKey

    applied = []
    handler = MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "result", "hash"),
        lambda *args: applied.append("applied"),
        lambda *args: None,
    )
    tasks = MemoryTasks(
        settings=station.settings,
        registrations=[TaskRegistration("handler", "v1", handler, None, None)],
        authorize=lambda *args: True,
        capture=lambda at: TimeInput(at, "clock", "segment", 1, "hub_corrected", "LIVE"),
        elapsed=lambda view, start, end: end.original_at - start.original_at,
    )
    audience = Audience("owner", "object", AudienceProjectionKey("owner", "task", 1), "policy")
    task = TaskInput(
        "task", "handler", "v1", "input", "hash", "release", None, actor(), audience, identity(), None
    )
    with station.begin(actor=actor(), correlation_id="task") as uow:
        assert tasks.submit(uow, task).state == "queued"
        assert tasks.submit(uow, task).task_id == "task"
        lease = tasks.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)[0]
        assert tasks.progress(uow, lease, Quantity(Decimal("0.5"), Unit("1")))
        tasks.cancel(uow, session(), "task")
        assert not tasks.complete(uow, lease, handler.execute(lease))
    assert not applied
    with station.snapshot() as view, pytest.raises(ProblemError):
        tasks.get(view, session(), "task")


@pytest.mark.parametrize("fault", ["computation", "storage", "io", "auth", "config", "input"])
def test_only_computation_failure_is_isolated(station: MemoryUowFactory, fault: str) -> None:
    from crew_health.contracts_gen.enums import ErrorCode
    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.errors import StorageUnavailable
    from crew_health.kernel.evaluation import (
        EvaluationInput,
        EvaluatorComputationFailure,
        EvaluatorRegistration,
    )
    from crew_health.kernel.testing import MemoryEvaluation, MemoryEvaluator

    errors = {
        "computation": EvaluatorComputationFailure("bounded"),
        "storage": StorageUnavailable("disk_full"),
        "io": OSError("injected"),
        "auth": ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, "denied"),
        "config": ProblemError(ErrorCode.CONFIG_INVALID, "invalid"),
        "input": ProblemError(ErrorCode.INVALID_VALUE, "invalid"),
    }

    def evaluate(uow: UnitOfWork, value: EvaluationInput) -> None:
        raise errors[fault]

    isolation = MemoryEvaluation(
        settings=station.settings,
        registrations=[EvaluatorRegistration("evaluator", "v1", MemoryEvaluator(evaluate))],
        fault_codes=frozenset({"bounded"}),
        retry_job=lambda uow, pending: _evaluation_retry(uow, pending),
        audit_entry=lambda uow, pending: _evaluation_audit(station, uow, pending),
    )
    value = EvaluationInput(
        "evaluator",
        "v1",
        "input",
        TimeInput(station.clock.now(), "clock", "segment", 1, "hub_corrected", "LIVE"),
        "release",
        "partition",
        1,
        "completion",
    )
    if fault == "computation":
        with station.begin(actor=actor(), correlation_id="evaluation") as uow:
            assert isolation.run(uow, value) == "pending"
        assert station.state.schedules
        assert station.state.audit
        with station.snapshot() as snapshot:
            assert isolation.pending(snapshot, after_id=None, limit=1)[0].input == value
    else:
        with (
            pytest.raises(type(errors[fault])),
            station.begin(actor=actor(), correlation_id="evaluation") as uow,
        ):
            isolation.run(uow, value)
        assert not station.state.pending


def test_audit_independent_preimage_and_every_field_hash() -> None:
    import hashlib
    from dataclasses import replace

    from crew_health.kernel.audit import AuditEntry, AuditEnvelope, audit_bytes, audit_hash
    from crew_health.kernel.testing import MemoryAuditVerifier

    envelope = AuditEnvelope(
        "node",
        "inc",
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
        AuditEntry("event", "kind", 1, actor(), {"record": "ref"}, "correlation", None, {"ok": True}),
        "0" * 64,
    )
    expected = (
        '{"at":"2026-01-01T00:00:00.000000Z","audit_seq":1,"entry":{"actor":{"id":"owner","kind":"person"},'
        '"causation_id":null,"correlation_id":"correlation","event_id":"event","event_type":"kind","event_version":1,'
        '"refs":{"record":"ref"},"safe_metadata":{"ok":true}},"origin_incarnation":"inc","origin_node_id":"node","prev_hash":"'
        + "0" * 64
        + '"}'
    ).encode()
    assert audit_bytes(envelope) == expected
    assert audit_hash(envelope) == hashlib.sha256(b"crew-health.audit.v1\0" + expected).hexdigest()
    verifier = MemoryAuditVerifier((envelope,))
    assert verifier.verify(expected_head_seq=1, expected_head_hash=audit_hash(envelope)).ok
    assert not verifier.verify(expected_head_seq=2, expected_head_hash="0" * 64).ok
    assert audit_hash(replace(envelope, audit_seq=2)) != audit_hash(envelope)
    assert audit_hash(replace(envelope, entry=replace(envelope.entry, event_version=2))) != audit_hash(
        envelope
    )


def test_private_fake_denies_other_owner_and_revoked_old_blob(station: MemoryUowFactory) -> None:
    from dataclasses import replace

    from crew_health.kernel.private import PrivateIntent
    from crew_health.kernel.testing import MemoryPrivateStore, MemoryVault

    vault = MemoryVault(
        ids=station.ids,
        settings=station.settings,
        secrets={"owner": b"explicit-test-secret"},
        authorize=lambda s, r: s.person_id == r.owner_person_id,
    )
    store = MemoryPrivateStore(vault=vault, settings=station.settings)
    intent = PrivateIntent(identity(), "owner", "entry", "journal", "version", None, b"private canary")
    reference = store.seal(intent)
    assert store.seal(intent) == reference
    assert store.verify(reference)
    blob = store.entries["entry"][2]
    data = b'["owner","entry","version"]'
    assert vault.decrypt(session(), reference=reference, blob=blob, associated_data=data) == b"private canary"
    with pytest.raises(ProblemError):
        vault.decrypt(
            replace(session(), person_id="other"), reference=reference, blob=blob, associated_data=data
        )
    receipt = vault.revoke(owner_person_id="owner", entry_ids=("entry",), command_key="delete")
    assert vault.revoke(owner_person_id="owner", entry_ids=("entry",), command_key="delete") == receipt
    assert vault.revocation(receipt.revocation_id).complete
    with pytest.raises(ProblemError):
        vault.decrypt(session(), reference=reference, blob=blob, associated_data=data)
    assert not store.verify(reference)


def test_ownership_fences_only_named_resource(station: MemoryUowFactory) -> None:
    from crew_health.kernel.ownership import OwnedResource, Transfer
    from crew_health.kernel.testing import MemoryOwnership

    source = MemoryOwnership(
        node_id="node", incarnation="incarnation", settings=station.settings, validate=lambda *args: True
    )
    destination = MemoryOwnership(
        node_id="dest", incarnation="dest-inc", settings=station.settings, validate=lambda *args: True
    )
    first = OwnedResource("supply", "a", "node", "incarnation", 1, 1)
    second = OwnedResource("supply", "b", "node", "incarnation", 1, 1)
    transfer = Transfer("transfer", first, "dest", "dest-inc", 2, "snapshot", "0" * 64, identity())
    with station.begin(actor=actor(), correlation_id="ownership") as uow:
        assert isinstance(uow, MemoryUow)
        uow.state.owned[first.kind, first.resource_id] = first
        uow.state.owned[second.kind, second.resource_id] = second
        prepared = destination.prepare(uow, transfer)
        fenced = source.fence(uow, transfer, prepared)
        with pytest.raises(ProblemError):
            source.require(uow, first)
        source.require(uow, second)
        with pytest.raises(ProblemError):
            source.cancel(uow, transfer)
        accepted = destination.accept(uow, transfer, fenced)
        assert accepted.phase == "accepted"
        assert source.complete(uow, transfer, accepted).phase == "completed"


def test_json_canonical_vectors_and_depth_bound() -> None:
    from crew_health.kernel.commands import canonical_json

    assert (
        canonical_json({"n": [1.0, 1e-7, 1e20, 1e21, -0.0]})
        == b'{"n":[1,1e-7,100000000000000000000,1e+21,0]}'
    )
    assert canonical_json({"\ue000": 1, "\U0001f600": 2}) == '{"😀":2,"":1}'.encode()
    nested: list[object] = []
    for _ in range(66):
        nested = [nested]
    with pytest.raises(ValueError):
        canonical_json(nested)


def test_candidate_and_audit_maps_do_not_follow_mutation(station: MemoryUowFactory) -> None:
    from crew_health.kernel.audit import AuditEntry
    from crew_health.kernel.config import CandidateRelease

    documents = dict(memory_release(station).documents)
    candidate = CandidateRelease("candidate", documents)
    documents.clear()
    assert candidate.documents
    metadata = {"ok": True}
    refs = {"record": "reference"}
    entry = AuditEntry("audit", "event", 1, actor(), refs, "correlation", None, metadata)
    metadata["ok"] = False
    refs.clear()
    assert entry.safe_metadata["ok"] is True
    assert entry.refs["record"] == "reference"


def test_receipt_allocation_is_stable_before_record(station: MemoryUowFactory) -> None:
    from crew_health.kernel.testing import MemoryCommands

    commands = MemoryCommands(
        ids=station.ids, settings=station.settings, authorize=lambda *args: True, operations={}
    )
    with station.begin(actor=actor(), correlation_id="receipt") as uow:
        assert commands.accepted(uow, identity()) == commands.accepted(uow, identity())


def test_release_activation_revision_uses_transaction(station: MemoryUowFactory) -> None:
    from dataclasses import replace

    from crew_health.contracts_gen.enums import Role
    from crew_health.kernel.config import CandidateRelease, ReleaseRevision
    from crew_health.kernel.testing import MemoryLoaderChecks, MemoryReleaseActivation

    activation = MemoryReleaseActivation(
        checks=MemoryLoaderChecks(maximum=2),
        candidates={"release": CandidateRelease("release", memory_release(station).documents)},
        initial={"release": ReleaseRevision("release", 1, '"1"')},
    )
    operator = replace(session(), roles=frozenset({Role.OPERATOR}))
    with station.begin(actor=actor(), correlation_id="activation") as uow:
        value = activation.activate(uow, operator, release_id="release", expected_etag='"1"')
        assert value.revision == 2
    with station.snapshot() as view:
        assert activation.revision(view, "release").revision == 2


def test_task_retry_attempt_count_is_independent_of_generation(station: MemoryUowFactory) -> None:
    from datetime import timedelta

    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.tasks import TaskInput, TaskRegistration, TaskResult
    from crew_health.kernel.testing import MemoryTaskHandler, MemoryTasks
    from crew_health.kernel.uow import Audience, AudienceProjectionKey

    handler = MemoryTaskHandler(
        lambda lease: TaskResult(lease.task.task_id, lease.generation, "result", "hash"),
        lambda *args: None,
        lambda *args: None,
    )
    tasks = MemoryTasks(
        settings=station.settings,
        registrations=[TaskRegistration("handler", "v1", handler, None, None)],
        authorize=lambda *args: True,
        capture=lambda at: TimeInput(at, "clock", "segment", 1, "hub_corrected", "LIVE"),
        elapsed=lambda view, start, end: end.original_at - start.original_at,
    )
    audience = Audience("owner", "object", AudienceProjectionKey("owner", "task", 1), "policy")
    task = TaskInput(
        "retry", "handler", "v1", "input", "hash", "release", None, actor(), audience, identity(), None
    )
    with station.begin(actor=actor(), correlation_id="claim") as uow:
        tasks.submit(uow, task)
        tasks.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)
    memory_clock(station).at += timedelta(seconds=2)
    with station.begin(actor=actor(), correlation_id="recover") as uow:
        assert tasks.recover(uow, limit=1) == 1
        next_lease = tasks.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)[0]
        assert next_lease.generation == 3
    memory_clock(station).at += timedelta(seconds=2)
    with station.begin(actor=actor(), correlation_id="exhaust") as uow:
        tasks.recover(uow, limit=1)
        assert not tasks.claim(uow, worker_id="worker", handlers=frozenset({"handler"}), limit=1)


@given(st.decimals(min_value="-100000", max_value="100000", places=4, allow_nan=False, allow_infinity=False))
def test_unit_conversion_round_trip_property(value: Decimal) -> None:
    from crew_health.kernel.testing import MemoryUnits

    units = MemoryUnits(
        {"m": ("length", Decimal(1), Decimal(0)), "cm": ("length", Decimal("0.01"), Decimal(0))}
    )
    original = Quantity(value, units.parse("cm"))
    assert units.convert(units.convert(original, units.parse("m")), units.parse("cm")) == original


@given(st.integers(min_value=0, max_value=999), st.integers(min_value=0, max_value=86399))
def test_mission_time_half_open_property(day: int, seconds: int) -> None:
    from datetime import timedelta

    from crew_health.kernel.met import MissionClockVersion
    from crew_health.kernel.testing import MemoryMissionClock

    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    clock = MemoryMissionClock([MissionClockVersion("version", 1, epoch, epoch, None, mission_template())])
    at = epoch + timedelta(days=day, seconds=seconds)
    label = clock.met(at)
    first, last = clock.day_bounds(label.mission_day, version=label.clock_version)
    assert first <= at < last
    assert label.mission_day == day + 1


def test_savepoint_discards_buffered_nested_domain_events(station: MemoryUowFactory) -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Event:
        value: int
        event_type: ClassVar[str] = "test"

    observed = []

    def handle(uow: UnitOfWork, event: Event) -> None:
        if event.value == 0:
            with pytest.raises(ValueError), uow.savepoint("event-savepoint"):
                uow.publish(Event(1))
                raise ValueError("evaluation failure")
        else:
            observed.append(event.value)

    station.bus.subscribe(Event, handle, name="handler")
    with station.begin(actor=actor(), correlation_id="buffered") as uow:
        uow.publish(Event(0))
    assert observed == []


def test_private_coordinator_retries_and_rejects_publication(station: MemoryUowFactory) -> None:
    from dataclasses import replace

    from crew_health.kernel.commands import canonical_json
    from crew_health.kernel.private import PrivateIntent, PrivatePublicationRejected
    from crew_health.kernel.testing import (
        MemoryCommands,
        MemoryPrivateCoordinator,
        MemoryPrivateStore,
        MemoryPrivateWriter,
        MemoryVault,
    )

    vault = MemoryVault(
        ids=station.ids,
        settings=station.settings,
        secrets={"owner": b"test-only-secret"},
        authorize=lambda s, r: s.person_id == r.owner_person_id,
    )
    store = MemoryPrivateStore(vault=vault, settings=station.settings)
    commands = MemoryCommands(
        ids=station.ids, settings=station.settings, authorize=lambda *args: True, operations={}
    )

    def canonical(intent: PrivateIntent) -> bytes:
        return canonical_json(
            [
                intent.identity.operation,
                intent.identity.target,
                intent.identity.parameters_sha256,
                intent.plaintext.decode(),
            ]
        )

    coordinator = MemoryPrivateCoordinator(
        factory=station,
        store=store,
        vault=vault,
        writers={"journal": MemoryPrivateWriter()},
        commands=commands,
        authorize=lambda s, owner: s.person_id == owner,
        identity_bytes=canonical,
    )
    intent = PrivateIntent(identity(), "owner", "entry", "journal", "version", None, b"private canary")
    fingerprint = vault.fingerprint(owner_person_id="owner", canonical_identity_and_body=canonical(intent))
    intent = replace(
        intent,
        identity=replace(intent.identity, fingerprint_kind="hmac_sha256", body_fingerprint=fingerprint),
    )
    receipt = coordinator.write(session(), intent)
    assert coordinator.write(session(), intent) == receipt
    assert station.state.references["entry"].entry_id == "entry"
    assert b"private canary" not in repr(station.state).encode()
    with coordinator.backup_barrier(), pytest.raises(ProblemError):
        coordinator.write(session(), intent)
    rejected = replace(
        intent, entry_id="rejected", version_id="second", identity=replace(intent.identity, key="second")
    )
    reference = store.seal(rejected)
    with pytest.raises(PrivatePublicationRejected):
        coordinator.publish(session(), rejected, reference, lambda uow: False)
    assert "rejected" not in station.state.references
    assert coordinator.recover(limit=4) == 1
    assert vault.is_revoked(entry_id="rejected")
    with pytest.raises(ProblemError):
        coordinator.write(session(), replace(intent, identity=identity()))


def test_replica_control_lane_and_duplicate_conflict(station: MemoryUowFactory) -> None:
    from dataclasses import replace

    from crew_health.kernel.replication import (
        Delivery,
        ProjectionBody,
        ProjectionKey,
        Recipient,
        RevocationControl,
    )
    from crew_health.kernel.testing import MemoryReplicaExchange

    recipient = Recipient("recipient", "purpose", 1, frozenset(), ("grant",))
    delivery = Delivery(
        "stream",
        1,
        1,
        "recipient",
        "purpose",
        "node",
        "inc",
        "opaque",
        ProjectionBody(ProjectionKey("owner", "projection", 1), "object", ("/field",), b'{"field":1}', ()),
        "hash",
    )
    applied = []
    exchange = MemoryReplicaExchange(
        settings=station.settings,
        ids=station.ids,
        materializer=lambda *args: (),
        validate=lambda d, r: d.recipient_id == r.recipient_id and d.policy_epoch == r.policy_epoch,
        applier=lambda *args: applied.append("applied"),
        control=lambda r, stream, control: RevocationControl(
            control,
            r.recipient_id,
            stream,
            r.policy_epoch,
            r.policy_epoch + 1,
            "authority",
            b"verified-test-control",
        ),
        verify_control=lambda c: c.signature == b"verified-test-control",
    )
    with station.begin(actor=actor(), correlation_id="replica") as uow:
        assert exchange.apply(uow, recipient, delivery) == 1
        assert exchange.apply(uow, recipient, delivery) == 1
        with pytest.raises(ProblemError):
            exchange.apply(uow, recipient, replace(delivery, envelope_sha256="conflict"))
        assert exchange.materialize(uow, recipient, limit=1) == 0
        control = exchange.revoke(uow, recipient, stream_id="stream")
        assert exchange.apply_revocation(uow, control) == control.control_id
        exchange.acknowledge_revocation(uow, control_id=control.control_id, recipient_id="recipient")
        with pytest.raises(ProblemError):
            exchange.apply(uow, recipient, delivery)
    assert applied == ["applied"]
    with station.snapshot() as view, pytest.raises(ProblemError):
        exchange.page(view, recipient, stream_id="stream", after=0, limit=1)


def test_checks_templates_health_and_explicit_grants(station: MemoryUowFactory) -> None:
    from crew_health.kernel.config import CandidateRelease
    from crew_health.kernel.testing import (
        MemoryActivationGuard,
        MemoryAudienceRule,
        MemoryHealth,
        MemoryLoaderChecks,
        MemoryReleaseCheck,
        MemoryTemplateRenderer,
    )
    from crew_health.kernel.uow import Audience, AudienceProjectionKey

    checks = MemoryLoaderChecks(maximum=2)
    check = MemoryReleaseCheck("check", lambda candidate: ())
    guard = MemoryActivationGuard("guard", lambda uow, candidate: ())
    checks.register(check)
    checks.register_guard(guard)
    with pytest.raises(ProblemError):
        checks.register(check)
    candidate = CandidateRelease("release", memory_release(station).documents)
    assert check.check(candidate) == ()
    with station.begin(actor=actor(), correlation_id="guard") as uow:
        assert guard.check(uow, candidate) == ()
    renderer = MemoryTemplateRenderer(unknown="unknown", allowed={"field": frozenset({"value"})})
    assert renderer.render("field", "{value}", {}) == "unknown"
    with pytest.raises(ProblemError):
        renderer.render("missing", "{value}", {})
    health = MemoryHealth(maximum=2)
    health.mark_degraded("component", "code", station.clock.now())
    assert health.snapshot().status == "degraded"
    health.mark_ok("component")
    assert health.snapshot().status == "ok"
    audience = Audience("owner", "record", AudienceProjectionKey("owner", "event", 1), "policy")
    with station.snapshot() as view:
        assert not MemoryAudienceRule(frozenset()).admits(view, session(), audience)
        assert MemoryAudienceRule(frozenset({("session", "owner", "record", "policy")})).admits(
            view, session(), audience
        )


def _evaluation_retry(uow: UnitOfWork, pending: PendingEvaluation) -> ScheduledJob:
    from datetime import timedelta

    from crew_health.kernel.clock import TimeInput
    from crew_health.kernel.scheduler import ElapsedDeadline, ScheduledJob

    anchor = TimeInput(uow.now, "clock", "segment", 1, "hub_corrected", "LIVE")
    return ScheduledJob(
        "retry_" + pending.input.completion_id,
        "test-evaluator",
        ElapsedDeadline(anchor, timedelta(seconds=1)),
        {"completion_id": pending.input.completion_id},
        None,
    )


def _evaluation_audit(station: MemoryUowFactory, uow: UnitOfWork, pending: PendingEvaluation) -> AuditEntry:
    from crew_health.kernel.audit import AuditEntry

    return AuditEntry(
        station.ids.new("audit"),
        "test-evaluation",
        1,
        uow.actor,
        {"completion_id": pending.input.completion_id},
        uow.correlation_id,
        None,
        {"fault_code": pending.fault_code},
    )


def ui_document() -> bytes:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "contracts/api/examples/config/ui-copy.example.json"
    value = json.loads(path.read_bytes())["value"]
    validate_ui(value)
    return json.dumps(value, sort_keys=True).encode()


def validate_ui(value: object) -> None:
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schemas = Path(__file__).resolve().parents[3] / "contracts/api/schemas"
    registry = Registry().with_resources(
        (p.name, Resource.from_contents(json.loads(p.read_bytes()))) for p in sorted(schemas.glob("*.json"))
    )
    Draft202012Validator(
        json.loads((schemas / "config-ui-copy.schema.json").read_bytes()), registry=registry
    ).validate(value)


def mission_template() -> str:
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "contracts/api/examples/config/environment-profiles.example.json"
    )
    value = json.loads(path.read_bytes())["value"]["profiles"][0]["day_label"]["short_template"]
    assert isinstance(value, str)
    return value


def test_invalid_derived_unicode_is_a_typed_input_failure() -> None:
    with pytest.raises(ProblemError):
        MemoryIds(identifiers=()).derived("prefix", "\ud800")


def test_task_status_enforces_dimensionless_progress(station: MemoryUowFactory) -> None:
    from crew_health.kernel.tasks import TaskStatus

    for progress in [Quantity(Decimal("1.1"), Unit("1")), Quantity(Decimal("0.5"), Unit("m"))]:
        with pytest.raises(ProblemError):
            TaskStatus(
                "task", None, None, "queued", progress, None, None, station.clock.now(), station.clock.now()
            )


def test_settings_reject_nonfinite_descriptors_as_configuration_errors() -> None:
    from crew_health.kernel.settings import SettingSpec
    from crew_health.kernel.testing import MemorySettings

    with pytest.raises(ProblemError):
        MemorySettings(
            [
                SettingSpec(
                    "storage.max_rows",
                    "integer",
                    None,
                    Decimal(1),
                    Decimal("NaN"),
                    None,
                    "SYSTEM 5.11 test invalid descriptor",
                )
            ],
            {"storage.max_rows": 1},
        )


def test_canonical_json_does_not_depend_on_decimal_context() -> None:
    from decimal import localcontext

    from crew_health.kernel.commands import canonical_json

    with localcontext() as context:
        context.prec = 2
        assert canonical_json(1.234567e-9) == b"1.234567e-9"


def memory_release(station: MemoryUowFactory) -> MemoryRelease:
    release = station.release
    assert isinstance(release, MemoryRelease)
    return release


def memory_clock(station: MemoryUowFactory) -> MemoryClock:
    clock = station.clock
    assert isinstance(clock, MemoryClock)
    return clock


def apply_operator(name: str, target: object, key: object, value: object) -> None:
    operation: Callable[[object, object, object], None] = getattr(operator, name)
    operation(target, key, value)
