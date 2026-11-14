# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Exact-equality functions against the oracle (spec 15.2): derive_seed, jcgm_batch_size,
numerical_tolerance, weighted_quantile, q1e_max_shelf_life_months, q3b_thresholds,
q3b_exceeds, ordering_key and sort_lots."""

import dataclasses
from typing import Any, cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pharmacy.config import ConfigBundle
from pharmacy.types import JSONValue, OrderingKey, QuantileValue
from tests.oracle import exact
from tests.oracle.compare import assert_equal

HYPOTHESIS = settings(derandomize=True, max_examples=300, deadline=None)
JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**12), 10**12),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=12),
)
JSON_VALUES = st.recursive(
    JSON_SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4), st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=20,
)


# --- Seeds (spec 9.8) --------------------------------------------------------------------------
@HYPOTHESIS
@given(material=JSON_VALUES)
def test_derive_seed(material: Any) -> None:
    from pharmacy import uncertainty

    assert_equal(
        "uncertainty.derive_seed",
        {"material": material},
        uncertainty.derive_seed(material),
        exact.derive_seed(material),
        "9.8",
    )


def test_derive_seed_of_a_spec_shaped_material() -> None:
    from pharmacy import uncertainty

    material = {
        "engine_version": "0.1.0",
        "config": [{"document_id": "engine-settings", "version": "0.1.0", "sha256": "ab" * 32}],
        "sensor_series": [{"series_id": "m:mbp-t1", "sha256": "cd" * 32}],
        "observations_sha256": "ef" * 32,
        "lot_id": "lot-apap-001",
        "ingredient_id": "acetaminophen",
        "as_of_utc": "2028-04-19T00:00:00Z",
        "purpose": "potency_estimate",
    }
    assert_equal(
        "uncertainty.derive_seed",
        {"material": "spec 9.8 shape"},
        uncertainty.derive_seed(cast(JSONValue, material)),
        exact.derive_seed(material),
        "9.8",
    )


def test_batch_generator_uses_spawn_key() -> None:
    from pharmacy import uncertainty

    seed = exact.derive_seed({"x": 1})
    for r in (0, 1, 7):
        expected = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(r,)))).random(
            5
        )
        assert list(uncertainty.batch_generator(seed, r).random(5)) == list(expected)


# --- JCGM 101 (spec 9.9) -------------------------------------------------------------------------
@pytest.mark.parametrize("p", [0.5, 0.68, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999, 0.9995, 0.9999, 0.99999])
def test_jcgm_batch_size(p: float) -> None:
    from pharmacy import uncertainty

    assert_equal(
        "uncertainty.jcgm_batch_size",
        {"p": p},
        uncertainty.jcgm_batch_size(p),
        exact.jcgm_batch_size(p),
        "9.9",
    )


@pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.5])
def test_jcgm_batch_size_domain(p: float) -> None:
    from pharmacy import uncertainty
    from pharmacy.errors import InputError

    with pytest.raises(InputError):
        uncertainty.jcgm_batch_size(p)


@HYPOTHESIS
@given(u=st.one_of(st.floats(1e-300, 1e300), st.just(0.0)), n_dig=st.integers(1, 4))
def test_numerical_tolerance(u: float, n_dig: int) -> None:
    from pharmacy import uncertainty

    assert_equal(
        "uncertainty.numerical_tolerance",
        locals(),
        uncertainty.numerical_tolerance(u, n_dig),
        exact.numerical_tolerance(u, n_dig),
        "9.9",
    )


@pytest.mark.parametrize(
    "u", [0.0124, 0.0996, 0.0995, 0.00995, 1.0, 9.96, 99.5, 0.125, 0.135, 2.5e-7, 123456.0, 0.1, 0.95]
)
def test_numerical_tolerance_edges(u: float) -> None:
    from pharmacy import uncertainty

    for n_dig in (1, 2, 3):
        assert_equal(
            "uncertainty.numerical_tolerance",
            {"u": u, "n_dig": n_dig},
            uncertainty.numerical_tolerance(u, n_dig),
            exact.numerical_tolerance(u, n_dig),
            "9.9",
        )


# --- Weighted quantile (spec 9.10) ----------------------------------------------------------------
@HYPOTHESIS
@given(
    data=st.lists(st.tuples(st.floats(-1e3, 1e3), st.floats(0.0, 10.0)), min_size=1, max_size=40).filter(
        lambda d: sum(w for _, w in d) > 0
    ),
    probability=st.floats(0.0, 1.0),
    weighted=st.booleans(),
)
def test_weighted_quantile(data: list[tuple[float, float]], probability: float, weighted: bool) -> None:
    from pharmacy import uncertainty

    values = np.asarray([v for v, _ in data], dtype=np.float64)
    weights = np.asarray([w for _, w in data], dtype=np.float64) if weighted else None
    engine = uncertainty.weighted_quantile(values, weights, probability)
    oracle = exact.weighted_quantile(list(values), None if weights is None else list(weights), probability)
    assert_equal(
        "uncertainty.weighted_quantile",
        {"values": list(values), "weights": None if weights is None else list(weights), "p": probability},
        engine,
        oracle,
        "9.10",
    )


def test_weighted_quantile_ties_and_reported_probabilities(bundle: ConfigBundle) -> None:
    from pharmacy import uncertainty

    rng = np.random.Generator(np.random.PCG64(20261114))
    values = np.round(rng.normal(95.0, 2.0, 10_000), 1)
    weights = rng.random(10_000)
    probs = sorted({*bundle.settings["monte_carlo"]["reported_quantiles"], 0.05, 0.95, 0.5})
    for p in probs:
        for w in (None, weights):
            engine = uncertainty.weighted_quantile(values, w, p)
            oracle = exact.weighted_quantile(list(values), None if w is None else list(w), p)
            assert_equal(
                "uncertainty.weighted_quantile", {"p": p, "weighted": w is not None}, engine, oracle, "9.10"
            )


# --- ICH Q1E and Q3B (spec 11.6) ------------------------------------------------------------------
@pytest.mark.parametrize("case", exact.Q1E_CASES)
@pytest.mark.parametrize("x", [0.0, 3.0, 6.0, 12.0, 18.0, 24.0, 36.0, 7.5])
def test_q1e(case: Any, x: float) -> None:
    from pharmacy import decision

    engine = decision.q1e_max_shelf_life_months(case, x)
    oracle = exact.q1e_max_shelf_life_months(case, x)
    assert_equal("decision.q1e_max_shelf_life_months", {"case": case, "x": x}, engine, float(oracle), "11.6")


MDD_GRID = [
    0.1,
    0.5,
    0.999,
    1.0,
    1.001,
    5.0,
    9.999,
    10.0,
    10.001,
    50.0,
    100.0,
    100.001,
    150.0,
    999.0,
    1000.0,
    1000.001,
    1500.0,
    2000.0,
    2000.001,
    3000.0,
]


@pytest.mark.parametrize("mdd", MDD_GRID)
def test_q3b_thresholds(mdd: float, bundle: ConfigBundle) -> None:
    from pharmacy import decision

    engine = decision.q3b_thresholds(mdd, settings=bundle.settings["q3b"])
    oracle = exact.q3b_thresholds(mdd)
    assert engine.maximum_daily_dose_mg == mdd
    for name, value in oracle.items():
        expected = None if value is None else float(value)
        assert_equal(
            "decision.q3b_thresholds", {"mdd": mdd, "member": name}, getattr(engine, name), expected, "11.6"
        )


Q3B_EXCEEDS = [
    (3.4, 3.0),
    (3.5, 3.0),
    (2.5, 3.0),
    (3.49, 3.0),
    (0.18, 0.15),
    (0.155, 0.15),
    (0.154, 0.15),
    (0.149, 0.15),
    (0.25, 0.2),
    (0.24, 0.2),
    (0.05, 0.05),
    (0.054, 0.05),
    (0.055, 0.05),
    (0.5, 0.5),
    (0.54, 0.5),
    (0.56, 0.5),
]


@pytest.mark.parametrize(("observed", "threshold"), Q3B_EXCEEDS)
def test_q3b_exceeds(observed: float, threshold: float) -> None:
    from pharmacy import decision

    assert_equal(
        "decision.q3b_exceeds",
        {"observed": observed, "threshold": threshold},
        decision.q3b_exceeds(observed, threshold),
        exact.q3b_exceeds(observed, threshold),
        "11.6",
    )


# --- Ordering (spec 11.5) ----------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class _Estimate:
    estimate_id: str
    ingredient_id: str
    quantiles: tuple[QuantileValue, ...]


@dataclasses.dataclass(frozen=True)
class _Forecast:
    ingredient_id: str
    crossing_s: float | None
    status: str
    quantile_probability: float = 0.05


@dataclasses.dataclass(frozen=True)
class _Decision:
    lot_id: str
    state: str
    ordering_key: OrderingKey


def _estimate(name: str, q_lo: float) -> _Estimate:
    return _Estimate(
        name,
        name,
        (QuantileValue(0.05, q_lo), QuantileValue(0.5, q_lo + 3.0), QuantileValue(0.95, q_lo + 6.0)),
    )


ORDERING_CASES: list[tuple[str, str, list[_Estimate], list[_Forecast]]] = [
    ("in_specification", "lot-a", [_estimate("x", 95.0)], [_Forecast("x", 3.0e7, "within_horizon")]),
    (
        "in_specification",
        "lot-b",
        [_estimate("x", 95.0), _estimate("y", 97.0)],
        [_Forecast("x", 5.0e7, "within_horizon"), _Forecast("y", 2.0e7, "within_horizon")],
    ),
    ("in_specification", "lot-c", [_estimate("x", 95.0)], [_Forecast("x", None, "beyond_horizon")]),
    (
        "in_specification",
        "lot-d",
        [_estimate("x", 95.0), _estimate("y", 97.0)],
        [_Forecast("x", None, "beyond_horizon"), _Forecast("y", 4.0e7, "within_horizon")],
    ),
    (
        "adjust",
        "lot-e",
        [_estimate("x", 85.0), _estimate("y", 80.5)],
        [_Forecast("x", 1.0e7, "already_crossed")],
    ),
    ("do_not_use", "lot-f", [_estimate("x", 50.0)], [_Forecast("x", 1.0e7, "already_crossed")]),
]


@pytest.mark.parametrize("case", ORDERING_CASES, ids=lambda c: c[1])
def test_ordering_key(bundle: ConfigBundle, case: tuple[str, str, list[_Estimate], list[_Forecast]]) -> None:
    from pharmacy import decision

    state, lot_id, estimates, forecasts = case
    engine = decision.ordering_key(state, lot_id, cast(Any, estimates), cast(Any, forecasts))
    oracle = exact.ordering_key(
        cast(Any, state),
        lot_id,
        cast(Any, estimates),
        cast(Any, forecasts),
        bundle.settings["decision"]["lower_quantile"]["value"],
    )
    assert_equal("decision.ordering_key", {"state": state, "lot": lot_id}, engine, oracle, "11.5")


@HYPOTHESIS
@given(
    keys=st.lists(
        st.tuples(
            st.integers(0, 2),
            st.one_of(st.none(), st.floats(-1e9, 1e9)),
            st.sampled_from(["a", "b", "c", "d", "e"]),
        ),
        min_size=1,
        max_size=12,
        unique_by=lambda k: k[2],
    )
)
def test_sort_lots(keys: list[tuple[int, float | None, str]]) -> None:
    from pharmacy import decision

    states = {0: "in_specification", 1: "adjust", 2: "do_not_use"}
    decisions = [_Decision(lot, states[rank], OrderingKey(rank, primary, lot)) for rank, primary, lot in keys]
    engine = [d.lot_id for d in decision.sort_lots(cast(Any, decisions))]
    oracle = [d.lot_id for d in exact.sort_lots(cast(Any, decisions))]
    assert_equal("decision.sort_lots", {"keys": keys}, engine, oracle, "11.5")
