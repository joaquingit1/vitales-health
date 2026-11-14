"""Unit tests of pharmacy.decision (spec 11): every row of the state table, eligibility
combinations, combination products, reason order, ordering keys and ties, forecast crossing
times with crafted potency functions, and the regulatory helpers (TV-Q1E-1 to -4, TV-Q3B-1,
TV-Q3B-2)."""

import copy
import dataclasses
import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from pharmacy import decision
from pharmacy.config import ConfigBundle, load_bundle
from pharmacy.errors import ComputationError, InputError
from pharmacy.kinetics import FloatArray
from pharmacy.types import (
    DecisionSettings,
    DegradantIdentification,
    ForecastSettings,
    LotDoc,
    Placard,
    PotencyEstimate,
    ProductDoc,
    iso_to_seconds,
)
from tests.unit.core import builders as b

AS_OF = "2028-06-01T00:00:00Z"
AS_OF_S = iso_to_seconds(AS_OF)


@pytest.fixture(scope="module")
def products(bundle: ConfigBundle) -> dict[str, ProductDoc]:
    return {product["product_id"]: product for product in bundle.formulary["products"]}


@pytest.fixture(scope="module")
def lot(bundle: ConfigBundle) -> LotDoc:
    out: Any = copy.deepcopy(bundle.lots["lots"][0])
    out["lot_id"] = "lot-1"
    out["labeled_expiry_utc"] = "2029-01-01T00:00:00Z"
    return out  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def settings(bundle: ConfigBundle) -> DecisionSettings:
    return bundle.settings["decision"]


@pytest.fixture(scope="module")
def forecast_settings(bundle: ConfigBundle) -> ForecastSettings:
    return bundle.settings["forecast"]


def _product(products: dict[str, ProductDoc], product_id: str = "ibuprofen_400_tab", **changes: Any) -> Any:
    out: Any = copy.deepcopy(products[product_id])
    for key, value in changes.items():
        out[key] = value
    return out


def _decide(
    lot: LotDoc,
    product: ProductDoc,
    estimates: list[PotencyEstimate],
    settings: DecisionSettings,
    forecasts: Any = None,
    alternatives: Any = ((), ()),
) -> Any:
    if forecasts is None:
        forecasts = [b.forecast(e.ingredient_id, AS_OF_S + 1e6) for e in estimates]
    return decision.decide_lot(lot, product, estimates, forecasts, alternatives, settings, b.pins())


def _codes(found: Any) -> list[tuple[str, str, str | None]]:
    return [(r.code, r.severity, r.ingredient_id) for r in found.reasons]


FROZEN = Placard("regime_frozen", 2.0, "K", 3600.0, 0.1, "frozen")


# =========================================================================================
# The state table (spec 11.1), one row at a time
# =========================================================================================


def test_in_specification(lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings) -> None:
    product = _product(products, degradant_profile_reviewed=True)
    found = _decide(lot, product, [b.potency_estimate("ibuprofen", 95.0, 102.0)], settings)
    assert found.state == "in_specification"
    assert _codes(found) == [("within_specification", "info", "ibuprofen")]
    assert found.ordering_key.primary == AS_OF_S + 1e6
    assert found.ordering_key.state_rank == 0
    assert found.decision_id == f"lot-1:{AS_OF}:decision"
    assert found.product_id == "ibuprofen_400_tab"
    assert found.adjustment.eligible
    assert not found.past_labeled_expiry


@pytest.mark.parametrize(
    ("q_lo", "q_hi", "state", "code"),
    [
        (90.0, 110.0, "in_specification", "within_specification"),  # limits are inclusive
        (95.0, 110.01, "do_not_use", "above_upper_limit"),
        (89.99, 100.0, "adjust", "below_lower_limit"),
    ],
)
def test_limit_edges(
    q_lo: float,
    q_hi: float,
    state: str,
    code: str,
    lot: LotDoc,
    products: dict[str, ProductDoc],
    settings: DecisionSettings,
) -> None:
    found = _decide(lot, _product(products), [b.potency_estimate("ibuprofen", q_lo, q_hi)], settings)
    assert found.state == state
    assert found.reasons[0].code == code


