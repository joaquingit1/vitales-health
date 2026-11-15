"""Wire forms of the participation views (CONTRACTS 8) and the stored intent payload."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, cast

from crew_health.participation.domain.canonical import award_form, crew_goal_form, streak_form
from crew_health.participation.domain.errors import InvalidBasis
from crew_health.participation.domain.types import (
    CompletionBasis,
    CrewGoalView,
    PatchAward,
    StreakView,
)


def wire_instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def streak_wire(view: StreakView) -> dict[str, object]:
    return {**cast(dict[str, object], streak_form(view)), "as_of": wire_instant(view.as_of)}


def award_wire(award: PatchAward) -> dict[str, object]:
    return dict(cast(dict[str, object], award_form(award)))


def crew_goal_wire(view: CrewGoalView, *, as_of: datetime) -> dict[str, object]:
    return {**cast(dict[str, object], crew_goal_form(view)), "as_of": wire_instant(as_of)}


def _optional_text(fields: Mapping[str, object], name: str) -> str | None:
    value = fields.get(name)
    if value is not None and not isinstance(value, str):
        raise InvalidBasis(name + " is not text")
    return value


def basis_from_fields(fields: Mapping[str, object]) -> CompletionBasis:
    """Rebuild the basis of a stored intent; the constructor validates every field again."""
    occurred = fields.get("occurred_at")
    if not isinstance(occurred, str):
        raise InvalidBasis("occurred_at is not text")
    complete = fields.get("required_answers_complete")
    if complete is not None and not isinstance(complete, bool):
        raise InvalidBasis("required_answers_complete is not a boolean")
    return CompletionBasis(
        basis=cast(
            Literal["daily_check", "concern_reported", "episode_updated"], _optional_text(fields, "basis")
        ),
        basis_ref=cast(str, _optional_text(fields, "basis_ref")),
        patient_id=cast(str, _optional_text(fields, "patient_id")),
        mission_id=cast(str, _optional_text(fields, "mission_id")),
        origin_mode=cast(Literal["LIVE", "REPLAY", "SIM"], _optional_text(fields, "origin_mode")),
        occurred_at=datetime.strptime(occurred, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC),
        original_offset=_optional_text(fields, "original_offset"),
        actor_person_id=_optional_text(fields, "actor_person_id"),
        cause_kind=cast(Literal["answer", "urgent_event"] | None, _optional_text(fields, "cause_kind")),
        cause_ref=_optional_text(fields, "cause_ref"),
        required_answers_complete=complete,
    )
