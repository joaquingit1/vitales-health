"""Reads (E22 6): the own streak, the own patches and the crew goal. Derived on read from stored completions, the
roster, the mission clock and the active `participation-rules`; nothing is cached, so a revocation or a rules edit
shows on the next read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from crew_health.participation.application.context import (
    Deps,
    partition_of,
    rules_of,
    today_of,
)
from crew_health.participation.application.errors import unavailable
from crew_health.participation.application.ports import View
from crew_health.participation.application.projector import Projector
from crew_health.participation.application.wire import crew_goal_wire, streak_wire
from crew_health.participation.domain.crew_goal import CrewConsent, derive_crew_goal
from crew_health.participation.domain.derivation import award_views, derive_streak, event_cursor
from crew_health.participation.domain.types import CrewGoalView, PatchAward, StreakView, ViewOrigin


@dataclass(frozen=True, slots=True)
class CrewGoalRead:
    view: CrewGoalView
    as_of: datetime

    def wire(self) -> dict[str, object]:
        return crew_goal_wire(self.view, as_of=self.as_of)


class Queries:
    def __init__(self, deps: Deps, projector: Projector) -> None:
        self.deps, self.projector = deps, projector

    def _ready(self, person: str | None) -> None:
        """Projects what is pending; a parked intent of this person makes the view unavailable rather than a view
        that omits a completion (E22 4.1)."""
        self.projector.drain()
        with self.deps.snapshots.snapshot() as view:
            parked = self.deps.store.count_state(view.connection, "parked", person)
        if parked:
            raise unavailable()

    def streak(self, person: str) -> StreakView:
        deps = self.deps
        self._ready(person)
        with deps.snapshots.snapshot() as view:
            mission, origin = partition_of(deps, view)
            return self._streak(view, person, mission, origin)

    def _streak(self, view: View, person: str, mission: str, origin: ViewOrigin) -> StreakView:
        deps = self.deps
        today, version = today_of(deps, view.now)
        facts = deps.store.facts(
            view.connection,
            mission_id=mission,
            origin=origin,
            patient_id=person,
            limit=deps.limits.max_completion_days,
        )
        return derive_streak(
            patient_id=person,
            mission_id=mission,
            origin_mode=origin,
            facts=facts,
            today=today,
            roster_first_day=deps.roster.first_mission_day(view, person),
            as_of=view.now,
            rules=rules_of(view),
            clock_version=version,
            cursor=event_cursor(((f.completion_id, False) for f in facts), version),
        )

    def streak_wire(self, person: str) -> dict[str, object]:
        return streak_wire(self.streak(person))

    def patches(self, person: str) -> tuple[PatchAward, ...]:
        deps = self.deps
        self._ready(person)
        self.projector.refresh_awards(person)
        with deps.snapshots.snapshot() as view:
            mission, origin = partition_of(deps, view)
            ledger = deps.store.ledger(view.connection, person, mission, origin)
        return award_views(person, mission, origin, ledger)

    def crew_goal(self) -> CrewGoalRead:
        self._ready(None)
        return self.derive_crew_goal()

    def derive_crew_goal(self) -> CrewGoalRead:
        """The aggregate from what is stored now, without the readiness check (a rebuild repairs what blocks it)."""
        deps = self.deps
        with deps.snapshots.snapshot() as view:
            mission, origin = partition_of(deps, view)
            rules = rules_of(view)
            today, version = today_of(deps, view.now)
            first = today - rules.crew_goal_window_days + 1
            members = tuple(deps.roster.members(view))[: deps.limits.roster_max]
            on_roster = {
                day: frozenset(
                    m.person_id
                    for m in members
                    if m.from_day <= day and (m.until_day is None or day <= m.until_day)
                )
                for day in range(first, today + 1)
            }
            consents = dict(deps.store.consents(view.connection, mission, origin, deps.limits.roster_max))
            consents = _guarded(consents, on_roster, deps.limits.min_counting_members)
            checked = deps.store.checked_days(
                view.connection,
                mission_id=mission,
                origin=origin,
                first_day=first,
                last_day=today,
                limit=deps.limits.max_completion_days,
            )
            goal = derive_crew_goal(
                mission_id=mission,
                origin_mode=origin,
                today=today,
                roster_by_day=on_roster,
                consents=consents,
                checked_days={p: d for p, d in checked.items() if p in consents},
                as_of=view.now,
                rules=rules,
                clock_version=version,
            )
            return CrewGoalRead(goal, view.now)


def _guarded(
    consents: Mapping[str, CrewConsent], on_roster: Mapping[int, frozenset[str]], minimum: int
) -> dict[str, CrewConsent]:
    """Open gap of D10: with a single counting member the totals would be that person's own record, and with any
    number the due total divided by the window length reveals how many members count. The safest reading that
    keeps the goal useful: no member counts until at least `minimum` members (hub setting, floor 3) count in the window."""
    counting = {
        person
        for person, consent in consents.items()
        for day, members in on_roster.items()
        if person in members and consent.counts_on(day)
    }
    return dict(consents) if len(counting) >= minimum else {}
