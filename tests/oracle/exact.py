"""Oracle for the exact-equality functions of spec 15.2: seed derivation (9.8), the JCGM 101
batch size and numerical tolerance (9.9), the weighted type-1 quantile (9.10), ICH Q1E and
Q3B helpers (11.6) and the lot ordering (11.5).

Exact rational or decimal arithmetic throughout (``fractions``, ``decimal``); no engine
module is imported. Interpretations where the spec is silent are stated in each docstring
and filed in docs/requests/B3.md.
"""

import decimal
import hashlib
import json
import math
from collections.abc import Sequence
from fractions import Fraction
from typing import Any, Literal

from pharmacy.types import LOT_STATE_RANK, Forecast, LotDecision, LotState, OrderingKey, PotencyEstimate

SEED_BYTES = 16
QUANTILE_SLACK = Fraction(1, 10**12)
"""Spec 9.10: smallest value with cumulative weight >= q - 1e-12."""


# --- Seeds (spec 1.4, 9.8) -------------------------------------------------------------------------
def canonical_json_bytes(value: Any) -> bytes:
    """Spec 1.4, restated: json.dumps(sort_keys, compact separators, ensure_ascii False,
    allow_nan False) encoded as UTF-8."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def derive_seed(material: Any) -> int:
    """int.from_bytes(sha256(canonical JSON)[:16], "big") (spec 9.8)."""
    return int.from_bytes(hashlib.sha256(canonical_json_bytes(material)).digest()[:SEED_BYTES], "big")


def seed_hex(seed: int) -> str:
    """32 lower-case hex digits, big endian (spec 9.8)."""
    return f"{seed:032x}"


# --- JCGM 101 (spec 9.9) ----------------------------------------------------------------------------
JCGM_MIN_BATCH = 10**4
JCGM_J_NUMERATOR = 100


def jcgm_batch_size(coverage_probability: float) -> int:
    """M = max(ceil(100 / (1 - p)), 10^4) with p read as the decimal number it is written
    as (its shortest repr), so p = 0.9995 gives J = 200000 exactly (request B3-R5)."""
    p = Fraction(repr(float(coverage_probability)))
    if not 0 < p < 1:
        raise ValueError("p must lie in (0, 1)")
    return max(math.ceil(JCGM_J_NUMERATOR / (1 - p)), JCGM_MIN_BATCH)


def numerical_tolerance(standard_uncertainty: float, n_dig: int) -> float:
    """delta = 0.5 x 10^l where u written with n_dig significant digits is c x 10^l, c an
    n_dig-digit integer (JCGM 101 7.9.2). u is taken at its exact binary value and rounded
    half-even; a rounding that carries into the next decade raises l (0.0996 with two digits
    is 0.10 = 10 x 10^-2). Returns the correctly rounded float of 0.5 x 10^l; u = 0 gives 0."""
    if n_dig < 1:
        raise ValueError("n_dig must be positive")
    u = decimal.Decimal(standard_uncertainty)
    if u < 0:
        raise ValueError("u must be non-negative")
    if u == 0:
        return 0.0
    context = decimal.Context(prec=n_dig, rounding=decimal.ROUND_HALF_EVEN)
    rounded = context.plus(u)
    _, digits, exponent = rounded.as_tuple()
    assert isinstance(exponent, int)
    # c x 10^l with c having exactly n_dig digits
    exponent_l = exponent + len(digits) - n_dig
    exact = decimal.Decimal(5) * decimal.Decimal(10) ** (exponent_l - 1)
    return float(exact)


# --- Weighted quantile (spec 9.10) -----------------------------------------------------------------
def weighted_quantile(values: Sequence[float], weights: Sequence[float] | None, probability: float) -> float:
    """Type-1 inverse weighted ECDF: stable ascending sort, normalized cumulative weights
    W_k, the smallest value with W_k >= p - 1e-12. Exact rational arithmetic."""
    if not values:
        raise ValueError("no values")
    n = len(values)
    w = [Fraction(1)] * n if weights is None else [Fraction(x) for x in weights]
    if len(w) != n or any(x < 0 for x in w):
        raise ValueError("weights must be non-negative and match the values")
    total = sum(w, Fraction(0))
    if total <= 0:
        raise ValueError("weights must not all be zero")
    order = sorted(range(n), key=lambda i: values[i])
    target = Fraction(probability) - QUANTILE_SLACK
    cumulative = Fraction(0)
    for i in order:
        cumulative += w[i] / total
        if cumulative >= target:
            return float(values[i])
    return float(values[order[-1]])


# --- ICH Q1E Appendix A (spec 11.6) --------------------------------------------------------------
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
Q1E_CASES: tuple[Q1eCase, ...] = (
    "no_significant_change_accelerated_little_variability",
    "no_significant_change_accelerated_with_statistics",
    "no_significant_change_accelerated_no_statistics",
    "significant_change_accelerated_none_intermediate_with_statistics",
    "significant_change_accelerated_none_intermediate_no_statistics",
    "refrigerated_with_statistics",
    "refrigerated_no_statistics",
    "significant_change_intermediate",
)


def q1e_max_shelf_life_months(case: Q1eCase, x_months: float) -> Fraction:
    """ICH Q1E Appendix A decision tree (MDS02), restated from the guideline:

    * room temperature, no significant change at accelerated: long-term data with little
      change and variability, or amenable to statistics and analysed: up to 2X but not
      beyond X + 12; not analysed: up to 1.5X but not beyond X + 6;
    * significant change at accelerated, none at intermediate: analysed 1.5X / X + 6; not
      analysed X + 3;
    * refrigerated: analysed 1.5X / X + 6; not analysed X + 3;
    * significant change at intermediate: no extrapolation (X)."""
    x = Fraction(repr(float(x_months)))
    two_x = min(2 * x, x + 12)
    one_half_x = min(Fraction(3, 2) * x, x + 6)
    table: dict[str, Fraction] = {
        "no_significant_change_accelerated_little_variability": two_x,
        "no_significant_change_accelerated_with_statistics": two_x,
        "no_significant_change_accelerated_no_statistics": one_half_x,
        "significant_change_accelerated_none_intermediate_with_statistics": one_half_x,
        "significant_change_accelerated_none_intermediate_no_statistics": x + 3,
        "refrigerated_with_statistics": one_half_x,
        "refrigerated_no_statistics": x + 3,
        "significant_change_intermediate": x,
    }
    return table[case]


# --- ICH Q3B(R2) Attachment 1 (spec 11.6) -------------------------------------------------------------
def _percent_of(tdi_mg: Fraction, mdd_mg: Fraction) -> Fraction:
    return tdi_mg * 100 / mdd_mg


def q3b_thresholds(maximum_daily_dose_mg: float) -> dict[str, Fraction | None]:
    """Thresholds in percent and their TDI limbs (mg) per spec 11.6; the lower of percent
    and TDI (expressed in percent of the MDD, exactly) applies. Band edges: reporting
    <= 1 g; identification < 1 mg, 1 to 10 mg, > 10 mg to 2 g, > 2 g; qualification
    < 10 mg, 10 to 100 mg, > 100 mg to 2 g, > 2 g (closed where the guideline writes "to")."""
    mdd = Fraction(repr(float(maximum_daily_dose_mg)))
    if mdd <= 0:
        raise ValueError("MDD must be positive")
    reporting = Fraction(1, 10) if mdd <= 1000 else Fraction(5, 100)
    if mdd < 1:
        id_pct, id_tdi = Fraction(1), Fraction(5, 1000)
    elif mdd <= 10:
        id_pct, id_tdi = Fraction(1, 2), Fraction(20, 1000)
    elif mdd <= 2000:
        id_pct, id_tdi = Fraction(2, 10), Fraction(2)
    else:
        id_pct, id_tdi = Fraction(1, 10), None
    if mdd < 10:
        q_pct, q_tdi = Fraction(1), Fraction(50, 1000)
    elif mdd <= 100:
        q_pct, q_tdi = Fraction(1, 2), Fraction(200, 1000)
    elif mdd <= 2000:
        q_pct, q_tdi = Fraction(2, 10), Fraction(3)
    else:
        q_pct, q_tdi = Fraction(15, 100), None
    identification = id_pct if id_tdi is None else min(id_pct, _percent_of(id_tdi, mdd))
    qualification = q_pct if q_tdi is None else min(q_pct, _percent_of(q_tdi, mdd))
    return {
        "reporting_percent": reporting,
        "identification_percent": identification,
        "identification_tdi_mg": id_tdi,
        "qualification_percent": qualification,
        "qualification_tdi_mg": q_tdi,
    }


def decimals_as_written(threshold: float) -> int:
    """Decimal places of the threshold's shortest repr with trailing zeros dropped (3.0
    is "3", 0.15 is "0.15"). Thresholds the guideline writes with a trailing zero (0.10%,
    1.0%) are ambiguous under this reading (request B3-R6)."""
    normalized = decimal.Decimal(repr(float(threshold))).normalize()
    exponent = normalized.as_tuple().exponent
    assert isinstance(exponent, int)
    return max(0, -exponent)


def q3b_exceeds(observed: float, threshold: float) -> bool:
    """Round ``observed`` half up (on its decimal repr) to the decimals of ``threshold`` as
    written, then exceeded when the rounded value is strictly greater (TV-Q3B-1)."""
    places = decimals_as_written(threshold)
    quantum = decimal.Decimal(1).scaleb(-places)
    rounded = decimal.Decimal(repr(float(observed))).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
    return rounded > decimal.Decimal(repr(float(threshold)))


# --- Ordering (spec 11.5) ------------------------------------------------------------------------------
def _quantile_at(estimate: PotencyEstimate, probability: float) -> float:
    for q in estimate.quantiles:
        if q.probability == probability:
            return q.value
    raise ValueError(f"estimate {estimate.estimate_id} has no quantile at {probability}")


def ordering_key(
    decision_state: LotState,
    lot_id: str,
    estimates: Sequence[PotencyEstimate],
    forecasts: Sequence[Forecast],
    lower_quantile: float,
) -> OrderingKey:
    """OrderingKey(state_rank, primary, lot_id) of spec 11.5. ``lower_quantile`` is
    ``decision.lower_quantile`` (the engine signature does not take it: request B3-R7)."""
    primary: float | None
    if decision_state == "in_specification":
        crossings = [f.crossing_s for f in forecasts if f.crossing_s is not None]
        primary = min(crossings) if crossings else None
    elif decision_state == "adjust":
        primary = -min(_quantile_at(e, lower_quantile) for e in estimates)
    else:
        primary = None
    return OrderingKey(state_rank=LOT_STATE_RANK[decision_state], primary=primary, lot_id=lot_id)


def sort_key(key: OrderingKey) -> tuple[int, bool, float, str]:
    """(state_rank, primary is None, primary or 0.0, lot_id) of spec 11.5."""
    return (key.state_rank, key.primary is None, key.primary if key.primary is not None else 0.0, key.lot_id)


def sort_lots(decisions: Sequence[LotDecision]) -> tuple[LotDecision, ...]:
    """Ascending by the sort key of each decision's ordering key."""
    return tuple(sorted(decisions, key=lambda d: sort_key(d.ordering_key)))
