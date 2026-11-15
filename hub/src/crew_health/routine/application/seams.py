"""Which of routine's seams resolved (B2), and what each absence does.

A seam whose owning epic is not installed is a missing capability, not a fault (SYSTEM 5.10): it never marks
health degraded, so the station's general fault notice stays for real faults (the evaluator fault and its
re-apply job, a parked job, storage errors, an unreadable pin, a cold release cache at a deadline). Each use of an
absent seam refuses with its typed reason instead, the way E32 handles absent providers, and `RoutineRuntime.missing`
names the absent seams for diagnostics.

| Seam                 | Owner          | Absent                                                                  |
|----------------------|----------------|-------------------------------------------------------------------------|
| roster               | E02 identity   | no person is materialized; stored open work still misses on time        |
| mission              | E23 mission    | CAPABILITY_UNAVAILABLE on work that needs it; stored deadlines decided   |
| location             | E23 mission    | a location-scoped audience resolves to nobody, audited                  |
| visibility           | E02 identity   | no care read: only the person's own routine is visible                  |
| records              | E01 records    | no record-driven binding; linked records and answers are refused        |
| warnings             | E24 warnings   | overrides are not attached back to their warning                        |
| release_history      | E01 config     | an evicted pin is unreadable: the stored deadline stands (M1, degraded)  |
| clock_trust          | E01 clock      | the clock is never trusted: misses are recorded and never counted       |
| hub_clock            | E01 clock      | elapsed timers fall back to their absolute instant                      |

No mission clock set yet is missing context, not a fault either: work that needs the mission day refuses with
CONTEXT_INAPPLICABLE (adapters/mission_clock.py) and health stays ok.

Requests are not a seam here: in create_app the hub's operation middleware authenticates and authorizes every
contract operation through its HttpSecurity and routine reads that session (module.py `authenticate`); without a
session a request is 401, visibly, per request.

Misses keep being decided with every one of them absent (AGENTS 1 rule 3): deadlines are durable scheduler jobs and
the deadline path reads only routine's own store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

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
