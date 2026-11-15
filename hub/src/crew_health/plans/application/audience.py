"""D9: the subject's crew session is the sole audience for a plan run."""

from crew_health.kernel.uow import Audience, ReadSnapshot, SessionContext
from crew_health.plans.application.repository import PlanRunsRepository


class RunViewersRule:
    def __init__(self, repository: PlanRunsRepository) -> None:
        self.repository = repository

    def admits(self, snapshot: ReadSnapshot, session: SessionContext, audience: Audience) -> bool:
        if session.kind != "crew" or session.person_id is None:
            return False
        run = self.repository.load(snapshot, audience.object_ref)
        return run is not None and run.subject_person_id == session.person_id
