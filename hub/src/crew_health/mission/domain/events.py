"""Mission event recording rules (E23 3.6)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from crew_health.mission.domain.errors import InvalidValue, OriginMismatch, UnauthorizedScope

TimeState = Literal["known", "replay", "unknown", "future"]  # S5.2 NormalizedTime.state


@dataclass(frozen=True, slots=True)
class EventTypeFacts:
    id: str
    label: str
    starts_overlay: str | None
    ends_overlay: str | None
    who_may_record: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    recorded_by_kind: str
    recorded_by_id: str
    origin_mode: str
    environment_id: str
    person_ids: tuple[str, ...]
    location: str | None
    time_state: TimeState
    effective_at: datetime | None  # normalized instant, or None when unknown
    # The note is free text: kept out of repr so no log line, audit entry or error body can carry it.
    note: str | None = field(default=None, repr=False)

    @property
    def orders_state(self) -> bool:
        """Only a known or replayed normalized time may change an overlay or a fact (S5.2)."""
        return self.time_state in ("known", "replay") and self.effective_at is not None


def check_record(
    *,
    event_type: str,
    types: Sequence[EventTypeFacts],
    roles: Collection[str],
    mission_mode: str,
    body_origin: str,
    person_ids: Collection[str],
    roster: Collection[str],
) -> EventTypeFacts:
    """In order: the type exists, a session role may record it, the origin is the mission mode, persons exist."""
    found = next((t for t in types if t.id == event_type), None)
    if found is None:
        raise InvalidValue("unknown mission event type")
    if not set(roles) & set(found.who_may_record):
        raise UnauthorizedScope("role may not record this event type")
    if body_origin != mission_mode:
        raise OriginMismatch("origin_mode differs from the mission mode")
    if len(set(person_ids)) != len(tuple(person_ids)) or not set(person_ids) <= set(roster):
        raise InvalidValue("person_ids must be distinct roster members")
    return found
