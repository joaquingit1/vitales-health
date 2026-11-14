"""One measured row per validation arm to prove predictive execution, not acceptance."""

import dataclasses
import time

import pytest

from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError
from tests.validation.products import flight_zone, v1_cases, v6_cases, with_flight_condition
from tests.validation.runner import REPORT, run_case

pytestmark = pytest.mark.validation


@pytest.mark.parametrize("dataset", ["V1", "V1-flight", "V6"])
def test_one_measured_row(validation_bundle: ConfigBundle, dataset: str) -> None:
    if dataset == "V1-flight":
        try:
            flight_zone(validation_bundle)
            validation_bundle = with_flight_condition(validation_bundle)
        except ConfigReferenceError as reason:
            pytest.skip(str(reason))
    cases = v6_cases() if dataset == "V6" else v1_cases("flight" if dataset == "V1-flight" else "control")
    case = next(case for case in cases if case.label.lower().startswith("ibuprofen"))
    case = dataclasses.replace(
        case,
        measurement_days=case.measurement_days[-1:],
        measured_percent=case.measured_percent[-1:],
        measured_sd_percent=case.measured_sd_percent[-1:],
    )
    started = time.perf_counter()
    rows = run_case(validation_bundle, case, 0 if dataset == "V6" else 13)
    elapsed = time.perf_counter() - started
    print(f"{dataset} one-row predictive smoke: {elapsed:.3f} wall s/replicate")
    REPORT.add(
        f"SMOKE {dataset}",
        {"rule": "execution only, no dataset acceptance claim", "seconds_per_row": elapsed},
        rows,
    )
    assert len(rows) == 1
    assert rows[0].error is None, rows[0].error
    assert rows[0].true_interval_low_percent is not None
    assert rows[0].interval_kind == "measurement_predictive"
