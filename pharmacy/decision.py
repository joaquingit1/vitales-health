"""Decision stage: lot states with reasons, adjustment eligibility, forecasts, ordering,
and the regulatory helper rules (ICH Q1E extrapolation, ICH Q3B thresholds).

ICH Q1E poolability (vector TV-Q1E-5): before batches are pooled, the slopes are tested
for equality first, then the intercepts, each at a significance level of 0.25 (to offset
the low power of a typical stability design). Different slopes: individual slopes and
intercepts with the pooled mean square error, and the shortest batch estimate governs.
Common slope, different intercepts: the common slope with individual intercepts. The
"with statistics" cases of ``q1e_max_shelf_life_months`` assume this analysis.

Owner: B1. Contract: docs/ENGINE-SPEC.md section 11.
"""

import dataclasses
import math
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Final, Literal

import numpy as np

from pharmacy.errors import ComputationError, InputError
from pharmacy.kinetics import FloatArray
from pharmacy.types import (
    LOT_STATE_RANK,
    AdjustmentEligibility,
    DecisionSettings,
    EligibilityCheck,
    EstimateBasis,
    Forecast,
    ForecastSettings,
    LotDecision,
    LotDoc,
    LotState,
    OrderingKey,
    Pins,
    PotencyEstimate,
    ProductDoc,
    Q3bBand,
    Q3bSettings,
    Reason,
    ReasonCode,
    ReasonSeverity,
    iso_to_seconds,
)

Q1eCase = Literal[
    "no_significant_change_accelerated_little_variability",
    "no_significant_change_accelerated_with_statistics",
    "no_significant_change_accelerated_no_statistics",
    "significant_change_accelerated_none_intermediate_with_statistics",
    "significant_change_accelerated_none_intermediate_no_statistics",
    "refrigerated_with_statistics",
    "refrigerated_no_statistics",
    "significant_change_intermediate",
]

# ICH Q1E Appendix A extrapolation limits (MDS02): regulatory rules, not tunable values.
_Q1E_DOUBLE: Final[float] = 2.0
_Q1E_DOUBLE_CAP_MONTHS: Final[float] = 12.0
_Q1E_HALF: Final[float] = 1.5
_Q1E_HALF_CAP_MONTHS: Final[float] = 6.0
_Q1E_NO_STATISTICS_MONTHS: Final[float] = 3.0


