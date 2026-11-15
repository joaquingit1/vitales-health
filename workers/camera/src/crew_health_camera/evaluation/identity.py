"""Optional face matching experiment (E06-03, V39), kept apart from acquisition.

Nothing in the worker's domain, application or adapters imports this module (import-linter contract
camera-identity-experiment-separate): a face match never pairs a camera, authorizes a write or picks a patient. It
only scores labeled probe and gallery pairs offline. An uncertain comparison (no score) is a rejection, and an empty
study is not measured.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal


@dataclass(frozen=True, slots=True)
class Comparison:
    probe_id: str
    gallery_id: str
    score: float | None


@dataclass(frozen=True, slots=True)
class IdentityReport:
    status: Literal["measured", "not_measured"]
    comparisons: int
    false_match_rate: Fraction | None
    false_non_match_rate: Fraction | None
    rejection_rate: Fraction | None


def _rate(numerator: int, denominator: int) -> Fraction | None:
    return None if denominator == 0 else Fraction(numerator, denominator)


def identity_report(comparisons: Sequence[Comparison], *, threshold: float) -> IdentityReport:
    if not comparisons:
        return IdentityReport("not_measured", 0, None, None, None)
    decided = [c for c in comparisons if c.score is not None]
    genuine = [c for c in decided if c.probe_id == c.gallery_id]
    impostor = [c for c in decided if c.probe_id != c.gallery_id]
    false_match = sum(1 for c in impostor if c.score is not None and c.score >= threshold)
    false_non_match = sum(1 for c in genuine if c.score is not None and c.score < threshold)
    return IdentityReport(
        status="measured",
        comparisons=len(comparisons),
        false_match_rate=_rate(false_match, len(impostor)),
        false_non_match_rate=_rate(false_non_match, len(genuine)),
        rejection_rate=Fraction(len(comparisons) - len(decided), len(comparisons)),
    )