@pytest.mark.parametrize(("p_above", "blocked"), [(0.051, True), (0.05, False), (None, False)])
def test_degradant_above_limit(
    p_above: float | None,
    blocked: bool,
    lot: LotDoc,
    products: dict[str, ProductDoc],
    settings: DecisionSettings,
) -> None:
    estimate = b.potency_estimate("ibuprofen", 95.0, 102.0, degradants=[("deg_x", p_above, "unknown")])
    estimate = dataclasses.replace(
        estimate,
        degradants=tuple(
            dataclasses.replace(d, identification=DegradantIdentification(True, "anchor", "test", ("MDP01",)))
            for d in estimate.degradants
        ),
    )
    found = _decide(lot, _product(products), [estimate], settings)
    assert found.state == ("do_not_use" if blocked else "in_specification")
    assert ("degradant_above_limit" in [r.code for r in found.reasons]) == blocked


@pytest.mark.parametrize(
    ("do_not_freeze", "placards", "blocked"),
    [(True, [FROZEN], True), (False, [FROZEN], False), (True, [], False)],
)
def test_regime_switch(
    do_not_freeze: bool,
    placards: list[Placard],
    blocked: bool,
    lot: LotDoc,
    products: dict[str, ProductDoc],
    settings: DecisionSettings,
) -> None:
    estimate = b.potency_estimate("ibuprofen", 95.0, 102.0, placards=placards)
    found = _decide(lot, _product(products, do_not_freeze=do_not_freeze), [estimate], settings)
    assert found.state == ("do_not_use" if blocked else "in_specification")
    assert ("regime_switch" in [r.code for r in found.reasons]) == blocked


def test_below_lower_limit_and_eligible_adjusts(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings
) -> None:
    found = _decide(lot, _product(products), [b.potency_estimate("ibuprofen", 88.0, 96.0)], settings)
    assert found.state == "adjust"
    assert found.reasons[0].code == "below_lower_limit"
    assert "is allowed" in found.reasons[0].message
    assert found.ordering_key.primary == -88.0
    assert found.ordering_key.state_rank == 1


def test_below_lower_limit_not_eligible_lists_every_failed_check(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings
) -> None:
    product = _product(
        products, fixed_dose_device=True, potency_adjustment={"allowed": False, "reason": None}
    )
    estimate = b.potency_estimate("ibuprofen", 88.0, 96.0, flags=["coverage_low"])
    found = _decide(lot, product, [estimate], settings)
    assert found.state == "do_not_use"
    assert [r.code for r in found.reasons] == [
        "below_lower_limit",
        "adjustment_not_allowed_by_product",
        "fixed_dose_device",
        "estimate_coverage_low",
    ]  # the unreviewed-profile info reason follows the estimate's flag, which the pipeline sets
    assert found.reasons[1].message.endswith("no reason given.")
    assert all(r.severity == "blocking" for r in found.reasons[:4])
    assert not found.adjustment.eligible
    assert [c.passed for c in found.adjustment.checks] == [False, False, True, True, False]


def test_inspection_is_lot_level_and_first(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings
) -> None:
    estimate = b.potency_estimate("ibuprofen", 95.0, 102.0, inspection="color_change")
    found = _decide(lot, _product(products, degradant_profile_reviewed=True), [estimate], settings)
    assert found.state == "do_not_use"
    assert _codes(found) == [("inspection_found_change", "blocking", None)]
    assert "color_change" in found.reasons[0].message
    assert found.ordering_key.primary is None
    assert found.ordering_key.state_rank == 2