def q1e_max_shelf_life_months(case: Q1eCase, x_months: float) -> float:
    """Maximum proposed shelf life Y from X months of long-term data (ICH Q1E Appendix A,
    spec 11.6): little variability or statistics with no significant change at
    accelerated: min(2X, X + 12); no statistics: min(1.5X, X + 6); significant change at
    accelerated but not intermediate, with statistics: min(1.5X, X + 6), without: X + 3;
    refrigerated with statistics: min(1.5X, X + 6), without: X + 3; significant change at
    intermediate: X (no extrapolation)."""
    if not (math.isfinite(x_months) and x_months >= 0.0):
        raise InputError(f"X must be finite and non-negative, got {x_months!r}")
    if case in (
        "no_significant_change_accelerated_little_variability",
        "no_significant_change_accelerated_with_statistics",
    ):
        return min(_Q1E_DOUBLE * x_months, x_months + _Q1E_DOUBLE_CAP_MONTHS)
    if case in (
        "no_significant_change_accelerated_no_statistics",
        "significant_change_accelerated_none_intermediate_with_statistics",
        "refrigerated_with_statistics",
    ):
        return min(_Q1E_HALF * x_months, x_months + _Q1E_HALF_CAP_MONTHS)
    if case in (
        "significant_change_accelerated_none_intermediate_no_statistics",
        "refrigerated_no_statistics",
    ):
        return x_months + _Q1E_NO_STATISTICS_MONTHS
    if case == "significant_change_intermediate":
        return x_months
    raise InputError(f"unknown Q1E case {case!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class Q3bThresholds:
    """ICH Q3B(R2) Attachment 1 thresholds for a maximum daily dose, as percent of the
    drug (the lower of the percent and the total-daily-intake limit expressed in percent,
    rounded as the guideline tabulates)."""

    maximum_daily_dose_mg: float
    reporting_percent: float
    identification_percent: float
    identification_tdi_mg: float | None
    qualification_percent: float
    qualification_tdi_mg: float | None


def _q3b_row(table: Sequence[Q3bBand], dose_mg: float) -> tuple[float, float | None]:
    for row in table:
        upper = row["dose_upper_mg"]
        if (
            upper is None
            or dose_mg < upper["value"]
            or (row["upper_inclusive"] and dose_mg == upper["value"])
        ):
            tdi = row["tdi"]
            if tdi is not None and tdi["unit"] not in ("mg", "ug"):
                raise InputError("Q3B TDI unit must be mg or ug")
            mass = (
                None
                if tdi is None
                else float(Fraction(repr(tdi["value"])) / (1000 if tdi["unit"] == "ug" else 1))
            )
            return row["percent"]["value"], mass
    raise InputError(f"no Q3B row for {dose_mg!r} mg")


def q3b_thresholds(maximum_daily_dose_mg: float, *, settings: Q3bSettings) -> Q3bThresholds:
    """Thresholds from the configured ICH Q3B bands, in percent and mg (spec 11.6)."""
    dose = maximum_daily_dose_mg
    if not (math.isfinite(dose) and dose > 0.0):
        raise InputError(f"the maximum daily dose must be positive, got {dose!r}")
    reporting, _ = _q3b_row(settings["reporting"], dose)

    def lower_of(percent: float, tdi: float | None) -> float:
        if tdi is None:
            return percent
        return min(percent, float(Fraction(repr(tdi)) * 100 / Fraction(repr(dose))))

    identification, identification_tdi = _q3b_row(settings["identification"], dose)
    qualification, qualification_tdi = _q3b_row(settings["qualification"], dose)
    return Q3bThresholds(
        maximum_daily_dose_mg=dose,
        reporting_percent=reporting,
        identification_percent=lower_of(identification, identification_tdi),
        identification_tdi_mg=identification_tdi,
        qualification_percent=lower_of(qualification, qualification_tdi),
        qualification_tdi_mg=qualification_tdi,
    )


def q3b_exceeds(observed: float, threshold: float, *, decimals: int | None = None) -> bool:
    """ICH Q3B comparison with the guideline's rounding: round ``observed`` half up to the
    number of decimals of ``threshold`` as written (for a TDI threshold of 3 mg: an integer)
    before comparing; exceeded when rounded > threshold (vector TV-Q3B-1)."""
    for name, value in (("observed", observed), ("threshold", threshold)):
        if not math.isfinite(value):
            raise InputError(f"{name} must be finite, got {value!r}")
    written = Decimal(repr(threshold)).normalize()
    exponent = written.as_tuple().exponent
    if decimals is None:
        decimals = max(0, -exponent) if isinstance(exponent, int) else 0
    if not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0:
        raise InputError("threshold decimals must be a non-negative integer")
    rounded = Decimal(repr(observed)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return rounded > written


# =========================================================================================
# Eligibility and decisions (spec 11.1 to 11.3)
# =========================================================================================

_CHECKS: Final[tuple[ReasonCode, ...]] = (
    "adjustment_not_allowed_by_product",
    "fixed_dose_device",
    "toxic_degradant",
    "estimate_outside_validated_range",
    "estimate_coverage_low",
)


def adjustment_eligibility(
    product: ProductDoc,
    estimates: Sequence[PotencyEstimate],
) -> tuple[bool, tuple[tuple[str, bool, str], ...]]:
    """Engine-side adjustment checks (spec 11.2), exactly these five, in this order and
    named by their blocking reason codes: ``adjustment_not_allowed_by_product``
    (potency_adjustment.allowed is false), ``fixed_dose_device``, ``toxic_degradant`` (a
    degradant of any ingredient has toxicity_flag "toxic"), ``estimate_outside_validated_range``
    and ``estimate_coverage_low`` (any estimate carries that flag). An unreviewed degradant
    profile and an assay-model conflict are info reasons only and never block.
    Returns (eligible, ((check name, passed, detail), ...)); eligible when all five pass."""
    toxic = sorted(
        {
            d["degradant_id"]
            for ingredient in product["ingredients"]
            for d in ingredient["degradants"]
            if d["toxicity_flag"] == "toxic"
        }
        | {
            d.degradant_id
            for estimate in estimates
            for d in estimate.degradants
            if d.toxicity_flag == "toxic"
        }
    )
    outside = sorted(e.ingredient_id for e in estimates if "outside_validated_range" in e.flags)
    coverage = sorted(e.ingredient_id for e in estimates if "coverage_low" in e.flags)
    allowed = product["potency_adjustment"]["allowed"]
    checks: tuple[tuple[str, bool, str], ...] = (
        (
            "adjustment_not_allowed_by_product",
            allowed,
            "the product allows potency adjustment"
            if allowed
            else "the product does not allow potency adjustment: "
            + (product["potency_adjustment"]["reason"] or "no reason given"),
        ),
        (
            "fixed_dose_device",
            not product["fixed_dose_device"],
            "the product is a fixed-dose device"
            if product["fixed_dose_device"]
            else "not a fixed-dose device",
        ),
        (
            "toxic_degradant",
            not toxic,
            f"toxic degradants: {', '.join(toxic)}" if toxic else "no toxic degradant",
        ),
        (
            "estimate_outside_validated_range",
            not outside,
            f"outside the validated range: {', '.join(outside)}"
            if outside
            else "every estimate inside the range",
        ),
        (
            "estimate_coverage_low",
            not coverage,
            f"low sensor coverage: {', '.join(coverage)}" if coverage else "sensor coverage adequate",
        ),
    )
    return all(passed for _, passed, _ in checks), checks


def _quantile(estimate: PotencyEstimate, probability: float) -> float:
    for entry in estimate.quantiles:
        if entry.probability == probability:
            return entry.value
    raise InputError(f"estimate {estimate.estimate_id!r} has no quantile at {probability!r}")


# Row rank of each reason in the table of spec 11.1, then the info reasons in listed order.
_REASON_ORDER: Final[tuple[ReasonCode, ...]] = (
    "inspection_found_change",
    "above_upper_limit",
    "degradant_above_limit",
    "degradant_unidentified_above_limit",
    "regime_switch",
    "below_lower_limit",
    "within_specification",
    "computed_only",
    "mc_not_converged",
    "assay_model_conflict",
    "degradant_profile_not_reviewed",
    "radiosensitive_dose_exceeded",
    "past_labeled_expiry",
)
_INFO_FLAGS: Final[tuple[ReasonCode, ...]] = (
    "computed_only",
    "mc_not_converged",
    "assay_model_conflict",
    "degradant_profile_not_reviewed",
    "radiosensitive_dose_exceeded",
)


@dataclasses.dataclass(frozen=True, slots=True)
class _Ranked:
    row: int
    ingredient: str
    sub: int
    reason: Reason


def _worst(states: Sequence[LotState]) -> LotState:
    return max(states, key=lambda state: LOT_STATE_RANK[state])


def decide_lot(
    lot: LotDoc,
    product: ProductDoc,
    estimates: Sequence[PotencyEstimate],
    forecasts: Sequence[Forecast],
    alternatives: tuple[tuple[str, ...], tuple[str, ...]],
    settings: DecisionSettings,
    pins: Pins,
) -> LotDecision:
    """State and reasons of one lot (spec 11.1 to 11.3). Per ingredient:
    in_specification if q_lower >= lower limit, q_upper <= upper limit and no degradant has
    P(above limit) > 1 - degradant_quantile; below lower limit: adjust when eligible else
    do_not_use; above upper limit or degradant above limit: do_not_use; inspection finding:
    do_not_use. Combination products take the worst ingredient state. ``alternatives`` is
    (lot ids of the same product, product ids with the same ingredient set)."""
    if not estimates:
        raise InputError(f"lot {lot['lot_id']!r} has no estimate to decide on")
    ordered = sorted(estimates, key=lambda e: e.ingredient_id)
    as_of_utc = ordered[0].as_of_utc
    as_of_s = ordered[0].as_of_s
    for estimate in ordered:
        if estimate.lot_id != lot["lot_id"] or estimate.as_of_s != as_of_s:
            raise InputError("every estimate must belong to the lot and share one as_of")
    lower_q = float(settings["lower_quantile"]["value"])
    upper_q = float(settings["upper_quantile"]["value"])
    degradant_q = float(settings["degradant_quantile"]["value"])
    eligible, checks = adjustment_eligibility(product, ordered)
    unidentified = sorted(
        {
            d.degradant_id
            for e in ordered
            for d in e.degradants
            if not d.identification.identified
            and d.p_above_limit is not None
            and d.p_above_limit >= 1.0 - degradant_q
        }
    )
    if unidentified:
        eligible = False
        checks = (
            *checks,
            (
                "degradant_unidentified_above_limit",
                False,
                "Inspect the lot or assay degradants: " + ", ".join(unidentified),
            ),
        )
    failed = [
        (name, detail)
        for name, passed, detail in checks
        if not passed and name != "degradant_unidentified_above_limit"
    ]
    ranked: list[_Ranked] = []

    def add(
        code: ReasonCode, severity: ReasonSeverity, ingredient_id: str | None, message: str, sub: int = 0
    ) -> None:
        ranked.append(
            _Ranked(
                row=_REASON_ORDER.index(code)
                if code in _REASON_ORDER
                else _REASON_ORDER.index("below_lower_limit"),
                ingredient="" if ingredient_id is None else ingredient_id,
                sub=sub,
                reason=Reason(code=code, severity=severity, ingredient_id=ingredient_id, message=message),
            )
        )

    states: list[LotState] = []
    inspected = next((e.inspection_override for e in ordered if e.inspection_override is not None), None)
    if inspected is not None:
        add(
            "inspection_found_change",
            "blocking",
            None,
            f"A crew inspection found {inspected}; do not use the lot.",
        )
        states.append("do_not_use")
    for estimate in ordered:
        ingredient = estimate.ingredient_id
        limits = estimate.specification
        q_lo = _quantile(estimate, lower_q)
        q_hi = _quantile(estimate, upper_q)
        applied = inspected is not None
        if q_hi > limits.upper_percent:
            add(
                "above_upper_limit",
                "blocking",
                ingredient,
                f"The {upper_q:g} quantile {q_hi:.2f}% is above the upper limit {limits.upper_percent:g}%.",
            )
            states.append("do_not_use")
            applied = True
        for degradant in sorted(estimate.degradants, key=lambda d: d.degradant_id):
            if degradant.p_above_limit is not None and degradant.p_above_limit >= 1.0 - degradant_q:
                if not degradant.identification.identified:
                    add(
                        "degradant_unidentified_above_limit",
                        "warning",
                        ingredient,
                        f"Unidentified degradant {degradant.degradant_id} may exceed its limit "
                        f"(probability {degradant.p_above_limit:.3f}). "
                        "Inspect the lot or assay the degradant; "
                        "potency adjustment is not allowed.",
                    )
                    continue
                add(
                    "degradant_above_limit",
                    "blocking",
                    ingredient,
                    f"Degradant {degradant.degradant_id} exceeds its limit with probability "
                    f"{degradant.p_above_limit:.3f}.",
                )
                states.append("do_not_use")
                applied = True
        if product["do_not_freeze"] and any(p.kind == "regime_frozen" for p in estimate.placards):
            add(
                "regime_switch",
                "blocking",
                ingredient,
                "The product must not freeze and was frozen or possibly frozen.",
            )
            states.append("do_not_use")
            applied = True
        if q_lo < limits.lower_percent:
            message = (
                f"The {lower_q:g} quantile {q_lo:.2f}% is below the lower limit {limits.lower_percent:g}%."
            )
            if eligible:
                add("below_lower_limit", "blocking", ingredient, message + " A dose adjustment is allowed.")
                states.append("adjust")
            else:
                add(
                    "below_lower_limit",
                    "blocking",
                    ingredient,
                    message + " A dose adjustment is not allowed.",
                )
                for sub, (name, detail) in enumerate(failed, start=1):
                    code = _check_code(name)
                    add(code, "blocking", ingredient, detail[:1].upper() + detail[1:] + ".", sub=sub)
                states.append("do_not_use")
            applied = True
        if not applied:
            add(
                "within_specification",
                "info",
                ingredient,
                f"The {lower_q:g} to {upper_q:g} quantiles lie within {limits.lower_percent:g}% to "
                f"{limits.upper_percent:g}%.",
            )
            states.append("in_specification")
        for flag in _INFO_FLAGS:
            if flag in estimate.flags:
                add(flag, "info", ingredient, _INFO_MESSAGES[flag])
    past_expiry = as_of_s >= iso_to_seconds(lot["labeled_expiry_utc"])
    if past_expiry:
        add("past_labeled_expiry", "info", None, "The lot is past its labeled expiry.")
    state = _worst(states)
    reasons = tuple(item.reason for item in sorted(ranked, key=lambda r: (r.row, r.ingredient, r.sub)))
    lot_forecasts = sorted(forecasts, key=lambda f: f.ingredient_id)
    bases: tuple[tuple[str, EstimateBasis], ...] = tuple((e.ingredient_id, e.estimate_basis) for e in ordered)
    return LotDecision(
        decision_id=f"{lot['lot_id']}:{as_of_utc}:decision",
        lot_id=lot["lot_id"],
        product_id=product["product_id"],
        as_of_utc=as_of_utc,
        as_of_s=as_of_s,
        state=state,
        reasons=reasons,
        ordering_key=ordering_key(state, lot["lot_id"], ordered, lot_forecasts),
        estimate_ids=tuple(e.estimate_id for e in ordered),
        forecast_ids=tuple(f.forecast_id for f in lot_forecasts),
        adjustment=AdjustmentEligibility(
            eligible=eligible,
            checks=tuple(
                EligibilityCheck(name=name, passed=passed, detail=detail) for name, passed, detail in checks
            ),
            estimate_bases=bases,
            assay_ids_used=tuple(sorted({a for e in ordered for a in e.assay_ids_used})),
            degradant_profile_reviewed=product["degradant_profile_reviewed"],
        ),
        alternative_lot_ids=tuple(alternatives[0]),
        alternative_product_ids=tuple(alternatives[1]),
        past_labeled_expiry=past_expiry,
        pins=pins,
    )


def _check_code(name: str) -> ReasonCode:
    for code in _CHECKS:
        if code == name:
            return code
    raise InputError(f"unknown eligibility check {name!r}")  # pragma: no cover - names come from _CHECKS


_INFO_MESSAGES: Final[dict[ReasonCode, str]] = {
    "computed_only": "No measured model contributes and no assay was used.",
    "mc_not_converged": "The Monte Carlo estimate did not converge within the batch limit.",
    "assay_model_conflict": "An assay conflicts with the model; the estimate was re-anchored to the assay.",
    "degradant_profile_not_reviewed": "The product's degradant profile has not been reviewed.",
    "radiosensitive_dose_exceeded": "A radiosensitive product received more than the tested absorbed dose.",
}


def ordering_key(
    decision_state: str, lot_id: str, estimates: Sequence[PotencyEstimate], forecasts: Sequence[Forecast]
) -> OrderingKey:
    """Ordering key of spec 11.5: in_specification uses the earliest forecast crossing
    time (None sorts last); adjust uses minus the lowest lower quantile; do_not_use has
    primary None."""
    if decision_state == "in_specification":
        crossings = [f.crossing_s for f in forecasts if f.crossing_s is not None]
        return OrderingKey(state_rank=0, primary=min(crossings) if crossings else None, lot_id=lot_id)
    if decision_state == "adjust":
        if not forecasts:
            # The lower-quantile probability is carried by the forecasts (docs/requests/B1.md R-12).
            raise InputError("ordering an adjust lot needs its forecasts (they carry the lower quantile)")
        probability = forecasts[0].quantile_probability
        lowest = min(_quantile(estimate, probability) for estimate in estimates)
        return OrderingKey(state_rank=1, primary=-lowest, lot_id=lot_id)
    if decision_state == "do_not_use":
        return OrderingKey(state_rank=2, primary=None, lot_id=lot_id)
    raise InputError(f"unknown lot state {decision_state!r}")


def _sort_tuple(key: OrderingKey) -> tuple[int, bool, float, str]:
    return (key.state_rank, key.primary is None, 0.0 if key.primary is None else key.primary, key.lot_id)


def sort_lots(decisions: Sequence[LotDecision]) -> tuple[LotDecision, ...]:
    """Sort by (state_rank, primary is None, primary or 0.0, lot_id) ascending."""
    return tuple(sorted(decisions, key=lambda d: _sort_tuple(d.ordering_key)))


def crossing_times(
    potency_at: Callable[[FloatArray], FloatArray],
    n: int,
    lower_limit_percent: float,
    settings: ForecastSettings,
) -> FloatArray:
    """Per-draw first time (s after as_of) at which potency falls below the lower limit
    (spec 11.4). ``potency_at(t)`` takes per-draw offsets t (shape (n,), s after as_of) and
    returns per-draw potency under the projection; it is non-increasing in t. Vectorized
    bisection on [0, horizon] to ``time_tolerance_s``: returns the upper end of the final
    bracket, ``math.inf`` when potency at the horizon is still >= the limit, and 0.0 when
    potency at as_of is already below it. The forecast is the weighted lower-quantile of
    these times (P(potency(t) < L) = P(T_cross <= t))."""
    if n <= 0:
        raise InputError(f"crossing times need at least one draw, got {n!r}")
    horizon = float(settings["horizon_s"]["value"])
    tolerance = float(settings["time_tolerance_s"]["value"])
    if not (math.isfinite(horizon) and horizon > 0.0 and math.isfinite(tolerance) and tolerance > 0.0):
        raise InputError("the forecast horizon and time tolerance must be positive")
    if not math.isfinite(lower_limit_percent):
        raise InputError("the lower limit must be finite")

    def evaluate(t: FloatArray) -> FloatArray:
        values = np.asarray(potency_at(t), dtype=np.float64)
        if values.shape != (n,) or not bool(np.all(np.isfinite(values))):
            raise ComputationError(f"potency_at must return {n} finite values")
        return values

    already = evaluate(np.zeros(n, dtype=np.float64)) < lower_limit_percent
    beyond = ~already & (evaluate(np.full(n, horizon, dtype=np.float64)) >= lower_limit_percent)
    active = ~already & ~beyond
    lo = np.zeros(n, dtype=np.float64)
    hi = np.full(n, horizon, dtype=np.float64)
    iterations = max(0, math.ceil(math.log2(horizon / tolerance)))
    for _ in range(iterations):
        if not bool(np.any(active)):
            break
        mid = np.where(active, 0.5 * (lo + hi), hi)
        below = evaluate(mid) < lower_limit_percent
        hi = np.where(active & below, mid, hi)
        lo = np.where(active & ~below, mid, lo)
    return np.where(already, 0.0, np.where(beyond, math.inf, hi))
