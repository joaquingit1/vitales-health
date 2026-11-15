"""Best-view selection across cameras for one indicator and window (E06-02, R11, V07).

Only qualified views compete. The highest signal to noise ratio wins; a tie goes to the smaller device ID, so the
choice is stable. Every qualified view is kept with the choice and its reason, and a spread wider than the policy's
agreement tolerance is a disagreement that stays visible: nothing is averaged. Agreeing cameras watch the same face
and remain one correlated source, not an independent reference (PRD R11, E30 OD2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from crew_health_camera.domain.analysis import Decision, NoReading, Reading


@dataclass(frozen=True, slots=True)
class Selection:
    selected: Reading
    others: tuple[Reading, ...]
    refused: tuple[NoReading, ...]
    reason: str
    spread: Decimal
    disagree: bool


@dataclass(frozen=True, slots=True)
class Refusal:
    """No camera qualified. reason is the first refusal by device ID; every refusal is kept."""

    code: str
    reason: str
    refused: tuple[NoReading, ...]


def select(code: str, decisions: Sequence[Decision], tolerance_per_min: float) -> Selection | Refusal:
    readings = sorted(
        (d for d in decisions if isinstance(d, Reading) and d.code == code),
        key=lambda r: (-r.snr_db, r.device_id),
    )
    refused = tuple(
        sorted(
            (d for d in decisions if isinstance(d, NoReading) and d.code == code), key=lambda r: r.device_id
        )
    )
    if not readings:
        return Refusal(code, refused[0].reason if refused else "no_camera", refused)
    values = [r.value for r in readings]
    spread = max(values) - min(values)
    return Selection(
        selected=readings[0],
        others=tuple(readings[1:]),
        refused=refused,
        reason="highest_signal_quality" if len(readings) > 1 else "only_qualified_view",
        spread=spread,
        disagree=spread > Decimal(repr(tolerance_per_min)),
    )
