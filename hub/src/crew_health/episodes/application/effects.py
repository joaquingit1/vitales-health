from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta

from crew_health.episodes.application.codec import array, decode, instant, integer, obj, text
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.episodes.domain.types import EpisodeState
from crew_health.kernel.scheduler import HealthState
from crew_health.kernel.uow import Actor, ReadSnapshotFactory, UnitOfWorkFactory


class RunCorrectionEffects:
    """A single bounded worker. Queries run outside the write transaction; one in-flight call at most."""

    def __init__(
        self,
        service: EpisodeService,
        uows: UnitOfWorkFactory,
        snapshots: ReadSnapshotFactory,
        health: HealthState,
    ) -> None:
        self.service, self.uows, self.snapshots, self.health = service, uows, snapshots, health
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="episode-effects")
        self.pending = False

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def run(self) -> int:
        s = self.service
        if self.pending:
            return 0
        with self.snapshots.snapshot() as view:
            rows = view.connection.rows(
                "SELECT effect_id,episode_id,record_id,superseded_id,attempts FROM episodes_effect WHERE state=? AND next_at<=? ORDER BY next_at,effect_id LIMIT ?",
                ("due", instant(view.now), s.limits.effect_batch_size),
                limit=s.limits.effect_batch_size,
            )
        for row in rows:
            refs: tuple[str, ...] = ()
            failed = False
            if s.providers.assessments is None:
                failed = True
            else:

                def lookup(episode_id: str = text(row[1]), superseded: str = text(row[3])) -> tuple[str, ...]:
                    with self.snapshots.snapshot() as view:
                        return (
                            tuple(s.providers.assessments.citing(view, episode_id, [superseded]))
                            if s.providers.assessments is not None
                            else ()
                        )

                future = self.pool.submit(lookup)
                try:
                    refs = future.result(s.limits.effect_call_timeout_ms / 1000)
                except TimeoutError:
                    failed = True
                    self.pending = True
                    future.add_done_callback(lambda _: setattr(self, "pending", False))
                except Exception:
                    failed = True
            if len(refs) > s.limits.max_links_per_episode:
                failed = True
                refs = ()
            with self.uows.begin(
                actor=Actor("engine", "episodes_engine"), correlation_id=text(row[0])
            ) as uow:
                ep = s.repo.require(uow, text(row[1]))
                attempt = integer(row[4]) + 1
                current = uow.connection.rows(
                    "SELECT state FROM episodes_effect WHERE effect_id=?", (row[0],), limit=1
                )
                if not current or current[0][0] != "due":
                    continue
                exhausted = failed and attempt >= s.limits.effect_max_attempts
                if not failed:
                    if refs and s.providers.assessment_commands is None:
                        failed = True
                        exhausted = attempt >= s.limits.effect_max_attempts
                    elif s.providers.assessment_commands is not None:
                        for ref in refs:
                            s.providers.assessment_commands.mark_stale(uow, ref, text(row[2]))
                linked_assessments = uow.connection.rows(
                    "SELECT 1 FROM episodes_link WHERE episode_id=? AND kind=? LIMIT 1",
                    (ep.episode_id, "assessment"),
                    limit=1,
                )
                evidence = False
                if ep.last_resolution_id:
                    resolution = uow.connection.rows(
                        "SELECT data FROM episodes_resolution WHERE resolution_id=?",
                        (ep.last_resolution_id,),
                        limit=1,
                    )
                    if resolution:
                        ev = obj(decode(resolution[0][0])["evidence"])
                        evidence = row[3] in array(ev.get("observation_ids", [])) or (
                            bool(refs) and ev.get("review_id") is not None
                        )
                if refs or evidence or (exhausted and linked_assessments):
                    s.open_task(uow, ep, "review", None, None, "correction", text(row[2]))
                    s.changed(
                        uow,
                        ep,
                        "evidence_changed",
                        cause=text(row[2]),
                        reopen=ep.state == EpisodeState.RESOLVED and evidence,
                    )
                state = "failed" if exhausted else "due" if failed else "done"
                uow.connection.execute(
                    "UPDATE episodes_effect SET state=?,attempts=?,next_at=?,last_error_code=? WHERE effect_id=?",
                    (
                        state,
                        attempt,
                        instant(uow.now + timedelta(seconds=s.limits.effect_retry_backoff_seconds)),
                        "DEPENDENCY_UNAVAILABLE" if failed else None,
                        row[0],
                    ),
                )
                if exhausted:
                    s.audit(uow, ep, "effect_failed")
                    self.health.mark_degraded("episodes_effects", "DEPENDENCY_UNAVAILABLE", uow.now)
        return len(rows)
