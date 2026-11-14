"""Unit tests of pharmacy.uncertainty (spec 9): seeds and generators, the JCGM 101 batch size
(TV-MC-2) and numerical tolerance, weighted type-1 quantiles, the fixed variate layout of
draw_parameters (rebuilt column by column from the same generator) and the adaptive loop."""

import copy
import dataclasses
import hashlib
import math
from typing import Any

import numpy as np
import pytest
from scipy.special import ndtr, ndtri

from pharmacy import uncertainty
from pharmacy.config import ConfigBundle, canonical_json_bytes
from pharmacy.errors import ComputationError, InputError, ModelDomainError
from pharmacy.types import EngineSettingsDoc, FreeShare, JSONValue, MonteCarloSettings, StorageZoneDoc
from tests.unit.core import builders as b


@pytest.fixture(scope="module")
def zones(bundle: ConfigBundle) -> dict[str, StorageZoneDoc]:
    return {zone["zone_id"]: zone for zone in bundle.zones["zones"]}


@pytest.fixture(scope="module")
def settings(bundle: ConfigBundle) -> EngineSettingsDoc:
    return bundle.settings


def _mc(settings: EngineSettingsDoc, **values: float) -> MonteCarloSettings:
    out: Any = copy.deepcopy(settings["monte_carlo"])
    for key, value in values.items():
        out[key]["value"] = value
    return out  # type: ignore[no-any-return]


# =========================================================================================
# Seeds (spec 9.8)
# =========================================================================================


def test_derive_seed_is_the_first_16_digest_bytes() -> None:
    material: dict[str, JSONValue] = {
        "lot_id": "lot-1",
        "as_of_utc": "2028-01-01T00:00:00Z",
        "purpose": "estimate",
    }
    digest = hashlib.sha256(canonical_json_bytes(material)).digest()
    seed = uncertainty.derive_seed(material)
    assert seed == int.from_bytes(digest[:16], "big")
    reordered: dict[str, JSONValue] = dict(reversed(list(material.items())))
    assert uncertainty.derive_seed(reordered) == seed
    changed: dict[str, JSONValue] = {**material, "purpose": "forecast"}
    assert uncertainty.derive_seed(changed) != seed
    assert uncertainty.seed_hex(seed) == digest[:16].hex()


def test_seed_hex_range() -> None:
    assert uncertainty.seed_hex(0) == "0" * 32
    assert uncertainty.seed_hex((1 << 128) - 1) == "f" * 32
    with pytest.raises(InputError):
        uncertainty.seed_hex(-1)
    with pytest.raises(InputError):
        uncertainty.seed_hex(1 << 128)


def test_batch_generator_streams() -> None:
    first = uncertainty.batch_generator(12345, 0).standard_normal(5)
    again = uncertainty.batch_generator(12345, 0).standard_normal(5)
    other = uncertainty.batch_generator(12345, 1).standard_normal(5)
    reference = np.random.Generator(
        np.random.PCG64(np.random.SeedSequence(12345, spawn_key=(0,)))
    ).standard_normal(5)
    assert first.tolist() == again.tolist() == reference.tolist()
    assert first.tolist() != other.tolist()
    with pytest.raises(InputError):
        uncertainty.batch_generator(-1, 0)
    with pytest.raises(InputError):
        uncertainty.batch_generator(1, -1)


# =========================================================================================
# JCGM 101 (spec 9.9)
# =========================================================================================


def test_tv_mc_2_batch_size(vector: Any) -> None:
    v = vector("TV-MC-2")
    p = v["inputs"]["coverage_probability"]
    assert uncertainty.jcgm_batch_size(p) == v["expected"]["batch_m"]
    # J itself, exactly, where it exceeds 10^4: ceil(100 / 0.001) and ceil(100 / 0.004).
    assert uncertainty.jcgm_batch_size(0.999) == 100_000
    assert uncertainty.jcgm_batch_size(0.996) == 25_000
    assert uncertainty.jcgm_batch_size(0.9) == 10_000
    for bad in (0.0, 1.0, -0.5, math.nan):
        with pytest.raises(InputError):
            uncertainty.jcgm_batch_size(bad)


