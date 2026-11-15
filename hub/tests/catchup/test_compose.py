"""CONTRACTS 26 goldens, independently transcribed from the example words."""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crew_health.catchup.domain.compose import CatchUpWords, GroundWords, PlanFacts, compose_for_help
from crew_health.catchup.domain.render import render

AT = datetime(2026, 11, 20, 12, 2, tzinfo=UTC)
CONFIG = Path(__file__).resolve().parents[3] / "config"


@dataclass(frozen=True)
class Pin:
    name: str
    version: str


def config_pins() -> tuple[Pin, ...]:
    """The versions help pins: sha256 of the released ui-copy and help-presentation documents."""
    return tuple(
        Pin(name, hashlib.sha256((CONFIG / f"{name}.json").read_bytes()).hexdigest())
        for name in ("ui-copy", "help-presentation", "responder-flows")
    )


@dataclass(frozen=True)
class Tick:
    text: str
    time_text: str | None
    flow_id: str = "eye_injury"
    index: int = 0
    ticked_at: datetime = AT


@dataclass(frozen=True)
class Snapshot:
    help_session_id: str = "help_1"
    session_revision: int = 1
    subject_name: str = "Tomas Lind"
    place: str | None = "airlock"
    flow_id: str | None = "eye_injury"
    flow_title: str | None = "Eye injury or something in the eye"
    answer_lines: tuple[str, ...] = ("Air safe.", "Tomas Lind responding.")
    ticks: tuple[Tick, ...] = (Tick("Do not rub the eye.", "12:02"), Tick("Call the CMO.", "12:03"))
    crew_called_at: datetime | None = AT
    environment_id: str | None = "lunar_surface"
    queued_at: datetime = AT
    crew_time_text: str | None = "12:04"
    helping: str = "Mara Okafor is helping."
    pins: tuple[Pin, ...] = field(default_factory=config_pins)
    subject_person_id: str | None = "person_tomas"


def words() -> CatchUpWords:
    return CatchUpWords(
        "{time} {step}", "{step} {recheck}", "{topic}", "Nothing is being watched.", "Unknown"
    )


def ground_words() -> GroundWords:
    return GroundWords(
        "{subject_name} needs help at the {place}. {flow_title}.",
        "{answers}.",
        "Crew called at {time}. {helping}",
    )


@pytest.mark.trace("R61", "E29-04")
def test_golden_help_has_header_answers_ticks_then_crew_and_unknown_plan() -> None:
    result = compose_for_help(Snapshot(), words(), ground_words(), crew_time_text="12:02", plan=None)
    assert result.what_happened == (
        "Tomas Lind needs help at the airlock. Eye injury or something in the eye.",
        "Air safe. Tomas Lind responding.",
    )
    assert result.what_was_done == (
        "12:02 Do not rub the eye.",
        "12:03 Call the CMO.",
        "Crew called at 12:02. Unknown",
    )
    assert result.being_watched == ("Unknown",)
    assert result.advice_welcome == ("Unknown",)


@pytest.mark.trace("R61", "E29-04")
def test_known_empty_plan_is_the_only_nothing_watched_case() -> None:
    result = compose_for_help(
        Snapshot(), words(), ground_words(), crew_time_text=None, plan=PlanFacts((), (), (), ())
    )
    assert result.being_watched == ("Nothing is being watched.",)
    assert result.advice_welcome == ("Unknown",)
    assert result.what_was_done[-1] == "Crew called at Unknown. Unknown"


@pytest.mark.trace("R61", "E29-04")
def test_plan_steps_follow_crew_and_advice_keeps_source_order() -> None:
    plan = PlanFacts(
        (("12:04", "Manual check."),), (("Watch sight.", "Check again."),), ("Task.",), ("Log.",)
    )
    result = compose_for_help(Snapshot(), words(), ground_words(), crew_time_text="12:02", plan=plan)
    assert result.what_was_done[-2:] == ("Crew called at 12:02. Unknown", "12:04 Manual check.")
    assert result.being_watched == ("Watch sight. Check again.",)
    assert result.advice_welcome == ("Task.", "Log.")


@pytest.mark.trace("R61", "E29-04")
def test_missing_and_none_template_values_render_unknown() -> None:
    assert (
        render("{missing} {none} {known}", {"none": None, "known": "Known"}, "Unknown")
        == "Unknown Unknown Known"
    )
