"""Which of routine's seams resolved, reported as health (B2: an absent seam fails closed and visibly).

Each absent seam is its own health component `routine.seam.<name>` with code CAPABILITY_UNAVAILABLE, so the hub
health turns degraded and the diagnostics snapshot names exactly what is missing. What each absence does:

| Seam                 | Owner          | Absent                                                                  |
|----------------------|----------------|-------------------------------------------------------------------------|
| roster               | E02 identity   | no person is materialized; stored open work still misses on time        |
| mission              | E23 mission    | MISSION_UNAVAILABLE: no new occurrence; stored deadlines still decided  |
| location             | E23 mission    | a location-scoped audience resolves to nobody, audited                  |
| visibility           | E02 identity   | no care read: only the person's own routine is visible                  |
| records              | E01 records    | no record-driven binding; linked records and answers are refused        |
| warnings             | E24 warnings   | overrides are not attached back to their warning                        |
| release_history      | E01 config     | an evicted pin is unreadable: the stored deadline stands (M1)           |
| clock_trust          | E01 clock      | the clock is never trusted: misses are recorded and never counted       |
| hub_clock            | E01 clock      | elapsed timers fall back to their absolute instant                      |

Requests are not a seam here: in create_app the hub's operation middleware authenticates and authorizes every
contract operation through its HttpSecurity and routine reads that session (module.py `authenticate`); without a
session a request is 401, visibly, per request.

Misses keep being decided with every one of them absent (AGENTS 1 rule 3): deadlines are durable scheduler jobs and
the deadline path reads only routine's own store."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from crew_health.kernel.scheduler import HealthState

type Seam = Literal[
    "roster",
    "mission",
    "location",
    "visibility",
    "records",
    "warnings",
    "release_history",
    "clock_trust",
    "hub_clock",
]  # internal enum

SEAMS: tuple[Seam, ...] = (
    "roster",
    "mission",
    "location",
    "visibility",
    "records",
    "warnings",
    "release_history",
    "clock_trust",
    "hub_clock",
)


def absent(resolved: Mapping[Seam, object | None]) -> tuple[Seam, ...]:
    return tuple(seam for seam in SEAMS if resolved.get(seam) is None)


def report(health: HealthState, missing: tuple[Seam, ...], at: datetime) -> None:
    for seam in SEAMS:
        if seam in missing:
            health.mark_degraded("routine.seam." + seam, "CAPABILITY_UNAVAILABLE", at)
        else:
            health.mark_ok("routine.seam." + seam)
