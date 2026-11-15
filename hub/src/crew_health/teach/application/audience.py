"""Audience rules of teach events (E30 4.6, section 10). E01 asks these for every event and subscriber; a missing
check, session person or help session reader denies (fail closed)."""

from __future__ import annotations

from crew_health.kernel.uow import Audience, ReadSnapshot, SessionContext
from crew_health.teach.application import access
from crew_health.teach.application.ports import GuidedCheckRepository, HelpSessionReader


class GuidedCheckAudience:
    """guided_check.beat and guided_check.checked: readable by the check's parties only."""

    def __init__(self, repository: GuidedCheckRepository) -> None:
        self._repository = repository

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        check = self._repository.get(snapshot, audience.object_ref)
        return check is not None and access.allows(session, check, "read")


class CoachingAudience:
    """coaching.sample: readable exactly when E26 says the help session is; until E26 binds, nobody."""

    def __init__(self) -> None:
        self.help: HelpSessionReader | None = None

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        return self.help is not None and self.help.readable_by(session, audience.object_ref)
