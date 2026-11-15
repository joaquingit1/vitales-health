"""Crew goal consent and the CrewGoalView aggregate (CONTRACTS 8; COORDINATOR-DECISIONS D10).

D10: every member is outside the crew goal until they opt in, per mission and origin; enabling counts prospectively
(from the next mission day) and revocation excludes the member at once. Both the completed and the due totals use
consent. The view holds two totals and no person, per person count or per day count. Zero consenting due
contributions is no goal activity: `reached` is false, never a fabricated success."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from crew_health.participation.domain.errors import InvalidConsent
from crew_health.participation.domain.rules import ParticipationRules
from crew_health.participation.domain.types import CrewGoalView, ViewOrigin


@dataclass(frozen=True, slots=True)
class CrewConsent:
    """Per member, mission and origin. A missing row is revision 0 and disabled."""

    enabled: bool = False
    effective_from_day: int | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if self.enabled != (self.effective_from_day is not None) or self.revision < 0:
            raise InvalidConsent("consent state is inconsistent")

    def counts_on(self, mission_day: int) -> bool:
        return self.effective_from_day is not None and mission_day >= self.effective_from_day


def set_consent(current: CrewConsent, *, enabled: bool, today: int, expected_revision: int) -> CrewConsent:
    """Apply the owner's choice. Setting the state already in force changes nothing (idempotent), a stale
    `expected_revision` is refused, enabling starts counting on `today + 1` and disabling excludes at once."""
    if expected_revision != current.revision:
        raise InvalidConsent("stale consent revision")
    if enabled == current.enabled:
        return current
    return CrewConsent(enabled, today + 1 if enabled else None, current.revision + 1)


def derive_crew_goal(
    *,
    mission_id: str,
    origin_mode: ViewOrigin,
    today: int,
    roster_by_day: Mapping[int, frozenset[str]],
    consents: Mapping[str, CrewConsent],
    checked_days: Mapping[str, frozenset[int]],
    as_of: datetime,
    rules: ParticipationRules,
    clock_version: str,
) -> CrewGoalView:
    """Totals over the window of `crew_goal_window_days` mission days ending today.

    A pair (member, day) is due when the member is on the roster that day and has consented for that day; it is
    completed when that day is one of the member's checked days. Members without a consent row do not count."""
    first = today - rules.crew_goal_window_days + 1
    due = completed = 0
    for day in range(first, today + 1):
        for member in sorted(roster_by_day.get(day, frozenset())):
            consent = consents.get(member)
            if consent is None or not consent.counts_on(day):
                continue
            due += 1
            if day in checked_days.get(member, frozenset()):
                completed += 1
    return CrewGoalView(
        mission_id=mission_id,
        origin_mode=origin_mode,
        window_first_mission_day=first,
        window_last_mission_day=today,
        completed_total=completed,
        due_total=due,
        reached=due > 0 and completed >= due,
        as_of=as_of,
        derivation_version=rules.derivation_version,
        mission_clock_version=clock_version,
    )
