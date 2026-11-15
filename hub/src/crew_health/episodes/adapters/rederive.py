from __future__ import annotations

from crew_health.episodes.application.codec import text
from crew_health.episodes.application.use_cases import EpisodeService
from crew_health.episodes.domain.state_machine import next_state
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import UnitOfWork


class ReDerive:
    """Keyset batches preserve history; the host's authenticated operator CLI supplies the UOW."""

    def __init__(self, service: EpisodeService) -> None:
        self.service = service

    def run(self, uow: UnitOfWork, *, cursor: str | None, limit: int) -> tuple[int, str | None]:
        if uow.actor.kind != "operator":
            raise ProblemError(ErrorCode.ACTION_NOT_ALLOWED, "operator required")
        if not 1 <= limit <= self.service.limits.max_links_per_episode:
            raise ProblemError(ErrorCode.INVALID_VALUE, "batch bound")
        rows = uow.connection.rows(
            "SELECT episode_id FROM episodes_episode WHERE state<>? AND state<>? AND episode_id>? ORDER BY episode_id LIMIT ?",
            ("draft", "resolved", cursor or "", limit),
            limit=limit,
        )
        changed = 0
        for row in rows:
            ep = self.service.repo.require(uow, text(row[0]))
            state = next_state(
                ep,
                self.service.repo.task_facts(uow, ep.episode_id),
                self.service.repo.link_facts(uow, ep.episode_id),
                answered=frozenset(),
            )
            if state != ep.state:
                self.service.changed(uow, ep, "state")
                changed += 1
        return changed, text(rows[-1][0]) if len(rows) == limit else None
