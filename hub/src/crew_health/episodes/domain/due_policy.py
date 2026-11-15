from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from crew_health.episodes.domain.errors import refused
from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ErrorCode


@dataclass(frozen=True, slots=True)
class DuePolicy:
    policy_id: str
    kind: str
    seconds: Decimal | None = None
    minimum_seconds: Decimal | None = None
    maximum_seconds: Decimal | None = None


def duration_seconds(value: Decimal, unit: str) -> Decimal:
    # UCUM time definitions, not a clinical threshold.
    factors = {"s": Decimal(1), "min": Decimal(60), "h": Decimal(3600), "d": Decimal(86400)}
    if unit not in factors or not value.is_finite() or value <= 0:
        raise refused(ErrorCode.CONFIG_INVALID, "duration")
    result = value * factors[unit]
    if result > Decimal(315576000):
        raise refused(ErrorCode.CONFIG_INVALID, "duration", "overflow")
    return result


def due_time(
    policy: DuePolicy,
    created_at: datetime,
    *,
    chosen: datetime | None = None,
    day_end: datetime | None = None,
) -> tuple[datetime | None, str]:
    require_utc(created_at)
    if chosen is not None:
        require_utc(chosen)
    if policy.kind == "none":
        if chosen is not None:
            raise refused(ErrorCode.INVALID_VALUE, "due_at")
        return None, "none"
    if policy.kind == "after":
        if chosen is not None:
            raise refused(ErrorCode.INVALID_VALUE, "due_at")
        return (
            (None, "threshold_pending")
            if policy.seconds is None
            else (created_at + timedelta(seconds=float(policy.seconds)), "set")
        )
    if policy.kind == "end_of_mission_day":
        if day_end is None or day_end <= created_at:
            raise refused(ErrorCode.INTERNAL_ERROR, "due_at", "mission_clock_unavailable")
        return day_end, "set"
    if (
        policy.kind == "chosen_by_person"
        and policy.minimum_seconds is not None
        and policy.maximum_seconds is not None
    ):
        if chosen is None:
            return None, "none"
        interval = Decimal(str((chosen - created_at).total_seconds()))
        if policy.minimum_seconds <= interval <= policy.maximum_seconds:
            return chosen, "set"
        raise refused(ErrorCode.INVALID_VALUE, "due_at", "outside_bounds")
    raise refused(ErrorCode.CONFIG_INVALID, "due_policy")
