# ruff: noqa: PLC0415
"""Fast checks that fits receive sensor logs, sample packages and complete input hashes."""

import dataclasses

from pharmacy.config import ConfigBundle
from tests.virtual.studies import chamber_run, design, load_designs, observation_digest, pull_log


def test_chamber_sensor_log_and_pull_package(bundle: ConfigBundle) -> None:
    from pharmacy.fit import StudyObservation

    designs = load_designs()
    study = design(designs, "ich")
    condition = study["conditions"][0]
    _, temperatures, _, log = chamber_run(bundle, study, condition, 10800.0, 20261114)
    assert log[0].t0_s == 0.0
    assert log[-1].t1_s == 10800.0
    assert log[0].temperature_k != temperatures[0]  # Independent sensor noise and bias.
    package = next(p for p in bundle.packages["packages"] if p["package_id"] == "original_bottle")
    clipped = pull_log(log, 6000.0, package)
    assert clipped[-1].t1_s == 6000.0
    assert all(s.package_id == package["package_id"] for s in clipped)
    assert pull_log(log, 0.0, package) == ()
    opened = pull_log(log, 6000.0, None)
    assert all(s.package_id is None for s in opened)
    observation = StudyObservation(6000.0, 298.15, 60.0, 99.0, 1.0, "long_term", clipped, package)
    digest = observation_digest([observation])
    assert digest == observation_digest([observation])
    assert digest != observation_digest([dataclasses.replace(observation, chamber_log=opened, package=None)])
    assert digest != observation_digest([dataclasses.replace(observation, sd_percent=2.0)])
