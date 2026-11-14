# ruff: noqa: PLC0415
"""Independent exact checks of the new reference and correlated draw rules."""

import copy
import dataclasses
import math

import numpy as np
import pytest

from pharmacy.config import ConfigBundle
from pharmacy.types import FreeShare, ParameterBlock, ZoneCorrection
from tests.oracle import chemistry as oracle
from tests.oracle.compare import assert_agrees
from tests.oracle.precision import MP
from tests.oracle.test_agreement_draws import solid_model


def test_free_share_draws_match_log_softmax_per_draw(bundle: ConfigBundle) -> None:
    from pharmacy.uncertainty import draw_parameters

    model = solid_model("F1", {})
    deltas, errors = [2.0, -3.0], [0.7, 1.2]
    priors = oracle.split_class_prior(
        -20.0, 0.9, [oracle.SplitMember(False, d, s) for d, s in zip(deltas, errors, strict=True)]
    )
    paths = tuple(
        dataclasses.replace(
            p,
            parameters=ParameterBlock(
                ("ln_k_ref_per_s",), (float(priors[i].mu),), ((0.9**2,),), 0.0, 1.0, ()
            ),
            free_share=FreeShare(deltas[i], errors[i]),
        )
        for i, p in enumerate(model.pathways[:2])
    )
    model = dataclasses.replace(model, pathways=paths)
    seed, n = 3217, 19
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    result = draw_parameters(model, 100.0, 1.5, zones, ["ground_storage"], np.random.default_rng(seed), n)
    z = np.random.default_rng(seed).standard_normal((n, 2 + 8 * len(paths) + 3))
    for r in range(n):
        expected = oracle.free_group_ln_k(
            [float(p.mu) for p in priors],
            0.9,
            float(z[r, 1]),
            deltas,
            errors,
            [float(z[r, 2]), float(z[r, 10])],
        )
        for i in range(len(paths)):
            assert_agrees(
                "draw_parameters free share",
                {"r": r, "i": i},
                result.ln_k_ref_per_s[r][i],
                expected[i],
                "9.5",
            )


@pytest.mark.parametrize("custody", [["ground_storage"], ["station_kit"]])
def test_anchor_correction_exact_shared_zone_draw(bundle: ConfigBundle, custody: list[str]) -> None:
    from pharmacy.uncertainty import draw_parameters

    zones = copy.deepcopy({z["zone_id"]: z for z in bundle.zones["zones"]})
    zones["station_kit"]["rate_multiplier"].update({"family": "lognormal", "mu": 0.5, "sigma": 0.4})
    model = solid_model("F1", {})
    sp, va, sz = 0.9, 0.25, 0.4
    vc = va + sz**2
    post, _, _ = oracle.correlated_anchor_moments(sp, va, sz)
    variance = float(post)
    path = dataclasses.replace(
        model.pathways[0],
        parameters=ParameterBlock(("ln_k_ref_per_s",), (-20.0,), ((variance,),), 0.0, variance / sp**2, ()),
        zone_correction=ZoneCorrection("station_kit", variance / vc, sz),
    )
    model = dataclasses.replace(model, pathways=(path,))
    seed, n = 8291, 23
    result = draw_parameters(model, 100.0, 1.5, zones, custody, np.random.default_rng(seed), n)
    zone_ids = sorted({*custody, "station_kit"})
    z = np.random.default_rng(seed).standard_normal((n, 2 + 8 + 3 * len(zone_ids)))
    column = 10 + 3 * zone_ids.index("station_kit")
    assert tuple(result.zone_ln_multiplier) == tuple(zone_ids)
    for r in range(n):
        expected = oracle.correlated_anchor_draw(
            -20.0, math.sqrt(variance), sp, vc, sz, float(z[r, 1]), float(z[r, 2]), sz * float(z[r, column])
        )
        assert_agrees(
            "draw_parameters zone_correction",
            {"r": r, "custody": custody},
            result.ln_k_ref_per_s[r][0],
            expected,
            "9.3/9.7",
        )


