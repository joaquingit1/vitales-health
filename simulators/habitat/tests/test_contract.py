"""Every payload the simulator emits validates against E23's contract snapshot (commit in SNAPSHOT.json)."""

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from crew_health_sim_habitat.app import Loaded
from crew_health_sim_habitat.payload import WireReading, batch_json
from crew_health_sim_habitat.replay import ReplayCounts, dataset_rows, to_wire
from crew_health_sim_habitat.runner import RunReport, WireMapper, build_devices, simulate, wire_readings

from conftest import E23, REPO_ROOT, Validate, read_json

START = datetime(2026, 11, 14, tzinfo=UTC)
# Long enough to cross every scenario event start (the latest starts at hour 6) and its first hours.
CONTRACT_HOURS = 9.0
BATCH = 1000  # requests.schema.json AdapterReadingBatch maxItems


def test_snapshot_files_are_the_ones_read_from_e23() -> None:
    snapshot = read_json(E23 / "SNAPSHOT.json")
    assert re.fullmatch(r"[0-9a-f]{40}", str(snapshot["commit"]))
    hashes = snapshot["sha256"]
    assert isinstance(hashes, dict)
    for relative, digest in hashes.items():
        assert hashlib.sha256((E23 / relative).read_bytes()).hexdigest() == digest, relative


def _scenario_wire(loaded: Loaded, scenario_id: str) -> list[WireReading]:
    scenario = loaded.scenario(scenario_id)
    scenario = replace(scenario, duration_s=min(scenario.duration_s, CONTRACT_HOURS * 3600.0))
    devices = build_devices(scenario, loaded.wiring, loaded.kinds)
    mapper = WireMapper(loaded.wiring, START, devices, pressure_max_age_s=10.0)
    report = RunReport()
    wire: list[WireReading] = []
    for tick in simulate(scenario, devices):
        wire.extend(wire_readings(mapper, tick, report))
    assert report.missing_pressure == 0
    return wire


def _validate_all(wire: list[WireReading], validate_batch: Validate) -> None:
    for start in range(0, len(wire), BATCH):
        items = [(start + i, r) for i, r in enumerate(wire[start : start + BATCH])]
        validate_batch(batch_json(f"bat_test_{start:09d}", items))


SCENARIOS = (
    "station_nominal",
    "station_scrubber_degradation",
    "station_fire",
    "station_ammonia_leak",
    "station_pressure_leak",
    "station_humidity_loss",
    "station_solar_particle_event",
    "mars_eva_ingress_dust",
    "lunar_nominal",
    "station_sensor_faults",
)


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_every_sim_payload_validates(loaded: Loaded, validate_batch: Validate, scenario_id: str) -> None:
    wire = _scenario_wire(loaded, scenario_id)
    assert wire
    assert {r.origin_mode for r in wire} == {"SIM"}
    _validate_all(wire, validate_batch)


def test_every_scenario_is_covered(loaded: Loaded) -> None:
    assert sorted(SCENARIOS) == sorted(loaded.scenarios)


def test_every_replay_payload_validates(loaded: Loaded, validate_batch: Validate) -> None:
    for feed in loaded.wiring.replay_feeds:
        if feed.channel_id is None:
            continue
        dataset = loaded.wiring.datasets[feed.dataset]
        wire = [
            to_wire(row, feed, "accepted") for row in dataset_rows(REPO_ROOT, dataset, feed, ReplayCounts())
        ]
        assert wire
        assert all(r.origin_mode == "REPLAY" and r.replay_dataset for r in wire)
        _validate_all(wire, validate_batch)


def test_contract_example_batch_shape_matches_ours(validate_batch: Validate) -> None:
    example = read_json(E23 / "examples" / "adapter-batch.json")["value"]
    validate_batch(example)


def test_channel_registry_entries_are_sensor_channels(loaded: Loaded, validate_channel: Validate) -> None:
    registry = read_json(REPO_ROOT / loaded.wiring.registry_path)
    channels = registry["channels"]
    assert isinstance(channels, list)
    for channel in channels:
        assert isinstance(channel, dict)
        validate_channel(
            channel | {"last_received_at": None, "last_observed_at": None, "freshness": "never_reported"}
        )


def test_simulated_devices_declare_themselves_simulated(loaded: Loaded) -> None:
    sim_adapter = loaded.wiring.adapters["SIM"].adapter_id
    assert all(d.device_id.startswith("sim_") for d in loaded.wiring.devices)
    for binding in loaded.wiring.bindings:
        channel = loaded.registry[binding.channel_id]
        assert channel.origin_mode == "SIM"
        assert channel.adapter_id == sim_adapter
        assert channel.channel_id.endswith("_sim")


def test_ingest_result_examples_validate(validate_ingest_result: Validate) -> None:
    for name in ("ingest-result.json", "ingest-result-rejected.json"):
        validate_ingest_result(read_json(E23 / "examples" / name)["value"])
