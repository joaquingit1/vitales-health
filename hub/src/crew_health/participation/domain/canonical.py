"""Canonical forms for determinism checks (CONTRACTS 8 rule 9; E22 3.2): views serialize without `as_of`, and
`result_sha256` hashes every StreakView, every active award and the crew goal, sorted."""

from __future__ import annotations

from collections.abc import Iterable

from crew_health.participation.domain.identity import Json, canonical_json, sha256_hex
from crew_health.participation.domain.types import CrewGoalView, PatchAward, StreakView


def streak_form(view: StreakView) -> Json:
    return {
        "patient_id": view.patient_id,
        "mission_id": view.mission_id,
        "origin_mode": view.origin_mode,
        "current_mission_day": view.current_mission_day,
        "today": view.today,
        "current_streak_days": view.current_streak_days,
        "longest_streak_days": view.longest_streak_days,
        "completed_days_total": view.completed_days_total,
        "first_mission_day": view.first_mission_day,
        "days": [{"mission_day": d.mission_day, "status": d.status} for d in view.days],
        "derivation_version": view.derivation_version,
        "mission_clock_version": view.mission_clock_version,
        "event_cursor": view.event_cursor,
    }


def award_form(award: PatchAward) -> Json:
    return {
        "award_id": award.award_id,
        "patient_id": award.patient_id,
        "mission_id": award.mission_id,
        "origin_mode": award.origin_mode,
        "threshold": award.threshold,
        "patch_design_id": award.patch_design_id,
        "reached_by_completion_id": award.reached_by_completion_id,
        "reached_on_mission_day": award.reached_on_mission_day,
        "derivation_version": award.derivation_version,
    }


def crew_goal_form(view: CrewGoalView) -> Json:
    return {
        "mission_id": view.mission_id,
        "origin_mode": view.origin_mode,
        "window_first_mission_day": view.window_first_mission_day,
        "window_last_mission_day": view.window_last_mission_day,
        "completed_total": view.completed_total,
        "due_total": view.due_total,
        "reached": view.reached,
        "derivation_version": view.derivation_version,
        "mission_clock_version": view.mission_clock_version,
    }


def result_sha256(
    streaks: Iterable[StreakView], awards: Iterable[PatchAward], crew_goal: CrewGoalView | None
) -> str:
    """Order independent digest of the derived results. Practice awards are not included (E31 owns them)."""
    body: Json = {
        "streaks": sorted(canonical_json(streak_form(s)) for s in streaks),
        "awards": sorted(canonical_json(award_form(a)) for a in awards),
        "crew_goal": None if crew_goal is None else crew_goal_form(crew_goal),
    }
    return sha256_hex(canonical_json(body))
