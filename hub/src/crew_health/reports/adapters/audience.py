"""The reports.owner audience rule: a report's task and Job events reach the current owner session only
(PLAN design decisions). The grant is read at delivery time, so a deleted report or another person is refused."""

from __future__ import annotations

from crew_health.kernel.uow import Audience, ReadSnapshot, SessionContext
from crew_health.reports.adapters.index import ReportIndex


class ReportOwnerAudience:
    def __init__(self, index: ReportIndex) -> None:
        self.index = index

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        owner = self.index.owner(snapshot, audience.object_ref)
        return owner is not None and session.kind == "crew" and session.person_id == owner
