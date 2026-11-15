"""Composition adapters: E22's consumed ports over E02's real roster (`IdentityPorts`). The roster is the one
source of the mission, its origin namespace, who is a member and from which mission day (E02 3.1)."""

from __future__ import annotations

from collections.abc import Sequence

from crew_health.identity.application.ports import RosterPort
from crew_health.participation.application.ports import Namespace, RosterMember, View


class IdentityRoster:
    def __init__(self, roster: RosterPort, *, limit: int) -> None:
        self.roster, self.limit = roster, limit

    def partition(self, view: View) -> tuple[str, Namespace] | None:
        return self.roster.partition(view)

    def members(self, view: View) -> Sequence[RosterMember]:
        return tuple(
            RosterMember(entry.person_id, entry.on_roster_from_day, entry.on_roster_until_day)
            for entry in self.roster.members(view, after_index=None, limit=self.limit)
        )

    def first_mission_day(self, view: View, person_id: str) -> int | None:
        entry = self.roster.member(view, person_id)
        return None if entry is None else entry.on_roster_from_day


class IdentityOrigins:
    """A current roster member records in the roster's namespace; anyone else has no record origin."""

    def __init__(self, roster: RosterPort) -> None:
        self.roster = roster

    def record_origin(self, view: View, person_id: str) -> Namespace | None:
        if self.roster.member(view, person_id) is None:
            return None
        found = self.roster.partition(view)
        return None if found is None else found[1]
