"""A configuration edit changes what the simulator emits with no code change; a bad document is refused."""

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health_sim_habitat.app import Loaded, load_all
from crew_health_sim_habitat.errors import ConfigError
from crew_health_sim_habitat.runner import RunReport, WireMapper, build_devices, simulate, wire_readings

from conftest import CATALOG, PROJECT, REPO_ROOT

START = datetime(2026, 11, 14, tzinfo=UTC)
CONFIG = PROJECT / "config"


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(CONFIG, target)
    return target


def _edit(path: Path, change: object) -> None:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    assert callable(change)
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _channels(loaded: Loaded, device_id: str, minutes: float) -> list[tuple[str, datetime]]:
    scenario = replace(loaded.scenario("station_nominal"), duration_s=minutes * 60.0)
    devices = build_devices(scenario, loaded.wiring, loaded.kinds)
    mapper = WireMapper(loaded.wiring, START, devices, pressure_max_age_s=10.0)
    report = RunReport()
    out: list[tuple[str, datetime]] = []
    for tick in simulate(scenario, devices):
        if any(r.device_id == device_id for r in tick.readings):
            out.extend((w.channel_id, w.observed_at) for w in wire_readings(mapper, tick, report))
    return out


def _co2_times(loaded: Loaded) -> list[datetime]:
    return [t for channel, t in _channels(loaded, "sim_ndir_co2_cabin", 2) if channel.startswith("ch_co2")]


def test_editing_the_co2_interval_changes_the_cadence(tmp_path: Path) -> None:
    config = _copy(tmp_path)
    before = _co2_times(load_all(REPO_ROOT, config, CATALOG))
    _edit(config / "devices.json", lambda d: d["device_kinds"]["ndir_co2"]["interval"].update(value=10))
    after = _co2_times(load_all(REPO_ROOT, config, CATALOG))
    assert (before[1] - before[0]).total_seconds() == 5
    assert (after[1] - after[0]).total_seconds() == 10
    assert len(after) < len(before)


def test_editing_the_binding_and_registry_changes_the_channel(tmp_path: Path) -> None:
    config = _copy(tmp_path)
    renamed = "ch_co2_galley_sim"

    def rename_channel(data: dict[str, list[dict[str, object]]]) -> None:
        for channel in data["channels"]:
            if channel["channel_id"] == "ch_co2_cabin_sim":
                channel["channel_id"] = renamed

    def rebind(data: dict[str, object]) -> None:
        bindings = data["bindings"]
        assert isinstance(bindings, list)
        for binding in bindings:
            if binding["channel_id"] == "ch_co2_cabin_sim":
                binding["channel_id"] = renamed
        data["channel_registry_path"] = str(config / "sensor-channels.sim.json")

    _edit(config / "sensor-channels.sim.json", rename_channel)
    _edit(config / "feeds.json", rebind)
    channels = {c for c, _ in _channels(load_all(REPO_ROOT, config, CATALOG), "sim_ndir_co2_cabin", 1)}
    assert renamed in channels
    assert "ch_co2_cabin_sim" not in channels


def test_a_corrupt_document_is_refused(tmp_path: Path) -> None:
    config = _copy(tmp_path)
    (config / "scenarios.json").write_text('{"scenarios": ', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_all(REPO_ROOT, config, CATALOG)


def test_a_value_in_the_wrong_unit_is_refused(tmp_path: Path) -> None:
    config = _copy(tmp_path)
    _edit(
        config / "devices.json",
        lambda d: d["device_kinds"]["ec_co"]["outputs"]["co"]["noise_abs"].update(unit="ppb"),
    )
    with pytest.raises(ConfigError, match="must be in 'ppm'"):
        load_all(REPO_ROOT, config, CATALOG)


def test_a_catalog_that_no_longer_accepts_the_unit_is_refused(tmp_path: Path) -> None:
    catalog = tmp_path / "sensor-catalog.json"
    shutil.copyfile(CATALOG, catalog)

    def drop_ppm(data: dict[str, list[dict[str, object]]]) -> None:
        for sensor in data["sensor_types"]:
            if sensor["id"] == "co2":
                units = sensor["accepted_units"]
                assert isinstance(units, list)
                sensor["accepted_units"] = [u for u in units if u["unit"] != "ppm"]

    _edit(catalog, drop_ppm)
    with pytest.raises(ConfigError, match="not an accepted unit"):
        load_all(REPO_ROOT, catalog_path=catalog)


def test_a_fault_on_an_unknown_device_is_refused(tmp_path: Path) -> None:
    config = _copy(tmp_path)

    def bad_fault(data: dict[str, list[dict[str, object]]]) -> None:
        for scenario in data["scenarios"]:
            faults = scenario["device_faults"]
            if scenario["scenario_id"] == "station_sensor_faults" and isinstance(faults, list):
                faults[0]["device_id"] = "sim_missing"

    _edit(config / "scenarios.json", bad_fault)
    with pytest.raises(ConfigError, match="unknown devices"):
        load_all(REPO_ROOT, config, CATALOG)
