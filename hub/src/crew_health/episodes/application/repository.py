from __future__ import annotations

from datetime import datetime
from typing import Protocol

from crew_health.episodes.domain.types import Episode, EpisodeState, LinkFact, TaskFact
from crew_health.kernel.storage import SqlValue
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork


class EpisodeRepository(Protocol):
    def get(self, view: View, ref: str) -> Episode | None: ...
    def require(self, view: View, ref: str) -> Episode: ...
    def create(self, uow: UnitOfWork, ep: Episode) -> None: ...
    def onset(self, uow: UnitOfWork, ep: Episode) -> None: ...
    def change(
        self,
        uow: UnitOfWork,
        ep: Episode,
        state: EpisodeState,
        kind: str,
        *,
        cause: str | None = None,
        revision: int | None = None,
    ) -> Episode: ...
    def links(self, view: View, episode_id: str) -> tuple[tuple[SqlValue, ...], ...]: ...
    def task_facts(self, view: View, episode_id: str) -> tuple[TaskFact, ...]: ...
    def link_facts(self, view: View, episode_id: str) -> tuple[LinkFact, ...]: ...
    def timeline(
        self,
        uow: UnitOfWork,
        ep: Episode,
        *,
        entry_id: str,
        kind: str,
        ref_id: str,
        state: str | None = None,
        occurred_at: datetime | None = None,
        dedupe: str | None = None,
        source: str = "episode",
    ) -> bool: ...
