"""The agreement rule (CONTRACTS 23 steps 1 to 5 with OD1/Q8 and OD2; E30 3.4). Exact rational arithmetic,
inclusive at the tolerance (I30-06). A pending tolerance or a missing camera value never yields agree or disagree
(I30-07). Disagreement is no verdict on the helper: both readings are kept by the caller."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from crew_health.teach.domain.errors import InvalidValue

type Result = Literal["agree", "disagree", "no_reference"]
type PhraseKey = Literal["ok", "too_slow", "too_fast", "no_reference"]


@dataclass(frozen=True, slots=True)
class AgreementOutcome:
    result: Result
    difference: Fraction | None  # count minus camera; None without a comparison
    phrase_key: PhraseKey


def comparable_origins(count_origin: str, camera_origin: str | None) -> bool:
    """Only equal origins compare (3.6): LIVE with LIVE, SIM with SIM. REPLAY never pairs with a manual count."""
    return camera_origin is not None and camera_origin == count_origin and count_origin in ("LIVE", "SIM")


def evaluate_count_agreement(
    count: Fraction, camera: Fraction | None, tolerance: Fraction | None
) -> AgreementOutcome:
    if camera is None or tolerance is None:
        return AgreementOutcome("no_reference", None, "no_reference")
    difference = count - camera
    if abs(difference) <= tolerance:
        return AgreementOutcome("agree", difference, "ok")
    return AgreementOutcome("disagree", difference, "too_slow" if difference < 0 else "too_fast")


def rounded(value: Fraction, decimals: int) -> Decimal:
    """Round half even to the indicator's decimals (3.4), by integer arithmetic on the exact rational."""
    if not 0 <= decimals <= 6:
        raise InvalidValue("decimals_out_of_range")
    scaled = value * (10**decimals)
    whole, remainder = divmod(scaled.numerator, scaled.denominator)
    twice = remainder * 2
    if twice > scaled.denominator or (twice == scaled.denominator and whole % 2 == 1):
        whole += 1
    return Decimal(whole).scaleb(-decimals)


def exact_text(value: Fraction) -> str:
    """Storage text n/d, so 60/7 is never lost to a terminating decimal (3.4)."""
    return f"{value.numerator}/{value.denominator}"


def parse_exact(text: str) -> Fraction:
    numerator, _, denominator = text.partition("/")
    return Fraction(int(numerator), int(denominator))


MAX_DISPLAY_DECIMALS = 6


def display_decimals(
    count: Fraction, camera: Fraction | None, tolerance: Fraction | None, result: str, base: int
) -> int:
    """Decimals for the shown count, camera and difference so the numbers never contradict the verdict (3.4).

    The verdict uses exact values; the screen rounds half even to the indicator's decimals. When that rounding would
    put the shown difference, or the gap between the shown count and camera, on the other side of the tolerance (a
    96.4 camera against 86 shows "10" for a disagreement over a tolerance of 10), more decimals are shown, up to six.
    Counts are n * 60 / seconds and camera values are short decimals, so an exact difference that is not equal to
    the tolerance differs from it by far more than half a millionth and six decimals always separate them."""
    if camera is None or tolerance is None or result not in ("agree", "disagree"):
        return base
    for decimals in range(base, MAX_DISPLAY_DECIMALS + 1):
        shown_difference = Fraction(rounded(abs(count - camera), decimals))
        shown_gap = abs(Fraction(rounded(count, decimals)) - Fraction(rounded(camera, decimals)))
        if result == "agree" and shown_difference <= tolerance and shown_gap <= tolerance:
            return decimals
        if result == "disagree" and shown_difference > tolerance and shown_gap > tolerance:
            return decimals
    return MAX_DISPLAY_DECIMALS