@pytest.mark.parametrize(
    ("u", "n_dig", "delta"),
    [
        (0.0123, 2, 0.0005),
        (25.0, 2, 0.5),
        (0.0996, 2, 0.005),  # rounds up to 0.10
        (3.0, 1, 0.5),
        (0.000456, 1, 0.00005),
        (1234.5, 3, 5.0),
        (0.0, 2, 0.0),
    ],
)
def test_numerical_tolerance(u: float, n_dig: int, delta: float) -> None:
    assert uncertainty.numerical_tolerance(u, n_dig) == pytest.approx(delta, rel=1e-12, abs=0.0)


def test_numerical_tolerance_errors() -> None:
    for u, n_dig in ((-1.0, 2), (math.nan, 2), (math.inf, 2), (1.0, 0)):
        with pytest.raises(InputError):
            uncertainty.numerical_tolerance(u, n_dig)


# =========================================================================================
# Weighted quantiles (spec 9.10)
# =========================================================================================


def test_weighted_quantile_type_1() -> None:
    values = np.array([3.0, 1.0, 2.0])
    assert uncertainty.weighted_quantile(values, None, 0.5) == 2.0
    assert uncertainty.weighted_quantile(values, None, 1.0 / 3.0) == 1.0  # W_k >= q - 1e-12
    assert uncertainty.weighted_quantile(values, None, 0.0) == 1.0
    assert uncertainty.weighted_quantile(values, None, 1.0) == 3.0
    weights = np.array([8.0, 1.0, 1.0])
    assert uncertainty.weighted_quantile(values, weights, 0.21) == 3.0
    assert uncertainty.weighted_quantile(values, weights, 0.2) == 2.0
    ties = np.array([1.0, 1.0, 1.0, 2.0])
    assert uncertainty.weighted_quantile(ties, np.array([0.0, 0.0, 0.0, 1.0]), 0.5) == 2.0


def test_weighted_quantile_errors() -> None:
    values = np.array([1.0, 2.0])
    cases: list[tuple[Any, Any, float]] = [
        (values, np.array([1.0]), 0.5),
        (values, np.array([1.0, -1.0]), 0.5),
        (values, np.array([0.0, 0.0]), 0.5),
        (values, np.array([1.0, math.nan]), 0.5),
        (np.array([1.0, math.nan]), None, 0.5),
        (np.zeros((2, 2)), None, 0.5),
        (np.array([]), None, 0.5),
        (values, None, 1.5),
        (values, None, math.nan),
    ]
    for v, w, p in cases:
        with pytest.raises(InputError):
            uncertainty.weighted_quantile(v, w, p)


def test_coverage_interval_and_quantiles() -> None:
    values = np.arange(1.0, 1001.0)
    interval = uncertainty.coverage_interval(values, None, 0.9)
    assert (interval.low, interval.high, interval.kind) == (50.0, 950.0, "probabilistically_symmetric")
    out = uncertainty.quantiles(values, None, [0.75, 0.25])
    assert [(q.probability, q.value) for q in out] == [(0.75, 750.0), (0.25, 250.0)]
    with pytest.raises(InputError):
        uncertainty.coverage_interval(values, None, 1.0)


# =========================================================================================
# draw_parameters (spec 9.7)
# =========================================================================================


def _two_pathway_model(*, shared_weight: float = 0.4, ea_lower: float = 60000.0) -> Any:
    hydrolysis = b.pathway(
        "api:hydrolysis",
        b.block(
            ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"],
            [-18.0, 90000.0, 0.03],
            [0.5, 8000.0, 0.01],
            shared_weight=shared_weight,
            ea_lower=ea_lower,
        ),
    )
    radiolysis = b.pathway(
        "api:radiolysis",
        b.block(["ln_k_dose_per_gy", "ln_frozen_rate_multiplier"], [-14.0, 1.2], [1.0, 0.25]),
        driver="radiation",
        mechanism="radiolysis",
    )
    light = b.pathway(
        "api:photolysis",
        b.block(
            ["ln_k_vis_per_lux_h", "ln_k_uv_per_w_h_per_m2", "ln_k_dose_frozen_per_gy"],
            [-16.0, -9.0, -12.0],
            [0.7, 0.9, 0.4],
        ),
        driver="light",
        mechanism="photolysis",
    )
    return b.model([hydrolysis, radiolysis, light])


