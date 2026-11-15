from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from crew_health.kernel.ids import require_id
from crew_health.kernel.scheduler import ElapsedDeadline
from crew_health.kernel.uow import SessionContext
from crew_health.tools.domain.errors import ToolsInvalid
from crew_health.tools.domain.phase import Phase


@dataclass(frozen=True, slots=True)
class ToolScope:
    session: SessionContext
    subject_person_id: str | None
    mission_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]
    episode_id: str | None
    help_session_id: str | None
    plan_run_id: str | None
    turn_id: str
    conversation_id: str
    config_release_id: str
    evidence_release_id: str
    model_release_id: str
    phase: Phase
    source_revision: int
    generation: int
    deadline: ElapsedDeadline

    def __post_init__(self) -> None:
        for value in (
            self.subject_person_id,
            self.mission_id,
            self.episode_id,
            self.help_session_id,
            self.plan_run_id,
            self.turn_id,
            self.conversation_id,
            self.config_release_id,
            self.evidence_release_id,
            self.model_release_id,
        ):
            if value is not None:
                require_id(value)
        if (
            self.origin_mode not in {"LIVE", "REPLAY", "SIM"}
            or not isinstance(self.phase, Phase)
            or type(self.source_revision) is not int
            or self.source_revision < 1
            or type(self.generation) is not int
            or self.generation < 1
            or self.deadline.duration.total_seconds() <= 0
        ):
            raise ToolsInvalid("MODEL_OUTPUT_REJECTED")


def require_own_subject(session: SessionContext, subject_person_id: str | None) -> None:
    if (
        session.kind != "crew"
        or session.actor.kind != "person"
        or subject_person_id is None
        or session.person_id != subject_person_id
        or session.actor.id != subject_person_id
    ):
        raise ToolsInvalid("MODEL_OUTPUT_REJECTED")
    require_id(subject_person_id)
