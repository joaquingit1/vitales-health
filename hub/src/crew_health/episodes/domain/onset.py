from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ErrorCode, ProblemError


@dataclass(frozen=True, slots=True)
class Onset:
    at: datetime | None
    precision: Literal["exact", "hour", "day", "unknown"]

    def __post_init__(self) -> None:
        if self.precision not in ("exact", "hour", "day", "unknown") or (self.at is None) != (
            self.precision == "unknown"
        ):
            raise ProblemError(ErrorCode.INVALID_VALUE, "inconsistent onset")
        if self.at is not None:
            require_utc(self.at)

    def validate_at(self, now: datetime) -> None:
        if self.at is not None and self.at > now:
            raise ProblemError(ErrorCode.INVALID_VALUE, "future onset")
