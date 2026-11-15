"""Absolute mission deadlines (E23 3.1): proposal effective_at and post_eva day end.

Both are `AbsoluteDeadline` jobs, so a clock correction never moves them. The scheduler store, generation checks
and restart recovery are E01's; `sweep_overdue` is the independent catch-up for a forward clock jump.
"""

from __future__ import annotations

from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob, SchedulerRegistry
from crew_health.kernel.uow import UnitOfWork
from crew_health.mission.application.use_cases import (
    ACTIVATE_HANDLER,
    POST_EVA_HANDLER,
    Services,
    activate,
    activate_due,
    end_post_eva,
)
from crew_health.mission.domain import overlay as overlay_rules
from crew_health.mission.domain import profile as profile_rules


def register_deadlines(registry: SchedulerRegistry, services: Services) -> None:
    registry.register(ACTIVATE_HANDLER, lambda uow, job: activate_due(services, uow, job))
    registry.register(POST_EVA_HANDLER, lambda uow, job: end_post_eva(services, uow, job))


def sweep_overdue(services: Services, uow: UnitOfWork, *, people: tuple[str, ...]) -> int:
    """Finish work whose absolute deadline passed while nothing ran. Returns how many items it completed."""
    done = 0
    for record in services.store.profiles(uow):
        if record.state == "proposed" and profile_rules.due(record, uow.now):
            activate(services, uow, record.profile_id)
            done += 1
    for overlay in services.store.overlays_of(uow, people):
        if (
            overlay.kind == overlay_rules.POST_EVA
            and overlay.state == "active"
            and overlay.started_at is not None
        ):
            if services.day_end(overlay.started_at) <= uow.now:
                end_post_eva_for(services, uow, overlay.overlay_id)
                done += 1
    return done


def end_post_eva_for(services: Services, uow: UnitOfWork, overlay_id: str) -> None:
    end_post_eva(
        services,
        uow,
        ScheduledJob("sweep", POST_EVA_HANDLER, AbsoluteDeadline(uow.now), {"overlay_id": overlay_id}, None),
    )
