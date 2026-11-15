"""Streak, patches and award ledger derivation (CONTRACTS 8 rules 3 to 8; E22 3.2, DE22-8).

The inputs are mission day numbers and ids. There is no field here for a value, so the derivation cannot read one.
Nothing in this module reads a clock: `today` and `as_of` are passed in."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from crew_health.participation.domain.errors import InvalidBasis, ViewBoundExceeded
from crew_health.participation.domain.identity import Json, award_id, canonical_json, sha256_hex
from crew_health.participation.domain.rules import ParticipationRules
from crew_health.participation.domain.types import (
    CompletionFact,
    DayEntry,
    PatchAward,
    StreakView,
    ViewOrigin,
)

MAX_VIEW_DAYS: Final[int] = 4096
CURSOR_HEX: Final[int] = 32


def resolve_first_day(roster_first_day: int | None, facts: Sequence[CompletionFact], today: int) -> int:
    """First eligible day: the roster's; else the day of the first completion; else today (E22 3.2)."""
    if roster_first_day is not None:
        return roster_first_day
    if facts:
        return min(f.mission_day for f in facts)
    return today


def eligible_days(facts: Iterable[CompletionFact], first_day: int, today: int) -> frozenset[int]:
    """The one filter that feeds the streak, the total, the day row and the patches."""
    return frozenset(f.mission_day for f in facts if first_day <= f.mission_day <= today)


def streak_counts(checked: Collection[int], today: int) -> tuple[int, int]:
    """Current and longest streak over checked days (CONTRACTS 8 rules 4 and 5)."""
    days = set(checked)
    cursor = today if today in days else today - 1
    current = 0
    while cursor in days:
        current += 1
        cursor -= 1
    return current, _longest_run(sorted(days))


def _longest_run(ordered: Sequence[int]) -> int:
    best = run = 0
    previous: int | None = None
    for day in ordered:
        run = run + 1 if previous is not None and day == previous + 1 else 1
        best = max(best, run)
        previous = day
    return best


def day_entries(checked: Collection[int], first_day: int, today: int) -> tuple[DayEntry, ...]:
    """Today is `due` until it is checked and never `missed`; a past day without an event is `missed`."""
    entries = []
    for day in range(first_day, today + 1):
        if day in checked:
            status: Literal["checked", "missed", "due"] = "checked"
        else:
            status = "due" if day == today else "missed"
        entries.append(DayEntry(day, status))
    return tuple(entries)


def event_cursor(pairs: Iterable[tuple[str, bool]], history_version: str) -> str:
    """Person scoped and identical across databases: SHA-256 over sorted (completion_id, voided) and the clock history."""
    body: list[Json] = [[cid, voided] for cid, voided in sorted(pairs)]
    return sha256_hex(canonical_json([body, history_version]))[:CURSOR_HEX]


def derive_streak(
    *,
    patient_id: str,
    mission_id: str,
    origin_mode: ViewOrigin,
    facts: Sequence[CompletionFact],
    today: int,
    roster_first_day: int | None,
    as_of: datetime,
    rules: ParticipationRules,
    clock_version: str,
    cursor: str,
) -> StreakView:
    """The StreakView of one person, mission and origin. A fact of another person is a caller defect."""
    if any(f.patient_id != patient_id for f in facts):
        raise InvalidBasis("a completion of another person reached the derivation")
    first_day = resolve_first_day(roster_first_day, facts, today)
    if today - first_day + 1 > MAX_VIEW_DAYS:
        raise ViewBoundExceeded("the eligible days exceed the view bound")
    checked = eligible_days(facts, first_day, today)
    current, longest = streak_counts(checked, today)
    return StreakView(
        patient_id=patient_id,
        mission_id=mission_id,
        origin_mode=origin_mode,
        as_of=as_of,
        current_mission_day=today,
        today="checked" if today in checked else "due",
        current_streak_days=current,
        longest_streak_days=longest,
        completed_days_total=len(checked),
        first_mission_day=first_day,
        days=day_entries(checked, first_day, today),
        derivation_version=rules.derivation_version,
        mission_clock_version=clock_version,
        event_cursor=cursor,
    )


