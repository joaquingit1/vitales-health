import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.routine.domain.errors import InvalidBody, InvalidTransition
from crew_health.routine.domain.release import BindingEffect, Cadence
from crew_health.routine.domain.vocabulary import (
    Audience,
    EndReason,
    EndsKind,
    MemberBasis,
    OriginMode,
    SourceKind,
    StartedBy,
)


@dataclass(frozen=True, slots=True)
class Override:
    override_id: str
    revision: int
    item_id: str
    person_id: str | None
    audience: Audience
    binding_id: str | None
    trigger_index: int
    trigger_text: str
    started_by: StartedBy
    source_kind: SourceKind
    source_ref: str
    new_cadence: Cadence
    cadence_text: str
    reason_text: str
    state: Literal["active", "ended"]
    started_at: datetime
    ends_kind: EndsKind
    ends_at: datetime | None
    ends_text: str
    stop_role: str | None
    ended_at: datetime | None
    end_reason: EndReason | None
    basis: str
    source_ids: tuple[str, ...]
    config_release_id: str
    member_ids: tuple[str, ...]
    member_basis: Mapping[str, MemberBasis]
    source_origin_mode: OriginMode
    source_at: datetime
    source_chain: tuple[str, ...]
    effect: BindingEffect
    awaiting_disposition: bool
    note: str | None

    def __post_init__(self) -> None:
        for instant in (self.started_at, self.ends_at, self.ended_at, self.source_at):
            if instant is not None:
                require_utc(instant)
        if self.revision < 1:
            raise InvalidBody("/revision", "positive_revision_required")
        if (self.state == "ended") != (self.ended_at is not None and self.end_reason is not None):
            raise InvalidBody("/state", "end_state_mismatch")
        object.__setattr__(self, "member_basis", MappingProxyType(dict(self.member_basis)))


def reason_text(trigger_text: str) -> str:
    return re.sub(r"\s+\((?:sensor_limit|rule)\)$", "", trigger_text.strip()).strip()


def started_by_for(raised_by: str) -> StartedBy:
    return "sensor_limit" if raised_by == "sensor_limit" else "rule"


def end(ovr: Override, *, at: datetime, reason: EndReason) -> Override:
    if ovr.state == "ended":
        raise InvalidTransition(ovr.revision)
    return replace(
        ovr,
        revision=ovr.revision + 1,
        state="ended",
        ended_at=at,
        end_reason=reason,
        awaiting_disposition=False,
    )


def repoint(ovr: Override, new_warning_id: str) -> Override:
    if ovr.state == "ended":
        raise InvalidTransition(ovr.revision)
    return replace(
        ovr,
        revision=ovr.revision + 1,
        source_ref=new_warning_id,
        source_chain=(*ovr.source_chain, new_warning_id),
    )


def clear_decision(
    ovr: Override, *, warning_id: str, clear_reason: str
) -> Literal["end", "environment_changed", "await", "repoint", "ignore"]:  # internal enum
    if (
        ovr.state == "ended"
        or ovr.ends_kind != "warning_cleared"
        or not ovr.source_chain
        or warning_id != ovr.source_chain[-1]
    ):
        return "ignore"
    if clear_reason == "superseded":
        return "repoint"
    if clear_reason == "config_changed":
        return "await"
    if clear_reason == "environment_changed":
        return "environment_changed"
    return "end"


def days_end_at(started_at: datetime, days: int, day_length: timedelta) -> datetime:
    require_utc(started_at)
    return started_at + days * day_length


def applies_to(ovr: Override, person_id: str) -> bool:
    return ovr.person_id == person_id if ovr.audience == "person" else person_id in ovr.member_ids
