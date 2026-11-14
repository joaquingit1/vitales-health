"""Property test of pipeline determinism (spec 9.8, 15.3): the same inputs give a
byte-identical canonical JSON estimate on a second run and with the sensor series list in
any order. The stability model is passed explicitly (a hand-built thermal model), so this
does not depend on the chemistry layer. Marked slow: 200 examples of three estimates."""

import copy
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pharmacy import pipeline
from pharmacy.config import ConfigBundle, canonical_json_bytes, load_bundle
from pharmacy.types import AssayObservation, iso_to_seconds, seconds_to_iso, to_json
from tests.unit.core import builders as b

pytestmark = pytest.mark.slow

LAUNCH = "2028-01-10T00:00:00Z"
LAUNCH_S = iso_to_seconds(LAUNCH)


def _fast_bundle() -> ConfigBundle:
    bundle = load_bundle()
    data: Any = copy.deepcopy(bundle.documents["engine-settings"].data)
    data["monte_carlo"]["n_dig"]["value"] = 1
    data["monte_carlo"]["max_batches"]["value"] = 4
    return b.bundle_with(bundle, "engine-settings", data)


BUNDLE = _fast_bundle()
PRODUCTS = {p["product_id"]: p for p in BUNDLE.formulary["products"]}
TARGETS = sorted(
    (lot["lot_id"], lot["product_id"], ingredient["ingredient_id"])
    for lot in BUNDLE.lots["lots"]
    for ingredient in PRODUCTS[lot["product_id"]]["ingredients"]
)


def _estimate(
    lot_id: str, product_id: str, ingredient_id: str, as_of: str, series: Any, assays: Any
) -> bytes:
    model = b.thermal_model(product_id=product_id, ingredient_id=ingredient_id, ln_k=-19.5)
    found = pipeline.estimate_potency(
        BUNDLE, lot_id, ingredient_id, as_of, list(series), list(assays), [], model=model
    )
    return canonical_json_bytes(to_json(found))


@given(
    st.sampled_from(TARGETS),
    st.integers(min_value=3600, max_value=5 * 86400 - 1),
    st.lists(st.floats(min_value=280.0, max_value=310.0), min_size=1, max_size=4),
    st.lists(st.floats(min_value=20.0, max_value=80.0), min_size=1, max_size=4),
    st.one_of(st.none(), st.floats(min_value=85.0, max_value=105.0)),
    st.randoms(use_true_random=False),
)
def test_estimates_are_byte_identical_across_runs_and_series_order(
    target: tuple[str, str, str],
    offset_s: int,
    temperatures: list[float],
    humidities: list[float],
    assay_value: float | None,
    shuffler: Any,
) -> None:
    lot_id, product_id, ingredient_id = target
    as_of = seconds_to_iso(LAUNCH_S + offset_s).replace(".000000Z", "Z")
    series = [
        b.constant_series(
            "trk-t", "trk-t1", "transit_kit", "temperature_k", "K", LAUNCH, as_of, 1800.0, temperatures
        ),
        b.constant_series(
            "trk-rh",
            "trk-rh1",
            "transit_kit",
            "relative_humidity",
            "percent",
            LAUNCH,
            as_of,
            1800.0,
            humidities,
        ),
        b.constant_series(
            "trk-d", "trk-dos1", "transit_kit", "dose_rate_gy_per_s", "Gy/s", LAUNCH, as_of, 1800.0, 3e-9
        ),
    ]
    assays = []
    if assay_value is not None:
        at = "2027-12-01T00:00:00Z"
        assays.append(
            AssayObservation(
                "a-1",
                lot_id,
                ingredient_id,
                at,
                iso_to_seconds(at),
                "total",
                assay_value,
                1.0,
                "ground",
                "hplc",
                "lab",
                "",
            )
        )
    first = _estimate(lot_id, product_id, ingredient_id, as_of, series, assays)
    assert _estimate(lot_id, product_id, ingredient_id, as_of, series, assays) == first
    shuffler.shuffle(series)
    assert _estimate(lot_id, product_id, ingredient_id, as_of, series, assays) == first