def test_variate_layout_column_by_column(zones: dict[str, StorageZoneDoc]) -> None:
    model = _two_pathway_model()
    zone_ids = ["station_kit", "ground_storage", "station_kit"]
    n = 64
    draws = uncertainty.draw_parameters(
        model, 99.0, 1.5, zones, zone_ids, uncertainty.batch_generator(7, 3), n
    )
    generator = uncertainty.batch_generator(7, 3)
    count = 3
    z = generator.standard_normal((n, 2 + 8 * count + 3 * 2))
    u = generator.random((n, count))
    ids = [p.pathway_id for p in model.pathways]
    assert ids == ["api:hydrolysis", "api:photolysis", "api:radiolysis"]
    assert draws.n == n
    assert np.array_equal(draws.c0_percent, 99.0 + 1.5 * z[:, 0])
    assert np.array_equal(draws.z_c0, z[:, 0])
    # Hydrolysis (p = 0): ln k shares the class normal z[:, 1] with weight 0.4.
    hyd = 2
    expected_ln_k = -18.0 + 0.5 * (math.sqrt(0.4) * z[:, 1] + math.sqrt(0.6) * z[:, hyd + 0])
    assert np.allclose(draws.ln_k_ref_per_s[:, 0], expected_ln_k, rtol=0.0, atol=1e-15)
    assert np.allclose(draws.b_per_percent_rh[:, 0], 0.03 + 0.01 * z[:, hyd + 1], rtol=0.0, atol=1e-17)
    phi_a = float(ndtr((60000.0 - 90000.0) / 8000.0))
    expected_ea = 90000.0 + 8000.0 * ndtri(phi_a + u[:, 0] * (1.0 - phi_a))
    assert np.allclose(draws.ea_j_per_mol[:, 0], expected_ea, rtol=1e-15, atol=0.0)
    assert bool(np.all(draws.ea_j_per_mol[:, 0] >= 60000.0))
    assert np.array_equal(draws.extrapolation_z_temperature[:, 0], z[:, hyd + 6])
    assert np.array_equal(draws.extrapolation_z_humidity[:, 0], z[:, hyd + 7])
    # Photolysis (p = 1): slots 2 and 3, and its own frozen dose constant in slot 4.
    pho = 2 + 8
    assert np.allclose(draws.ln_k_vis_per_lux_h[:, 1], -16.0 + 0.7 * z[:, pho + 2], rtol=0.0, atol=1e-14)
    assert np.allclose(draws.ln_k_uv_per_w_h_per_m2[:, 1], -9.0 + 0.9 * z[:, pho + 3], rtol=0.0, atol=1e-14)
    assert np.allclose(draws.ln_k_dose_frozen_per_gy[:, 1], -12.0 + 0.4 * z[:, pho + 4], rtol=0.0, atol=1e-14)
    assert draws.ea_j_per_mol[:, 1].tolist() == [0.0] * n
    # Radiolysis (p = 2): dose in slot 4 doubles as the frozen constant; frozen multiplier in slot 5.
    rad = 2 + 16
    assert np.allclose(draws.ln_k_dose_per_gy[:, 2], -14.0 + z[:, rad + 4], rtol=0.0, atol=1e-14)
    assert np.array_equal(draws.ln_k_dose_frozen_per_gy[:, 2], draws.ln_k_dose_per_gy[:, 2])
    assert np.allclose(draws.ln_frozen_multiplier[:, 2], 1.2 + 0.25 * z[:, rad + 5], rtol=0.0, atol=1e-15)
    # Zones sorted and de-duplicated, three columns each after the pathways.
    assert sorted(draws.zone_ln_multiplier) == ["ground_storage", "station_kit"]
    ground, station = 2 + 24, 2 + 27
    assert draws.zone_ln_multiplier["ground_storage"].tolist() == [0.0] * n  # fixed, mu 1
    sk = zones["station_kit"]["rate_multiplier"]
    assert np.allclose(
        draws.zone_ln_multiplier["station_kit"], sk["mu"] + sk["sigma"] * z[:, station], atol=1e-15
    )
    t_sd = next(s for s in zones["station_kit"]["sensors"] if s["quantity"] == "temperature_k")
    assert np.allclose(
        draws.temperature_offset_k["station_kit"],
        t_sd["calibration_standard_uncertainty"]["value"] * z[:, station + 1],
        atol=1e-15,
    )
    declared = zones["ground_storage"]["declared_conditions"]
    assert np.allclose(
        draws.temperature_offset_k["ground_storage"],
        declared["temperature_k"]["sigma"] * z[:, ground + 1],  # type: ignore[index]
        atol=1e-15,
    )
    assert np.allclose(
        draws.rh_offset_percent["ground_storage"],
        declared["relative_humidity_percent"]["sigma"] * z[:, ground + 2],  # type: ignore[index]
        atol=1e-15,
    )


