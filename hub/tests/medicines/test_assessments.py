"""E32 medicines potency assessments end to end: assays and inspections into the engine inputs, scheduling through the
platform task queue, the worker's failure modes, artifacts and retention, the bundle import guard, replay, the domain
events and their audiences, and the body weight reader."""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from crew_health.contracts_gen.enums import EventType, Role
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor, Audience, SessionContext
from crew_health.medicines.adapters.documents import parse, read_documents, read_pharmacy
from crew_health.medicines.adapters.potency import BUNDLE_FILES, PotencyWorker
from crew_health.medicines.adapters.weights import RecordsObservations, RecordsWeightReader
from crew_health.medicines.application import assessments, events
from crew_health.medicines.application.ports import Partition, RunOutcome
from crew_health.medicines.application.use_cases import Documents, latest_summaries
from crew_health.records.application.ports import RecordsStore

from .harness import (
    CONFIG,
    ENGINE_PYTHON,
    REPO,
    SESSIONS,
    SUBJECT,
    FakeEngine,
    Station,
    StepClock,
    build_station,
    fixture_config,
    fixture_summaries,
    headers,
    lunar,
)
from .kit import condition_ids
from .test_documents_and_adapters import _validator
from .test_engine import _weighted
from .test_flow import CLIENT_TIME, _confirm, _context, _diagnose, _key, _post, _propose

ASSAY: dict[str, object] = {
    "lot_id": "lot-pmz-tab-001",
    "ingredient_id": "promethazine",
    "measured_at": "2028-03-01T07:30:00Z",
    "measurand": "total",
    "value_percent": 96.5,
    "sd_percent": 1.2,
    "site": "onboard",
    "method": "HPLC",
    "laboratory": "station",
    "client_time": CLIENT_TIME,
}
STARTUP = Actor("engine", "test")


@dataclass
class Recording(FakeEngine):
    """Keeps every inputs document; `during` runs while the run is running (its start already committed)."""

    seen: list[dict[str, Any]] = field(default_factory=list)
    during: Callable[[], None] | None = None

    def assess(self, bundle: Mapping[str, bytes], inputs: bytes) -> RunOutcome:
        self.seen.append(json.loads(inputs))
        if self.during is not None:
            self.during()
        return super().assess(bundle, inputs)


def _schedule(st: Station, token: str = "operator") -> dict[str, Any]:
    response = st.client.post("/v1/medicines/assessments", headers=headers(token, _key()))
    assert response.status_code == 202, response.text
    return cast(dict[str, Any], response.json())


def _status(st: Station, run_id: str) -> tuple[str, str | None]:
    with st.db.snapshot() as view:
        return st.deps.store.run_status(view, run_id)


def _runs(st: Station) -> list[str]:
    return [str(r[0]) for r in st.rows("SELECT run_id FROM med_assessment_run ORDER BY rowid")]


def _documents_of(config_dir: Path) -> Callable[[Mapping[str, bytes]], Documents]:
    treatments, policy = read_documents(config_dir)
    conditions = condition_ids()

    def documents_of(files: Mapping[str, bytes]) -> Documents:
        loaded = parse(treatments, policy, files, conditions)
        return Documents(loaded.treatments, loaded.policy, loaded.formulary, loaded.lots, conditions)

    return documents_of


