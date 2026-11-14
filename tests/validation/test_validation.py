# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Validation against published datasets (spec 15.5, marker validation).

Pass rules live in tests/validation/rules.py and were fixed before the engine ran. A failed
rule is a finding reported in docs/VALIDATION-REPORT.md with its rows; it is never tuned
away and never marked xfail without the team lead's sign-off and a finding id.
"""

import math
from collections import defaultdict

import pytest

from pharmacy.config import ConfigBundle
from pharmacy.errors import ConfigReferenceError
from tests.validation import rules
from tests.validation.products import (
    V1_EXCLUDED_ROWS,
    ValidationCase,
    flight_zone,
    load_dataset,
    v1_cases,
    v6_cases,
    with_flight_condition,
)
from tests.validation.runner import REPORT, RowResult, run_case

pytestmark = pytest.mark.validation

SECOND_COMPONENT = {8: 7, 12: 11, 26: 25, 36: 35}
"""V1 rows sharing one formulation (V1 calibration_split.caution): second row to first."""
_RESULTS: dict[str, tuple[RowResult, ...]] = {}


def results(bundle: ConfigBundle, dataset: str) -> tuple[RowResult, ...]:
    """Engine rows of a dataset, computed once per session (the session has one bundle)."""
    if dataset not in _RESULTS:
        _RESULTS[dataset] = _compute(bundle, dataset)
    return _RESULTS[dataset]


def _compute(bundle: ConfigBundle, dataset: str) -> tuple[RowResult, ...]:
    cases: list[ValidationCase]
    if dataset == "V1":
        cases = [c for c in v1_cases("control") if c.excluded_reason is None]
        first = 13
    elif dataset == "V1-flight-all":
        try:
            flight_zone(bundle)
            bundle = with_flight_condition(bundle)
        except ConfigReferenceError as error:
            pytest.skip(str(error))
        cases = v1_cases("flight", all_rows=True)
        first = 13
    elif dataset == "V6":
        cases, first = v6_cases(), 0
    else:
        raise KeyError(dataset)
    return tuple(r for case in cases for r in run_case(bundle, case, first))


def _binomial_summary(rows: list[RowResult]) -> dict[str, object]:
    n = len(rows)
    k = sum(1 for r in rows if r.covered)
    return {
        "rule": (
            f"covered count k >= binom.ppf({rules.BINOMIAL_RULE_QUANTILE}, n, {rules.INTERVAL_PROBABILITY})"
        ),
        "n": n,
        "k": k,
        "k_min": rules.minimum_covered(n) if n else None,
        "passed": bool(n) and k >= rules.minimum_covered(n),
        "not_covered": [f"row {r.row_number} {r.label}" for r in rows if not r.covered],
        "engine_errors": sorted({r.error for r in rows if r.error}),
    }


def _failure_message(summary: dict[str, object], rows: list[RowResult]) -> str:
    lines = [f"k = {summary['k']} of n = {summary['n']}, need k >= {summary['k_min']}"]
    for r in rows:
        if not r.covered:
            lines.append(
                f"  row {r.row_number} {r.label}: measured {r.measured_percent}, interval "
                f"[{r.interval_low_percent}, {r.interval_high_percent}], median {r.median_percent}, "
                f"error {r.error}"
            )
    return "\n".join(lines)


def test_v1_du2011_control_day_880(validation_bundle: ConfigBundle) -> None:
    """V1: 16 even rows (22 and 30 excluded), day-880 control inside the 90% interval for
    k >= binom.ppf(0.05, 16, 0.90) = 12 rows. Expected miss, reported and not tuned:
    clavulanate (row 12). Co-formulated rows straddle the split, so V1 is not fully
    independent of the calibration."""
    all_rows = list(results(validation_bundle, "V1"))
    judged = [r for r in all_rows if r.day == 880]
    summary = _binomial_summary(judged)
    summary["caution"] = (
        "co-formulated rows (8, 12, 26, 36) share package and history with calibration rows 7, 11, 25, 35"
    )
    REPORT.add("V1", summary, all_rows)
    assert len(judged) == 16
    assert summary["passed"], _failure_message(summary, judged)


def test_v1_flight_arm_report_only(validation_bundle: ConfigBundle) -> None:
    """V1 flight arm in the declared station zone: report only (the rows must be produced)."""
    rows = [
        r
        for r in results(validation_bundle, "V1-flight-all")
        if r.row_number % 2 == 0 and r.row_number not in V1_EXCLUDED_ROWS
    ]
    judged = [r for r in rows if r.day == 880]
    summary = _binomial_summary(judged)
    summary["rule"] = "report only"
    summary.pop("passed")
    REPORT.add("V1-flight", summary, rows)
    errors = [r for r in rows if r.error and r.day != 13]
    assert not errors, f"{len(errors)} flight rows have no estimate: {errors[0].error}"


def test_v2_failure_counts_report_only(validation_bundle: ConfigBundle) -> None:
    """V2 and TV-SF-2: formulations whose predicted flight median is below its own USP lower limit at each
    payload, reported against the documented ranges (never asserted against them)."""
    rows = list(results(validation_bundle, "V1-flight-all"))
    limits = {
        c.row_number: c.product["ingredients"][0]["specification"]["lower_percent"]["value"]
        for c in v1_cases("flight", all_rows=True)
    }
    medians_by_day: dict[int, dict[int, float]] = defaultdict(dict)
    for row in rows:
        if row.day != 13 and row.median_percent is not None:
            medians_by_day[row.day][row.row_number] = row.median_percent
    by_day = {
        day: rules.failed_formulations(medians, limits, SECOND_COMPONENT)
        for day, medians in sorted(medians_by_day.items())
    }
    v2 = load_dataset("validation-du2011.json", "V2")
    payload_of_day = {
        row["parsed"]["payload_days"]["days"]: row["parsed"]["payload_days"]["payload"]
        for row in v2["expected"]["rows"]
    }
    counts = []
    for day in sorted(by_day):
        payload = payload_of_day.get(day)
        documented = rules.SF2_DOCUMENTED_RANGES.get(payload) if payload is not None else None
        predicted = sum(by_day[day].values())
        counts.append(
            {
                "day": day,
                "payload": payload,
                "formulations": len(by_day[day]),
                "predicted_failed_flight": predicted,
                "documented_range": list(documented) if documented else None,
                "inside_documented_range": None
                if documented is None
                else documented[0] <= predicted <= documented[1],
            }
        )
    REPORT.add(
        "V2",
        {
            "rule": "report only",
            "threshold": "each component row own USP lower limit",
            "row_lower_limits_percent": {str(n): limits[n] for n in sorted(limits)},
        },
        counts,
    )
    errors = [r for r in rows if r.error and r.day != 13]
    assert not errors, f"{len(errors)} flight rows have no estimate: {errors[0].error}"


def test_v6_wotring_550_days(validation_bundle: ConfigBundle) -> None:
    """V6: eight rows (aspirin excluded), declared station zone, initial assay 100 +- 1.5,
    measured %LC at 550 days inside the interval for k >= binom.ppf(0.05, 8, 0.90)."""
    rows = list(results(validation_bundle, "V6"))
    summary = _binomial_summary(rows)
    REPORT.add("V6", summary, rows)
    assert len(rows) == 8
    assert summary["passed"], _failure_message(summary, rows)


def test_v9_gcrsim_radiation_report(bundle: ConfigBundle) -> None:
    """V9 (amoxicillin arms excluded): at the delivered doses the upper-bound loss is below
    0.01 and the central loss below 1e-4 for every arm; potency rows are report only."""
    from pharmacy.chemistry import radiolysis

    v9 = load_dataset("validation-radiation.json", "V9")
    names = {
        "Acetaminophen": ("acetaminophen_325_tab", "acetaminophen"),
        "Ibuprofen": ("ibuprofen_400_tab", "ibuprofen"),
        "Promethazine": ("promethazine_25_tab", "promethazine"),
    }
    products = {p["product_id"]: p for p in bundle.formulary["products"]}
    g_mu = bundle.radiation["direct_effect_g_loss_mol_per_j"]["mu"]
    factor = bundle.settings["radiation"]["dose_rate_safety_factor"]["value"]
    arms = {
        "0_5_gy": v9["inputs"]["delivered_doses_mgy"]["0_5_gy"],
        "1_0_gy": v9["inputs"]["delivered_doses_mgy"]["1_0_gy"],
    }
    rows, failures = [], []
    for row in v9["expected"]["rows"]:
        p = row["parsed"]
        product_name = p["product_usp_range"]["product"].split()[0]
        if product_name == "Amoxicillin":
            continue
        product_id, ingredient_id = names[product_name]
        product = products[product_id]
        ingredient = next(i for i in product["ingredients"] if i["ingredient_id"] == ingredient_id)
        k_central = radiolysis.k_dose_direct(math.exp(g_mu), ingredient["molar_mass_g_per_mol"]["value"])
        k_upper = radiolysis.upper_bound_k_per_gy(bundle.radiation, product["dose_form_family"])
        for arm, delivered in arms.items():
            for dose_mgy in (delivered["low"], delivered["high"]):
                dose = dose_mgy / 1000.0
                central = radiolysis.central_loss_fraction(k_central, dose)
                upper = radiolysis.upper_bound_loss_fraction(k_upper, factor, dose)
                ok = upper < rules.V9_UPPER_BOUND_LIMIT and central < rules.V9_CENTRAL_LIMIT
                entry = {
                    "product": p["product_usp_range"]["product"],
                    "analysis": p["analysis"],
                    "arm": arm,
                    "dose_gy": dose,
                    "central_loss_fraction": central,
                    "upper_bound_loss_fraction": upper,
                    "passed": ok,
                    "measured_percent_arm": p[arm]["values"],
                    "measured_percent_controls": [
                        *p["jsc_control"]["values"],
                        *p["traveling_control"]["values"],
                    ],
                }
                rows.append(entry)
                if not ok:
                    failures.append(entry)
    summary = {
        "rule": "upper-bound loss < 0.01 and central loss < 1e-4 for every arm; potency rows report only",
        "n": len(rows),
        "passed": not failures,
        "note": "promethazine falls in every arm including controls (time trend, not radiation)",
    }
    REPORT.add("V9", summary, rows)
    assert rows
    assert not failures, failures


UNVERIFIED = {
    "V5": ("validation-opportunistic.json", "report only (unverified data)"),
    "V7": ("validation-iss-short.json", "report only (n = 1, unverified)"),
    "V8": ("validation-iss-short.json", "report only (unverified data)"),
    "V10": ("validation-radiation.json", "runs only once the rows are verified"),
    "V11": ("validation-radiation.json", "runs only once the rows are verified"),
    "V12": ("validation-radiation.json", "runs only once the rows are verified"),
    "V13-LYON": ("validation-terrestrial.json", "report only (unverified data)"),
    "V13-CANTRELL": ("validation-terrestrial.json", "report only (unverified data)"),
}


@pytest.mark.parametrize("vector_id", sorted(UNVERIFIED))
def test_unverified_datasets_are_skipped(vector_id: str) -> None:
    file_name, rule = UNVERIFIED[vector_id]
    data = load_dataset(file_name, vector_id)
    assert data["status"] == "unverified"
    REPORT.add(vector_id, {"rule": rule, "status": "unverified", "run": False, "note": data["note"]}, [])
    pytest.skip(f"{vector_id} is unverified: {rule}. {data['note']}")


@pytest.mark.parametrize("vector_id", ["V3", "V4"])
def test_not_engine_outputs(vector_id: str) -> None:
    data = load_dataset("validation-du2011.json", vector_id)
    REPORT.add(vector_id, {"rule": "not engine outputs; not used (spec 15.5)", "run": False}, [])
    pytest.skip(f"{vector_id}: not an engine output, not used (spec 15.5). {data['note']}")