def test_zone_without_declared_rh_has_no_offset(zones: dict[str, StorageZoneDoc]) -> None:
    zone: Any = copy.deepcopy(zones["ground_storage"])
    zone["declared_conditions"]["relative_humidity_percent"] = None
    model = b.thermal_model()
    draws = uncertainty.draw_parameters(
        model, 100.0, 1.0, {"ground_storage": zone}, ["ground_storage"], uncertainty.batch_generator(1, 0), 8
    )
    assert draws.rh_offset_percent["ground_storage"].tolist() == [0.0] * 8


def test_fixed_ea_and_unbounded_ea(zones: dict[str, StorageZoneDoc]) -> None:
    fixed = b.model(
        [b.pathway("api:h", b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-18.0, 80000.0], [0.5, 0.0]))]
    )
    draws = uncertainty.draw_parameters(fixed, 100.0, 0.0, zones, [], uncertainty.batch_generator(1, 0), 4)
    assert draws.ea_j_per_mol[:, 0].tolist() == [80000.0] * 4
    assert draws.c0_percent.tolist() == [100.0] * 4
    below = b.model(
        [
            b.pathway(
                "api:h",
                b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-18.0, 80000.0], [0.5, 0.0], ea_lower=90000.0),
            )
        ]
    )
    with pytest.raises(ModelDomainError):
        uncertainty.draw_parameters(below, 100.0, 1.0, zones, [], uncertainty.batch_generator(1, 0), 4)
    free = b.model(
        [
            b.pathway(
                "api:h",
                b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [-18.0, 80000.0], [0.5, 1e4], ea_lower=-math.inf),
            )
        ]
    )
    generator = uncertainty.batch_generator(2, 0)
    draws = uncertainty.draw_parameters(free, 100.0, 1.0, zones, [], generator, 16)
    generator = uncertainty.batch_generator(2, 0)
    generator.standard_normal((16, 10))
    u = generator.random((16, 1))
    assert np.allclose(draws.ea_j_per_mol[:, 0], 80000.0 + 1e4 * ndtri(u[:, 0]), rtol=1e-15)


def _measured_model(covariance: list[list[float]]) -> Any:
    block = b.block(
        ["ln_k_ref_per_s", "ea_j_per_mol", "b_per_percent_rh"],
        [-17.0, 85000.0, 0.02],
        [math.sqrt(covariance[i][i]) for i in range(3)],
        layer="measured_model",
        covariance=covariance,
    )
    return b.model([b.pathway("api:h", block)], measured_model_id="m-1")


def test_measured_block_draws_conditionally_on_ea(zones: dict[str, StorageZoneDoc]) -> None:
    cov = [[0.04, 300.0, 0.0001], [300.0, 4e6, 0.5], [0.0001, 0.5, 1e-4]]
    model = _measured_model(cov)
    n = 32
    draws = uncertainty.draw_parameters(model, 100.0, 1.0, zones, [], uncertainty.batch_generator(5, 0), n)
    generator = uncertainty.batch_generator(5, 0)
    z = generator.standard_normal((n, 10))
    u = generator.random((n, 1))
    c = np.array(cov)
    ea = 85000.0 + math.sqrt(c[1, 1]) * ndtri(
        float(ndtr((0.0 - 85000.0) / math.sqrt(c[1, 1])))
        + u[:, 0] * (1.0 - float(ndtr((0.0 - 85000.0) / math.sqrt(c[1, 1]))))
    )
    idx = [0, 2]
    c_re = c[idx, 1]
    mean = np.array([-17.0, 0.02])[None, :] + np.outer(ea - 85000.0, c_re / c[1, 1])
    cond = c[np.ix_(idx, idx)] - np.outer(c_re, c_re) / c[1, 1]
    factor = np.linalg.cholesky(cond)
    slots = np.stack([z[:, 2 + 0], z[:, 2 + 1]], axis=1)
    expected = mean + slots @ factor.T
    assert np.allclose(draws.ea_j_per_mol[:, 0], ea, rtol=1e-15)
    assert np.allclose(draws.ln_k_ref_per_s[:, 0], expected[:, 0], rtol=0.0, atol=1e-12)
    assert np.allclose(draws.b_per_percent_rh[:, 0], expected[:, 1], rtol=0.0, atol=1e-15)