@dataclass(frozen=True, slots=True)
class Attribution:
    threshold: int
    completion_id: str
    mission_day: int


def reached_attributions(
    facts: Sequence[CompletionFact], first_day: int, today: int, thresholds: Collection[int]
) -> dict[int, Attribution]:
    """Threshold k is reached on the k-th eligible checked day; its completion is that day's least
    (occurred_at, completion_id) (E22 3.2)."""
    by_day: dict[int, list[CompletionFact]] = {}
    for fact in facts:
        if first_day <= fact.mission_day <= today:
            by_day.setdefault(fact.mission_day, []).append(fact)
    ordered = sorted(by_day)
    reached: dict[int, Attribution] = {}
    for threshold in sorted(thresholds):
        if threshold < 1 or threshold > len(ordered):
            continue
        day = ordered[threshold - 1]
        winner = min(by_day[day], key=lambda f: (f.occurred_at, f.completion_id))
        reached[threshold] = Attribution(threshold, winner.completion_id, day)
    return reached


@dataclass(frozen=True, slots=True)
class AwardEvent:
    """One append only ledger entry; the design id and version are recorded when the award is earned."""

    kind: Literal["earned", "removed"]
    threshold: int
    patch_design_id: str
    completion_id: str
    mission_day: int
    derivation_version: str


def active_awards(ledger: Iterable[AwardEvent]) -> dict[int, AwardEvent]:
    """Fold the ledger in its stored order: earned adds, removed drops."""
    active: dict[int, AwardEvent] = {}
    for event in ledger:
        if event.kind == "earned":
            active[event.threshold] = event
        else:
            active.pop(event.threshold, None)
    return active


def next_award_events(
    ledger: Sequence[AwardEvent],
    *,
    total: int,
    reached: Mapping[int, Attribution],
    rules: ParticipationRules,
    void_applied: bool,
) -> tuple[AwardEvent, ...]:
    """Ledger entries to append (E22 DE22-8). Earned when first reached; removed only by a void that drops the
    total below the threshold. A clock correction or a threshold edit never removes an award, so a retained award
    keeps the attribution recorded when it was earned."""
    active = active_awards(ledger)
    events: list[AwardEvent] = []
    for threshold in sorted(active):
        if void_applied and total < threshold:
            held = active[threshold]
            events.append(
                AwardEvent(
                    "removed",
                    threshold,
                    held.patch_design_id,
                    held.completion_id,
                    held.mission_day,
                    held.derivation_version,
                )
            )
    for design in rules.patch_designs:
        if design.threshold in active or total < design.threshold:
            continue
        where = reached.get(design.threshold)
        if where is None:
            raise InvalidBasis("a reached threshold has no attributed completion")
        events.append(
            AwardEvent(
                "earned",
                design.threshold,
                design.patch_design_id,
                where.completion_id,
                where.mission_day,
                rules.derivation_version,
            )
        )
    return tuple(events)


def award_views(
    patient_id: str, mission_id: str, origin_mode: ViewOrigin, ledger: Iterable[AwardEvent]
) -> tuple[PatchAward, ...]:
    """The PatchAward list from the ledger, ascending by threshold."""
    active = active_awards(ledger)
    return tuple(
        PatchAward(
            award_id=award_id(patient_id, mission_id, origin_mode, threshold),
            patient_id=patient_id,
            mission_id=mission_id,
            origin_mode=origin_mode,
            threshold=threshold,
            patch_design_id=event.patch_design_id,
            reached_by_completion_id=event.completion_id,
            reached_on_mission_day=event.mission_day,
            derivation_version=event.derivation_version,
        )
        for threshold, event in sorted(active.items())
    )
