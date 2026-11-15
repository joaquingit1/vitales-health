"""CONTRACTS 26: facts and released words form four ordered sections."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.catchup.domain.render import render


class TickFacts(Protocol):
    @property
    def text(self) -> str: ...
    @property
    def time_text(self) -> str | None: ...


class SnapshotFacts(Protocol):
    @property
    def subject_name(self) -> str: ...
    @property
    def place(self) -> str | None: ...
    @property
    def flow_title(self) -> str | None: ...
    @property
    def answer_lines(self) -> tuple[str, ...]: ...
    @property
    def ticks(self) -> Sequence[TickFacts]: ...
    @property
    def crew_called_at(self) -> datetime | None: ...


type Sections = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class CatchUpWords:
    step_done_line: str
    watch_line: str
    advice_line: str
    nothing_watched: str
    unknown: str


@dataclass(frozen=True, slots=True)
class GroundWords:
    header_template: str
    answers_template: str
    crew_template: str


@dataclass(frozen=True, slots=True)
class PlanFacts:
    done_steps: tuple[tuple[str | None, str], ...]
    watch_steps: tuple[tuple[str, str], ...]
    replaces_ground_task: tuple[str, ...]
    ground_log_items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Composition:
    what_happened: tuple[str, ...]
    what_was_done: tuple[str, ...]
    being_watched: tuple[str, ...]
    advice_welcome: tuple[str, ...]

    def sections(self) -> Sections:
        return self.what_happened, self.what_was_done, self.being_watched, self.advice_welcome


def compose_for_help(
    snapshot: SnapshotFacts,
    words: CatchUpWords,
    ground_words: GroundWords,
    *,
    crew_time_text: str | None,
    plan: PlanFacts | None,
    helping: str | None = None,
) -> Composition:
    unknown = words.unknown
    happened = (
        render(
            ground_words.header_template,
            {
                "subject_name": snapshot.subject_name,
                "place": snapshot.place,
                "flow_title": snapshot.flow_title,
            },
            unknown,
        ),
        render(
            ground_words.answers_template,
            {"answers": ". ".join(line.rstrip(".") for line in snapshot.answer_lines) or None},
            unknown,
        ),
    )
    done = [
        render(words.step_done_line, {"time": t.time_text, "step": t.text}, unknown) for t in snapshot.ticks
    ]
    if snapshot.crew_called_at is not None:
        done.append(render(ground_words.crew_template, {"time": crew_time_text, "helping": helping}, unknown))
    watched: tuple[str, ...] = (unknown,)
    advice: tuple[str, ...] = (unknown,)
    if plan is not None:
        done.extend(
            render(words.step_done_line, {"time": time, "step": step}, unknown)
            for time, step in plan.done_steps
        )
        watched = tuple(
            render(words.watch_line, {"step": step, "recheck": recheck}, unknown)
            for step, recheck in plan.watch_steps
        ) or (words.nothing_watched,)
        advice = tuple(
            render(words.advice_line, {"topic": topic}, unknown)
            for topic in (*plan.replaces_ground_task, *plan.ground_log_items)
        ) or (unknown,)
    return Composition(happened, tuple(done), watched, advice)