def test_measured_block_covariance_is_honored_statistically(zones: dict[str, StorageZoneDoc]) -> None:
    cov = [[0.04, 300.0, 0.0], [300.0, 4e6, 0.0], [0.0, 0.0, 0.0]]
    model = _measured_model(cov)
    draws = uncertainty.draw_parameters(
        model, 100.0, 1.0, zones, [], uncertainty.batch_generator(9, 0), 200_000
    )
    sample = np.cov(np.stack([draws.ln_k_ref_per_s[:, 0], draws.ea_j_per_mol[:, 0]]))
    assert sample[0, 1] == pytest.approx(300.0, rel=0.02)
    assert sample[0, 0] == pytest.approx(0.04, rel=0.02)
    assert draws.b_per_percent_rh[:, 0].tolist() == [0.02] * 200_000  # zero variance: exactly the mean


def test_measured_block_errors(zones: dict[str, StorageZoneDoc]) -> None:
    bad = _measured_model([[0.04, 0.0, 0.3], [0.0, 4e6, 0.0], [0.3, 0.0, 0.01]])
    with pytest.raises(ComputationError):
        uncertainty.draw_parameters(bad, 100.0, 1.0, zones, [], uncertainty.batch_generator(1, 0), 4)
    only_ea = b.model(
        [b.pathway("api:h", b.block(["ea_j_per_mol"], [85000.0], [100.0], layer="measured_model"))]
    )
    draws = uncertainty.draw_parameters(only_ea, 100.0, 1.0, zones, [], uncertainty.batch_generator(1, 0), 4)
    assert draws.ln_k_ref_per_s[:, 0].tolist() == [0.0] * 4  # not a measured ln k: plain path
    no_ea = _measured_model([[0.04, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1e-4]])
    draws = uncertainty.draw_parameters(no_ea, 100.0, 1.0, zones, [], uncertainty.batch_generator(1, 0), 4)
    assert draws.ea_j_per_mol[:, 0].tolist() == [85000.0] * 4
    solo = b.model([b.pathway("api:h", b.block(["ln_k_ref_per_s"], [-17.0], [0.0], layer="measured_model"))])
    draws = uncertainty.draw_parameters(solo, 100.0, 1.0, zones, [], uncertainty.batch_generator(1, 0), 4)
    assert draws.ln_k_ref_per_s[:, 0].tolist() == [-17.0] * 4


class _FixedVariates:
    """A generator stand-in returning a given Z and U = 1/2 (spec 9.7 call order)."""

    def __init__(self, z: Any) -> None:
        self.z = np.asarray(z, dtype=np.float64)

    def standard_normal(self, shape: tuple[int, int]) -> Any:
        assert shape == self.z.shape
        return self.z.copy()

    def random(self, shape: tuple[int, int]) -> Any:
        return np.full(shape, 0.5)


def _free_model(
    deltas: list[float],
    method_sd: list[float],
    *,
    mu_total: float = -17.0,
    s_p: float = 0.8,
    anchored: int = 0,
) -> Any:
    """N = len(deltas) + anchored split members: the free ones carry free_share and the
    spec 9.5 mean mu_total + ln(N_F/N) + delta_i - ln sum exp(delta); anchored ones keep
    mu_total - ln N with w = 0.5."""
    n_free = len(deltas)
    total = n_free + anchored
    lse = math.log(math.fsum(math.exp(d) for d in deltas))
    paths = []
    for i, (delta, sd) in enumerate(zip(deltas, method_sd, strict=True)):
        mean = mu_total + math.log(n_free / total) + delta - lse
        block = b.block(["ln_k_ref_per_s", "ea_j_per_mol"], [mean, 90000.0], [s_p, 5000.0], shared_weight=1.0)
        paths.append(dataclasses.replace(b.pathway(f"api:f{i}", block), free_share=FreeShare(delta, sd)))
    for i in range(anchored):
        block = b.block(
            ["ln_k_ref_per_s", "ea_j_per_mol"],
            [mu_total - math.log(total), 90000.0],
            [s_p, 5000.0],
            shared_weight=0.5,
        )
        paths.append(b.pathway(f"api:x{i}", block))
    return b.model(paths)


def _without_free_share(model: Any) -> Any:
    return dataclasses.replace(
        model, pathways=tuple(dataclasses.replace(p, free_share=None) for p in model.pathways)
    )


