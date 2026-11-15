"""Faults the fallbacks exist for, on the real stack (T-E23-051/054/055/057/060/089/090/091).

Crash around commit, disk full, a corrupt registry, restart, and the health codes a fault raises."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from e23_station import ADAPTER, Station, build_station, channel, reading

from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.scheduler import HealthState

pytestmark = pytest.mark.e23
PATH = "/v1/adapters/" + ADAPTER + "/readings"


def make(tmp: Path, **kw: Any) -> Station:
    st = build_station(tmp, registry={"channels": [channel("ch_co2", "co2", "co2", "SIM", "mmHg")]}, **kw)
    st.initialize("SIM")
    st.advance(seconds=60)
    return st


def send(st: Station, key: str, seq: int = 1) -> Any:
    row = reading("ch_co2", seq, 2.5, "mmHg", st.clock.at - timedelta(seconds=3))
    return st.client.post(PATH, json={"batch_id": "b_" + key, "readings": [row]}, headers=st.headers("adapter", key))


def count(st: Station, table: str) -> int:
    connection = sqlite3.connect(st.db.path)
    try:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


@pytest.mark.trace("T-E23-051", "T-E23-054", "R35", "AGENTS-6")
def test_disk_full_at_the_outbox_aborts_the_whole_command_and_a_retry_with_the_same_key_succeeds(tmp_path: Path) -> None:
    st = make(tmp_path)
    original = st.db.event_sink
    calls = {"n": 0}

    def full(uow: Any, event: Any) -> Any:
        calls["n"] += 1
        raise StorageUnavailable("disk_full")

    st.db.event_sink = full
    failed = send(st, "key_00001")
    assert failed.status_code == 500 and failed.json()["retryable"] is True  # nothing was acknowledged
    assert calls["n"] >= 1
    assert count(st, "sensors_reading") == 0 and count(st, "sensors_evaluation_input") == 0  # nothing partial
    assert count(st, "platform_command_ledger") == 0
    st.db.event_sink = original
    ok = send(st, "key_00001")  # the adapter's queue is intact: the same key retries
    assert ok.status_code == 200 and ok.json()["accepted"] == 1
    assert count(st, "sensors_reading") == 1


@pytest.mark.trace("T-E23-051", "AGENTS-6")
def test_a_crash_after_the_effects_but_before_the_commit_leaves_no_trace(tmp_path: Path) -> None:
    st = make(tmp_path)
    boom = RuntimeError("process killed before commit")
    original = st.db.audit_policies.get("sensors.batch_ingested")
    assert original is not None
    real = st.resolver().require(__import__("crew_health.kernel.commands", fromlist=["IdempotencyStore"]).IdempotencyStore)
    saved = real.record

    def kill(uow: Any, identity: Any, result: Any) -> None:
        raise boom

    real.record = kill  # type: ignore[method-assign]
    log_before = count(st, "repl_log")
    try:
        reply = send(st, "key_00002")
    finally:
        real.record = saved  # type: ignore[method-assign]
    assert reply.status_code == 500
    assert count(st, "sensors_reading") == 0 and count(st, "repl_log") == log_before  # rolled back whole
    assert send(st, "key_00002").status_code == 200
    assert count(st, "sensors_reading") == 1


@pytest.mark.trace("T-E23-055", "AGENTS-3")
def test_a_corrupt_registry_is_rejected_and_the_last_good_one_stays_loaded(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert count(st, "sensors_channel") == 1
    st.registry_file.write_text('{"channels": [{"channel_id": "ch_bad"}]}', encoding="utf-8")
    with st.begin() as uow:
        from crew_health.sensors.application.registry import install_file

        live = st.resolver().require(__import__("crew_health.sensors.application.ports", fromlist=["FreshnessSweeper"]).FreshnessSweeper)
        runtime = live._live.need()  # type: ignore[attr-defined]
        assert install_file(runtime, uow, str(st.registry_file), source="registry") == 0
    assert count(st, "sensors_channel") == 1  # nothing partly accepted, nothing removed
    names = [r[0] for r in sqlite3.connect(st.db.path).execute("SELECT channel_id FROM sensors_channel")]
    assert names == ["ch_co2"]
    audited = sqlite3.connect(st.db.path).execute("SELECT count(*) FROM platform_audit").fetchone()[0]
    assert audited > 0  # the rejection is audited by reference, never by document text


@pytest.mark.trace("T-E23-057", "T-E23-091", "AGENTS-2")
def test_a_missing_optional_part_degrades_only_its_feature(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert send(st, "key_00003").status_code == 200  # no E24, no E29, no model, no ground: capture still works
    assert st.client.get("/v1/mission", headers=st.headers("crew_a")).status_code == 500  # E29 absent: loud, retryable
    assert st.client.get("/v1/mission", headers=st.headers("crew_a")).json()["retryable"] is True
    assert st.client.get("/v1/sensors/co2", headers=st.headers("crew_a")).status_code == 200


@pytest.mark.trace("T-E23-089", "T-E23-090", "S5.10")
def test_health_carries_the_sensors_fault_code_after_a_conflicting_instant(tmp_path: Path) -> None:
    st = make(tmp_path)
    at = st.clock.at - timedelta(seconds=3)
    first = reading("ch_co2", 1, 2.5, "mmHg", at)
    second = reading("ch_co2", 2, 3.5, "mmHg", at)  # the same instant, another value
    st.client.post(PATH, json={"batch_id": "b1", "readings": [first, second]}, headers=st.headers("adapter", "key_00004"))
    health = st.resolver().require(HealthState).snapshot()
    sensors = next(c for c in health.components if c.component == "sensors")
    assert health.status == "degraded" and sensors.code == "INSTANT_CONFLICT"
    assert count(st, "sensors_reading") == 2  # both kept: a real reading is never discarded


@pytest.mark.trace("T-E23-089", "AGENTS-2")
def test_the_migration_is_tracked_and_a_second_start_applies_nothing_twice(tmp_path: Path) -> None:
    from crew_health.sensors.adapters.migration import apply_migration

    st = make(tmp_path)
    assert send(st, "key_00005").status_code == 200
    factory = st.resolver().require(__import__("crew_health.kernel.uow", fromlist=["UnitOfWorkFactory"]).UnitOfWorkFactory)
    apply_migration(factory, "adapters/migrations/0001_sensors.sql", name="sensors.0001")  # a restart runs it again
    assert count(st, "sensors_reading") == 1 and count(st, "sensors_channel") == 1