def test_info_reasons_and_expiry(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings
) -> None:
    flags = [
        "computed_only",
        "mc_not_converged",
        "assay_model_conflict",
        "radiosensitive_dose_exceeded",
        "degradant_profile_not_reviewed",
        "assay_reanchored",
    ]
    late = "2029-02-01T00:00:00Z"
    estimate = b.potency_estimate("ibuprofen", 95.0, 102.0, flags=flags, as_of_utc=late)  # type: ignore[arg-type]
    found = _decide(
        lot,
        _product(products),
        [estimate],
        settings,
        forecasts=[b.forecast("ibuprofen", None, as_of_utc=late)],
    )
    assert found.state == "in_specification"
    assert found.past_labeled_expiry
    assert [r.code for r in found.reasons] == [
        "within_specification",
        "computed_only",
        "mc_not_converged",
        "assay_model_conflict",
        "degradant_profile_not_reviewed",
        "radiosensitive_dose_exceeded",
        "past_labeled_expiry",
    ]
    assert all(r.severity == "info" for r in found.reasons)
    assert found.reasons[-1].ingredient_id is None
    assert found.ordering_key.primary is None  # beyond the horizon sorts last


def test_combination_product_takes_the_worst_state(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings
) -> None:
    product = _product(products, "amoxicillin_clavulanate_875_125_tab", degradant_profile_reviewed=True)
    amox = b.potency_estimate("amoxicillin", 95.0, 105.0, limits=(90.0, 120.0), assays=["a2", "a1"])
    clav = b.potency_estimate("clavulanic_acid", 85.0, 125.0, limits=(90.0, 120.0), assays=["a1"])
    found = _decide(lot, product, [clav, amox], settings, alternatives=(("lot-2",), ("other_product",)))
    assert found.state == "do_not_use"
    assert _codes(found) == [
        ("above_upper_limit", "blocking", "clavulanic_acid"),
        ("below_lower_limit", "blocking", "clavulanic_acid"),
        ("within_specification", "info", "amoxicillin"),
    ]
    assert found.estimate_ids == (amox.estimate_id, clav.estimate_id)
    assert found.adjustment.assay_ids_used == ("a1", "a2")
    assert found.adjustment.estimate_bases == (
        ("amoxicillin", "class_prior"),
        ("clavulanic_acid", "class_prior"),
    )
    assert (found.alternative_lot_ids, found.alternative_product_ids) == (("lot-2",), ("other_product",))
    one_low = b.potency_estimate("clavulanic_acid", 85.0, 105.0, limits=(90.0, 120.0))
    assert _decide(lot, product, [amox, one_low], settings).state == "adjust"


def test_decide_lot_errors(lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings) -> None:
    product = _product(products)
    with pytest.raises(InputError):
        _decide(lot, product, [], settings)
    with pytest.raises(InputError):
        _decide(lot, product, [b.potency_estimate("ibuprofen", 95.0, 102.0, lot_id="lot-9")], settings)
    mixed = [
        b.potency_estimate("a", 95.0, 102.0),
        b.potency_estimate("b", 95.0, 102.0, as_of_utc="2028-06-02T00:00:00Z"),
    ]
    with pytest.raises(InputError):
        _decide(lot, product, mixed, settings)
    no_q = b.potency_estimate("ibuprofen", 95.0, 102.0)
    no_q = type(no_q)(**{**{f: getattr(no_q, f) for f in no_q.__dataclass_fields__}, "quantiles": ()})
    with pytest.raises(InputError):
        _decide(lot, product, [no_q], settings)


# =========================================================================================
# Adjustment eligibility (spec 11.2)
# =========================================================================================


