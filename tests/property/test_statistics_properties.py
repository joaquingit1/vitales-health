"""Property tests of seeds, canonical JSON, quantiles, decisions and ordering (spec 9, 11.7,
15.3): canonical JSON independent of key order; changing any pin changes the seed; weighted
quantiles non-decreasing in probability; lowering every draw never improves a state (on the
lower-limit side); the lot ordering is a total order; crossing times bracket the exact
crossing of a linear potency."""

import copy
import itertools
import math
from typing import Any

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from pharmacy import decision, uncertainty
from pharmacy.config import canonical_json_bytes, load_bundle
from pharmacy.types import LOT_STATE_RANK, LotDecision, OrderingKey
from tests.unit.core import builders as b

BUNDLE = load_bundle()
LOT: Any = copy.deepcopy(BUNDLE.lots["lots"][0])
LOT["lot_id"] = "lot-1"
LOT["labeled_expiry_utc"] = "2029-01-01T00:00:00Z"
PRODUCTS = {p["product_id"]: p for p in BUNDLE.formulary["products"]}

json_leaves = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**6), 10**6),
    st.text(max_size=8),
    st.floats(allow_nan=False, allow_infinity=False),
)
json_values = st.recursive(
    json_leaves,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=6), inner, max_size=5)
    ),
    max_leaves=20,
)


def _reorder(value: Any, reverse: bool) -> Any:
    if isinstance(value, dict):
        keys = sorted(value, reverse=reverse)
        return {k: _reorder(value[k], reverse) for k in keys}
    if isinstance(value, list):
        return [_reorder(v, reverse) for v in value]
    return value


@given(st.dictionaries(st.text(max_size=6), json_values, max_size=6))
def test_canonical_json_ignores_key_order(value: dict[str, Any]) -> None:
    assert canonical_json_bytes(_reorder(value, False)) == canonical_json_bytes(_reorder(value, True))


PIN_FIELDS = (
    "engine_version",
    "config",
    "sensor_series",
    "observations_sha256",
    "lot_id",
    "ingredient_id",
    "as_of_utc",
    "purpose",
)


def _material() -> dict[str, Any]:
    return {
        "engine_version": "0.1.0",
        "config": [{"document_id": "engine-settings", "version": "0.2.0", "sha256": "a" * 64}],
        "sensor_series": [{"series_id": "trk-t", "sha256": "b" * 64}],
        "observations_sha256": "c" * 64,
        "lot_id": "lot-apap-001",
        "ingredient_id": "acetaminophen",
        "as_of_utc": "2028-01-12T00:00:00Z",
        "purpose": "potency_estimate",
    }


@given(st.sampled_from(PIN_FIELDS), st.text(min_size=1, max_size=12))
def test_changing_any_pin_changes_the_seed(field: str, text: str) -> None:
    material = _material()
    changed = copy.deepcopy(material)
    if field == "config":
        changed["config"][0]["sha256"] = text
    elif field == "sensor_series":
        changed["sensor_series"][0]["sha256"] = text
    else:
        changed[field] = text
    assume(changed != material)
    assert uncertainty.derive_seed(changed) != uncertainty.derive_seed(material)


@given(
    st.lists(st.floats(min_value=-1e6, max_value=1e6), min_size=1, max_size=40),
    st.data(),
    st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=2, max_size=10),
)
def test_weighted_quantiles_are_non_decreasing(
    values: list[float], data: Any, probabilities: list[float]
) -> None:
    weights = data.draw(
        st.one_of(
            st.none(),
            st.lists(st.floats(min_value=0.0, max_value=10.0), min_size=len(values), max_size=len(values)),
        )
    )
    if weights is not None:
        assume(math.fsum(weights) > 0.0)
    v = np.array(values)
    w = None if weights is None else np.array(weights)
    found = [uncertainty.weighted_quantile(v, w, p) for p in sorted(probabilities)]
    assert found == sorted(found)
    assert all(min(values) <= q <= max(values) for q in found)


def _state(q_lo: float, q_hi: float, product: Any, flags: tuple[Any, ...]) -> str:
    estimate = b.potency_estimate("ibuprofen", q_lo, q_hi, flags=flags)
    forecast = b.forecast("ibuprofen", None)
    found = decision.decide_lot(
        LOT, product, [estimate], [forecast], ((), ()), BUNDLE.settings["decision"], b.pins()
    )
    return found.state


