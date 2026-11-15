"""Occurrence identity and outstanding occurrences of mission-event warnings (E24 3.9).

Every triggering event is an occurrence. A clear event with `occurred_at = T` resolves every outstanding
occurrence of its key with `occurred_at < T`; equal times stay outstanding (fail safe). The warning clears
only when no occurrence remains outstanding. Causality uses `occurred_at`, never `recorded_at`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class Occurrence:
    warning_id: str
    definition_id: str
    scope_key: str
    trigger_ref: str
    occurred_at: datetime
    resolved_by_event_id: str | None


type TriggerClass = Literal["clear_later", "duplicate", "attach", "raise"]  # internal enum


def outstanding(occurrences: Iterable[Occurrence]) -> tuple[Occurrence, ...]:
    return tuple(sorted((o for o in occurrences if o.resolved_by_event_id is None), key=_order))


def _order(o: Occurrence) -> tuple[datetime, str]:
    return (o.occurred_at, o.trigger_ref)


def classify_trigger(
    *,
    trigger_ref: str,
    occurred_at: datetime,
    latest_clear: datetime | None,
    known: Iterable[Occurrence] | None,
) -> TriggerClass:
    """What a triggering event does for one key.

    `known` is every occurrence, resolved or not, of the key's active warning; `None` when no warning is
    active for the key. An event that precedes an already recorded clear event raises and attaches nothing.
    """
    if latest_clear is not None and occurred_at < latest_clear:
        return "clear_later"
    if known is None:
        return "raise"
    if any(o.trigger_ref == trigger_ref for o in known):
        return "duplicate"
    return "attach"


@dataclass(frozen=True, slots=True)
class ClearEffect:
    resolves: tuple[Occurrence, ...]
    remaining: int
    clears_warning: bool


def apply_clear_event(occurrences: Iterable[Occurrence], clear_at: datetime) -> ClearEffect:
    """The effect of a clear event on one active warning's occurrences."""
    items = tuple(occurrences)
    pending = outstanding(items)
    resolves = tuple(o for o in pending if o.occurred_at < clear_at)
    remaining = len(pending) - len(resolves)
    # A warning whose occurrences were all resolved while clears were frozen clears on the next clear event.
    return ClearEffect(resolves, remaining, remaining == 0 and len(items) > 0)


def fully_resolved(occurrences: Iterable[Occurrence]) -> bool:
    """True when the warning has occurrences and none is outstanding (used to reconcile a frozen clear)."""
    items = tuple(occurrences)
    return len(items) > 0 and not outstanding(items)