def test_free_group_hand_values(zones: dict[str, StorageZoneDoc]) -> None:
    """Spec 9.5 on a fixed Z. delta = (0, ln 3) gives shares (1/4, 3/4); s_m = (1, 0) and
    z_a = ln 3 shift member a to delta + s_m z = (ln 3, ln 3), shares (1/2, 1/2): both
    rates become exp(mu_total + s_p z_cls) / 2. At z = 0 nothing moves."""
    model = _free_model([0.0, math.log(3.0)], [1.0, 0.0])
    z = np.zeros((3, 2 + 8 * 2))
    z[0, 2] = math.log(3.0)  # member a's own ln k column
    z[1, 2], z[1, 1] = math.log(3.0), 0.5  # and z_cls = 0.5
    z[1, 10] = 7.0  # member b's ln k column: s_m,b = 0, so it never matters
    draws = uncertainty.draw_parameters(model, 100.0, 1.0, zones, [], _FixedVariates(z), 3)  # type: ignore[arg-type]
    half = -17.0 - math.log(2.0)
    assert draws.ln_k_ref_per_s[0].tolist() == pytest.approx([half, half], rel=1e-15)
    assert draws.ln_k_ref_per_s[1].tolist() == pytest.approx([half + 0.4, half + 0.4], rel=1e-15)
    nominal = [-17.0 - math.log(4.0), -17.0 + math.log(3.0) - math.log(4.0)]
    assert draws.ln_k_ref_per_s[2].tolist() == pytest.approx(nominal, rel=1e-15)
    assert draws.ln_k_ref_per_s[2].tolist() == [p.parameters.mean[0] for p in model.pathways]


def test_free_group_conserves_the_total_in_every_draw(zones: dict[str, StorageZoneDoc]) -> None:
    deltas, method_sd = [0.0, -2.3, 1.1, -5.0], [0.5, 3.0, 0.0, 1.2]
    model = _free_model(deltas, method_sd, anchored=1)
    n = 10_000
    draws = uncertainty.draw_parameters(
        model, 100.0, 1.0, zones, ["station_kit"], uncertainty.batch_generator(3, 1), n
    )
    plain = uncertainty.draw_parameters(
        _without_free_share(model), 100.0, 1.0, zones, ["station_kit"], uncertainty.batch_generator(3, 1), n
    )
    z = uncertainty.batch_generator(3, 1).standard_normal((n, 2 + 8 * 5 + 3))
    free = draws.ln_k_ref_per_s[:, :4]
    total = np.exp(free).sum(axis=1)
    expected = np.exp(-17.0 + 0.8 * z[:, 1]) * 4.0 / 5.0
    assert np.allclose(total, expected, rtol=1e-12, atol=0.0)
    # Each member against the formula with z_F from its own ln k column.
    shifted = np.array(deltas)[None, :] + np.array(method_sd)[None, :] * z[:, [2, 10, 18, 26]]
    lse_x = np.log(np.exp(shifted).sum(axis=1))
    lse_d = math.log(math.fsum(math.exp(d) for d in deltas))
    term = (shifted - lse_x[:, None]) - (np.array(deltas) - lse_d)[None, :]
    assert np.allclose(free, plain.ln_k_ref_per_s[:, :4] + term, rtol=0.0, atol=1e-12)
    # The anchored member, the other parameters and the zone columns are untouched.
    assert np.array_equal(draws.ln_k_ref_per_s[:, 4], plain.ln_k_ref_per_s[:, 4])
    assert np.array_equal(draws.ea_j_per_mol, plain.ea_j_per_mol)
    assert np.array_equal(
        draws.temperature_offset_k["station_kit"], plain.temperature_offset_k["station_kit"]
    )
    assert np.array_equal(draws.c0_percent, plain.c0_percent)
    # A member with s_m = 0 still moves (its share changes when the others move).
    assert not np.array_equal(free[:, 2], plain.ln_k_ref_per_s[:, 2])


def test_free_group_extreme_shifts_stay_finite(zones: dict[str, StorageZoneDoc]) -> None:
    """The log-sum-exp subtracts the maximum: deltas of +-700 do not overflow."""
    model = _free_model([700.0, -700.0, 0.0], [3.0, 3.0, 3.0], mu_total=-17.0)
    draws = uncertainty.draw_parameters(model, 100.0, 1.0, zones, [], uncertainty.batch_generator(4, 0), 1000)
    assert bool(np.all(np.isfinite(draws.ln_k_ref_per_s)))
    z = uncertainty.batch_generator(4, 0).standard_normal((1000, 2 + 8 * 3))
    assert np.allclose(
        np.exp(draws.ln_k_ref_per_s).sum(axis=1), np.exp(-17.0 + 0.8 * z[:, 1]), rtol=1e-12, atol=0.0
    )


