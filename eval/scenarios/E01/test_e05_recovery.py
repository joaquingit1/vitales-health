"""T-E01-19: E05 commands on E01 SQLite, with explicit pending-seam port doubles.

The command ledger is E01's production CommandLedger (E05 keeps its revision reference there). The records
append port is a transactional double: records' producer fails closed until a producer carries the mission
partition and proof a record append needs. This tests caller-owned transaction atomicity, not clinical
acceptance or physical worker dispatch. Manual linking and actual worker reconciliation remain deferred.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

# isort: split
from crew_health.contracts_gen.enums import Role
from crew_health.contracts_gen.schemas.measurement_request_schema import MeasurementRequest as WireRequest
from crew_health.contracts_gen.schemas.observation_schema import Observation as WireObservation
from crew_health.events.adapters.repository import EventRepository, EventValidator
from crew_health.kernel.commands import (
    AcceptedReceipt,
    CommandIdentity,
    command_identity,
)
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.testing import MemoryUnits
from crew_health.kernel.units import Unit
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
)
from crew_health.measurement.adapters.repository import SqliteMeasurementStore
from crew_health.measurement.application.commands import (
    AttemptInput,
    Limits,
    MeasurementService,
    ObservationInput,
    Platform,
    Providers,
)
from crew_health.measurement.application.ports import CapabilityView
from crew_health.measurement.application.queries import CapabilityService, MeasurementQueries
from crew_health.measurement.application.store import AcquisitionBinding, Transition
from crew_health.measurement.domain.model import KitCheck, MeasurementRequest, Requester
from crew_health.platform.adapters import codec
from crew_health.platform.adapters.ledger import CommandLedger
from crew_health.platform.domain.audit import AuditPolicy
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.records.application.ports import AttemptObservationDraft, ObservationDraft

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-19", "R25", "V35")]

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "contracts/api/examples"


def declaration(kind: str, reference: str) -> ReplicationChange:
    return ReplicationChange(
        None,
        kind,
        reference,
        "append",
        b"{}",
        (),
        "release_1",
        Audience("test", reference, AudienceProjectionKey("tests", "recovery", 1), "1"),
    )


class RevisionReceiptPort(CommandLedger):
    """The production E01 CommandLedger, which keeps E05's revision reference durably, with one injected
    fault: the receipt allocation can fail before anything is recorded."""

    def __init__(self, db: SQLiteDatabase) -> None:
        super().__init__(db.ids, settings=db.settings)
        self.fail_before_receipt = False

    def accepted(self, uow: UnitOfWork, identity: CommandIdentity) -> AcceptedReceipt:
        if self.fail_before_receipt:
            raise StorageUnavailable("io_error")
        return super().accepted(uow, identity)


class TransactionalObservationPort:
    """Records boundary double: a durable opaque value with its supplied lineage."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        self.calls = 0
        self.fail_after_append = False

    def append_for_attempt(self, uow: UnitOfWork, draft: AttemptObservationDraft) -> str:
        self.calls += 1
        observation_id = self.db.ids.new("obs")
        uow.connection.execute(
            "INSERT INTO recovery_observation VALUES (?, ?, ?, ?)",
            (
                observation_id,
                draft.measurement_request_id,
                draft.attempt_id,
                codec(AttemptObservationDraft).dump_json(draft),
            ),
        )
        for kind, reference in (
            ("observation", observation_id),
            ("measurement_attempt", draft.attempt_id),
        ):
            uow.replicate(declaration(kind, reference))
        if self.fail_after_append:
            raise StorageUnavailable("io_error")
        return observation_id

    def canonical_unit(self, code: str) -> Unit:
        raise NotImplementedError("not a manual-entry test")

    def append_observation(self, uow: UnitOfWork, draft: ObservationDraft, *, idempotency_key: str) -> str:
        raise NotImplementedError("manual linking awaits R-E05-2")


@dataclass
class Station:
    db: SQLiteDatabase
    service: MeasurementService
    store: SqliteMeasurementStore
    records: TransactionalObservationPort
    ledger: RevisionReceiptPort
    session: SessionContext
    owner: SessionContext
    request_id: str
    attempt: AttemptInput
    identity: CommandIdentity
    queries: MeasurementQueries

    def record(self) -> None:
        result = self.service.record_attempt(
            self.session,
            self.identity,
            self.request_id,
            self.attempt,
            correlation_id="attempt_command",
        )
        assert result.status == 200
        assert result.body["state"] == "completed"


