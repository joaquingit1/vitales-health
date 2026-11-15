from collections.abc import Sequence

from crew_health.episodes.domain.types import MR_PENDING, EpisodeState, LinkFact, TaskFact


def derive(open_tasks: Sequence[TaskFact], links: Sequence[LinkFact]) -> EpisodeState:
    if any(t.state == "open" and t.kind == "review" for t in open_tasks):
        return EpisodeState.AWAITING_REVIEW
    if any(link.kind == "measurement_request" and link.state in MR_PENDING for link in links):
        return EpisodeState.AWAITING_INFORMATION
    if any(t.state == "open" for t in open_tasks):
        return EpisodeState.FOLLOWUP
    return EpisodeState.OPEN
