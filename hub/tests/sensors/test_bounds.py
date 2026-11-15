"""Every bound is a named hub setting; registry, envelope, timer and fault mechanisms at their limits
(T-E23-070/083/085/092; E23 5.3, 11). Pure mechanisms, no station."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from e23_helpers import ROOT

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.platform.settings import ValidatedSettings
from crew_health.sensors.adapters.config_reader import load_sensor_config
from crew_health.sensors.adapters.sqlite_repository import SqliteSensorStore
from crew_health.sensors.adapters.timer import SweepTimer
from crew_health.sensors.application.commands import problem_of
from crew_health.sensors.application.ingest import check_envelope
from crew_health.sensors.application.registry import RegistryRejected, parse_registry
from crew_health.sensors.application.runtime import FAULT_CODES, Faults
from crew_health.sensors.domain.errors import InvalidValue, SensorDomainError
from crew_health.sensors.settings import DEFAULTS, INTEGERS, specifications

pytestmark = pytest.mark.e23
NAMED = {
    "sensors.batch_max_readings",
    "sensors.aggregate_bucket_s",
    "sensors.window_scan_max_readings",
    "sensors.window_max_buckets",
    "sensors.series_max_points",
    "sensors.page_max",
    "sensors.max_channels",
    "sensors.max_types",
    "sensors.max_limits",
    "sensors.freshness_sweep_s",
    "sensors.adapter_heartbeat_max_s",
    "sensors.adapter_poll_s",
    "sensors.adapter_poll_timeout_s",
    "sensors.adapter_poll_max",
    "sensors.scheduler_lag_max_s",
}


def types() -> Any:
    raw = (ROOT / "config/sensor-catalog.json").read_bytes()
    return load_sensor_config(
        lambda name: raw if name.value == "sensor-catalog" else _other(name)
    ).sensor_types


def _other(name: Any) -> bytes:
    path = ROOT / "config" / f"{name.value}.json"
    return (
        path.read_bytes() if path.is_file() else (ROOT / "docs/product" / f"{name.value}.json").read_bytes()
    )


def item(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "channel_id": "ch_a",
        "sensor_type_id": "co2",
        "hazard_id": "co2",
        "environment_ids": ["station_leo"],
        "location": "cabin",
        "location_label": None,
        "adapter_id": "adp_sim",
        "origin_mode": "SIM",
        "input_unit": "mmHg",
        "expected_interval_seconds": 5,
        "stale_after_seconds": 300,
        "source_label": "Sim",
        "dataset": None,
        "display_note": None,
        "trusted": True,
        "health_url": "http://127.0.0.1:1/h",
    }
    base.update(changes)
    return base


def parse(doc: object, **kw: Any) -> Any:
    raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    limits: dict[str, Any] = {"max_channels": 4, "max_bytes": 100000, "criteria_available": False}
    limits.update(kw)
    return parse_registry(raw, types(), **limits)


@pytest.mark.trace("T-E23-083", "T-E23-085", "AGENTS-3.2")
def test_every_bound_is_a_registered_named_setting_with_a_ceiling_and_a_basis() -> None:
    specs = {s.key: s for s in specifications()}
    assert NAMED <= set(specs) and set(DEFAULTS) == set(specs)
    for key, seed, low in INTEGERS:
        assert specs[key].maximum == Decimal(seed) and specs[key].minimum == Decimal(low) and specs[key].basis
    assert specs["sensors.batch_max_readings"].maximum == 1000  # never above the contract's 1,000
    values = {**DEFAULTS, "sensors.batch_max_readings": 1001}
    with pytest.raises(ProblemError):
        ValidatedSettings(specifications(), values)  # a value above its ceiling is refused at startup


@pytest.mark.trace("T-E23-085", "R35")
def test_the_registry_accepts_a_valid_document_and_names_every_defect_of_an_invalid_one() -> None:
    assert [c.facts.channel_id for c in parse({"channels": [item()]})] == ["ch_a"]
    cases: list[tuple[dict[str, Any], str]] = [
        (item(sensor_type_id="nope"), "not in the active catalog"),
        (item(hazard_id="radiation"), "hazard"),
        (item(input_unit="kPa"), "input_unit"),
        (item(stale_after_seconds=5), "must exceed the interval"),
        (item(expected_interval_seconds=7), "override needs its basis"),
        (item(origin_mode="REPLAY"), "names its dataset"),
        (item(origin_mode="LIVE"), "refused"),
        (item(health_url=None), "health url"),
    ]
    for entry, expected in cases:
        with pytest.raises(RegistryRejected) as caught:
            parse({"channels": [entry]})
        assert expected in " ".join(caught.value.findings), expected
    assert parse({"channels": [item(expected_interval_seconds=7, expected_interval_basis="vendor sheet")]})
    with pytest.raises(RegistryRejected, match="duplicate"):
        parse({"channels": [item(), item()]})


@pytest.mark.trace("T-E23-085")
def test_a_malformed_registry_document_is_rejected_whole() -> None:
    for doc in (b"{", b"[]", b'{"channels": 1}', b'{"channels": [], "extra": 1}', b'{"channels": [3]}'):
        with pytest.raises(RegistryRejected):
            parse(doc)
    with pytest.raises(RegistryRejected, match="more than"):
        parse({"channels": [item(channel_id=f"ch_{n}") for n in range(5)]})
    with pytest.raises(RegistryRejected, match="byte bound"):
        parse({"channels": [item()]}, max_bytes=10)
    with pytest.raises(RegistryRejected, match="fields"):
        parse({"channels": [{"channel_id": "x", "bogus": 1}]})
    with pytest.raises(RegistryRejected, match="SensorChannel"):
        parse({"channels": [item(channel_id="bad id with spaces")]})
    with pytest.raises(RegistryRejected, match="health_url"):
        parse({"channels": [item(health_url="")]})
    ok = parse(
        {"channels": [item(origin_mode="LIVE")]}, criteria_available=True
    )  # sourced criteria would allow LIVE
    assert ok[0].facts.origin_mode == "LIVE"


@pytest.mark.trace("T-E23-092", "R35")
def test_the_batch_envelope_is_bounded_and_shaped_before_any_row() -> None:
    assert check_envelope({"batch_id": "bat_1", "readings": [{}]}, 2) == ("bat_1", [{}])
    for body, code in (
        ([], ErrorCode.MALFORMED_REQUEST),
        ({"batch_id": "bat_1"}, ErrorCode.MALFORMED_REQUEST),
        ({"batch_id": "bat_1", "readings": "x"}, ErrorCode.MALFORMED_REQUEST),
        ({"batch_id": "bad id", "readings": [{}]}, ErrorCode.MALFORMED_REQUEST),
        ({"batch_id": "bat_1", "readings": []}, ErrorCode.MALFORMED_REQUEST),
        ({"batch_id": "bat_1", "readings": [{}] * 3}, ErrorCode.PAYLOAD_TOO_LARGE),
    ):
        with pytest.raises(ProblemError) as caught:
            check_envelope(body, 2)
        assert caught.value.code is code


@pytest.mark.trace("T-E23-089", "S5.10")
def test_the_five_health_codes_degrade_and_recover_and_nothing_else_is_accepted() -> None:
    from crew_health.platform.application.health import ComponentHealthState

    health = ComponentHealthState()
    faults = Faults(health)
    assert FAULT_CODES == {
        "EVALUATION_UNAVAILABLE",
        "SCHEDULER_LAG",
        "INSTANT_CONFLICT",
        "SEQUENCE_CONFLICT",
        "WINDOW_SCAN_BOUND",
    }
    now = datetime(2026, 1, 1, tzinfo=UTC)
    faults.mark("SCHEDULER_LAG", now)
    faults.mark("INSTANT_CONFLICT", now)
    assert health.snapshot().status == "degraded"
    faults.clear("INSTANT_CONFLICT", now)
    assert health.snapshot().components[0].code == "SCHEDULER_LAG"
    faults.clear("SCHEDULER_LAG", now)
    assert health.snapshot().status == "ok"
    with pytest.raises(ValueError):
        faults.mark("SOMETHING_ELSE", now)


@pytest.mark.trace("T-E23-083")
def test_a_failed_sweep_tick_is_announced_and_the_loop_keeps_running() -> None:
    ticks, failures, done = [], [], threading.Event()

    def tick() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("the unit of work failed")
        done.set()

    timer = SweepTimer(tick, lambda: failures.append(1), period_s=1)
    timer.start()
    timer.start()  # a second start is a no-op
    assert done.wait(5)
    timer.stop()
    assert failures == [1] and len(ticks) >= 2
    with pytest.raises(ValueError):
        SweepTimer(tick, lambda: None, period_s=0)


@pytest.mark.trace("AGENTS-3.2")
def test_store_row_limits_stay_inside_storage_max_rows_and_domain_errors_map_to_the_contract() -> None:
    with pytest.raises(ValueError):
        SqliteSensorStore(row_limit=1001, page_rows=10)
    with pytest.raises(ValueError):
        SqliteSensorStore(row_limit=10, page_rows=0)
    assert problem_of(InvalidValue("x")).code is ErrorCode.INVALID_VALUE
    odd = problem_of(SensorDomainError("INTEGRITY_VIOLATION"))
    assert odd.code is ErrorCode.INTERNAL_ERROR and odd.retryable is False
