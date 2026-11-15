"""Camera quality study (E06-03, R46 contribution, V80): held-out error, bias, rejection and coverage.

Participants are split before any trial is scored, by a seeded hash of their ID, so held-out people never tune
anything. Every trial with a reference stays in the denominator: a refused camera reading counts as a rejection,
never as a dropped row. With no held-out participant, or none accepted, the error is not measured (None), never
zero. Paced and natural breathing are reported separately by condition (PRD 616); agreeing views are correlated.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal


@dataclass(frozen=True, slots=True)
class Trial:
    participant_id: str
    condition: str
    code: str
    reference: Decimal | None
    camera: Decimal | None
    reason: str | None
    views: int


@dataclass(frozen=True, slots=True)
class StudyReport:
    code: str
    condition: str | None
    status: Literal["measured", "not_measured"]
    participants: int
    trials: int
    accepted: int
    rejection_rate: Fraction | None
    coverage: Fraction | None
    mae: Decimal | None
    bias: Decimal | None
    multi_view_trials: int


def split(
    participants: Iterable[str], *, held_out_fraction: Fraction, seed: str
) -> tuple[frozenset[str], frozenset[str]]:
    """(development, held out). Deterministic for a seed; at least one held-out participant when any exist."""
    if not Fraction(0) < held_out_fraction < Fraction(1):
        raise ValueError("held_out_fraction")
    ordered = sorted(set(participants), key=lambda p: hashlib.sha256(f"{seed}:{p}".encode()).hexdigest())
    if not ordered:
        return frozenset(), frozenset()
    count = max(1, int(len(ordered) * held_out_fraction))
    return frozenset(ordered[count:]), frozenset(ordered[:count])


def evaluate(
    trials: Sequence[Trial], *, held_out: frozenset[str], code: str, condition: str | None = None
) -> StudyReport:
    scored = [
        t
        for t in trials
        if t.code == code
        and t.participant_id in held_out
        and t.reference is not None
        and (condition is None or t.condition == condition)
    ]
    accepted = [t for t in scored if t.camera is not None]
    people = len({t.participant_id for t in scored})
    if not scored:
        return StudyReport(code, condition, "not_measured", 0, 0, 0, None, None, None, None, 0)
    errors = [t.camera - t.reference for t in accepted if t.camera is not None and t.reference is not None]
    mae = sum((abs(e) for e in errors), Decimal(0)) / len(errors) if errors else None
    bias = sum(errors, Decimal(0)) / len(errors) if errors else None
    return StudyReport(
        code=code,
        condition=condition,
        status="measured" if errors else "not_measured",
        participants=people,
        trials=len(scored),
        accepted=len(accepted),
        rejection_rate=Fraction(len(scored) - len(accepted), len(scored)),
        coverage=Fraction(len(accepted), len(scored)),
        mae=mae,
        bias=bias,
        multi_view_trials=sum(1 for t in accepted if t.views > 1),
    )