def _config(tmp_path: Path, lots: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> Path:
    """A copy of config/ whose pharmacy lots.json went through `lots`."""
    from .harness import config_copy

    directory = config_copy(tmp_path, lambda t, p: None)
    path = directory / "pharmacy/lots.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["lots"] = lots(document["lots"])
    path.write_text(json.dumps(document), encoding="utf-8")
    return directory


# ---- assays, inspections and the inputs (E32 4.4, 5.3; INV-M7) ----


@pytest.mark.trace("T-57", "INV-M7", "E32-4.4", "E32-5.3")
def test_an_assay_and_an_inspection_reach_the_next_run_of_their_partition_only(tmp_path: Path) -> None:
    engine = Recording()
    st = build_station(tmp_path, engine=engine, config_dir=_lots(tmp_path))
    assay = _post(st, "/v1/medicines/assays", ASSAY)
    assert assay.status_code == 201 and assay.json()["assay_id"].startswith("medy")
    inspection = _post(
        st,
        "/v1/medicines/inspections",
        {"lot_id": "lot-apap-001", "finding": "color_change", "client_time": CLIENT_TIME},
    )
    assert inspection.status_code == 201
    assert len(_runs(st)) == 1  # the start-up run was still queued: both triggers coalesced into it
    assert st.drain() == 1
    (inputs,) = engine.seen
    validator = _validator(REPO / "contracts/pharmacy", "inputs.schema.json")
    assert sorted(validator.iter_errors(inputs), key=str) == []
    assert inputs["as_of_utc"] == "2028-03-01T08:00:00.000000Z" and inputs["sensor_series"] == []
    (row,) = inputs["assays"]
    measured = datetime(2028, 3, 1, 7, 30, tzinfo=UTC)
    assert (row["value_percent"], row["sd_percent"], row["measured_at_s"]) == (
        96.5,
        1.2,
        measured.timestamp(),
    )
    assert [i["finding"] for i in inputs["inspections"]] == ["color_change"]
    st.mission.facts = lunar("REPLAY")  # same mission, other mode: none of it is visible
    _schedule(st)
    assert st.drain() == 1
    assert engine.seen[1]["assays"] == [] and engine.seen[1]["inspections"] == []


@pytest.mark.trace("E32-4.4", "C6", "T-70")
def test_an_assay_is_refused_for_a_foreign_ingredient_a_future_time_another_lot_or_a_crew_member(
    st_plain: Station,
) -> None:
    st = st_plain
    foreign = _post(st, "/v1/medicines/assays", {**ASSAY, "ingredient_id": "acetaminophen"})
    assert foreign.status_code == 422 and foreign.json()["field_errors"][0]["field"] == "/ingredient_id"
    future = _post(st, "/v1/medicines/assays", {**ASSAY, "measured_at": "2028-03-01T09:00:00Z"})
    assert future.status_code == 422 and future.json()["field_errors"][0]["field"] == "/measured_at"
    missing = _post(st, "/v1/medicines/assays", {**ASSAY, "lot_id": "lot-none"})
    assert missing.status_code == 404
    crew = _post(st, "/v1/medicines/assays", ASSAY, token="crew_b")
    assert crew.status_code == 403 and crew.json()["code"] == "UNAUTHORIZED_SCOPE"
    assert st.rows("SELECT count(*) FROM med_record WHERE kind='assay'") == [(0,)]


def _lots(tmp_path: Path) -> Path:
    """The recorded run's two lots as the whole inventory, so the default FakeEngine answer is complete."""
    return fixture_config(tmp_path / "config-fixture-lots")


@pytest.fixture
def st_plain(tmp_path: Path) -> Station:
    return build_station(tmp_path, config_dir=_lots(tmp_path))


@pytest.mark.trace("E32-4.4", "E32-9")
def test_without_a_task_queue_scheduling_is_refused_and_an_assay_is_still_recorded(tmp_path: Path) -> None:
    st = build_station(tmp_path, queue=False)
    refused = st.client.post("/v1/medicines/assessments", headers=headers("operator", _key()))
    assert refused.json()["code"] == "CAPABILITY_UNAVAILABLE"
    assert _post(st, "/v1/medicines/assays", ASSAY).status_code == 201
    assert _runs(st) == []


# ---- scheduling, the run lifecycle and restarts (E32 4.4, 5.2; T-71) ----


@pytest.mark.trace("E32-4.4", "T-71")
def test_runs_coalesce_while_queued_and_a_trigger_during_a_run_queues_exactly_one_more(
    tmp_path: Path,
) -> None:
    engine = Recording()
    st = build_station(tmp_path, engine=engine, config_dir=_lots(tmp_path))
    first = _schedule(st)
    assert _schedule(st)["job_id"] == first["job_id"]  # coalesced into the start-up run
    during: list[str] = []
    engine.during = lambda: during.extend([_schedule(st)["job_id"], _schedule(st)["job_id"]])
    assert st.drain(limit=1) == 1
    second, again = during
    assert second == again != first["job_id"]
    engine.during = None
    assert st.drain() == 1
    assert [_status(st, r)[0] for r in _runs(st)] == ["completed", "completed"]
    job = st.client.post("/v1/medicines/assessments", headers=headers("cmo", "same-key-0001")).json()
    replayed = st.client.post("/v1/medicines/assessments", headers=headers("cmo", "same-key-0001")).json()
    assert job["job_id"] == replayed["job_id"] and job["kind"] == "assessment"
    validator = _validator(REPO / "contracts/api/schemas", "job.schema.json")
    assert sorted(validator.iter_errors(job), key=str) == []


@pytest.mark.trace("T-71", "E32-5.2", "C5")
def test_a_restart_marks_unfinished_runs_interrupted_and_schedules_one_run(tmp_path: Path) -> None:
    st = build_station(tmp_path, config_dir=_lots(tmp_path))
    (queued,) = _runs(st)
    with st.db.begin(actor=STARTUP, correlation_id="claim") as uow:
        st.tasks.claim(uow, worker_id="worker_1", handlers=frozenset({"medicines.assessment"}), limit=1)
    assert assessments.start(st.deps, queued, STARTUP) is not None  # running, then the process dies
    restarted = build_station(tmp_path, clock=st.clock, config_dir=_lots(tmp_path))
    old, new = _runs(restarted)
    assert old == queued and _status(restarted, old) == ("interrupted", None)
    assert _status(restarted, new) == ("queued", None)
    assert restarted.drain() == 1 and _status(restarted, new)[0] == "completed"
    statuses = [
        e["data"]["status"] for e in restarted.events("medicine.assessment") if e["data"]["run_id"] == old
    ]
    assert statuses == ["queued", "running", "interrupted"]


@pytest.mark.trace("T-56", "DD9", "E32-5.3")
def test_a_failed_then_timed_out_run_keeps_the_last_completed_assessment(st_plain: Station) -> None:
    st = st_plain
    assert st.drain() == 1
    board = st.client.get("/v1/medicines/lots", headers=headers()).json()
    completed = board["assessment"]["run_id"]
    for outcome in (
        RunOutcome("failed", None, "engine_error", "ValueError: no"),
        RunOutcome("timed_out", None, "timed_out", "past 600 s"),
    ):
        st.use_engine(FakeEngine(outcome))
        run = _schedule(st)["job_id"]
        assert st.drain() == 1 and _status(st, run) == (outcome.status, outcome.error_code)
        again = st.client.get("/v1/medicines/lots", headers=headers()).json()
        assert again["assessment"]["run_id"] == completed
    job = st.client.post("/v1/medicines/assessments", headers=headers("operator", _key())).json()
    assert job["state"] == "queued"


@pytest.mark.trace("T-76", "E32-11")
def test_inputs_over_the_bound_fail_the_run_input_limit_exceeded(tmp_path: Path) -> None:
    st = build_station(tmp_path, own={"medicines.max_inputs_bytes": 1024}, config_dir=_lots(tmp_path))
    for n in range(12):
        assert _post(st, "/v1/medicines/assays", {**ASSAY, "note": "x" * 100 + str(n)}).status_code == 201
    (run,) = _runs(st)
    assert st.drain() == 1 and _status(st, run) == ("failed", "input_limit_exceeded")


# ---- the worker in its child process (E32 5.3; HZ16) ----


def _worker(**changes: object) -> PotencyWorker:
    values: dict[str, Any] = {
        "python": Path(sys.executable),
        "timeout_seconds": 30,
        "memory_limit_bytes": 4 * 1024**3,
        "memory_sample_ms": 10,
    }
    values.update(changes)
    python = values.pop("python")
    return PotencyWorker(python, **values)


@pytest.mark.trace("T-63", "HZ16", "E32-5.3")
def test_a_child_over_its_memory_limit_fails_the_run_memory_limit() -> None:
    outcome = _worker(memory_limit_bytes=1).assess(read_pharmacy(CONFIG / "pharmacy"), b"{}")
    assert (outcome.status, outcome.error_code, outcome.result) == ("failed", "memory_limit", None)


@pytest.mark.trace("T-56", "E32-5.3")
def test_an_engine_error_in_the_child_is_a_failed_run_named_by_its_class() -> None:
    bundle = {**read_pharmacy(CONFIG / "pharmacy"), "lots": b"{}"}
    outcome = _worker().assess(bundle, b"{}")
    assert outcome.status == "failed" and outcome.error_code == "engine_error" and outcome.detail


@pytest.mark.trace("E32-5.3", "AGENTS-10")
@pytest.mark.skipif(not ENGINE_PYTHON.is_file(), reason="the root environment with pharmacy is not installed")
def test_the_bundle_members_copy_matches_pharmacy() -> None:
    import subprocess

    shown = subprocess.run(  # noqa: S603 (the repository's own interpreter)
        [
            str(ENGINE_PYTHON),
            "-I",
            "-c",
            "import json, pharmacy.config as c; print(json.dumps(dict(c.BUNDLE_FILES)))",
        ],
        capture_output=True,
        check=True,
        cwd=REPO,
    )
    assert json.loads(shown.stdout) == dict(BUNDLE_FILES)


# ---- replay with the real engine (E32-T-64, T-72) ----


def _two_lots(lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [lot for lot in lots if lot["lot_id"] in ("lot-apap-001", "lot-pmz-tab-001")]


@pytest.mark.slow
@pytest.mark.trace("T-64", "T-72", "DESIGN-BRIEF-12")
@pytest.mark.skipif(not ENGINE_PYTHON.is_file(), reason="the root environment with pharmacy is not installed")
def test_a_run_replays_bit_for_bit_after_the_bundle_changes_late_readings_arrive_and_the_hub_restarts(
    tmp_path: Path,
) -> None:
    directory = _config(tmp_path, _two_lots)
    clock = StepClock(
        at=datetime(2027, 11, 1, tzinfo=UTC)
    )  # the recorded fixture run's as_of, lots on the ground
    st = build_station(tmp_path / "hub", config_dir=directory, engine_python=str(ENGINE_PYTHON), clock=clock)
    (run,) = _runs(st)
    assert st.drain() == 1 and _status(st, run) == ("completed", None)
    with st.db.snapshot() as view:
        _, mapped = latest_summaries(st.deps, view, Partition("mission_1", "SIM", ""))
    recorded = fixture_summaries("fixture")
    # The worker's run states what the recorded pipeline run of the same bundle and inputs stated (T-72); the
    # quantiles agree to 0.1 % of label claim (the fixture predates later pharmacy model data).
    assert {k: v.state for k, v in mapped.items()} == {k: v.state for k, v in recorded.items()}
    for lot_id, summary in mapped.items():
        for ingredient, potency in summary.potency.items():
            expected = recorded[lot_id].potency[ingredient]
            for got, want in zip(
                (potency.low, potency.median, potency.high),
                (expected.low, expected.median, expected.high),
                strict=True,
            ):
                assert abs(got - want) < Decimal("0.001")
    assert assessments.replay(st.deps, run).status == "reproduced"
    clock.at = datetime(2027, 11, 2, tzinfo=UTC)
    late = _post(
        st,
        "/v1/medicines/assays",
        {**ASSAY, "measured_at": "2027-11-01T12:00:00Z", "client_time": "2027-11-02T00:00:00Z"},
    )
    assert late.status_code in (201, 404)  # a late reading (the lot may sit outside this environment's zones)
    lots_path = directory / "pharmacy/lots.json"
    changed = json.loads(lots_path.read_text(encoding="utf-8"))
    changed["lots"][0]["quantity"]["value"] = 40  # the bundle on disk is replaced
    lots_path.write_text(json.dumps(changed), encoding="utf-8")
    restarted = build_station(
        tmp_path / "hub", config_dir=directory, engine_python=str(ENGINE_PYTHON), clock=st.clock
    )
    assert assessments.replay(restarted.deps, run).status == "reproduced"


# ---- the bundle import guard (E32-T-77, M-A1) ----


@pytest.mark.trace("T-77", "M-A1")
def test_a_bundle_below_its_net_movements_is_refused_and_the_last_good_bundle_stays(tmp_path: Path) -> None:
    directory = _config(tmp_path, lambda lots: lots)
    st = build_station(tmp_path / "hub", config_dir=directory)
    waste = _post(
        st,
        "/v1/medicines/lots/lot-apap-001/movements",
        {"kind": "waste", "quantity": {"value": 25, "ucum": "{tbl}"}, "client_time": CLIENT_TIME},
    )
    assert waste.status_code == 201, waste.text
    with st.db.snapshot() as view:
        good = st.deps.store.active_bundle(view)

    def edit(change: Callable[[dict[str, Any]], None]) -> None:
        path = directory / "pharmacy/lots.json"
        document = json.loads((CONFIG / "pharmacy/lots.json").read_text(encoding="utf-8"))
        change(next(lot for lot in document["lots"] if lot["lot_id"] == "lot-apap-001"))
        path.write_text(json.dumps(document), encoding="utf-8")

    for change, words in (
        (lambda lot: lot["quantity"].update(value=20), "below its net movements"),
        (lambda lot: lot.update(product_id="acetaminophen_500_tab"), "changed product"),
    ):
        edit(change)
        with pytest.raises(ProblemError) as refused:
            assessments.import_bundle(
                st.deps, STARTUP, read_pharmacy(directory / "pharmacy"), _documents_of(directory)
            )
        assert refused.value.code.value == "CONFIG_INVALID" and words in str(refused.value)
    edit(lambda lot: lot["quantity"].update(value=20))
    restarted = build_station(tmp_path / "hub", config_dir=directory, clock=st.clock)
    with restarted.db.snapshot() as view:
        assert restarted.deps.store.active_bundle(view) == good
    board = restarted.client.get("/v1/medicines/lots", headers=headers()).json()
    (apap,) = [lot for lot in board["lots"] if lot["lot_id"] == "lot-apap-001"]
    assert apap["quantity_now"]["value"] == 5  # 30 in the good bundle less the 25 wasted


# ---- artifact retention and integrity (E32-T-79, 5.2) ----


@pytest.mark.trace("T-79", "E32-5.2")
def test_retention_prunes_only_unreferenced_inputs_and_a_direct_delete_aborts(st_plain: Station) -> None:
    st = st_plain
    st.use_engine(FakeEngine(scale=0.98))
    _context(st)
    assert st.drain() == 1
    (referenced,) = _runs(st)
    proposal = _propose(st, _diagnose(st))
    assert cast(dict[str, Any], proposal["potency"])["assessment_run_id"] == referenced
    for _ in range(4):
        st.clock.at += timedelta(minutes=1)  # each run reads at its own hub clock, so its inputs differ
        _schedule(st)
        assert st.drain() == 1
    runs = _runs(st)
    replays = {run: assessments.replay(st.deps, run).status for run in runs}
    assert replays == {
        runs[0]: "reproduced",  # named by a proposal: kept for the mission's life
        runs[1]: "replay_unavailable",
        runs[2]: "replay_unavailable",
        runs[3]: "reproduced",  # the two newest unreferenced runs keep their inputs (retained 2)
        runs[4]: "reproduced",
    }
    assert sorted(str(r[0]) for r in st.rows("SELECT run_id FROM med_artifact_prune")) == sorted(runs[1:3])
    assert len(st.rows("SELECT run_id FROM med_assessment_result")) == 5  # results are never pruned
    with sqlite3.connect(st.db.path) as connection, pytest.raises(sqlite3.DatabaseError):
        connection.execute("DELETE FROM med_artifact WHERE kind = 'inputs'")
    with sqlite3.connect(st.db.path) as connection:
        connection.execute("DROP TRIGGER med_artifact_no_update")
        connection.execute(
            "UPDATE med_artifact SET bytes = CAST('{}' AS BLOB), size_bytes = 2 WHERE kind = 'inputs'"
        )
    assert assessments.replay(st.deps, runs[4]).status == "artifact_corrupt"


# ---- events and their audiences (E32 4.5; R-E32-11; T-75) ----


PRIVATE = ("amount", "product", "lot", "dose", "note", "reason", "diagnosis", "condition", "accounting")


@pytest.mark.trace("T-75", "R-E32-11", "E32-4.5", "E32-10.3")
def test_medicines_events_carry_ids_and_states_only_to_their_audiences(st_plain: Station) -> None:
    st = st_plain
    st.use_engine(FakeEngine(scale=0.98))
    _context(st)
    assert st.drain() == 1
    proposal = _propose(st, _diagnose(st))
    administration = _confirm(st, proposal).json()
    corrected = _post(
        st,
        f"/v1/medication-administrations/{administration['administration_id']}/corrections",
        {"reason_text": "wrong subject", "client_time": CLIENT_TIME},
    )
    assert corrected.status_code == 201, corrected.text
    found = st.events()
    kinds = [(e["type"], e["scope"]["kind"]) for e in found]
    assert ("medicine.assessment_changed", "crew") in kinds
    assert ("medicine.proposal_changed", "person") in kinds
    assert ("medication.administered", "person") in kinds
    assert ("medication.administration_corrected", "person") in kinds
    states = [e["data"]["state"] for e in found if e["type"] == "medicine.proposal_changed"]
    assert states == ["proposed", "confirmed"]
    for event in found:
        assert not any(word in key for key in event["data"] for word in PRIVATE if key != "proposal_id")
        if event["scope"]["kind"] == "person":
            assert event["scope"]["person_id"] == SUBJECT == event["data"]["subject_person_id"]
    person = Audience(events.PERSON_RULE, SUBJECT, events.projection(EventType.MEDICATION_ADMINISTERED), "1")
    crew = Audience(events.CREW_RULE, "medr_1", events.projection(EventType.MEDICINE_ASSESSMENT_CHANGED), "1")
    station = SessionContext(
        "session_station", "station", Actor("station", "station_1"), None, frozenset({Role.STATION})
    )
    with st.db.snapshot() as view:
        rule = events.PersonAudience(
            lambda: st.consent
        )  # the fixture's subject shared everything with the CMO
        assert rule.admits(view, SESSIONS["subject"], person) and rule.admits(view, SESSIONS["cmo"], person)
        assert not rule.admits(view, SESSIONS["crew_b"], person) and not rule.admits(view, station, person)
        assert events.CrewAudience().admits(view, SESSIONS["crew_b"], crew)
        assert events.CrewAudience().admits(view, station, crew)
        assert not rule.admits(view, SESSIONS["cmo"], crew)


# ---- body weight from records (E32 3.5 step 2) ----


@dataclass(frozen=True)
class _Unit:
    code: str


@dataclass(frozen=True)
class _Quantity:
    value: Decimal
    unit: _Unit


@dataclass(frozen=True)
class _Normalized:
    original_at: datetime | None
    corrected_at: datetime | None = None


@dataclass(frozen=True)
class _Observation:
    observation_id: str
    quantity: _Quantity
    normalized: _Normalized


@dataclass
class _Records:
    """records' store stand-in: newest first, as RecordsStore.observations answers."""

    rows: list[_Observation]
    asked: list[dict[str, object]] = field(default_factory=list)

    def observations(self, view: object, **query: object) -> tuple[_Observation, ...]:
        self.asked.append(query)
        return tuple(self.rows)

    def __getattr__(self, name: str) -> object:  # the rest of RecordsStore: never called by medicines
        if name not in {
            "observation",
            "answer",
            "add_observation",
            "add_answer",
            "answers",
            "observation_accepted",
        }:
            raise AttributeError(name)

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("medicines called records' " + name)

        return refuse


def _mass(observation_id: str, value: str, unit: str, at: datetime | None) -> _Observation:
    return _Observation(observation_id, _Quantity(Decimal(value), _Unit(unit)), _Normalized(at))


@pytest.mark.trace("E32-3.5", "T-73")
def test_the_weight_reader_copy_matches_records_query_port() -> None:
    copy = inspect.signature(RecordsObservations.observations).parameters
    original = inspect.signature(RecordsStore.observations).parameters
    assert [(p.name, p.kind, p.default) for p in copy.values()] == [
        (p.name, p.kind, p.default) for p in original.values()
    ]


@pytest.mark.trace("E32-3.5", "T-73")
def test_weights_convert_to_kilograms_and_an_unknown_unit_or_time_stays_absent() -> None:
    at = datetime(2028, 3, 1, 7, 0, tzinfo=UTC)
    records = _Records(
        [
            _mass("obs_g", "70500", "g", at),
            _mass("obs_lb", "150", "[lb_av]", at - timedelta(days=1)),
            _mass("obs_st", "11", "[stone_av]", at),
            _mass("obs_none", "70", "kg", None),
        ]
    )
    found = RecordsWeightReader(cast(RecordsObservations, records)).weights(
        cast(Any, None), SUBJECT, mission_id="mission_1", origin_mode="SIM", limit=8
    )
    assert [(w.observation_id, w.kg) for w in found] == [
        ("obs_g", Decimal("70.500")),
        ("obs_lb", Decimal("68.03885550")),
    ]
    assert records.asked[0]["code"] == "mass" and records.asked[0]["accepted_only"] is True


@pytest.mark.trace("E32-3.5", "T-73", "R-E32-01")
def test_a_per_kilogram_rule_refuses_without_a_weight_and_uses_the_latest_accepted_one(
    tmp_path: Path,
) -> None:
    records = _Records([])
    st = build_station(tmp_path, records=records, config_dir=_lots(tmp_path))
    weighted, _ = _weighted()
    documents = replace(st.deps.documents(), treatments=weighted.treatments)
    st.rebind(documents=lambda: documents)
    _context(st)
    diagnosis = _diagnose(st)
    refused = _post(
        st,
        "/v1/dose-proposals",
        {
            "working_diagnosis_id": diagnosis,
            "product_id": "acetaminophen_325_tab",
            "client_time": CLIENT_TIME,
        },
    ).json()
    assert refused["outcome"] == "refusal" and refused["refusal"]["code"] == "weight_missing"
    assert "body mass" in refused["refusal"]["detail_text"]
    records.rows.append(_mass("obs_1", "65", "kg", datetime(2028, 3, 1, 6, 0, tzinfo=UTC)))
    proposed = _post(
        st,
        "/v1/dose-proposals",
        {
            "working_diagnosis_id": diagnosis,
            "product_id": "acetaminophen_325_tab",
            "client_time": CLIENT_TIME,
        },
    ).json()
    assert proposed["outcome"] == "proposal", proposed
    assert "record:obs_1" in json.dumps(proposed["trace"])
    assert records.asked[-1]["mission_id"] == "mission_1" and records.asked[-1]["origin_mode"] == "SIM"