def test_packaged_anchors_match_reference_conversion(bundle: ConfigBundle) -> None:
    from pharmacy.chemistry.parameters import anchor_at_reference

    packages = {p["package_id"]: p for p in bundle.packages["packages"]}
    zones = {z["zone_id"]: z for z in bundle.zones["zones"]}
    for anchor in [a for c in bundle.reaction_classes["classes"] for a in c["anchors"]]:
        zone = zones[anchor["storage_zone_id"]]["rate_multiplier"]
        mu = zone["mu"] if zone["family"] == "lognormal" else math.log(zone["mu"])
        sd = zone["sigma"] if zone["family"] == "lognormal" else 0.0
        duration = anchor["duration_days"]
        package_id = anchor["package_id"]
        package = None if package_id is None else packages[package_id]
        rate = anchor["ln_k_per_s"]
        expected_mean, expected_variance = oracle.packaged_reference_rate(
            rate["mu"],
            rate["sigma"],
            anchor["temperature_k"]["value"],
            anchor["temperature_sd_k"]["value"],
            anchor["rh_percent"]["value"],
            90000.0,
            12000.0,
            0.04,
            0.01,
            298.15,
            40.0,
            1.0 if duration is None else duration["value"] * 86400.0,
            package,
            mu,
            sd,
        )
        actual = anchor_at_reference(anchor, 90000.0, 12000.0, 0.04, 0.01, 298.15, 40.0, bundle=bundle)
        assert_agrees(
            "anchor_at_reference mean", {"anchor": anchor["anchor_id"]}, actual[0], expected_mean, "9.3"
        )
        assert_agrees(
            "anchor_at_reference sd",
            {"anchor": anchor["anchor_id"]},
            actual[1],
            MP.sqrt(expected_variance),
            "9.3",
        )


@pytest.mark.parametrize("package_id", ["original_bottle", "repackaged_bag", "original_vial"])
def test_total_rate_calibration_reference_factor(bundle: ConfigBundle, package_id: str) -> None:
    """Calibration's mean-rate factor independently converts each row before prior storage."""
    from pharmacy.calibration import Condition, FamilyPhysics, mean_rate_factor

    prior = bundle.reaction_classes["total_rate_priors"][0]
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == package_id)
    condition = Condition(
        prior["temperature_k"]["value"],
        prior["temperature_sd_k"]["value"],
        prior["rh_percent"]["value"],
        prior["rh_sd_percent"]["value"],
    )
    physics = FamilyPhysics(90000.0, 12000.0, 0.04, 0.01)
    duration = (880 - 13) * 86400.0  # Spec 9.5 Du calibration endpoints.
    actual = mean_rate_factor(bundle, package, condition, physics, duration)
    reference = bundle.settings["reference_conditions"]
    expected_ln, _ = oracle.packaged_reference_rate(
        0,
        0,
        condition.temperature_k,
        condition.temperature_sd_k,
        condition.rh_percent,
        physics.ea,
        physics.ea_sd,
        physics.b,
        physics.b_sd,
        reference["temperature_k"]["value"],
        reference["relative_humidity_percent"]["value"],
        duration,
        package,
    )
    assert_agrees(
        "calibration.mean_rate_factor", {"package": package_id}, actual, MP.exp(-expected_ln), "9.3/9.5"
    )


def test_zone_correction_rejects_changed_calibration_sd(bundle: ConfigBundle) -> None:
    from pharmacy.errors import ModelDomainError
    from pharmacy.uncertainty import draw_parameters

    zones = copy.deepcopy({z["zone_id"]: z for z in bundle.zones["zones"]})
    zones["station_kit"]["rate_multiplier"].update({"family": "lognormal", "mu": 0.5, "sigma": 0.4})
    model = solid_model("F1", {})
    path = dataclasses.replace(model.pathways[0], zone_correction=ZoneCorrection("station_kit", 0.5, 0.3))
    model = dataclasses.replace(model, pathways=(path,))
    with pytest.raises(ModelDomainError):
        draw_parameters(model, 100.0, 1.5, zones, ["ground_storage"], np.random.default_rng(15), 2)