@pytest.mark.parametrize(
    ("deltas", "method_sd", "anchored"),
    [
        ([0.4], [2.0], 2),  # a group of one: log_softmax of one member is 0
        ([0.7, 0.7, 0.7], [0.0, 0.0, 0.0], 0),  # every s_m 0, deltas equal
        ([0.0, -3.0], [0.0, 0.0], 1),  # every s_m 0: the shares never move
    ],
)
def test_free_group_without_effect(
    zones: dict[str, StorageZoneDoc], deltas: list[float], method_sd: list[float], anchored: int
) -> None:
    model = _free_model(deltas, method_sd, anchored=anchored)
    found = uncertainty.draw_parameters(model, 100.0, 1.0, zones, [], uncertainty.batch_generator(6, 2), 256)
    plain = uncertainty.draw_parameters(
        _without_free_share(model), 100.0, 1.0, zones, [], uncertainty.batch_generator(6, 2), 256
    )
    assert np.array_equal(found.ln_k_ref_per_s, plain.ln_k_ref_per_s)


def test_free_group_skips_measured_blocks(zones: dict[str, StorageZoneDoc]) -> None:
    cov = [[0.04, 0.0, 0.0], [0.0, 4e6, 0.0], [0.0, 0.0, 1e-4]]
    measured = _measured_model(cov)
    path = dataclasses.replace(measured.pathways[0], free_share=FreeShare(1.0, 2.0))
    other = dataclasses.replace(_free_model([0.0, 1.0], [2.0, 2.0]).pathways[0], pathway_id="api:z")
    model = dataclasses.replace(measured, pathways=(path, other))
    found = uncertainty.draw_parameters(model, 100.0, 1.0, zones, [], uncertainty.batch_generator(8, 0), 64)
    plain = uncertainty.draw_parameters(
        _without_free_share(model), 100.0, 1.0, zones, [], uncertainty.batch_generator(8, 0), 64
    )
    assert np.array_equal(found.ln_k_ref_per_s, plain.ln_k_ref_per_s)  # one class-based member left: no group


def test_free_group_errors(zones: dict[str, StorageZoneDoc]) -> None:
    model = _free_model([0.0, 1.0], [0.5, 0.5])
    first = model.pathways[0]
    broken_paths = (
        dataclasses.replace(first, free_share=FreeShare(math.inf, 0.5)),
        dataclasses.replace(first, free_share=FreeShare(0.0, math.nan)),
        dataclasses.replace(first, free_share=FreeShare(0.0, -0.1)),
        dataclasses.replace(first, parameters=dataclasses.replace(first.parameters, shared_weight=0.5)),
        dataclasses.replace(
            first, parameters=b.block(["ea_j_per_mol"], [90000.0], [5000.0], shared_weight=1.0)
        ),
    )
    for broken in broken_paths:
        with pytest.raises(ModelDomainError):
            uncertainty.draw_parameters(
                dataclasses.replace(model, pathways=(broken, model.pathways[1])),
                100.0,
                1.0,
                zones,
                [],
                uncertainty.batch_generator(1, 0),
                4,
            )


