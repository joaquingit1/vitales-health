"""Edges of the assessment runs, the bundle import guard and replay (E32 4.4, 5.2, 5.3): a missing or corrupt
artifact, a refused bundle, readings after the run's instant, an evicted outcome, and scheduling without a queue."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from crew_health.kernel.errors import ProblemError
from crew_health.kernel.uow import Actor
from crew_health.medicines.adapters.documents import read_pharmacy
from crew_health.medicines.application import assessments, scheduling
from crew_health.medicines.application.ports import ArtifactCorrupt, PotencyStore, RunOutcome

from .harness import FakeEngine, Station, build_station, headers
from .test_assessments import ASSAY, STARTUP, _config, _documents_of, _lots, _runs, _status
from .test_flow import CLIENT_TIME, _post

SUPERVISOR = Actor("engine", "supervisor")


@pytest.fixture
def st(tmp_path: Path) -> Station:
    return build_station(tmp_path, config_dir=_lots(tmp_path))


def _queued(st: Station) -> str:
    (run_id,) = _runs(st)  # the start-up run, still queued
    assert _status(st, run_id) == ("queued", None)
    return run_id


# ---- bundles ----


@pytest.mark.trace("E32-5.2", "T-79")
def test_a_bundle_with_a_missing_manifest_or_member_is_corrupt(st: Station) -> None:
    with st.db.snapshot() as view, pytest.raises(ArtifactCorrupt):
        assessments.bundle_files(st.deps, view, "0" * 64)
    absent = "f" * 64
    with st.db.begin(actor=STARTUP, correlation_id="cor_manifest") as uow:
        manifest = st.deps.store.put_artifact(uow, "bundle_manifest", json.dumps({"lots": absent}).encode())
    with st.db.snapshot() as view, pytest.raises(ArtifactCorrupt) as raised:
        assessments.bundle_files(st.deps, view, manifest)
    assert absent in str(raised.value)


@dataclass
class _Refusing:
    reason: str

    def check(self, files: Mapping[str, bytes]) -> str | None:
        return self.reason


def _waste(st: Station, lot_id: str, tablets: int) -> None:
    response = _post(
        st,
        f"/v1/medicines/lots/{lot_id}/movements",
        {"kind": "waste", "quantity": {"value": tablets, "ucum": "{tbl}"}, "client_time": CLIENT_TIME},
    )
    assert response.status_code == 201, response.text


@pytest.mark.trace("T-77", "M-A1")
def test_a_bundle_the_pharmacy_loader_refuses_never_becomes_active(tmp_path: Path) -> None:
    directory = _config(tmp_path, lambda lots: lots)
    st = build_station(tmp_path / "hub", config_dir=directory)
    with st.db.snapshot() as view:
        good = st.deps.store.active_bundle(view)
    st.rebind(bundle_check=_Refusing("lots.json: bad lot"))
    with pytest.raises(ProblemError) as refused:
        assessments.import_bundle(
            st.deps, STARTUP, read_pharmacy(directory / "pharmacy"), _documents_of(directory)
        )
    assert refused.value.code.value == "CONFIG_INVALID"
    assert "pharmacy bundle refused: lots.json: bad lot" in str(refused.value)
    with st.db.snapshot() as view:
        assert st.deps.store.active_bundle(view) == good


@pytest.mark.trace("T-77", "M-A1")
def test_a_bundle_that_drops_a_lot_with_movements_is_refused(tmp_path: Path) -> None:
    directory = _config(tmp_path, lambda lots: lots)
    st = build_station(tmp_path / "hub", config_dir=directory)
    _waste(st, "lot-apap-001", 5)
    gone = _config(tmp_path / "gone", lambda lots: [x for x in lots if x["lot_id"] != "lot-apap-001"])
    with pytest.raises(ProblemError) as refused:
        assessments.import_bundle(st.deps, STARTUP, read_pharmacy(gone / "pharmacy"), _documents_of(gone))
    assert refused.value.code.value == "CONFIG_INVALID" and "has movements and is gone" in str(refused.value)


@pytest.mark.trace("T-77", "M-A1")
def test_a_bundle_whose_lots_cover_their_movements_is_imported_and_counts_from_its_quantity(
    tmp_path: Path,
) -> None:
    directory = _config(tmp_path, lambda lots: lots)
    st = build_station(tmp_path / "hub", config_dir=directory)
    _waste(st, "lot-apap-001", 5)
    with st.db.snapshot() as view:
        before = st.deps.store.active_bundle(view)

    def restock(lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for lot in lots:
            if lot["lot_id"] == "lot-apap-001":
                lot["quantity"]["value"] = 40
        return lots

    restocked = _config(tmp_path / "restocked", restock)
    assessments.import_bundle(
        st.deps, STARTUP, read_pharmacy(restocked / "pharmacy"), _documents_of(restocked)
    )
    with st.db.snapshot() as view:
        after = st.deps.store.active_bundle(view)
    assert after is not None and after != before
    restarted = build_station(tmp_path / "hub", config_dir=restocked, clock=st.clock)
    board = restarted.client.get("/v1/medicines/lots", headers=headers()).json()
    (apap,) = [lot for lot in board["lots"] if lot["lot_id"] == "lot-apap-001"]
    assert apap["quantity_now"]["value"] == 35  # 40 in the new bundle less the 5 wasted


# ---- inputs ----


@pytest.mark.trace("INV-M7", "E32-5.3")
def test_the_inputs_hold_only_readings_at_or_before_the_runs_instant(st: Station) -> None:
    assert _post(st, "/v1/medicines/assays", ASSAY).status_code == 201  # measured 07:30
    inspection = _post(
        st,
        "/v1/medicines/inspections",
        {"lot_id": "lot-apap-001", "finding": "color_change", "client_time": CLIENT_TIME},
    )
    assert inspection.status_code == 201  # recorded 08:00
    with st.db.snapshot() as view:
        early = json.loads(
            assessments.build_inputs(
                st.deps, view, "mission_1", "SIM", datetime(2028, 3, 1, 7, 0, tzinfo=UTC)
            )
        )
        between = json.loads(
            assessments.build_inputs(
                st.deps, view, "mission_1", "SIM", datetime(2028, 3, 1, 7, 45, tzinfo=UTC)
            )
        )
        now = json.loads(assessments.build_inputs(st.deps, view, "mission_1", "SIM", st.clock.at))
    assert early["assays"] == [] and early["inspections"] == []
    assert [a["lot_id"] for a in between["assays"]] == ["lot-pmz-tab-001"] and between["inspections"] == []
    assert [i["finding"] for i in now["inspections"]] == ["color_change"]


# ---- one run ----


class _NoBundle:
    """The real store with no bundle ever activated."""

    def __init__(self, inner: PotencyStore) -> None:
        self._inner = inner

    def active_bundle(self, view: object) -> None:
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


@pytest.mark.trace("E32-4.4", "E32-5.2")
def test_a_run_starts_only_from_queued_and_fails_no_bundle_without_one(st: Station) -> None:
    assert assessments.start(st.deps, "medr_unknown", SUPERVISOR) is None
    run_id = _queued(st)
    st.rebind(store=_NoBundle(st.deps.store))
    assert assessments.start(st.deps, run_id, SUPERVISOR) is None
    assert _status(st, run_id) == ("failed", "no_bundle")
    assert st.rows("SELECT count(*) FROM medicines_assessment_start") == [(0,)]


@pytest.mark.trace("E32-4.4", "R-E32-04")
def test_a_completed_outcome_for_a_run_with_no_start_is_result_invalid(st: Station) -> None:
    run_id = _queued(st)
    with st.db.begin(actor=SUPERVISOR, correlation_id="cor_running") as uow:
        st.deps.store.add_run_event(uow, run_id, "running", None)
    with st.db.begin(actor=SUPERVISOR, correlation_id="cor_outcome") as uow:
        assessments.record_outcome(st.deps, uow, run_id, RunOutcome("completed", b"{}", None, ""))
    assert _status(st, run_id) == ("failed", "result_invalid")
    assert st.rows("SELECT count(*) FROM medicines_assessment_result") == [(0,)]


@pytest.mark.trace("E32-4.4", "E32-5.2")
def test_an_evicted_outcome_is_applied_as_interrupted_never_as_a_success(st: Station) -> None:
    run_id = _queued(st)
    with st.db.begin(actor=SUPERVISOR, correlation_id="claim") as uow:
        (lease,) = st.tasks.claim(
            uow, worker_id="worker_1", handlers=frozenset({"medicines.assessment"}), limit=1
        )
    handler = st.registrations["medicines.assessment"].handler
    result = handler.execute(lease)
    assert _status(st, run_id) == ("running", None)
    for generation in range(lease.generation + 1, lease.generation + 9):  # eight more: the first is evicted
        handler.execute(replace(lease, generation=generation))
    with st.db.begin(actor=SUPERVISOR, correlation_id="complete") as uow:
        handler.apply(uow, lease, result)
    assert _status(st, run_id) == ("failed", "interrupted")
    assert st.rows("SELECT count(*) FROM medicines_assessment_result") == [(0,)]


# ---- replay ----


@dataclass
class _ReplayFails(FakeEngine):
    def replay(self, bundle: Mapping[str, bytes], inputs: bytes, recorded: bytes) -> RunOutcome:
        return RunOutcome("failed", None, "engine_error", "ValueError: no")


@pytest.mark.trace("T-64", "E32-5.2")
def test_replay_says_not_completed_failed_or_corrupt_and_never_recomputes(st: Station) -> None:
    assert assessments.replay(st.deps, "medr_unknown") == assessments.Replay(
        "medr_unknown", "not_completed", ""
    )
    run_id = _queued(st)
    assert st.drain() == 1 and _status(st, run_id) == ("completed", None)
    assert assessments.replay(st.deps, run_id).status == "reproduced"
    st.use_engine(_ReplayFails())
    failed = assessments.replay(st.deps, run_id)
    assert failed.status == "failed" and failed.detail == "engine_error: ValueError: no"
    with closing(sqlite3.connect(st.db.path)) as connection, connection:
        connection.execute("DROP TRIGGER medicines_artifact_delete_only_pruned")
        connection.execute("DELETE FROM medicines_artifact WHERE kind = 'result'")
    assert assessments.replay(st.deps, run_id) == assessments.Replay(
        run_id, "artifact_corrupt", "result missing"
    )


# ---- scheduling ----


@pytest.mark.trace("E32-4.4", "E32-9")
def test_scheduling_without_a_task_queue_is_capability_unavailable_and_writes_nothing(st: Station) -> None:
    before = _runs(st)
    with pytest.raises(ProblemError) as refused, st.db.begin(actor=STARTUP, correlation_id="cor_s") as uow:
        scheduling.schedule(
            st.deps.store,
            None,
            st.deps.ids,
            st.deps.process_epoch,
            uow,
            mission_id="mission_1",
            origin_mode="SIM",
            trigger="assay",
        )
    assert refused.value.code.value == "CAPABILITY_UNAVAILABLE" and refused.value.retryable is False
    assert _runs(st) == before
