"""ObservationCommands for in-process producers (E30 teach, E05 measurement): canonical units from the active
release, unit check, partition, idempotency in the caller's unit of work, audit and fail closed paths."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import TypedDict, Unpack

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.kernel.config import ConfigDocumentView, ReleaseHandle
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.testing import MemoryRelease
from crew_health.kernel.units import Quantity, Unit, WireQuantity
from crew_health.kernel.uow import Actor
from crew_health.platform.adapters.units import UcumUnitRegistry
from crew_health.platform.storage import SQLiteDatabase
from crew_health.platform.testing.sqlite import database
from crew_health.records.adapters.repository import RecordRepository
from crew_health.records.application.ports import AttemptObservationDraft, ObservationDraft
from crew_health.records.application.producers import ObservationProducer
from crew_health.records.domain.observations import ObservationPolicy

pytestmark = [pytest.mark.E01, pytest.mark.trace("T-E01-05", "T-E01-06", "R01", "R06")]

ROOT = Path(__file__).resolve().parents[3]
OBSERVED = datetime(2026, 11, 13, 9, 30, tzinfo=UTC)


def _document(name: ConfigName, raw: bytes) -> ConfigDocumentView:
    return ConfigDocumentView(name, hashlib.sha256(raw).hexdigest(), raw)


def _documents() -> dict[ConfigName, ConfigDocumentView]:
    mapping = (ROOT / "config/unit-mapping.json").read_bytes()
    example = json.loads(
        (ROOT / "contracts/api/examples/config/sensor-catalog.example.json").read_text(encoding="utf-8")
    )
    catalog = json.dumps(example["value"]).encode("utf-8")
    return {
        ConfigName.UNIT_MAPPING: _document(ConfigName.UNIT_MAPPING, mapping),
        ConfigName.SENSOR_CATALOG: _document(ConfigName.SENSOR_CATALOG, catalog),
    }


def _mapping() -> dict[str, object]:
    value: dict[str, object] = json.loads((ROOT / "config/unit-mapping.json").read_text(encoding="utf-8"))
    return value


def _entry(mapping: dict[str, object], code: str, wire: str) -> dict[str, object]:
    entries = mapping["mappings"]
    assert isinstance(entries, list)
    return next(e for e in entries if e["indicator_code"] == code and e["wire_unit"] == wire)


@dataclass(frozen=True)
class Facts:
    mission_id: str


class Mission:
    def __init__(self, mission_id: str | None = "mission_1") -> None:
        self.mission_id = mission_id

    def current(self, uow: object = None) -> Facts | None:
        return None if self.mission_id is None else Facts(self.mission_id)


class Releases:
    def __init__(self, release: ReleaseHandle) -> None:
        self.handle = release

    def active(self) -> ReleaseHandle:
        return self.handle

    def release(self, release_id: str) -> ReleaseHandle:
        return self.handle


def station(
    tmp_path: Path,
    *,
    mission: Mission | None = None,
    documents: dict[ConfigName, ConfigDocumentView] | None = None,
) -> tuple[SQLiteDatabase, ObservationProducer]:
    db = database(tmp_path)
    for migration in sorted((ROOT / "hub/src/crew_health/records/adapters/migrations").glob("*_records.sql")):
        db.migrate(migration.read_text(encoding="utf-8"))
    release = MemoryRelease("release_1", _documents() if documents is None else documents)
    db.set_release_reader(lambda view: release)
    producer = ObservationProducer(
        repository=RecordRepository(),
        ids=db.ids,
        units=UcumUnitRegistry(max_cached=64),
        releases=Releases(release),
        mission=Mission() if mission is None else mission,
    )
    return db, producer


class PalpationChanges(TypedDict, total=False):
    value: Quantity
    patient_id: str
    entered_at: datetime


class AttemptChanges(TypedDict, total=False):
    observed_at: datetime
    quantity: Quantity
    operator_id: str | None
    acquisition_method: str


def palpation(**updates: Unpack[PalpationChanges]) -> ObservationDraft:
    pulse = _entry(_mapping(), "pulse", "bpm")
    draft = ObservationDraft(
        patient_id="person_a",
        operator_id="person_b",
        episode_id=None,
        code="pulse",
        value=Quantity(Decimal(72), Unit(str(pulse["ucum_unit"]))),
        observed_at=OBSERVED,
        entered_at=OBSERVED + timedelta(seconds=5),
        origin_mode="SIM",
        acquisition_method="crewmate_palpation",
        quality_status="accepted",
        quality_rule_version="teach_rule_1",
        body_region="wrist",
        context={"guided_check_id": "gc_1", "count_seconds": "30"},
    )
    return replace(draft, **updates)


def attempt(**updates: Unpack[AttemptChanges]) -> AttemptObservationDraft:
    pulse = _entry(_mapping(), "pulse", "bpm")
    draft = AttemptObservationDraft(
        patient_id="person_a",
        episode_id=None,
        code="pulse",
        quantity=Quantity(Decimal(64), Unit(str(pulse["ucum_unit"]))),
        observed_at=OBSERVED,
        origin_mode="LIVE",
        acquisition_method="device",
        device_id="oximeter_1",
        context={},
        body_region=None,
        supersedes=None,
        measurement_request_id="mrq_1",
        attempt_id="att_1",
        operator_id="person_a",
    )
    return replace(draft, **updates)


def counts(db: SQLiteDatabase) -> tuple[int, int, int]:
    with db.snapshot() as view:
        rows = view.connection.rows
        observations, changes, audits = (
            rows("SELECT count(*) FROM " + table, (), limit=1)[0][0]
            for table in ("records_observation", "repl_log", "platform_audit")
        )
    assert isinstance(observations, int) and isinstance(changes, int) and isinstance(audits, int)
    return observations, changes, audits


def append(
    db: SQLiteDatabase, producer: ObservationProducer, draft: ObservationDraft, key: str = "gc_1:1:obs"
) -> str:
    with db.begin(actor=Actor("person", "person_b"), correlation_id="cor_1") as uow:
        return producer.append_observation(uow, draft, idempotency_key=key)


def test_canonical_unit_comes_from_the_active_release(tmp_path: Path) -> None:
    _, producer = station(tmp_path)
    mapping = _mapping()
    assert producer.canonical_unit("pulse") == Unit(str(_entry(mapping, "pulse", "bpm")["ucum_unit"]))
    # co2 maps two wire units; the sensor catalog's unit picks the canonical one.
    assert producer.canonical_unit("co2") == Unit(str(_entry(mapping, "co2", "mmHg")["ucum_unit"]))
    with pytest.raises(ProblemError) as unknown:
        producer.canonical_unit("pulse_rate")
    assert unknown.value.code == ErrorCode.INVALID_UNIT


def test_a_configuration_edit_changes_the_canonical_unit(tmp_path: Path) -> None:
    mapping = _mapping()
    entry = _entry(mapping, "pulse", "bpm")
    entry["ucum_unit"] = "/h"
    raw = json.dumps(mapping).encode("utf-8")
    documents = {**_documents(), ConfigName.UNIT_MAPPING: _document(ConfigName.UNIT_MAPPING, raw)}
    _, producer = station(tmp_path, documents=documents)
    assert producer.canonical_unit("pulse") == Unit("/h")


def test_missing_or_ambiguous_release_documents_fail_closed(tmp_path: Path) -> None:
    full = _documents()
    _, no_mapping = station(
        tmp_path / "a", documents={ConfigName.SENSOR_CATALOG: full[ConfigName.SENSOR_CATALOG]}
    )
    with pytest.raises(ProblemError) as missing:
        no_mapping.canonical_unit("pulse")
    assert missing.value.code == ErrorCode.CONFIG_INVALID
    _, no_catalog = station(
        tmp_path / "b", documents={ConfigName.UNIT_MAPPING: full[ConfigName.UNIT_MAPPING]}
    )
    assert no_catalog.canonical_unit("pulse") == Unit("/min")
    with pytest.raises(ProblemError) as ambiguous:
        no_catalog.canonical_unit("co2")
    assert ambiguous.value.code == ErrorCode.CONFIG_INVALID


def test_append_persists_one_row_one_replication_change_and_one_audit_entry(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    before = counts(db)
    observation_id = append(db, producer, palpation())
    assert counts(db) == (before[0] + 1, before[1] + 1, before[2] + 1)
    with db.snapshot() as view:
        stored = RecordRepository().observation(view, observation_id)
        audit = view.connection.rows("SELECT * FROM platform_audit", (), limit=10)
    assert stored is not None
    assert (stored.patient_id, stored.mission_id, stored.origin_mode, stored.code) == (
        "person_a",
        "mission_1",
        "SIM",
        "pulse",
    )
    assert stored.quantity == Quantity(Decimal(72), Unit("/min"))
    assert stored.observed.original_at == OBSERVED and stored.normalized.state == "unknown"
    # No proven clock segment: never stored accepted, whatever the producer declared.
    assert (stored.quality_status, stored.quality_reasons) == ("uncertain", ("time_unknown",))
    assert stored.quality_rule_version == "teach_rule_1"
    assert sum("observation.recorded" in repr(row) and observation_id in repr(row) for row in audit) == 1


def test_replay_with_the_same_key_returns_the_same_id_and_writes_nothing(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    first = append(db, producer, palpation())
    after_first = counts(db)
    assert append(db, producer, palpation()) == first
    assert counts(db) == after_first


def test_same_key_with_different_content_is_a_duplicate_command(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    append(db, producer, palpation())
    after_first = counts(db)
    for changed in (
        palpation(value=Quantity(Decimal(73), Unit("/min"))),
        palpation(patient_id="person_c"),
        palpation(entered_at=OBSERVED + timedelta(seconds=6)),
    ):
        with pytest.raises(ProblemError) as conflict:
            append(db, producer, changed)
        assert conflict.value.code == ErrorCode.DUPLICATE_COMMAND
    assert counts(db) == after_first


def test_wrong_unit_is_rejected_and_nothing_is_written(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    before = counts(db)
    for unit in ("mm[Hg]", "bpm"):
        with pytest.raises(ProblemError) as refused:
            append(
                db, producer, palpation(value=Quantity(Decimal(72), Unit(unit))), key="gc_" + str(len(unit))
            )
        assert refused.value.code == ErrorCode.INVALID_UNIT
    assert counts(db) == before


def test_convertible_unit_is_stored_canonical_with_the_original_kept(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    observation_id = append(db, producer, palpation(value=Quantity(Decimal(4320), Unit("/h"))))
    with db.snapshot() as view:
        stored = RecordRepository().observation(view, observation_id)
    assert stored is not None
    assert stored.quantity == Quantity(Decimal(72), Unit("/min"))
    assert stored.conversion.rounding == "half_even:4"
    assert stored.conversion.original == WireQuantity(Decimal(4320), "/h")
    # The replay compares the original the producer sent, not the converted value.
    assert append(db, producer, palpation(value=Quantity(Decimal(4320), Unit("/h")))) == observation_id


def test_append_for_attempt_is_idempotent_on_the_attempt(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    actor = Actor("adapter", "adapter_1")
    with db.begin(actor=actor, correlation_id="cor_2") as uow:
        first = producer.append_for_attempt(uow, attempt())
        assert producer.append_for_attempt(uow, attempt()) == first
        later = producer.append_for_attempt(uow, attempt(observed_at=OBSERVED + timedelta(seconds=1)))
    assert later != first
    with db.snapshot() as view:
        stored = RecordRepository().observation(view, first)
    assert stored is not None
    assert (stored.measurement_request_id, stored.proof_ref, stored.care_basis_ref) == (
        "mrq_1",
        "att_1",
        "mrq_1",
    )
    assert (stored.quality_status, stored.quality_reasons) == ("not_assessed", ("no_policy", "time_unknown"))
    after = counts(db)
    with db.begin(actor=actor, correlation_id="cor_3") as uow:
        assert producer.append_for_attempt(uow, attempt()) == first
        with pytest.raises(ProblemError) as conflict:
            producer.append_for_attempt(uow, attempt(quantity=Quantity(Decimal(65), Unit("/min"))))
    assert conflict.value.code == ErrorCode.DUPLICATE_COMMAND
    assert counts(db) == after


def test_attempt_without_operator_or_with_unknown_method_fails_closed(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    before = counts(db)
    for draft, code in (
        (attempt(operator_id=None), ErrorCode.IDENTITY_MISMATCH),
        (attempt(acquisition_method="guess"), ErrorCode.INVALID_VALUE),
    ):
        with pytest.raises(ProblemError) as refused:
            with db.begin(actor=Actor("adapter", "adapter_1"), correlation_id="cor_4") as uow:
                producer.append_for_attempt(uow, draft)
        assert refused.value.code == code
    assert counts(db) == before


def test_rollback_of_the_caller_unit_of_work_leaves_nothing(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    before = counts(db)

    class CallerFailed(Exception):
        pass

    with pytest.raises(CallerFailed):
        with db.begin(actor=Actor("person", "person_b"), correlation_id="cor_5") as uow:
            observation_id = producer.append_observation(uow, palpation(), idempotency_key="gc_9:1:obs")
            raise CallerFailed
    assert counts(db) == before
    with db.snapshot() as view:
        assert RecordRepository().observation(view, observation_id) is None


def test_no_mission_fails_closed_and_writes_nothing(tmp_path: Path) -> None:
    db, producer = station(tmp_path, mission=Mission(None))
    before = counts(db)
    with pytest.raises(ProblemError) as refused:
        append(db, producer, palpation())
    assert refused.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert counts(db) == before


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.decimals(min_value=Decimal("0.01"), max_value=Decimal(100000), places=2))
def test_converted_value_is_the_exact_conversion_rounded_once(tmp_path: Path, per_hour: Decimal) -> None:
    db, producer = station(tmp_path / str(per_hour).replace(".", "_"))
    with db.begin(actor=Actor("person", "person_b"), correlation_id="cor_6") as uow:
        observation_id = producer.append_observation(
            uow, palpation(value=Quantity(per_hour, Unit("/h"))), idempotency_key="gc_p:1:obs"
        )
        stored = RecordRepository().observation(uow, observation_id)
    assert stored is not None
    expected = (per_hour / Decimal(60)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    assert stored.quantity == Quantity(expected, Unit("/min"))


def test_a_policy_assesses_quality_and_a_rejected_value_stays_rejected(tmp_path: Path) -> None:
    db, producer = station(tmp_path)
    canonical = producer.canonical_unit("pulse")
    # Synthetic policy limits, no clinical threshold: only the mechanism is under test.
    policy = ObservationPolicy(
        "pulse",
        "policy_1",
        "source-unit-test",
        canonical,
        frozenset({canonical.code}),
        frozenset({"crewmate_palpation"}),
        Decimal(0),
        Decimal(100),
        Decimal(0),
        Decimal(100),
        3,
        Decimal(10),
        True,
    )

    class Policies:
        def for_code(self, release: ReleaseHandle, code: str) -> ObservationPolicy:
            return policy

    producer.bind(mission=Mission(), policies=Policies())
    rejected = append(db, producer, palpation(value=Quantity(Decimal(150), canonical)))
    held = append(db, producer, palpation(), key="gc_2:1:obs")
    with db.snapshot() as view:
        first = RecordRepository().observation(view, rejected)
        second = RecordRepository().observation(view, held)
    assert first is not None and second is not None
    assert (first.quality_status, first.quality_rule_version) == ("rejected", "policy_1")
    assert first.quality_reasons == ("out_of_range", "saturation", "time_unknown")
    assert (second.quality_status, second.quality_reasons) == ("uncertain", ("time_unknown",))
