from collections.abc import Sequence

from crew_health.episodes.domain.derivation import derive
from crew_health.episodes.domain.types import Episode, EpisodeState, LinkFact, TaskFact


def next_state(
    ep: Episode,
    tasks: Sequence[TaskFact],
    links: Sequence[LinkFact],
    *,
    answered: frozenset[str],
    reopen: bool = False,
    resolve: bool = False,
) -> EpisodeState:
    if resolve:
        return EpisodeState.RESOLVED
    if ep.state == EpisodeState.DRAFT and not set(ep.red_flag_ids) <= answered:
        return EpisodeState.DRAFT
    if ep.state == EpisodeState.RESOLVED and not reopen:
        return EpisodeState.RESOLVED
    return derive(tasks, links)