def prepare_database(tmp_path: Path) -> SQLiteDatabase:
    db = database(tmp_path)
    db.migrate(
        (ROOT / "hub/src/crew_health/measurement/adapters/migrations/0001_measurement.sql").read_text(
            encoding="utf-8"
        )
    )
    db.migrate(
        (ROOT / "hub/src/crew_health/events/adapters/migrations/0001_events.sql").read_text(encoding="utf-8")
    )
    db.migrate(
        "CREATE TABLE recovery_observation(id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
        "attempt_id TEXT NOT NULL, draft BLOB NOT NULL) STRICT;"
    )
    return db


def running_request(wire: WireRequest, observation: WireObservation) -> MeasurementRequest:
    kit = wire.kit_check
    return MeasurementRequest(
        wire.request_id.root,
        4,
        observation.episode_id.root if observation.episode_id else None,
        wire.capability_code.root,
        wire.reason,
        wire.decision_affected,
        KitCheck(
            kit.can,
            tuple(i.root for i in kit.instrument_ids),
            tuple(i.root for i in kit.missing_instrument_ids),
            tuple(i.root for i in kit.condition_ids),
            tuple(str(i) for i in kit.cliff_urls),
            kit.text,
            kit.kit_map_version.root,
            kit.environment_id.root,
        ),
        Requester("person", observation.patient_id.root, None),
        observation.patient_id.root,
        observation.patient_id.root,
        None,
        "in_progress",
        (),
        (),
        wire.body_region.root.root if wire.body_region and wire.body_region.root else None,
        wire.created_at.root,
        "SIM",
        "mission_1",
        "release_1",
        "self",
        (observation.acquisition_method.root,),
        1,
        None,
        observation.patient_id.root,
    )


def seed_running_request(
    db: SQLiteDatabase,
    store: SqliteMeasurementStore,
    request: MeasurementRequest,
    owner: SessionContext,
    adapter: Actor,
    observation: WireObservation,
) -> None:
    with db.begin(actor=owner.actor, correlation_id="setup") as uow:
        store.insert_request(uow.connection, request)
        store.insert_transition(
            uow.connection,
            Transition(request.request_id, 4, owner.actor, "accepted", "in_progress", "start", None, uow.now),
            request,
        )
        store.start_acquisition(
            uow.connection,
            AcquisitionBinding(
                request.request_id,
                1,
                request.patient_id,
                adapter.id,
                observation.acquisition_method.root,
                None if observation.device_id is None else observation.device_id.root,
                False,
            ),
            at=uow.now,
        )
        uow.replicate(declaration("measurement_request", request.request_id))
    db.register_audit_policy(
        AuditPolicy(
            "measurement.request_attempt_usable",
            {"state": frozenset({"completed"}), "revision": frozenset({5})},
            frozenset({"request_id", "attempt_id"}),
            {
                "request_id": frozenset({"measurement_request"}),
                "attempt_id": frozenset({"measurement_attempt"}),
            },
        )
    )
    events = EventRepository(
        settings=db.settings,
        ids=db.ids,
        validator=EventValidator(schema_directory=ROOT / "contracts/api/schemas"),
    )
    db.event_sink = events.append


def attempt_input(
    wire: WireRequest, observation: WireObservation, request: MeasurementRequest
) -> AttemptInput:
    return AttemptInput(
        1,
        wire.attempts[1].started_at.root,
        wire.attempts[1].ended_at.root if wire.attempts[1].ended_at else wire.attempts[1].started_at.root,
        "success",
        None,
        (
            ObservationInput(
                observation.patient_id.root,
                request.episode_id,
                observation.code.root,
                Decimal(str(observation.value)),
                observation.unit,
                observation.observed_at.root,
                "SIM",
                observation.acquisition_method.root,
                None if observation.device_id is None else observation.device_id.root,
                tuple((k, str(v)) for k, v in observation.context.items()),
                observation.body_region.root.root
                if observation.body_region and observation.body_region.root
                else None,
                None,
                request.request_id,
            ),
        ),
        (),
        "opaque_test_fingerprint",
    )


class SingleValueCapabilities(CapabilityService):
    """No kit or mission provider is composed here. Since E07, `record_attempt` looks up the capability for
    its panel components; pulse is a single value with none, which is what this lookup reports."""

    def get(self, uow: object, code: str) -> CapabilityView | None:
        return None


