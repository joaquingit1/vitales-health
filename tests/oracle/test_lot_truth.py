"""Self-tests of the lot truth helper (tests/oracle/lot_truth.py) without engine code."""

import math
from typing import Any, cast

import pytest

from pharmacy.config import ConfigBundle
from pharmacy.types import LotDoc, PackageDoc, StabilityModel, StorageZoneDoc, iso_to_seconds
from tests.oracle.draws import EA, LN_K, Z_RH, Z_T, B, OracleDraw
from tests.oracle.lot_truth import LotWindow, TruthNotModelledError, lot_true_potency, lot_windows
from tests.oracle.precision import R_FLOAT


def _lot(custody: list[tuple[str, str, str | None]], packages: list[tuple[str, str]]) -> LotDoc:
    return cast(
        LotDoc,
        cast(
            Any,
            {
                "lot_id": "lot-test",
                "product_id": "p",
                "custody": [{"zone_id": z, "from_utc": a, "to_utc": b} for z, a, b in custody],
                "package_history": [{"package_id": p, "from_utc": a} for p, a in packages],
                "initial_assay": [],
            },
        ),
    )


def test_lot_windows_intersect_custody_and_packages() -> None:
    lot = _lot(
        [
            ("ground_storage", "2028-01-01T00:00:00Z", "2028-01-10T00:00:00Z"),
            ("moon", "2028-01-10T00:00:00Z", None),
        ],
        [("bottle", "2028-01-01T00:00:00Z"), ("bag", "2028-01-05T00:00:00Z")],
    )
    start = iso_to_seconds("2028-01-01T00:00:00Z")
    end = iso_to_seconds("2028-02-01T00:00:00Z")
    windows = lot_windows(lot, start, end)
    day = 86400.0
    assert windows == [
        LotWindow("ground_storage", "bottle", start, start + 4 * day),
        LotWindow("ground_storage", "bag", start + 4 * day, start + 9 * day),
        LotWindow("moon", "bag", start + 9 * day, end),
    ]


class _Range:
    temperature_min_k = 288.15
    temperature_max_k = 303.15
    rh_min_percent = 0.0
    rh_max_percent = 100.0


class _Block:
    names = ("ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh")
    mean = (-18.0, 90000.0, 0.03)
    covariance = ((0.1, 0.0, 0.0), (0.0, 1e6, 0.0), (0.0, 0.0, 1e-4))


class _Pathway:
    pathway_id = "x:hydrolysis"
    driver = "thermal"
    role = "destructive"
    parameters = _Block()
    validated_range = _Range()


class _Model:
    rate_law_kind = "F1"
    order_n = None
    alpha0 = None
    avrami_n = None
    reference_temperature_k = 298.15
    reference_rh_percent = 40.0
    pathways: tuple[Any, ...] = (_Pathway(),)


def _draw(c0: float = 100.0, z_t: float = 0.0) -> OracleDraw:
    return OracleDraw(
        c0,
        {"x:hydrolysis": {LN_K: -18.0, EA: 90000.0, B: 0.03, Z_T: z_t, Z_RH: 0.0}},
        {"moon_base_pharmacy": 0.2},
        {"moon_base_pharmacy": 0.0},
        {"moon_base_pharmacy": 0.0},
    )


def _zones_packages(bundle: ConfigBundle) -> tuple[dict[str, StorageZoneDoc], dict[str, PackageDoc]]:
    return (
        {z["zone_id"]: z for z in bundle.zones["zones"]},
        {p["package_id"]: p for p in bundle.packages["packages"]},
    )


def _k(temperature: float, rh: float, ln_m: float = 0.0) -> float:
    return math.exp(-18.0 + ln_m - 90000.0 / R_FLOAT * (1 / temperature - 1 / 298.15) + 0.03 * (rh - 40.0))


def test_constant_declared_zone_in_an_open_package_is_closed_form(bundle: ConfigBundle) -> None:
    zones, packages = _zones_packages(bundle)
    lot = _lot(
        [("ground_storage", "2028-01-01T00:00:00Z", None)], [("original_vial", "2028-01-01T00:00:00Z")]
    )
    start = iso_to_seconds("2028-01-01T00:00:00Z")
    times = [start + 30 * 86400.0, start + 200 * 86400.0]
    truth = lot_true_potency(
        cast(StabilityModel, _Model()), _draw(98.0), lot, {}, zones, packages, bundle.settings, times
    )
    declared = zones["ground_storage"]["declared_conditions"]
    assert declared is not None
    t_prior, rh_prior = declared["temperature_k"], declared["relative_humidity_percent"]
    assert t_prior is not None
    assert rh_prior is not None
    k = _k(t_prior["mu"], rh_prior["mu"])
    for t, theta, potency in zip(times, truth.theta, truth.potency_total_percent, strict=True):
        assert math.isclose(theta, k * (t - start), rel_tol=1e-12)
        assert math.isclose(potency, 98.0 * math.exp(-k * (t - start)), rel_tol=1e-12)


