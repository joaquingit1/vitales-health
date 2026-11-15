"""Activity overlays (E23 3.5, invariants I-O1 to I-O5)."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.mission.domain.errors import IntegrityViolation, InvalidTransition, InvalidValue

OverlayState = Literal["planned", "active", "ended"]
SourceKind = Literal["person", "mission_event", "operator"]  # `adapter` is never written here
EVA = "eva"  # CONTRACTS 12 fixed overlay vocabulary: post_eva follows eva
POST_EVA = "post_eva"


@dataclass(frozen=True, slots=True)
class OverlayRecord:
    overlay_id: str
    revision: int
    person_id: str
    kind: str
    state: OverlayState
    planned_start: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    source_kind: SourceKind
    source_ref: str | None
    location: str | None
    pinned_release_id: str
    last_event_at: datetime | None
    last_event_id: str | None


@dataclass(frozen=True, slots=True)
class OverlayEffect:
    action: Literal["start", "end"]
    kind: str


def create(
    *,
    overlay_id: str,
    person_id: str,
    kind: str,
    kinds_in_profile: Collection[str],
    planned_start: datetime | None,
    started_at: datetime | None,
    location: str | None,
    source_kind: SourceKind,
    source_ref: str | None,
    active_kinds: Collection[str],
    release_id: str,
) -> OverlayRecord:
    """I-O5 a kind absent from the active profile is INVALID_VALUE; I-O1 one active overlay per kind."""
    if kind not in kinds_in_profile:
        raise InvalidValue("overlay kind not in the active profile")
    if (planned_start is None) == (started_at is None):
        raise InvalidValue("give exactly one of planned_start and started_at")
    if started_at is not None and kind in active_kinds:
        raise InvalidTransition("an overlay of this kind is already active")
    state: OverlayState = "active" if started_at is not None else "planned"
    return OverlayRecord(
        overlay_id,
        1,
        person_id,
        kind,
        state,
        planned_start,
        started_at,
        None,
        source_kind,
        source_ref,
        location,
        release_id,
        None,
        None,
    )


def start(record: OverlayRecord, at: datetime, active_kinds: Collection[str]) -> OverlayRecord:
    """A planned overlay becomes active only by an explicit start, never because its time arrives."""
    if record.state != "planned":
        raise InvalidTransition("only a planned overlay can start")
    if record.kind in active_kinds:
        raise InvalidTransition("an overlay of this kind is already active")
    return replace(record, revision=record.revision + 1, state="active", started_at=at)


def end(record: OverlayRecord, ended_at: datetime) -> OverlayRecord:
    """I-O2 ended_at is not before started_at; ending a planned overlay keeps started_at null."""
    if record.state == "ended":
        raise InvalidTransition("the overlay has ended")
    if record.started_at is not None and ended_at < record.started_at:
        raise InvalidValue("ended_at before started_at")
    return replace(record, revision=record.revision + 1, state="ended", ended_at=ended_at)


def plan_event_effects(
    starts: str | None, ends: str | None, active_kinds: Collection[str]
) -> tuple[tuple[OverlayEffect, ...], bool]:
    """Effects of one mission event on one person, or ((), True) when any effect would break I-O1 to I-O4.

    `eva_ended` ends eva and starts post_eva together (I-O3); starting eva also ends post_eva (I-O4).
    """
    active = set(active_kinds)
    effects: list[OverlayEffect] = []
    if ends is not None:
        if ends not in active:
            return (), True
        effects.append(OverlayEffect("end", ends))
        active.discard(ends)
    if starts is not None:
        if starts in active:
            return (), True
        if starts == EVA and POST_EVA in active:
            effects.append(OverlayEffect("end", POST_EVA))
            active.discard(POST_EVA)
        effects.append(OverlayEffect("start", starts))
    return tuple(effects), False


def event_is_newer(last_at: datetime | None, last_id: str | None, at: datetime, event_id: str) -> bool:
    """An event changes an overlay only when newer than the last effect; event_id breaks equal times."""
    if last_at is None:
        return True
    if at != last_at:
        return at > last_at
    return last_id is None or event_id > last_id


def post_eva_end(day_end: datetime) -> datetime:
    """I-O4: post_eva ends at the end of the mission day, an absolute instant that clock corrections never move."""
    return day_end


def intervals(
    records: Sequence[OverlayRecord], kinds: Collection[str], since: datetime, until: datetime
) -> tuple[OverlayRecord, ...]:
    """Started overlays of the kinds whose exact interval meets [since, until]. Open ones have no end."""
    wanted = set(kinds)
    return tuple(
        r
        for r in records
        if r.kind in wanted
        and r.started_at is not None
        and r.started_at <= until
        and (r.ended_at is None or r.ended_at >= since)
    )


def assert_one_active_per_kind(records: Sequence[OverlayRecord]) -> None:
    seen: set[tuple[str, str]] = set()
    for record in records:
        if record.state != "active":
            continue
        key = (record.person_id, record.kind)
        if key in seen:
            raise IntegrityViolation("two active overlays of one kind for one person")
        seen.add(key)


def by_kind(records: Sequence[OverlayRecord]) -> Mapping[str, OverlayRecord]:
    return {r.kind: r for r in records if r.state == "active"}