@pytest.mark.parametrize(
    ("changes", "flags", "degradant", "failed"),
    [
        ({}, [], None, []),
        (
            {"potency_adjustment": {"allowed": False, "reason": "Injection."}},
            [],
            None,
            ["adjustment_not_allowed_by_product"],
        ),
        ({"fixed_dose_device": True}, [], None, ["fixed_dose_device"]),
        ({}, [], ("deg_t", 0.0, "toxic"), ["toxic_degradant"]),
        ({}, ["outside_validated_range"], None, ["estimate_outside_validated_range"]),
        ({}, ["coverage_low"], None, ["estimate_coverage_low"]),
        ({}, ["assay_model_conflict", "degradant_profile_not_reviewed"], None, []),  # info only
        (
            {"fixed_dose_device": True},
            ["outside_validated_range", "coverage_low"],
            ("deg_t", 0.0, "toxic"),
            [
                "fixed_dose_device",
                "toxic_degradant",
                "estimate_outside_validated_range",
                "estimate_coverage_low",
            ],
        ),
    ],
)
def test_eligibility_combinations(
    changes: dict[str, Any],
    flags: list[Any],
    degradant: Any,
    failed: list[str],
    products: dict[str, ProductDoc],
) -> None:
    estimate = b.potency_estimate(
        "ibuprofen", 95.0, 102.0, flags=flags, degradants=[degradant] if degradant else []
    )
    eligible, checks = decision.adjustment_eligibility(_product(products, **changes), [estimate])
    assert [name for name, _, _ in checks] == [
        "adjustment_not_allowed_by_product",
        "fixed_dose_device",
        "toxic_degradant",
        "estimate_outside_validated_range",
        "estimate_coverage_low",
    ]
    assert [name for name, passed, _ in checks if not passed] == failed
    assert eligible == (not failed)


def test_toxic_degradant_from_the_formulary(products: dict[str, ProductDoc]) -> None:
    eligible, checks = decision.adjustment_eligibility(products["acetaminophen_325_tab"], [])
    assert not eligible
    assert checks[2] == ("toxic_degradant", False, "toxic degradants: 4_aminophenol")


# =========================================================================================
# Ordering (spec 11.5)
# =========================================================================================


def test_ordering_key_branches() -> None:
    estimates = [b.potency_estimate("a", 91.0, 99.0), b.potency_estimate("b", 89.5, 99.0)]
    soon, later = b.forecast("a", AS_OF_S + 10.0), b.forecast("b", AS_OF_S + 50.0)
    assert (
        decision.ordering_key("in_specification", "lot-1", estimates, [later, soon]).primary == AS_OF_S + 10.0
    )
    assert (
        decision.ordering_key("in_specification", "lot-1", estimates, [b.forecast("a", None)]).primary is None
    )
    assert decision.ordering_key("adjust", "lot-1", estimates, [soon]).primary == -89.5
    assert decision.ordering_key("do_not_use", "lot-1", estimates, []).primary is None
    with pytest.raises(InputError):
        decision.ordering_key("adjust", "lot-1", estimates, [])
    with pytest.raises(InputError):
        decision.ordering_key("quarantine", "lot-1", estimates, [])
    with pytest.raises(InputError):
        decision.ordering_key("adjust", "lot-1", estimates, [b.forecast("a", None, probability=0.1)])


def test_sort_lots_orders_states_primaries_and_ties(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings
) -> None:
    product = _product(products)

    def decided(lot_id: str, q_lo: float, q_hi: float, crossing: float | None) -> Any:
        doc: Any = {**lot, "lot_id": lot_id}
        estimate = b.potency_estimate("ibuprofen", q_lo, q_hi, lot_id=lot_id)
        return _decide(
            doc, product, [estimate], settings, forecasts=[b.forecast("ibuprofen", crossing, lot_id=lot_id)]
        )

    lots = [
        decided("z-bad", 95.0, 120.0, None),
        decided("a-bad", 95.0, 120.0, None),
        decided("adj-low", 80.0, 95.0, None),
        decided("adj-high", 89.0, 95.0, None),
        decided("never", 95.0, 102.0, None),
        decided("soon", 95.0, 102.0, AS_OF_S + 100.0),
        decided("late-b", 95.0, 102.0, AS_OF_S + 900.0),
        decided("late-a", 95.0, 102.0, AS_OF_S + 900.0),
    ]
    ordered = [d.lot_id for d in decision.sort_lots(lots)]
    assert ordered == ["soon", "late-a", "late-b", "never", "adj-high", "adj-low", "a-bad", "z-bad"]


