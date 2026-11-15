"""Routine timers and their kind (PLAN row 34, SYSTEM 5.3 scheduler).

| Job key                    | Fires at                                                          | Kind     |
|----------------------------|-------------------------------------------------------------------|----------|
| routine.day.<person>       | the next mission day start                                        | absolute |
| routine.occ.<occurrence>   | the window end (day boundary or local-time slot)                  | absolute |
| routine.occ.<occurrence>   | the window remainder once a hold has extended it                  | elapsed  |
| routine.ovr.<override>     | the end of an override's `until` days duration                    | elapsed  |
| routine.every.<override>   | the next every_minutes slot                                       | elapsed  |
| routine.rca.<activation>   | the end of a cadence activation's days duration                   | elapsed  |
| routine.qi.<instance>      | the question instance window end (a day end or a policy window)   | absolute |

A person's stated later_until instant is the occurrence window end, so it is absolute.

An absolute timer is an AbsoluteDeadline and never shifts with a clock correction. An elapsed timer is the shared
scheduler's ElapsedDeadline anchored on a capture of the hub's own clock (ClockTrust.hub_time); create_app resolves
its due instant through the clock corrections and recomputes it on every ClockCorrected (app/assembly.py
_wire_declarations), so the duration survives a correction. When the hub clock has no open segment the capture is
unknown: the timer falls back to its absolute instant and routine.clock is marked degraded, so the miss is still
recorded instead of never being decided. While the shared clock is frozen an elapsed timer also keeps its absolute
instant: the kernel refuses to normalize any anchor until the sweep and nothing recomputes the job afterwards, and
the handler rechecks the trust, so no miss counts and no tightening ends on unknown time."""

from __future__ import annotations

from typing import Literal

from crew_health.routine.domain.occurrence import Occurrence

type TimerKind = Literal["absolute", "elapsed"]  # internal enum


def deadline_kind(occ: Occurrence) -> TimerKind:
    """An occurrence's window end is absolute; once a hold has extended it, the remainder is a duration."""
    return "absolute" if occ.due_until == occ.base_due_until else "elapsed"
