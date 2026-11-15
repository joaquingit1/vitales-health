from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta

from crew_health.episodes.application.effects import RunCorrectionEffects
from crew_health.episodes.application.overdue import NoticeOverdue
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.kernel.scheduler import AbsoluteDeadline, HealthState, ScheduledJob
from crew_health.kernel.uow import Actor, UnitOfWork, UnitOfWorkFactory


class EpisodeMaintenance:
    def __init__(
        self,
        service: EpisodeService,
        effects: RunCorrectionEffects,
        uows: UnitOfWorkFactory,
        health: HealthState,
    ) -> None:
        self.service, self.effects, self.uows, self.health = service, effects, uows, health
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="episode-maintenance")
        self.future: Future[int] | None = None

    def start(self) -> None:
        with self.uows.begin(
            actor=Actor("engine", "episodes_engine"), correlation_id="episodes_startup"
        ) as uow:
            self.tick(uow, None)

    def tick(self, uow: UnitOfWork, job: ScheduledJob | None) -> None:
        NoticeOverdue(self.service).run(uow)
        if self.future is None or self.future.done():
            if self.future is not None and self.future.exception() is not None:
                self.health.mark_degraded("episodes_effects", "WORKER_FAILED", uow.now)
            # Submission is bounded to one worker. All optional E13 reads run on that worker after this lock releases.
            self.future = self.pool.submit(self.effects.run)
        # The schedule row outlives the process and a fired job: replace it under its current generation, the
        # scheduler's compare-and-set token (the same read platform evaluation uses), so a restart or a tick
        # re-arms the tick instead of failing STALE_VERSION.
        rows = uow.connection.rows(
            "SELECT generation FROM platform_schedule WHERE key=?", ("episodes.maintenance",), limit=1
        )
        uow.schedule(
            ScheduledJob(
                "episodes.maintenance",
                "episodes.maintenance",
                AbsoluteDeadline(uow.now + timedelta(seconds=self.service.limits.overdue_tick_seconds)),
                {},
                None,
            ),
            expected_generation=int(str(rows[0][0])) if rows else None,
        )

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.effects.close()
