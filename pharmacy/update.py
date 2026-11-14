"""Bayesian assay update and inspection override.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 10.
"""

import dataclasses
import math
from collections.abc import Sequence

import numpy as np
from scipy.special import ndtr

from pharmacy.errors import InputError, ObservationError
from pharmacy.kinetics import FloatArray
from pharmacy.types import AssayObservation, InspectionObservation, UpdateSettings


@dataclasses.dataclass(frozen=True, slots=True)
class AssayPrediction:
    """Per-draw prediction of one assay: the engine's potency of the assayed measurand
    (total or active) at the assay time, percent of label claim, shape (n,)."""

    assay_id: str
    predicted_percent: FloatArray


def site_sd_percent(assay: AssayObservation, settings: UpdateSettings) -> float:
    """sd_site of spec 10.2: the onboard method sd or the ground inter-laboratory sd."""
    if assay.site == "onboard":
        return float(settings["onboard_method_sd_percent"]["value"])
    return float(settings["ground_inter_laboratory_sd_percent"]["value"])


def total_sd_percent(assay: AssayObservation, settings: UpdateSettings) -> float:
    """sqrt(sd_a^2 + sd_site^2); raises ``ObservationError`` unless sd_a > 0 and finite."""
    if not (math.isfinite(assay.sd_percent) and assay.sd_percent > 0.0):
        raise ObservationError(f"assay {assay.assay_id!r} needs a positive sd, got {assay.sd_percent!r}")
    if not math.isfinite(assay.value_percent):
        raise ObservationError(f"assay {assay.assay_id!r} has a non-finite value")
    return math.hypot(assay.sd_percent, site_sd_percent(assay, settings))


def assay_log_likelihood(
    assays: Sequence[AssayObservation],
    predictions: Sequence[AssayPrediction],
    settings: UpdateSettings,
) -> FloatArray:
    """Sum over assays of ln N(y_a; C_j(t_a), sd_a^2 + sd_site^2) per draw j (spec 10.2),
    with sd_site the ground inter-laboratory or onboard method standard deviation from
    settings. Raises ``ObservationError`` if an assay has no prediction or sd_a <= 0."""
    if not assays:
        raise ObservationError("the assay likelihood needs at least one assay")
    by_id = {prediction.assay_id: prediction for prediction in predictions}
    total: FloatArray | None = None
    for assay in sorted(assays, key=lambda a: a.assay_id):
        if assay.assay_id not in by_id:
            raise ObservationError(f"assay {assay.assay_id!r} has no prediction")
        predicted = np.asarray(by_id[assay.assay_id].predicted_percent, dtype=np.float64)
        if predicted.ndim != 1 or predicted.size == 0 or not bool(np.all(np.isfinite(predicted))):
            raise ObservationError(f"the prediction of assay {assay.assay_id!r} must be a finite 1-D array")
        sd = total_sd_percent(assay, settings)
        variance = sd * sd
        residual = assay.value_percent - predicted
        term = -0.5 * math.log(2.0 * math.pi * variance) - residual * residual / (2.0 * variance)
        if total is not None and total.shape != term.shape:
            raise ObservationError("assay predictions must have the same number of draws")
        total = term if total is None else total + term
    assert total is not None
    return np.asarray(total, dtype=np.float64)


def normalized_weights(log_weights: FloatArray) -> FloatArray:
    """w_j = exp(l_j - max l) / sum exp(l_k - max l)."""
    values = np.asarray(log_weights, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise InputError("log weights must be a non-empty 1-D array")
    if not bool(np.all(np.isfinite(values))):
        raise InputError("log weights must be finite")
    weights = np.exp(values - np.max(values))
    return np.asarray(weights / np.sum(weights), dtype=np.float64)


def effective_sample_size(weights: FloatArray) -> float:
    """ESS = 1 / sum w_j^2 for normalized weights (Kish)."""
    values = np.asarray(weights, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise InputError("weights must be a non-empty 1-D array")
    if not bool(np.all(np.isfinite(values))) or bool(np.any(values < 0.0)):
        raise InputError("weights must be finite and non-negative")
    total = float(np.sum(values))
    if not total > 0.0:
        raise InputError("weights must not all be zero")
    normalized = values / total
    return float(1.0 / np.sum(normalized * normalized))


def conflict_probability(
    assay: AssayObservation, prior_predicted_percent: FloatArray, total_sd_percent: float
) -> float:
    """Two-sided prior-predictive tail probability of an assay (spec 10.3):
    P = 2 min(F(y), 1 - F(y)) where F is the CDF of C_j + N(0, total_sd^2) over equally
    weighted prior draws j (mean of normal CDFs). Conflict when P < conflict_tail_probability."""
    if not (math.isfinite(total_sd_percent) and total_sd_percent > 0.0):
        raise ObservationError(f"the total assay sd must be positive, got {total_sd_percent!r}")
    predicted = np.asarray(prior_predicted_percent, dtype=np.float64)
    if predicted.ndim != 1 or predicted.size == 0 or not bool(np.all(np.isfinite(predicted))):
        raise ObservationError("prior predictions must be a finite non-empty 1-D array")
    standardized = (assay.value_percent - predicted) / total_sd_percent
    lower = float(np.mean(ndtr(standardized)))
    # The upper tail from its own CDF, so 1 - F never cancels when F is close to 1.
    upper = float(np.mean(ndtr(-standardized)))
    return min(1.0, 2.0 * min(lower, upper))


@dataclasses.dataclass(frozen=True, slots=True)
class ReanchorPlan:
    """Fallback re-anchor (spec 10.4): each draw's level at the anchor time becomes
    y* = mean_percent + sd_percent z_C0 (z_C0 the draw's own initial-content normal, so no
    new variates are consumed) and its initial content is rescaled to
    C0' = y* / ((1 - alpha_j(t_a)) rho_j(t_a)) (rho only for an ``active`` measurand); the
    draw keeps its own kinetics after t_a."""

    assay_id: str
    anchor_time_s: float
    mean_percent: float
    sd_percent: float


def plan_reanchor(assays: Sequence[AssayObservation], settings: UpdateSettings) -> ReanchorPlan:
    """Choose the latest assay (ties: highest assay_id) as the re-anchor point. Its sd is
    sqrt(sd_a^2 + sd_site^2)."""
    if not assays:
        raise ObservationError("a re-anchor needs at least one assay")
    latest = max(assays, key=lambda a: (a.measured_at_s, a.assay_id))
    return ReanchorPlan(
        assay_id=latest.assay_id,
        anchor_time_s=latest.measured_at_s,
        mean_percent=latest.value_percent,
        sd_percent=total_sd_percent(latest, settings),
    )


def inspection_override(
    inspections: Sequence[InspectionObservation], lot_id: str, as_of_s: float
) -> InspectionObservation | None:
    """The earliest inspection of ``lot_id`` at or before ``as_of_s`` whose finding is not
    ``no_change`` (spec 10.5), or None. A later ``no_change`` never clears an earlier
    finding."""
    findings = [
        inspection
        for inspection in inspections
        if inspection.lot_id == lot_id
        and inspection.inspected_at_s <= as_of_s
        and inspection.finding != "no_change"
    ]
    if not findings:
        return None
    return min(findings, key=lambda i: (i.inspected_at_s, i.inspection_id))
