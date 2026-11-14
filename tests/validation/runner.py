# Engine modules are imported inside the functions that use them, so an unfinished module fails
# its own tests and never the collection of this file.
# ruff: noqa: PLC0415
"""Runs the engine on the validation cases and collects the rows of tests/validation/report.json
(spec 15.5: every row's prediction, interval, measured value and pass or fail)."""

import dataclasses
import datetime
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pharmacy.config import ConfigBundle
from pharmacy.types import iso_to_seconds
from tests.scenarios.bundles import lot_package_ids, stability_model
from tests.scenarios.predictive import capture_draws, measurement_interval
from tests.validation.products import ValidationCase, iso

REPORT_PATH = Path(__file__).resolve().parent / "report.json"


@dataclasses.dataclass(frozen=True, slots=True)
class RowResult:
    dataset: str
    row_number: int
    label: str
    arm: str
    day: int
    as_of_utc: str
    measured_percent: float | None
    measured_sd_percent: float | None
    median_percent: float | None
    interval_low_percent: float | None
    interval_high_percent: float | None
    interval_probability: float | None
    covered: bool | None
    estimate_basis: str | None
    flags: tuple[str, ...]
    excluded_reason: str | None
    error: str | None
    true_interval_low_percent: float | None = None
    true_interval_high_percent: float | None = None
    interval_kind: str = "measurement_predictive"


def _median(quantiles: Sequence[Any]) -> float | None:
    for q in quantiles:
        if q.probability == 0.5:
            return float(q.value)
    return None


def run_case(bundle: ConfigBundle, case: ValidationCase, first_day: int) -> list[RowResult]:
    """Estimate the case's lot at each measurement day (no sensor series, no assays, no
    inspections: the class-based engine on declared conditions)."""
    from pharmacy import pipeline

    start = datetime.datetime.strptime(case.start_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC)
    out = []
    for day, measured, sd in zip(
        case.measurement_days, case.measured_percent, case.measured_sd_percent, strict=True
    ):
        as_of = iso(start + datetime.timedelta(days=day - first_day))
        base: dict[str, Any] = {
            "dataset": case.dataset,
            "row_number": case.row_number,
            "label": case.label,
            "arm": case.arm,
            "day": day,
            "as_of_utc": as_of,
            "measured_percent": measured,
            "measured_sd_percent": sd,
            "excluded_reason": case.excluded_reason,
        }
        if day == first_day:
            out.append(
                RowResult(
                    **base,
                    median_percent=None,
                    interval_low_percent=None,
                    interval_high_percent=None,
                    interval_probability=None,
                    covered=None,
                    estimate_basis=None,
                    flags=(),
                    error="initial assay (input, not predicted)",
                )
            )
            continue
        try:
            packages = lot_package_ids(case.lot, iso_to_seconds(as_of))
            model = stability_model(bundle, case.product["product_id"], case.ingredient_id, packages)
            with capture_draws() as draws:
                estimate = pipeline.estimate_potency(
                    bundle, case.lot["lot_id"], case.ingredient_id, as_of, (), (), (), model=model
                )
            if not draws:
                raise AssertionError("pipeline did not expose its public run_adaptive result")
            if measured is not None and sd is None:
                raise AssertionError("measured row has no assay sd")
            interval = measurement_interval(
                draws[-1],
                0.0 if sd is None else sd,
                estimate.coverage_interval.probability,
                f"{estimate.pins.seed_hex}:{case.dataset}:{case.row_number}:{day}:{case.arm}",
            )
        except Exception as error:
            out.append(
                RowResult(
                    **base,
                    median_percent=None,
                    interval_low_percent=None,
                    interval_high_percent=None,
                    interval_probability=None,
                    covered=None,
                    estimate_basis=None,
                    flags=(),
                    error=f"{type(error).__name__}: {error}",
                )
            )
            continue
        covered = None if measured is None else bool(interval.low <= measured <= interval.high)
        out.append(
            RowResult(
                **base,
                median_percent=_median(estimate.quantiles),
                interval_low_percent=interval.low,
                interval_high_percent=interval.high,
                interval_probability=interval.probability,
                covered=covered,
                estimate_basis=estimate.estimate_basis,
                flags=tuple(str(f) for f in estimate.flags),
                error=None,
                true_interval_low_percent=estimate.coverage_interval.low,
                true_interval_high_percent=estimate.coverage_interval.high,
            )
        )
    return out


class Report:
    """Accumulates dataset sections during a validation session and writes report.json."""

    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, Any]] = {}

    def add(
        self, dataset: str, summary: dict[str, Any], rows: Sequence[RowResult] | Sequence[dict[str, Any]]
    ) -> None:
        self.datasets[dataset] = {
            "summary": summary,
            "rows": [dataclasses.asdict(r) if isinstance(r, RowResult) else dict(r) for r in rows],
        }

    def write(self, path: Path = REPORT_PATH) -> None:
        document = {
            "document_id": "validation-report",
            "description": "Validation rows of spec 15.5 written by tests/validation (B3).",
            "datasets": {k: self.datasets[k] for k in sorted(self.datasets)},
        }
        text = json.dumps(document, indent=1, ensure_ascii=True, allow_nan=False, default=_json_default)
        path.write_text(text + "\n", encoding="utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


REPORT = Report()
