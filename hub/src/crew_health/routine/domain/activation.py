from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.errors import InvalidBody, InvalidTransition
from crew_health.routine.domain.release import Cadence
from crew_health.routine.domain.vocabulary import EndReason, OriginMode, SourceKind


@dataclass(frozen=True, slots=True)
class CadenceActivation:
    activation_id: str
    revision: int
    binding_id: str
    item_id: str
    person_id: str
    cadence: Cadence
    started_at: datetime
    ends_kind: Literal["days", "mission_event", "warning_cleared"]
    ends_at: datetime | None
    state: Literal["active", "ended"]
    ended_at: datetime | None
    end_reason: EndReason | None
    source_kind: SourceKind
    source_ref: str
    source_at: datetime
    source_origin_mode: OriginMode
    config_release_id: str

    def __post_init__(self) -> None:
        for at in (self.started_at, self.ends_at, self.ended_at, self.source_at):
            if at is not None:
                require_utc(at)
        if self.revision < 1 or (
            (self.state == "ended") != (self.ended_at is not None and self.end_reason is not None)
        ):
            raise InvalidBody("/activation", "invalid_revision_or_state")

    def end(self, *, at: datetime, reason: EndReason) -> CadenceActivation:
        if self.state != "active":
            raise InvalidTransition(self.revision)
        return replace(self, revision=self.revision + 1, state="ended", ended_at=at, end_reason=reason)