# =========================================================================================
# Forecast crossing times (spec 11.4)
# =========================================================================================


def _linear(start: FloatArray, rate: FloatArray) -> Callable[[FloatArray], FloatArray]:
    def potency_at(t: FloatArray) -> FloatArray:
        return np.asarray(start - rate * t, dtype=np.float64)

    return potency_at


def test_crossing_times_statuses(forecast_settings: ForecastSettings) -> None:
    horizon = forecast_settings["horizon_s"]["value"]
    tolerance = forecast_settings["time_tolerance_s"]["value"]
    start = np.array([89.0, 95.0, 95.0, 95.0, 90.0])
    rate = np.array([1e-6, 1e-6, 1e-9, 0.0, 1e-7])
    times = decision.crossing_times(_linear(start, rate), 5, 90.0, forecast_settings)
    exact_1 = 5.0 / 1e-6
    assert times[0] == 0.0  # already below at as_of
    assert exact_1 <= times[1] <= exact_1 + tolerance
    assert times[2] == math.inf
    assert times[3] == math.inf
    assert 0.0 < times[4] <= tolerance  # exactly at the limit: crosses at once
    assert horizon > exact_1


def test_crossing_times_step_function(forecast_settings: ForecastSettings) -> None:
    jump = 123456.789

    def step(t: FloatArray) -> FloatArray:
        return np.where(t >= jump, 80.0, 100.0)

    (found,) = decision.crossing_times(step, 1, 90.0, forecast_settings)
    assert jump <= found <= jump + forecast_settings["time_tolerance_s"]["value"]


def test_crossing_times_errors(forecast_settings: ForecastSettings) -> None:
    good = _linear(np.array([95.0]), np.array([1e-6]))
    with pytest.raises(InputError):
        decision.crossing_times(good, 0, 90.0, forecast_settings)
    with pytest.raises(InputError):
        decision.crossing_times(good, 1, math.nan, forecast_settings)
    broken: Any = copy.deepcopy(forecast_settings)
    broken["time_tolerance_s"]["value"] = 0.0
    with pytest.raises(InputError):
        decision.crossing_times(good, 1, 90.0, broken)
    with pytest.raises(ComputationError):
        decision.crossing_times(lambda t: np.full(2, 95.0), 1, 90.0, forecast_settings)
    with pytest.raises(ComputationError):
        decision.crossing_times(lambda t: np.full(1, math.nan), 1, 90.0, forecast_settings)


# =========================================================================================
# Regulatory helpers (spec 11.6)
# =========================================================================================