def test_draw_parameters_errors(zones: dict[str, StorageZoneDoc]) -> None:
    model = b.thermal_model()
    generator = uncertainty.batch_generator(1, 0)

    def draw(
        m: Any = model, mean: float = 100.0, sd: float = 1.0, zone_ids: tuple[str, ...] = (), n: int = 4
    ) -> None:
        uncertainty.draw_parameters(m, mean, sd, zones, zone_ids, generator, n)

    with pytest.raises(InputError):
        draw(n=0)
    with pytest.raises(InputError):
        draw(m=dataclasses.replace(model, pathways=model.pathways[::-1]))
    with pytest.raises(InputError):
        draw(zone_ids=("nowhere",))
    with pytest.raises(InputError):
        draw(mean=math.nan)
    with pytest.raises(InputError):
        draw(sd=-1.0)
    with pytest.raises(ComputationError):
        draw(mean=-50.0)
    path = model.pathways[0]
    for params, error in (
        (dataclasses.replace(path.parameters, mean=(1.0,)), ModelDomainError),
        (dataclasses.replace(path.parameters, mean=(math.nan, 9e4, 0.03)), ModelDomainError),
        (
            dataclasses.replace(
                path.parameters, covariance=((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            ),
            ModelDomainError,
        ),
        (dataclasses.replace(path.parameters, shared_weight=1.5), ModelDomainError),
    ):
        broken = dataclasses.replace(
            model, pathways=(dataclasses.replace(path, parameters=params), *model.pathways[1:])
        )
        with pytest.raises(error):
            draw(m=broken)


# =========================================================================================
# Adaptive Monte Carlo (spec 9.9)
# =========================================================================================


def _normal_batches(spread: float = 1.0, weights: bool = False, extras: bool = True) -> Any:
    def evaluate(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        values = 50.0 + spread * generator.standard_normal(10_000)
        log_w = -0.5 * ((values - 50.0) / 2.0) ** 2 if weights else None
        extra = {"double": 2.0 * values, "batch": np.full(10_000, float(r))} if extras else {}
        return uncertainty.BatchOutcome(values, log_w, extra)

    return evaluate


def test_adaptive_constant_converges_at_min_batches(settings: EngineSettingsDoc) -> None:
    def constant(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.full(10_000, 97.5), None, {})

    result = uncertainty.run_adaptive(constant, 1, settings["monte_carlo"])
    assert result.converged
    assert result.n_batches == 2
    assert result.batch_size == 10_000
    assert result.numerical_tolerance == 0.0
    assert result.log_weights is None
    assert result.batch_statistics == ((97.5, 0.0, 97.5, 97.5),) * 2


def test_adaptive_normal_converges_deterministically(settings: EngineSettingsDoc) -> None:
    result = uncertainty.run_adaptive(_normal_batches(), 11, settings["monte_carlo"])
    again = uncertainty.run_adaptive(_normal_batches(), 11, settings["monte_carlo"])
    assert result.converged
    assert 2 <= result.n_batches < 100
    assert result.values.size == result.n_batches * 10_000
    assert np.array_equal(result.values, again.values)
    assert np.array_equal(result.extras["double"], 2.0 * result.values)
    assert result.extras["batch"][-1] == result.n_batches - 1
    assert result.numerical_tolerance == uncertainty.numerical_tolerance(float(np.std(result.values)), 2)
    table = np.array(result.batch_statistics)
    assert bool(
        np.all(
            2.0 * np.std(table, axis=0, ddof=1) / math.sqrt(result.n_batches) <= result.numerical_tolerance
        )
    )


def test_adaptive_stops_unconverged_at_max_batches(settings: EngineSettingsDoc) -> None:
    result = uncertainty.run_adaptive(_normal_batches(), 3, _mc(settings, max_batches=3, n_dig=6))
    assert not result.converged
    assert result.n_batches == 3


def test_adaptive_with_weights_and_ess_floor(settings: EngineSettingsDoc) -> None:
    result = uncertainty.run_adaptive(
        _normal_batches(weights=True), 4, settings["monte_carlo"], ess_floor=1000.0
    )
    assert result.converged
    assert result.log_weights is not None
    assert result.log_weights.size == result.values.size
    starved = uncertainty.run_adaptive(
        _normal_batches(weights=True), 4, _mc(settings, max_batches=4), ess_floor=1e9
    )
    assert not starved.converged
    assert starved.n_batches == 4


def test_adaptive_errors(settings: EngineSettingsDoc) -> None:
    mc = settings["monte_carlo"]

    def short(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.zeros(10), None, {})

    def flipping(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.full(10_000, float(r)), np.zeros(10_000) if r else None, {})

    def renamed(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.full(10_000, float(r)), None, {f"x{r}": np.zeros(10_000)})

    def bad_weights(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.zeros(10_000), np.zeros(3), {})

    def infinite(r: int, generator: np.random.Generator) -> uncertainty.BatchOutcome:
        return uncertainty.BatchOutcome(np.full(10_000, math.inf), None, {})

    for evaluate in (short, flipping, renamed, bad_weights, infinite):
        with pytest.raises(ComputationError):
            uncertainty.run_adaptive(evaluate, 1, mc)
    for changes in ({"min_batches": 1}, {"min_batches": 5, "max_batches": 4}, {"max_batches": 0}):
        with pytest.raises(InputError):
            uncertainty.run_adaptive(_normal_batches(), 1, _mc(settings, **changes))
