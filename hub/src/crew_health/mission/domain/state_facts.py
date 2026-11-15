"""State facts set and cleared only by listed event types in listed environments (E23 3.6)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StateFactConfig:
    id: str
    label: str
    set_by_event_types: tuple[str, ...]
    cleared_by_event_types: tuple[str, ...]
    environments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FactState:
    fact: str
    environment_id: str
    value: bool
    cause_at: datetime
    cause_event_id: str
    origin_mode: str


def _later(state: FactState | None, at: datetime, event_id: str) -> bool:
    if state is None:
        return True
    if at != state.cause_at:
        return at > state.cause_at
    return event_id > state.cause_event_id


def apply_event(
    configs: Sequence[StateFactConfig],
    current: Mapping[str, FactState],
    *,
    event_type: str,
    environment_id: str,
    at: datetime | None,
    event_id: str,
    origin_mode: str,
) -> tuple[FactState, ...]:
    """Facts this event changes. An unordered or older event is recorded elsewhere and changes nothing here."""
    if at is None:
        return ()
    changed: list[FactState] = []
    for config in configs:
        if environment_id not in config.environments:
            continue
        sets = event_type in config.set_by_event_types
        clears = event_type in config.cleared_by_event_types
        if sets == clears:  # neither, or an ambiguous configuration that must not guess a direction
            continue
        if _later(current.get(config.id), at, event_id):
            changed.append(FactState(config.id, environment_id, sets, at, event_id, origin_mode))
    return tuple(changed)