@pytest.mark.parametrize("vector_id", ["TV-Q1E-1", "TV-Q1E-2", "TV-Q1E-3", "TV-Q1E-4"])
def test_q1e_vectors(vector_id: str, vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector(vector_id)
    got = decision.q1e_max_shelf_life_months(v["inputs"]["case"], v["inputs"]["x_months"])
    assert got == v["expected"]["y_max_months"]


@pytest.mark.parametrize(
    ("case", "x", "y"),
    [
        ("no_significant_change_accelerated_with_statistics", 6.0, 12.0),
        ("no_significant_change_accelerated_no_statistics", 24.0, 30.0),
        ("no_significant_change_accelerated_no_statistics", 6.0, 9.0),
        ("significant_change_accelerated_none_intermediate_with_statistics", 12.0, 18.0),
        ("refrigerated_no_statistics", 12.0, 15.0),
        ("significant_change_intermediate", 12.0, 12.0),
    ],
)
def test_q1e_every_case(case: decision.Q1eCase, x: float, y: float) -> None:
    assert decision.q1e_max_shelf_life_months(case, x) == y


def test_q1e_errors() -> None:
    with pytest.raises(InputError):
        decision.q1e_max_shelf_life_months("refrigerated_no_statistics", -1.0)
    with pytest.raises(InputError):
        decision.q1e_max_shelf_life_months("no_such_case", 12.0)  # type: ignore[arg-type]


def test_tv_q1e_5_documentation() -> None:
    """TV-Q1E-5 targets the decision documentation: the poolability test order (slopes,
    then intercepts) and its significance level 0.25."""
    text = decision.__doc__ or ""
    assert "0.25" in text
    assert 0 <= text.find("slopes") < text.find("intercepts")


def test_tv_q3b_1_rounding(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-Q3B-1")
    inputs = v["inputs"]
    assert not decision.q3b_exceeds(inputs["total_daily_intake_mg"], inputs["qualification_threshold_tdi_mg"])
    assert v["expected"]["qualification_threshold_exceeded"] is False
    assert decision.q3b_exceeds(3.5, 3)  # half up
    assert not decision.q3b_exceeds(0.154, 0.15)
    assert decision.q3b_exceeds(0.155, 0.15)
    for bad in ((math.nan, 1.0), (1.0, math.inf)):
        with pytest.raises(InputError):
            decision.q3b_exceeds(*bad)


def test_tv_q3b_2_thresholds(vector: Callable[[str], dict[str, Any]]) -> None:
    v = vector("TV-Q3B-2")
    found = decision.q3b_thresholds(
        v["inputs"]["maximum_daily_dose_mg"], settings=load_bundle().settings["q3b"]
    )
    expected = v["expected"]
    tolerance = v["tolerance"]["abs"]
    assert found.reporting_percent == pytest.approx(expected["reporting_threshold_percent"], abs=tolerance)
    assert found.identification_percent == pytest.approx(
        expected["identification_threshold_percent"], abs=tolerance
    )
    assert found.qualification_percent == pytest.approx(
        expected["qualification_threshold_percent"], abs=tolerance
    )
    exact = v["our_recomputation"]
    assert found.identification_percent == exact["foundation_identification_percent_exact"]
    assert found.qualification_percent == exact["foundation_qualification_percent_exact"]
    assert (found.identification_tdi_mg, found.qualification_tdi_mg) == (2.0, 3.0)


@pytest.mark.parametrize(
    ("dose", "reporting", "identification", "qualification"),
    [
        (0.5, 0.1, 1.0, 1.0),
        (1.0, 0.1, 0.5, 1.0),
        (10.0, 0.1, 0.2, 0.5),
        (100.0, 0.1, 0.2, 0.2),
        (1000.0, 0.1, 0.2, 0.2),
        (2000.0, 0.05, 0.1, 0.15),
        (2500.0, 0.05, 0.1, 0.15),
    ],
)
def test_q3b_bands(dose: float, reporting: float, identification: float, qualification: float) -> None:
    found = decision.q3b_thresholds(dose, settings=load_bundle().settings["q3b"])
    assert (found.reporting_percent, found.identification_percent, found.qualification_percent) == (
        reporting,
        identification,
        qualification,
    )


def test_q3b_threshold_errors() -> None:
    for bad in (0.0, -1.0, math.inf):
        with pytest.raises(InputError):
            decision.q3b_thresholds(bad, settings=load_bundle().settings["q3b"])


@pytest.mark.parametrize(("low", "state"), [(95.0, "in_specification"), (88.0, "do_not_use")])
def test_unidentified_degradant_warns_and_forbids_adjustment(
    lot: LotDoc, products: dict[str, ProductDoc], settings: DecisionSettings, low: float, state: str
) -> None:
    estimate = b.potency_estimate("ibuprofen", low, 102.0, degradants=[("unidentified", 0.9, "unknown")])
    result = _decide(lot, _product(products), [estimate], settings)
    assert result.state == state
    warnings = [r for r in result.reasons if r.code == "degradant_unidentified_above_limit"]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"
    assert "Inspect the lot or assay" in warnings[0].message
    assert not result.adjustment.eligible
    assert not any(r.code == "degradant_above_limit" for r in result.reasons)
