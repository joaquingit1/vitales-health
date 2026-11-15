"""Plausibility gate (DE23-11, R-E23-2).

The catalog schema carries no range, saturation, stuck or rate criteria and none may be invented or borrowed
(AGENTS 2, 6). Until a sourced criteria object exists this module can only answer "unavailable": captured data
is kept, nothing is evaluated, nothing raises or clears. A pass can never be produced from missing criteria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crew_health.sensors.domain.channel import Origin, RegistrationPolicy, SensorTypeFacts
from crew_health.sensors.domain.reading import ReadingFact

PlausibilityStatus = Literal["unavailable", "passed", "failed"]

CRITERIA_MISSING = "criteria_missing"


@dataclass(frozen=True, slots=True)
class PlausibilityResult:
    status: PlausibilityStatus
    reason: str
    failed_check: str | None

    @property
    def evaluable(self) -> bool:
        return self.status == "passed"


def assess(sensor_type: SensorTypeFacts, reading: ReadingFact) -> PlausibilityResult:
    """All four checks are mandatory in every origin. With no criteria none can pass."""
    del sensor_type, reading  # R-E23-2: no criteria exist in SensorTypeFacts, so there is nothing to check
    return PlausibilityResult("unavailable", CRITERIA_MISSING, None)


def registration_policy(origin: Origin, *, criteria_available: bool) -> RegistrationPolicy:
    """T-E23-070: LIVE is refused without criteria; REPLAY and SIM are captured but never evaluated."""
    if criteria_available:
        return RegistrationPolicy(True, True, True, "criteria_available")
    if origin == "LIVE":
        return RegistrationPolicy(False, False, False, CRITERIA_MISSING)
    return RegistrationPolicy(True, True, False, CRITERIA_MISSING)
