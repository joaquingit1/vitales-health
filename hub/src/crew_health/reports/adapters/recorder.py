"""FindingRecorder interim (PLAN P1, P2): a confirmed numeric finding stays in the confirmation lineage and is
not appended to records until the observation instant is contracted. A source date is a date, never an instant,
and midnight or a timezone is never invented to make one (AGENTS 1.5). Returns None, never a made-up id."""

from __future__ import annotations

from crew_health.kernel.uow import SessionContext, UnitOfWork
from crew_health.reports.domain.model import ConfirmedFinding


class DeferredObservationRecorder:
    def record(self, uow: UnitOfWork, session: SessionContext, finding: ConfirmedFinding) -> str | None:
        return None