@given(
    st.floats(min_value=50.0, max_value=110.0),
    st.floats(min_value=0.0, max_value=15.0),
    st.floats(min_value=0.0, max_value=40.0),
    st.booleans(),
    st.sampled_from([(), ("coverage_low",), ("outside_validated_range",), ("mc_not_converged",)]),
)
def test_lowering_every_draw_never_improves_the_state(
    q_lo: float, width: float, shift: float, allowed: bool, flags: tuple[Any, ...]
) -> None:
    """With q_hi at or below the upper limit (the lower-limit side of the table; lowering an
    estimate that is above the upper limit can of course bring it back inside)."""
    q_hi = min(q_lo + width, 110.0)
    assume(q_hi >= q_lo)
    product: Any = copy.deepcopy(PRODUCTS["ibuprofen_400_tab"])
    product["potency_adjustment"]["allowed"] = allowed
    before = _state(q_lo, q_hi, product, flags)
    after = _state(q_lo - shift, q_hi - shift, product, flags)
    assert LOT_STATE_RANK[after] >= LOT_STATE_RANK[before]  # type: ignore[index]


keys = st.builds(
    OrderingKey,
    state_rank=st.integers(min_value=0, max_value=2),
    primary=st.one_of(st.none(), st.floats(min_value=-1e10, max_value=1e10)),
    lot_id=st.text(alphabet="abcdef-0123", min_size=1, max_size=6),
)


def _decision(key: OrderingKey) -> LotDecision:
    template = _decision.template  # type: ignore[attr-defined]
    return LotDecision(
        **{
            **{f: getattr(template, f) for f in template.__dataclass_fields__},
            "lot_id": key.lot_id,
            "ordering_key": key,
        }
    )


_decision.template = decision.decide_lot(  # type: ignore[attr-defined]
    LOT,
    PRODUCTS["ibuprofen_400_tab"],
    [b.potency_estimate("ibuprofen", 95.0, 100.0)],
    [],
    ((), ()),
    BUNDLE.settings["decision"],
    b.pins(),
)


@given(
    st.lists(keys, min_size=1, max_size=12, unique_by=lambda k: k.lot_id), st.randoms(use_true_random=False)
)
def test_ordering_is_a_total_order(items: list[OrderingKey], shuffler: Any) -> None:
    decisions = [_decision(k) for k in items]
    first = [d.lot_id for d in decision.sort_lots(decisions)]
    shuffled = list(decisions)
    shuffler.shuffle(shuffled)
    assert [d.lot_id for d in decision.sort_lots(shuffled)] == first
    ordered = decision.sort_lots(decisions)
    for a, c in itertools.pairwise(ordered):
        ka, kc = a.ordering_key, c.ordering_key
        assert (ka.state_rank, ka.primary is None) <= (kc.state_rank, kc.primary is None)
        if (ka.state_rank, ka.primary is None) == (
            kc.state_rank,
            kc.primary is None,
        ) and ka.primary is not None:
            assert kc.primary is not None
            assert (ka.primary, ka.lot_id) <= (kc.primary, kc.lot_id)


@given(
    st.lists(
        st.tuples(st.floats(min_value=80.0, max_value=110.0), st.floats(min_value=0.0, max_value=1e-5)),
        min_size=1,
        max_size=20,
    ),
)
def test_crossing_times_bracket_the_exact_crossing(lines: list[tuple[float, float]]) -> None:
    settings = BUNDLE.settings["forecast"]
    horizon = float(settings["horizon_s"]["value"])
    tolerance = float(settings["time_tolerance_s"]["value"])
    start = np.array([s for s, _ in lines])
    rate = np.array([r for _, r in lines])
    times = decision.crossing_times(lambda t: start - rate * t, len(lines), 90.0, settings)
    for (s, r), found in zip(lines, times, strict=True):
        if s < 90.0:
            assert found == 0.0
            continue
        exact = (s - 90.0) / r if r > 0.0 else math.inf
        if exact > horizon or (exact == horizon and s - r * horizon >= 90.0):
            assert found == math.inf
        else:
            assert found >= exact * (1.0 - 1e-12) - 1e-9
            assert found <= exact + tolerance * (1.0 + 1e-9)
            assert found == pytest.approx(exact, abs=tolerance * 1.01)