def test_window_split_with_same_package_carries_internal_rh(bundle: ConfigBundle) -> None:
    """A custody change between two zones with identical truth must not change Theta: the
    package's internal RH carries over and the zone multiplier is the same."""
    zones, packages = _zones_packages(bundle)
    day = 86400.0
    start = iso_to_seconds("2028-01-01T00:00:00Z")
    ts = [start + i * 3600.0 for i in range(24 * 60 + 1)]
    temps = [296.0 + 2.0 * math.sin(i / 50.0) for i in range(len(ts))]
    rhs = [45.0 + 15.0 * math.sin(i / 70.0) for i in range(len(ts))]
    truth = {"moon_base_pharmacy": (ts, temps, rhs), "mars_base_pharmacy": (ts, temps, rhs)}
    draw = OracleDraw(
        100.0,
        {"x:hydrolysis": {LN_K: -18.0, EA: 90000.0, B: 0.03, Z_T: 0.0, Z_RH: 0.0}},
        {"moon_base_pharmacy": 0.2, "mars_base_pharmacy": 0.2},
        {},
        {},
    )
    one = _lot(
        [("moon_base_pharmacy", "2028-01-01T00:00:00Z", None)], [("original_bottle", "2028-01-01T00:00:00Z")]
    )
    two = _lot(
        [
            ("moon_base_pharmacy", "2028-01-01T00:00:00Z", "2028-01-21T07:00:00Z"),
            ("mars_base_pharmacy", "2028-01-21T07:00:00Z", None),
        ],
        [("original_bottle", "2028-01-01T00:00:00Z")],
    )
    times = [start + 50 * day]
    model = cast(StabilityModel, _Model())
    a = lot_true_potency(model, draw, one, truth, zones, packages, bundle.settings, times)
    b = lot_true_potency(model, draw, two, truth, zones, packages, bundle.settings, times)
    assert a.halving_relative_error < 1e-6
    assert b.halving_relative_error < 1e-6
    assert math.isclose(a.theta[0], b.theta[0], rel_tol=3e-6)


def test_extrapolation_widening_enters_above_the_validated_range(bundle: ConfigBundle) -> None:
    zones, packages = _zones_packages(bundle)
    start = iso_to_seconds("2028-01-01T00:00:00Z")
    ts = [start, start + 86400.0]
    truth = {"moon_base_pharmacy": (ts, [313.15, 313.15], [40.0, 40.0])}
    lot = _lot(
        [("moon_base_pharmacy", "2028-01-01T00:00:00Z", None)], [("original_vial", "2028-01-01T00:00:00Z")]
    )
    model = cast(StabilityModel, _Model())
    plain = lot_true_potency(model, _draw(), lot, truth, zones, packages, bundle.settings, [ts[1]])
    wide = lot_true_potency(model, _draw(z_t=1.5), lot, truth, zones, packages, bundle.settings, [ts[1]])
    sd_t = bundle.settings["extrapolation"]["ln_k_sd_per_kelvin"]["value"]
    assert math.isclose(wide.theta[0] / plain.theta[0], math.exp(1.5 * sd_t * 10.0), rel_tol=1e-12)
    assert math.isclose(plain.theta[0], _k(313.15, 40.0, 0.2) * 86400.0, rel_tol=1e-12)


def test_racemization_is_refused(bundle: ConfigBundle) -> None:
    zones, packages = _zones_packages(bundle)

    class _Racemization(_Pathway):
        role = "racemization"

    class _RacemicModel(_Model):
        pathways: tuple[Any, ...] = (_Pathway(), _Racemization())

    lot = _lot(
        [("ground_storage", "2028-01-01T00:00:00Z", None)], [("original_vial", "2028-01-01T00:00:00Z")]
    )
    with pytest.raises(TruthNotModelledError):
        lot_true_potency(
            cast(StabilityModel, _RacemicModel()),
            _draw(),
            lot,
            {},
            zones,
            packages,
            bundle.settings,
            [iso_to_seconds("2028-02-01T00:00:00Z")],
        )
