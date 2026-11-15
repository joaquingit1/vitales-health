"""Failure cases of AGENTS 6 for the sensors module on the real stack (E23 12, section 9 and 11).

Traces: T-E23-019 (independent sweep and its startup path), T-E23-051/052 (refused commands roll back whole,
a retry is a duplicate), T-E23-053 (a stale channel never clears), T-E23-055 (corrupt configuration), T-E23-060
(evaluator faults), T-E23-073 (before mission init), T-E23-079 and T-E23-089/091 (fail closed until bound)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from e23_station import ADAPTER, ROOT, Station, build_station, channel, reading

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.scheduler import HealthState
from crew_health.sensors import module as sensors_module
from crew_health.sensors.api.router import SensorsApi
from crew_health.sensors.application.deadlines import freshness_job, status_job
from crew_health.sensors.application.evaluate import SensorEvaluator
from crew_health.sensors.application.ports import FreshnessSweeper
from crew_health.sensors.application.runtime import SCHEDULER_LAG, Runtime
from crew_health.sensors.domain.errors import ConfigInconsistent

pytestmark = pytest.mark.e23
PATH = "/v1/adapters/" + ADAPTER + "/readings"
REGISTRY = {"channels": [channel("ch_co2", "co2", "co2", "SIM", "mmHg", stale_after_seconds=300)]}


def make(tmp: Path, *, init: bool = True, **kw: Any) -> Station:
    st = build_station(tmp, registry=REGISTRY, **kw)
    if init:
        st.initialize("SIM")
        st.advance(seconds=60)
    return st


def live(st: Station) -> Any:
    return st.resolver().require(FreshnessSweeper)._live  # type: ignore[attr-defined]


def runtime(st: Station) -> Runtime:
    found: Runtime = live(st).need()
    return found


def push(st: Station, key: str, rows: list[Any], **headers: str) -> Any:
    return st.client.post(
        PATH,
        json={"batch_id": "b_" + key, "readings": rows},
        headers=st.headers("adapter", key, **headers),
    )


def co2(st: Station, seq: int, ago: int = 3, value: float = 2.5) -> dict[str, Any]:
    return reading("ch_co2", seq, value, "mmHg", st.clock.at - timedelta(seconds=ago))


def rows_of(st: Station, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(st.db.path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def health_code(st: Station) -> str | None:
    snapshot = st.resolver().require(HealthState).snapshot()
    return next((c.code for c in snapshot.components if c.component == "sensors"), None)


# --- fail closed until bound (T-E23-089, T-E23-091, AGENTS 2) --------------------------------------------


def unbound() -> Any:
    return sensors_module._Live(SensorsApi())  # type: ignore[attr-defined]


def refused(call: Any) -> ProblemError:
    with pytest.raises(ProblemError) as caught:
        call()
    return caught.value


def test_every_late_bound_part_refuses_loudly_and_retryably_before_the_runtime_exists() -> None:
    state = unbound()
    module = sensors_module
    calls = [
        state.need,
        state.api.bound,
        lambda: module._storage_hook(state)(),  # type: ignore[attr-defined]
        lambda: module._sweep_hook(state)(),  # type: ignore[attr-defined]
        lambda: module._Sweeper(state).sweep_once(),  # type: ignore[attr-defined]
        lambda: module._Providers(state).channels(None),  # type: ignore[attr-defined]
        lambda: module._LateEvaluator(state).evaluate(None, None),  # type: ignore[attr-defined]
    ]
    for call in calls:
        error = refused(call)
        assert error.code == ErrorCode.INTERNAL_ERROR and error.retryable is True


def test_an_unbound_loader_check_is_a_finding_never_a_pass() -> None:
    check = sensors_module._Late("12.sensor")  # type: ignore[attr-defined]
    assert check.check_id == "12.sensor"
    (no_candidate,) = check.check("not a release")
    assert no_candidate.check_id == "12.sensor"
    (unavailable,) = check.check(SimpleNamespace(), _candidate())
    assert "unavailable" in unavailable.message
    (single,) = check.check(_candidate())
    assert "unavailable" in single.message


def _candidate() -> Any:
    from crew_health.kernel.config import CandidateRelease

    return CandidateRelease("release_x", {})


def test_a_guard_and_a_check_run_the_built_function_once_it_is_bound() -> None:
    check = sensors_module._Late("4")  # type: ignore[attr-defined]
    candidate = _candidate()
    check.build = lambda given: ()
    check.build_guard = lambda uow, given: ()
    assert check.check(candidate) == () and check.check(object(), candidate) == ()


# --- corrupt or missing configuration (T-E23-055, AGENTS 3) ---------------------------------------------


def test_a_release_without_error_messages_serves_none_and_a_bad_unit_mapping_gives_no_resolver(
    tmp_path: Path,
) -> None:
    broken = SimpleNamespace(document=lambda name: SimpleNamespace(raw=b'{"error_messages": 7}'))
    assert sensors_module._messages_of(broken) == {}  # type: ignore[attr-defined]
    missing = SimpleNamespace(document=lambda name: SimpleNamespace(raw=b"{}"))
    assert sensors_module._messages_of(missing) == {}  # type: ignore[attr-defined]
    resolver = sensors_module._mapping_resolver  # type: ignore[attr-defined]
    assert resolver("") is None  # no path: no resolver, so the loader reports a finding
    assert resolver(str(tmp_path / "absent.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert resolver(str(bad)) is None
    real = json.loads((ROOT / "config/unit-mapping.json").read_text(encoding="utf-8"))["mappings"][0]
    found = resolver(str(ROOT / "config/unit-mapping.json"))
    assert found is not None and found(real["indicator_code"], real["wire_unit"]) == real["ucum_unit"]
    assert found(real["indicator_code"], "no_such_unit") is None  # an unlisted spelling is never guessed


# --- the sweep timer startup path (T-E23-019) ------------------------------------------------------------


def test_the_timer_off_by_default_leaves_the_scheduler_deadline_alone(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert live(st).timer is None


def test_with_the_timer_enabled_startup_starts_it_and_a_failed_tick_is_a_visible_fault(
    tmp_path: Path,
) -> None:
    st = make(tmp_path, settings={"sensors.sweep_timer_enabled": True})
    state = live(st)
    try:
        assert state.timer is not None and state.timer._thread is not None  # started once
        assert health_code(st) is None
        state.timer._tick()  # one tick is one unit of work and changes nothing for a healthy hub
        assert health_code(st) is None
        state.timer._on_failure()  # a failed tick is announced, never silence
        assert health_code(st) == SCHEDULER_LAG
    finally:
        state.timer.stop()


def test_a_failed_tick_without_a_health_port_does_not_raise_in_the_timer_thread(tmp_path: Path) -> None:
    st = make(tmp_path, settings={"sensors.sweep_timer_enabled": True})
    state = live(st)
    try:
        state.runtime.faults = None
        state.timer._on_failure()
    finally:
        state.timer.stop()


def test_a_timer_that_was_never_started_stops_cleanly() -> None:
    from crew_health.sensors.adapters.timer import SweepTimer

    timer = SweepTimer(lambda: None, lambda: None, period_s=1)
    timer.stop()
    timer.start()
    timer.start()  # a second start does not spawn a second loop
    timer.stop()
    with pytest.raises(ValueError, match="bound"):
        SweepTimer(lambda: None, lambda: None, period_s=0)


# --- before mission init (T-E23-073) ----------------------------------------------------------------------


def test_before_mission_init_capture_works_and_status_reads_fail_loud_and_retryable(tmp_path: Path) -> None:
    st = make(tmp_path, init=False)
    st.advance(seconds=60)
    kept = push(st, "key_00001", [co2(st, 1)])
    assert kept.status_code == 200 and kept.json()["accepted"] == 1  # capture needs no environment
    for path in ("/v1/sensors", "/v1/sensors/co2", "/v1/sensors/co2/series"):
        reply = st.client.get(path, headers=st.headers("crew_a"))
        assert reply.status_code == 500 and reply.json()["retryable"] is True, path
    assert st.sweep() == 0  # no environment: nothing is judged, nothing is invented


# --- unknown and unmeasured things (T-E23-025, AGENTS 5) ------------------------------------------------


def test_an_unknown_sensor_type_and_a_type_with_no_channel_are_not_found(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert st.client.get("/v1/sensors/not_a_type", headers=st.headers("crew_a")).status_code == 404
    assert st.client.get("/v1/sensors/not_a_type/series", headers=st.headers("crew_a")).status_code == 404
    no_channel = st.client.get("/v1/sensors/rad/series", headers=st.headers("crew_a"))
    assert no_channel.status_code == 404  # a type nobody measures has no series, never a default


def test_a_series_above_its_point_bound_is_refused_never_truncated(tmp_path: Path) -> None:
    st = make(tmp_path, settings={"sensors.series_max_points": 2})
    rows = [co2(st, n, ago=10 - n) for n in range(1, 5)]
    assert push(st, "key_00002", rows).json()["accepted"] == 4
    reply = st.client.get("/v1/sensors/co2/series", headers=st.headers("crew_a"))
    assert reply.status_code == 422 and reply.json()["code"] == "INVALID_VALUE"


def test_malformed_rows_are_rejected_by_index_and_an_oversized_row_keeps_only_its_hash(
    tmp_path: Path,
) -> None:
    st = make(tmp_path)
    good = co2(st, 1)
    huge = {**co2(st, 2), "unexpected": "x" * 100000}
    bad_pressure = {**co2(st, 3), "pressure": {"value": "not a number"}}
    reply = push(st, "key_00003", [good, "not an object", huge, bad_pressure, 7])
    body = reply.json()
    assert reply.status_code == 200 and body["accepted"] == 1 and len(body["rejected"]) == 4
    (stored,) = rows_of(st, "SELECT rejected_json FROM sensors_batch")[0]
    assert "x" * 1000 not in stored and "sha256" in stored  # bounded raw provenance: the hash, never the row


# --- stale never clears; schedule lost; clock jump (T-E23-053, T-E23-019, AGENTS 6) ----------------------


def freshness(st: Station) -> str:
    items = st.client.get("/v1/sensor-channels", headers=st.headers("crew_a")).json()["items"]
    return str(items[0]["freshness"])


def test_a_stale_channel_stays_stale_for_a_late_arriving_old_reading_and_a_sweep(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert push(st, "key_00004", [co2(st, 1)]).status_code == 200
    st.advance(seconds=900)
    st.dispatch()
    st.sweep()
    assert freshness(st) == "stale"
    old = co2(st, 2, ago=800)  # observed long ago, arriving now: freshness is observation time (D3)
    assert push(st, "key_00005", [old]).status_code == 200
    assert freshness(st) == "stale"
    st.sweep()
    assert freshness(st) == "stale"  # a delayed reading never clears it, and neither does a sweep


def test_the_sweep_notices_a_lost_deadline_marks_lag_reschedules_and_clears_the_mark(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert push(st, "key_00006", [co2(st, 1)]).status_code == 200
    assert rows_of(st, "SELECT count(*) FROM platform_schedule")[0][0] == 1
    connection = sqlite3.connect(st.db.path)
    try:
        connection.execute("DELETE FROM platform_schedule")  # the primary path is gone
        connection.commit()
    finally:
        connection.close()
    st.sweep()
    assert health_code(st) == SCHEDULER_LAG  # the backup says so out loud
    st.sweep()
    assert health_code(st) is None  # the deadline is back, so the notice is lifted


def test_an_overdue_deadline_is_noticed_even_while_the_job_row_still_exists(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert push(st, "key_00007", [co2(st, 1)]).status_code == 200
    rt = runtime(st)
    assert rt.limits.scheduler_lag_max_s >= 1
    st.advance(seconds=rt.limits.scheduler_lag_max_s + 600)  # the scheduler stalled; the sweep is not stalled
    st.sweep()
    assert freshness(st) == "stale"
    assert health_code(st) == SCHEDULER_LAG


def test_a_deadline_job_for_a_removed_channel_or_another_environment_does_nothing(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert push(st, "key_00008", [co2(st, 1)]).status_code == 200
    rt = runtime(st)
    before = rows_of(st, "SELECT count(*) FROM platform_schedule")
    with st.begin() as uow:
        freshness_job(rt, uow, SimpleNamespace(payload={"channel_id": "ch_gone"}))  # type: ignore[arg-type]
        status_job(rt, uow, SimpleNamespace(payload={"sensor_type_id": "co2", "environment_id": "x"}))  # type: ignore[arg-type]
    assert rows_of(st, "SELECT count(*) FROM platform_schedule") == before


def test_a_channel_outside_the_active_environment_is_skipped_by_the_sweep(tmp_path: Path) -> None:
    other = {"channels": [channel("ch_far", "co2", "co2", "SIM", "mmHg", environment_ids=["lunar_surface"])]}
    st = build_station(tmp_path, registry=other)
    st.initialize("SIM")
    st.advance(seconds=60)
    st.sweep()
    assert health_code(st) is None
    st.advance(seconds=3600)
    st.sweep()
    assert health_code(st) is None
    assert rows_of(st, "SELECT count(*) FROM events_outbox WHERE event_type = 'sensor.quiet'")[0][0] == 0


# --- the evaluator refuses what it cannot judge (T-E23-060, AGENTS 5) -----------------------------------


def kernel_input(ref: str, release_id: str) -> Any:
    return SimpleNamespace(input_ref=ref, release_id=release_id)


def test_the_evaluator_refuses_an_input_that_was_never_stored_and_writes_nothing(tmp_path: Path) -> None:
    st = make(tmp_path)
    rt = runtime(st)
    before = rows_of(st, "SELECT count(*) FROM sensors_evaluation")
    with pytest.raises(ConfigInconsistent, match="not stored"):
        with st.begin() as uow:
            SensorEvaluator(rt).evaluate(uow, kernel_input("missing_ref", uow.release.release_id))
    assert rows_of(st, "SELECT count(*) FROM sensors_evaluation") == before


def test_an_input_of_a_channel_outside_the_active_environment_produces_no_evaluation(tmp_path: Path) -> None:
    other = {"channels": [channel("ch_far", "co2", "co2", "SIM", "mmHg", environment_ids=["lunar_surface"])]}
    st = build_station(tmp_path, registry=other)
    st.initialize("SIM")
    st.advance(seconds=60)
    far = reading("ch_far", 1, 2.5, "mmHg", st.clock.at - timedelta(seconds=3))
    assert push(st, "key_00009", [far]).status_code == 200
    assert (
        rows_of(st, "SELECT count(*) FROM sensors_evaluation")[0][0] == 0
    )  # never judged under another place


def test_a_conflicting_instant_is_kept_as_two_readings_and_evaluated_without_a_stored_default(
    tmp_path: Path,
) -> None:
    st = make(tmp_path)
    at = st.clock.at - timedelta(seconds=3)
    pair = [reading("ch_co2", 1, 2.5, "mmHg", at), reading("ch_co2", 2, 3.5, "mmHg", at)]
    assert push(st, "key_00010", pair).status_code == 200
    assert rows_of(st, "SELECT count(*) FROM sensors_reading")[0][0] == 2


# --- refused commands (T-E23-051, T-E23-052) --------------------------------------------------------------


def test_a_command_under_another_release_is_refused_whole_and_stores_nothing(tmp_path: Path) -> None:
    st = make(tmp_path)
    reply = push(st, "key_00011", [co2(st, 1)], **{"Config-Release": "release_other"})
    assert reply.status_code == 409 and reply.json()["code"] == "CONFIG_VERSION_MISMATCH"
    assert rows_of(st, "SELECT count(*) FROM sensors_reading")[0][0] == 0
    assert push(st, "key_00011", [co2(st, 1)]).status_code == 200  # the same key still works afterwards


def test_a_retried_key_after_acknowledgement_is_a_duplicate_and_adds_no_reading(tmp_path: Path) -> None:
    st = make(tmp_path)
    assert push(st, "key_00012", [co2(st, 1)]).status_code == 200
    again = push(st, "key_00012", [co2(st, 1)])
    assert again.status_code == 409 and again.json()["code"] == "DUPLICATE_COMMAND"
    assert rows_of(st, "SELECT count(*) FROM sensors_reading")[0][0] == 1


def test_a_session_that_is_not_the_identified_actor_is_refused_before_any_unit_of_work() -> None:
    from crew_health.sensors.application.commands import execute

    session = SimpleNamespace(actor=SimpleNamespace(id="adp_a"))
    identity = SimpleNamespace(actor_ref="adp_b")
    platform = SimpleNamespace(uows=None)  # never reached
    error = refused(lambda: execute(platform, session, identity, lambda uow: None, release_header=None))  # type: ignore[arg-type]
    assert error.code == ErrorCode.UNAUTHORIZED_SCOPE


def test_a_domain_error_inside_a_command_becomes_its_problem_and_rolls_everything_back(
    tmp_path: Path,
) -> None:
    from crew_health.kernel.commands import CommandIdentity
    from crew_health.sensors.application.commands import Platform, execute
    from crew_health.sensors.domain.errors import InvalidValue

    st = make(tmp_path)
    session = st.security.tokens["adapter"]
    identity = CommandIdentity("domainerr1", ADAPTER, "ingestReadings", ADAPTER, "0" * 64, "1" * 64, "sha256")
    resolver = st.resolver()
    from crew_health.kernel.commands import CommandReceipts, IdempotencyStore
    from crew_health.kernel.uow import UnitOfWorkFactory

    platform = Platform(
        uows=resolver.require(UnitOfWorkFactory),
        ledger=resolver.require(IdempotencyStore),
        receipts=resolver.require(CommandReceipts),
    )

    def apply(uow: Any) -> Any:
        raise InvalidValue("nope")

    error = refused(lambda: execute(platform, session, identity, apply, release_header=None))
    assert error.code == ErrorCode.INVALID_VALUE
    assert rows_of(st, "SELECT count(*) FROM platform_command_ledger")[0][0] == 0


# --- bounded ports and the demo registry fence (T-E23-073, T-E23-070, AGENTS 2) --------------------------


def test_a_missing_converter_or_isolation_or_release_reader_is_a_retryable_refusal_never_a_default(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    st = make(tmp_path)
    bare = replace(runtime(st), converter=None, isolation=None, release_reader=None)
    for call in (bare.need_converter, bare.need_isolation):
        error = refused(call)
        assert error.code == ErrorCode.INTERNAL_ERROR and error.retryable is True
    with st.begin() as uow:
        pinned = refused(lambda: bare.release_of(uow, "release_other"))
        assert (
            bare.release_of(uow, uow.release.release_id) is uow.release
        )  # the unit's own release needs no reader
    assert pinned.code == ErrorCode.CONFIG_INVALID and pinned.retryable is True


def test_the_series_port_refuses_an_unknown_channel_and_a_limit_outside_its_bound(tmp_path: Path) -> None:
    from crew_health.kernel.uow import ReadSnapshotFactory
    from crew_health.sensors.application.providers import SensorProviders
    from crew_health.sensors.domain.errors import InvalidValue

    st = make(tmp_path)
    providers = SensorProviders(runtime(st), st.resolver().require(ReadSnapshotFactory))
    for channel_id, limit in (
        ("ch_nope", 5),
        ("ch_co2", 0),
        ("ch_co2", runtime(st).limits.series_max_points + 1),
    ):
        with pytest.raises(InvalidValue):
            providers.accepted(channel_id, until=st.clock.at, limit=limit)
    assert providers.accepted("ch_co2", until=st.clock.at, limit=5) is not None


def test_the_demo_registry_loads_only_in_the_demo_profile_for_a_sim_or_replay_mission(tmp_path: Path) -> None:
    from dataclasses import replace

    from crew_health.sensors.application.activation import SensorActivation

    demo = tmp_path / "demo.json"
    demo.write_text(
        json.dumps({"channels": [channel("ch_demo", "co2", "co2", "SIM", "mmHg")]}), encoding="utf-8"
    )
    st = make(tmp_path / "hub")
    rt = runtime(st)
    cases = (
        ("", "SIM", 0),  # not the demo profile
        ("demo", "LIVE", 0),  # a LIVE mission never loads demo channels
        ("demo", "SIM", 1),
    )
    for profile, mode, expected in cases:
        with st.begin() as uow:
            SensorActivation(replace(rt, deployment_profile=profile, demo_registry_path=str(demo)))._demo(
                uow, mode
            )
        loaded = rows_of(st, "SELECT count(*) FROM sensors_channel WHERE channel_id = 'ch_demo'")[0][0]
        assert loaded == expected, (profile, mode)
    with st.begin() as uow:  # no path configured: nothing loads and nothing is invented
        SensorActivation(replace(rt, deployment_profile="demo", demo_registry_path=""))._demo(uow, "SIM")


def test_an_activation_before_any_mission_exists_changes_nothing(tmp_path: Path) -> None:
    from dataclasses import replace

    from crew_health.sensors.application.activation import SensorActivation

    st = make(tmp_path)
    before = rows_of(st, "SELECT count(*) FROM sensors_environment")
    with st.begin() as uow:
        SensorActivation(replace(runtime(st), mission=None)).on_activated(uow, SimpleNamespace())  # type: ignore[arg-type]
    assert rows_of(st, "SELECT count(*) FROM sensors_environment") == before


def test_an_unreadable_registry_file_is_audited_and_changes_nothing(tmp_path: Path) -> None:
    from crew_health.sensors.application.registry import install_file

    st = make(tmp_path)
    before = rows_of(st, "SELECT count(*) FROM sensors_channel")
    with st.begin() as uow:
        assert install_file(runtime(st), uow, str(tmp_path / "absent.json"), source="registry") == 0
    assert rows_of(st, "SELECT count(*) FROM sensors_channel") == before
    assert (
        rows_of(
            st, "SELECT count(*) FROM platform_audit WHERE envelope LIKE '%sensors.registry_unreadable%'"
        )[0][0]
        == 1
    )
