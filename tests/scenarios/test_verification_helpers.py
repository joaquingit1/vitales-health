"""Fast regressions for independent validation infrastructure."""

import copy
import dataclasses

import numpy as np
import pytest

from pharmacy import uncertainty
from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError, InputError
from tests.scenarios.parallel import ordered_map, worker_count
from tests.scenarios.predictive import capture_draws, measurement_interval
from tests.validation.products import declared_station_zone, flight_zone, with_flight_condition


def _seeded_task(r: int) -> tuple[int, float]:
    return r, float(np.random.default_rng(20261114 + r).standard_normal())


def test_parallel_results_are_independent_of_worker_count(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [0, 1, 2, 3]
    monkeypatch.setenv("PHARMACY_TEST_WORKERS", "1")
    serial = ordered_map(_seeded_task, items, label="serial determinism")
    monkeypatch.setenv("PHARMACY_TEST_WORKERS", "2")
    parallel = ordered_map(_seeded_task, items, label="parallel determinism")
    assert serial == parallel
    assert [r for r, _ in parallel] == items


@pytest.mark.parametrize("value", ["0", "-1", "bad"])
def test_worker_count_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PHARMACY_TEST_WORKERS", value)
    with pytest.raises(InputError, match="positive integer"):
        worker_count()


def test_measurement_interval_adds_own_error_without_changing_draws(bundle: ConfigBundle) -> None:
    settings = copy.deepcopy(bundle.settings["monte_carlo"])
    settings["min_batches"]["value"] = settings["max_batches"]["value"] = 1

    def evaluate(r: int, rng: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.full(10000, 100.0), None, {})

    with capture_draws() as draws:
        result = uncertainty.run_adaptive(evaluate, 123, settings)
    assert draws == [result]
    plain = measurement_interval(result, 0.0, 0.9, "measurement")
    noisy = measurement_interval(result, 3.0, 0.9, "measurement")
    assert plain.low == plain.high == 100.0
    assert noisy.low < 96.0
    assert noisy.high > 104.0
    assert noisy == measurement_interval(result, 3.0, 0.9, "measurement")
    assert np.all(result.values == 100.0)
    weighted = dataclasses.replace(result, log_weights=np.zeros(10000))
    assert noisy == measurement_interval(weighted, 3.0, 0.9, "measurement")
    for sd in [-1.0, float("nan"), float("inf")]:
        with pytest.raises(InputError, match="finite and nonnegative"):
            measurement_interval(result, sd, 0.9, "measurement")


def test_flight_zone_requires_provenance_and_tracks_its_statistics(bundle: ConfigBundle) -> None:
    bundle = copy.deepcopy(bundle)
    zone = next(z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit")
    zone["rate_multiplier_calibration"] = None
    with pytest.raises(ConfigReferenceError, match="CX-CAL"):
        flight_zone(bundle)
    zone["rate_multiplier_calibration"] = {
        "basis": "derived",
        "source_ids": ["MDP01"],
        "to_be_set_by": None,
        "note": "test metadata",
        "method": "test",
        "vector_id": "V1",
        "rows": [1],
        "engine_version": "test",
        "statistics": [
            {"name": name, "value": value, "unit": unit}
            for name, value, unit in (
                ("flight_temperature_k", 297.0, "K"),
                ("flight_temperature_sd_k", 1.25, "K"),
                ("flight_rh_percent", 24.0, "percent"),
                ("flight_rh_sd_percent", 0.5, "percent"),
            )
        ],
    }
    declared = flight_zone(bundle)["declared_conditions"]
    assert declared is not None
    t, rh = declared["temperature_k"], declared["relative_humidity_percent"]
    assert t is not None
    assert rh is not None
    assert (t["mu"], t["sigma"], rh["mu"], rh["sigma"]) == (297.0, 1.25, 24.0, 0.5)


def test_declared_station_keeps_zone_identity_and_multiplier(bundle: ConfigBundle) -> None:
    station = next(z for z in bundle.zones["zones"] if z["zone_id"] == "station_kit")
    declared = declared_station_zone(bundle)
    assert declared["zone_id"] == station["zone_id"]
    assert declared["rate_multiplier"] == station["rate_multiplier"]
    assert declared["rate_multiplier_calibration"] == station.get("rate_multiplier_calibration")
    if station.get("rate_multiplier_calibration") is not None:
        changed = with_flight_condition(bundle)
        flight = next(z for z in changed.zones["zones"] if z["zone_id"] == "station_kit")
        assert flight["kind"] == "declared"
        assert flight["rate_multiplier"] == station["rate_multiplier"]
        assert station["kind"] == "sensed"
