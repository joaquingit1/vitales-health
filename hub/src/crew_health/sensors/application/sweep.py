"""The independent freshness sweep (E23 3.4, 9): the backup of the scheduled deadline, sharing no state with it
except the stored readings. Each tick is one unit of work over a bounded page of channels; it skips channels
outside the active environment, records a missed transition and marks SCHEDULER_LAG when a job is overdue or gone."""

from __future__ import annotations

from datetime import timedelta

from crew_health.kernel.uow import UnitOfWork
from crew_health.sensors.application.deadlines import refresh, schedule_deadline, schedule_key
from crew_health.sensors.application.runtime import SCHEDULER_LAG, Runtime


class FreshnessSweep:
    def __init__(self, rt: Runtime) -> None:
        self._rt = rt
        self._after = ""  # resume point of the next page; not state any decision depends on

    def tick(self, uow: UnitOfWork) -> int:
        rt = self._rt
        environment = rt.store.environment(uow)
        if environment is None:
            return 0
        channels = [c for c in rt.store.channels(uow) if c.facts.channel_id > self._after][: rt.limits.sweep_page]
        self._after = channels[-1].facts.channel_id if len(channels) == rt.limits.sweep_page else ""
        lagging = False
        for channel in channels:
            if environment.environment_id not in channel.facts.environment_ids:
                continue
            after = refresh(rt, uow, channel, cause="sweep")
            lagging = self._lag(uow, after) or lagging
        if lagging:
            rt.fault(SCHEDULER_LAG, uow.now)
        elif rt.faults is not None and SCHEDULER_LAG in rt.faults.active:
            rt.faults.clear(SCHEDULER_LAG, uow.now)
        return len(channels)

    def _lag(self, uow: UnitOfWork, channel) -> bool:  # type: ignore[no-untyped-def]
        rt = self._rt
        if not channel.has_history or channel.reference is None:
            return False
        job = rt.store.schedule_of(uow, schedule_key(rt, channel.facts.channel_id))
        limit = timedelta(seconds=rt.limits.scheduler_lag_max_s)
        if job is None or (not job.completed and job.due is not None and job.due + limit < uow.now):
            if channel.freshness == "current":
                schedule_deadline(rt, uow, channel, anchor=None, at=channel.reference.corrected_at)
            return True
        return False