def station(tmp_path: Path) -> Station:
    db = prepare_database(tmp_path)
    wire = WireRequest.model_validate(
        json.loads((EXAMPLES / "clinical/measurement-request-pulse.json").read_bytes())["value"]
    )
    observation = WireObservation.model_validate(
        json.loads((EXAMPLES / "clinical/observation-pulse.json").read_bytes())["value"]
    )
    request = running_request(wire, observation)
    store = SqliteMeasurementStore(max_attempts=4, max_late_attempts=4)
    caps = SingleValueCapabilities(kit_capabilities=None, mission=None, readiness=None, max_capabilities=1)
    records, ledger = TransactionalObservationPort(db), RevisionReceiptPort(db)
    service = MeasurementService(
        platform=Platform(db, db.ids, ledger, ledger),
        store=store,
        capabilities=caps,
        providers=Providers(
            None,
            None,
            None,
            None,
            None,
            None,
            records,
            None,
            MemoryUnits({observation.unit: ("frequency", Decimal(1), Decimal(0))}),
        ),
        limits=Limits(4, 4, 32, 2048, 16, 16, 600000),
    )
    owner = SessionContext(
        "owner_session",
        "crew",
        Actor("person", request.patient_id),
        request.patient_id,
        frozenset({Role.CREW}),
    )
    adapter = Actor("adapter", "adp_camera")
    session = SessionContext("adapter_session", "adapter", adapter, None, frozenset())
    seed_running_request(db, store, request, owner, adapter, observation)
    identity = command_identity(
        key="attempt_command",
        actor_ref=adapter.id,
        operation="recordMeasurementAttempt",
        target=f"/v1/measurement-requests/{request.request_id}/attempts",
        parameters_json=b"{}",
        body_fingerprint="0" * 64,
        fingerprint_kind="hmac_sha256",
    )
    queries = MeasurementQueries(snapshots=db, store=store, capabilities=caps, consent=None, page_size=50)
    return Station(
        db,
        service,
        store,
        records,
        ledger,
        session,
        owner,
        request.request_id,
        attempt_input(wire, observation, request),
        identity,
        queries,
    )


def counts(db: SQLiteDatabase) -> tuple[int, ...]:
    with db.snapshot() as view:
        result = []
        for table in (
            "recovery_observation",
            "measurement_attempt",
            "platform_command_ledger",
            "platform_audit",
            "events_outbox",
            "measurement_acquisition",
        ):
            ((value,),) = view.connection.rows("SELECT count(*) FROM " + table, (), limit=1)
            assert isinstance(value, int)
            result.append(value)
        return tuple(result)


def assert_linked_once(s: Station) -> None:
    refreshed = s.queries.get_request(s.owner, s.request_id)
    assert refreshed.body["state"] == "completed"
    assert refreshed.revision == 5
    with s.db.snapshot() as view:
        request = s.store.get_request(view.connection, s.request_id)
        assert request is not None
        assert len(request.attempts) == 1
        (attempt,) = request.attempts
        ((observation_id, request_id, attempt_id, raw),) = view.connection.rows(
            "SELECT * FROM recovery_observation", (), limit=1
        )
        assert isinstance(raw, bytes)
        draft = codec(AttemptObservationDraft).validate_json(raw)
        assert (request_id, attempt_id) == (s.request_id, attempt.attempt_id)
        assert (draft.measurement_request_id, draft.attempt_id) == (request_id, attempt_id)
        assert request.result_observation_ids == attempt.observation_ids == (observation_id,)
        binding = s.store.acquisition(view.connection, s.request_id, 1)
        assert binding is not None
        assert binding.fenced
    assert counts(s.db) == (1, 1, 1, 1, 1, 1)


@pytest.mark.parametrize(
    "phase", ["after_observation", "after_attempt", "after_request_update", "before_receipt"]
)
def test_crash_in_attempt_uow_rolls_back_value_and_link_then_same_key_completes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    s = station(tmp_path)
    before = counts(s.db)
    with monkeypatch.context() as fault:
        if phase == "after_observation":
            fault.setattr(s.records, "fail_after_append", True)
        elif phase == "before_receipt":
            fault.setattr(s.ledger, "fail_before_receipt", True)
        else:
            method = "insert_attempt" if phase == "after_attempt" else "update_request"
            original: Callable[..., None] = getattr(s.store, method)

            def crash(*args: object, **kwargs: object) -> None:
                original(*args, **kwargs)
                raise StorageUnavailable("io_error")

            fault.setattr(s.store, method, crash)
        with pytest.raises(StorageUnavailable):
            s.record()
    assert counts(s.db) == before
    refresh = s.queries.get_request(s.owner, s.request_id)
    assert refresh.body["state"] == "in_progress"
    assert refresh.body["attempts"] == []
    with s.db.snapshot() as view:
        binding = s.store.acquisition(view.connection, s.request_id, 1)
        assert binding is not None
        assert not binding.fenced
    s.record()
    assert_linked_once(s)
    calls = s.records.calls
    s.record()
    assert s.records.calls == calls
    assert_linked_once(s)


def test_committed_attempt_is_found_on_refresh_and_same_key_retry_does_not_acquire_twice(
    tmp_path: Path,
) -> None:
    s = station(tmp_path)
    s.record()
    assert s.records.calls == 1
    assert_linked_once(s)
    s.record()
    assert s.records.calls == 1
    assert_linked_once(s)
