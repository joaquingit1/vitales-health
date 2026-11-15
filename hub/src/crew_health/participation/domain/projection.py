"""Projection rules for one intent (E22 3.4) and mission day assignment (CONTRACTS 8 rule 2)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.met import MissionClock
from crew_health.participation.domain.identity import completion_id
from crew_health.participation.domain.types import (
    CompletionBasis,
    CompletionRecord,
    StatusReason,
    ViewOrigin,
)


@dataclass(frozen=True, slots=True)
class ProjectionDecision:
    kind: Literal["nothing", "accepted", "rejected"]
    reason: StatusReason | None = None


@dataclass(frozen=True, slots=True)
class DayAssignment:
    clock_version: str
    mission_day: int


@dataclass(frozen=True, slots=True)
class DayMove:
    completion_id: str
    from_day: int
    to_day: int
    from_clock_version: str
    to_clock_version: str


def decide(
    basis: CompletionBasis, *, record_origin: ViewOrigin, replacement: bool = False
) -> ProjectionDecision:
    """Apply the rules of 3.4 in their stated order.

    A replacement (3.5) was established by an identity correction, so its patient is not the person who acted; it
    still passes the origin rule."""
    if basis.origin_mode == "REPLAY":
        return ProjectionDecision("nothing")
    if basis.origin_mode != record_origin:
        return ProjectionDecision("rejected", "ORIGIN_MISMATCH")
    if not replacement and not _actor_matches(basis):
        return ProjectionDecision("rejected", "IDENTITY_MISMATCH")
    if basis.basis == "daily_check" and basis.required_answers_complete is not True:
        return ProjectionDecision("rejected", "REQUIRED_ANSWER_MISSING")
    return ProjectionDecision("accepted")


def _actor_matches(basis: CompletionBasis) -> bool:
    if basis.basis == "episode_updated" and basis.cause_kind == "urgent_event":
        return True
    return basis.actor_person_id == basis.patient_id


def assign_day(clock: MissionClock, occurred_at: datetime) -> DayAssignment:
    """The day under the clock version in force at `occurred_at`; a later version never moves it."""
    version = clock.version_at(occurred_at).clock_version
    return DayAssignment(version, clock.mission_day(occurred_at, version=version))


def build_completion(
    basis: CompletionBasis,
    decision: ProjectionDecision,
    *,
    key: str,
    clock: MissionClock,
    rule_version: str,
    lineage_id: str | None = None,
    generation: int = 0,
    replaces_completion_id: str | None = None,
) -> CompletionRecord | None:
    """The stored completion for a decision, or None when the intent creates nothing (REPLAY)."""
    if decision.kind == "nothing":
        return None
    day = assign_day(clock, basis.occurred_at)
    cid = completion_id(key)
    return CompletionRecord(
        completion_id=cid,
        intent_key=key,
        lineage_id=cid if lineage_id is None else lineage_id,
        generation=generation,
        replaces_completion_id=replaces_completion_id,
        basis=basis,
        status="accepted" if decision.kind == "accepted" else "rejected",
        status_reason=decision.reason,
        mission_clock_version=day.clock_version,
        mission_day=day.mission_day,
        completion_rule_version=rule_version,
    )


def plan_day_moves(records: Iterable[CompletionRecord], clock: MissionClock) -> tuple[DayMove, ...]:
    """Every completion whose day or clock version differs under the clock now, in completion id order.

    A new clock version moves an event only when its effective range covers the event's instant, so adding a later
    version moves none."""
    moves = []
    for record in sorted(records, key=lambda r: r.completion_id):
        now = assign_day(clock, record.basis.occurred_at)
        if (now.mission_day, now.clock_version) != (record.mission_day, record.mission_clock_version):
            moves.append(
                DayMove(
                    record.completion_id,
                    record.mission_day,
                    now.mission_day,
                    record.mission_clock_version,
                    now.clock_version,
                )
            )
    return tuple(moves)
