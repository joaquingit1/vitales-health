"""GuidedCheck and its state machine (CONTRACTS 23; E30 3.2, 3.3). Pure: no I/O, no clock read. Guards are checked in
the order written; a refused command raises a typed error. The application loads, commands and saves in one unit of
work, so a refused command changes nothing it does not explicitly record (3.3 steps 3 and 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

from crew_health.teach.domain.capture import (
    CLOCK_DISCONTINUITY,
    HUB_RESTART,
    SEQUENCE_CONFLICT,
    Beat,
    ClockReading,
    CountWindow,
    completeness,
    count_per_minute,
)
from crew_health.teach.domain.catalog import SkillDefinition, TeachStep
from crew_health.teach.domain.errors import (
    BeatRefused,
    IncompleteCapture,
    InvalidTransition,
    InvalidValue,
)

type State = Literal["teaching", "counting", "checked", "retry", "cancelled"]
type AttemptStatus = Literal["open", "incomplete", "finished", "abandoned"]
TERMINAL: frozenset[str] = frozenset({"checked", "cancelled"})


def presence(choice: str) -> Literal["present", "absent", "unknown"]:
    """CONTRACTS 23 answer table, over the closed contract enum of question answers."""
    if choice == "regular":
        return "present"
    if choice == "irregular":
        return "absent"
    if choice == "not_sure":
        return "unknown"
    raise InvalidValue("answer_not_a_choice", fields=("/answer",))


@dataclass(frozen=True, slots=True)
class CameraResult:
    """The camera reference as the check shows it: a value from a real reading, or the reason there was none."""

    observation_id: str | None
    value: Fraction | None
    unit: str | None
    reason: str | None
    origin_mode: str


@dataclass(slots=True)
class Attempt:
    window: CountWindow
    beats: dict[int, Beat]
    status: AttemptStatus
    flags: set[str]
    camera_ticket_id: str | None = None
    camera_unavailable_reason: str | None = None
    count_exact: Fraction | None = None
    result: str | None = None
    difference_exact: Fraction | None = None
    observation_id: str | None = None
    finished_at: datetime | None = None
    new_beats: list[Beat] = field(default_factory=list)


@dataclass(slots=True)
class GuidedCheck:
    guided_check_id: str
    revision: int
    skill_id: str
    check_id: str
    environment_id: str | None  # None: unknown, so only steps for every environment show (fail safe)
    helper_id: str
    subject_id: str
    measured_record_id: str
    count_origin: str
    plan_run_id: str | None
    plan_step_id: str | None
    episode_id: str | None
    role_assignment_id: str | None
    state: State
    teach_step_no: int
    steps_done: list[tuple[int, datetime]]
    attempt: Attempt | None
    helper_answer: str | None
    answer_id: str | None
    previous_answer_id: str | None
    camera: CameraResult | None
    result: str | None
    difference_exact: Fraction | None
    count_exact: Fraction | None
    result_text: str | None
    observation_id: str | None
    experimental: bool
    pins: dict[str, str]  # configuration document name to the pinned version (I30-17)
    config_release_id: str
    created_at: datetime
    created_by_session: str

    # Navigation -------------------------------------------------------------------------------------------------

    def steps(self, skill: SkillDefinition) -> tuple[TeachStep, ...]:
        return skill.steps_for(self.environment_id)

    def step_on_screen(self, skill: SkillDefinition) -> TeachStep:
        for step in self.steps(skill):
            if step.step_no == self.teach_step_no:
                return step
        raise InvalidTransition("step_not_in_environment")

    def count_step(self, skill: SkillDefinition) -> TeachStep:
        step = skill.count_step(self.environment_id)
        if step is None:
            raise InvalidTransition("no_count_step")
        return step

    def _done(self) -> set[int]:
        return {n for n, _ in self.steps_done}

    def _advance(self, skill: SkillDefinition) -> None:
        later = [s.step_no for s in self.steps(skill) if s.step_no > self.teach_step_no]
        if later:
            self.teach_step_no = later[0]

    def _require_state(self, *states: State) -> None:
        if self.state not in states:
            raise InvalidTransition("state_" + self.state)

    # Teaching ---------------------------------------------------------------------------------------------------

    def step_done(self, skill: SkillDefinition, step_no: int, r: ClockReading) -> None:
        self._require_state("teaching")
        step = self.step_on_screen(skill)
        if step_no != self.teach_step_no or step.kind != "instruction":
            raise InvalidValue("not_the_instruction_on_screen", fields=("/step_no",))
        self.steps_done.append((step_no, r.utc))
        self._advance(skill)

    def start_count(self, skill: SkillDefinition, r: ClockReading, client_time: datetime) -> None:
        self._require_state("teaching")
        if self.step_on_screen(skill).kind != "count":
            raise InvalidValue("step_on_screen_is_not_a_count", fields=("/action",))
        if skill.count_check is None:
            raise InvalidTransition("skill_has_no_count_check")
        self.state = "counting"
        self.attempt = self._new_attempt(1, skill, r, client_time)

    def _new_attempt(
        self, number: int, skill: SkillDefinition, r: ClockReading, client_time: datetime
    ) -> Attempt:
        seconds = self.count_step(skill).count_seconds
        if seconds is None:
            raise InvalidTransition("count_step_without_seconds")
        window = CountWindow(number, r.utc, r.elapsed_ns, r.generation, seconds, client_time)
        return Attempt(window=window, beats={}, status="open", flags=set())

    # Counting ---------------------------------------------------------------------------------------------------

    def accept_beat(
        self,
        sequence: int,
        body_sha256: str,
        client_time: datetime,
        r: ClockReading,
        *,
        max_beats_per_second: int,
        clock_tolerance_ms: int,
    ) -> Beat | None:
        """3.3 acceptance order after idempotency. Returns None for a resend of an accepted beat (no change)."""
        attempt = self.attempt
        if self.state != "counting" or attempt is None:
            raise InvalidTransition("not_counting")
        window = attempt.window
        if client_time < window.opened_client_time:
            raise BeatRefused("beat_from_earlier_attempt", recorded=False)
        existing = attempt.beats.get(sequence)
        if existing is not None:
            if existing.body_sha256 == body_sha256:
                return None
            attempt.flags.add(SEQUENCE_CONFLICT)
            raise BeatRefused(SEQUENCE_CONFLICT, recorded=True)
        if r.generation != window.generation:
            attempt.flags.add(HUB_RESTART)
            raise BeatRefused(HUB_RESTART, recorded=True)
        elapsed = window.elapsed_ns(r)
        if not 0 <= elapsed < window.window_ns():
            raise BeatRefused("outside_count_window", recorded=False)
        if len(attempt.beats) >= window.count_seconds * max_beats_per_second:
            raise BeatRefused("beat_capacity", recorded=False)
        if window.diverged(r, clock_tolerance_ms):
            attempt.flags.add(CLOCK_DISCONTINUITY)
        beat = Beat(
            sequence, elapsed, window.started_utc + timedelta(microseconds=elapsed // 1000), body_sha256
        )
        attempt.beats[sequence] = beat
        attempt.new_beats.append(beat)
        return beat

    def observe_window_end(self, skill: SkillDefinition, r: ClockReading) -> bool:
        """Records the count step done at the window end the first time a command observes the end."""
        attempt = self.attempt
        if self.state != "counting" or attempt is None or not attempt.window.ended(r):
            return False
        count_no = self.count_step(skill).step_no
        if count_no not in self._done():
            self.steps_done.append((count_no, attempt.window.ends_utc()))
            self._advance(skill)
            return True
        return False

    def answer(self, skill: SkillDefinition, choice: str, r: ClockReading) -> TeachStep:
        self._require_state("teaching", "counting")
        if self.state == "counting":
            self.observe_window_end(skill, r)
            if self.attempt is None or not self.attempt.window.ended(r):
                raise InvalidTransition("count_not_ended")
        step = self.step_on_screen(skill)
        if step.kind != "question" or step.step_no in self._done():
            raise InvalidValue("step_on_screen_is_not_a_question", fields=("/action",))
        if choice not in {c.answer for c in step.choices}:
            raise InvalidValue("answer_not_a_choice", fields=("/answer",))
        self.helper_answer = choice
        self.steps_done.append((step.step_no, r.utc))
        self._advance(skill)
        return step

    def prepare_finish(self, skill: SkillDefinition, r: ClockReading, *, max_gap_seconds: int) -> Fraction:
        """Guards of finish; returns the exact count per minute of a complete capture (3.3, I30-15)."""
        self._require_state("counting")
        attempt = self.attempt
        if attempt is None or not attempt.window.ended(r):
            raise InvalidTransition("count_not_ended")
        self.observe_window_end(skill, r)
        count_no = self.count_step(skill).step_no
        pending = [s for s in self.steps(skill) if s.step_no > count_no and s.step_no not in self._done()]
        if pending:
            raise InvalidTransition("steps_after_count_pending")
        reasons = completeness(
            attempt.beats, attempt.window, frozenset(attempt.flags), max_gap_seconds=max_gap_seconds
        )
        if reasons:
            raise IncompleteCapture(reasons)
        return count_per_minute(len(attempt.beats), attempt.window.count_seconds)

    def apply_finish(
        self,
        *,
        result: str,
        difference: Fraction | None,
        count: Fraction,
        camera: CameraResult,
        result_text: str,
        observation_id: str,
        r: ClockReading,
    ) -> None:
        """agree and no_reference: checked. disagree: retry; the count is written all the same (Q8, OD1), but the
        wire link stays null under the current schema (X16) and the plan step is never completed (I30-03)."""
        attempt = self.attempt
        if attempt is None or result not in ("agree", "disagree", "no_reference"):
            raise InvalidTransition("finish_without_attempt")
        attempt.status = "finished"
        attempt.count_exact, attempt.result, attempt.difference_exact = count, result, difference
        attempt.observation_id, attempt.finished_at = observation_id, r.utc
        self.camera, self.result, self.difference_exact = camera, result, difference
        self.count_exact, self.result_text = count, result_text
        if result == "disagree":
            self.state, self.observation_id = "retry", None
        else:
            self.state, self.observation_id = "checked", observation_id

    def mark_incomplete(self, reasons: frozenset[str]) -> None:
        """A capture that failed CR1 to CR4 (3.3): the attempt keeps its beats as incomplete with its reasons and
        never yields a rate; the check leaves counting for retry, which offers a new count or cancel, never finish."""
        self._require_state("counting")
        attempt = self.attempt
        if attempt is None or not reasons:
            raise InvalidTransition("no_incomplete_attempt")
        attempt.status = "incomplete"
        attempt.flags |= reasons
        self.state = "retry"

    # Retry and cancel -------------------------------------------------------------------------------------------

    def retry(
        self, skill: SkillDefinition, r: ClockReading, client_time: datetime, *, max_attempts: int
    ) -> None:
        self._require_state("retry")
        if self.attempt is None or self.attempt.window.attempt_no >= max_attempts:
            raise InvalidTransition("max_attempts")
        count_no = self.count_step(skill).step_no
        self.attempt = self._new_attempt(self.attempt.window.attempt_no + 1, skill, r, client_time)
        self.state = "counting"
        self.steps_done = [(n, at) for n, at in self.steps_done if n < count_no]
        self.teach_step_no = count_no
        self.previous_answer_id = self.answer_id or self.previous_answer_id
        self.helper_answer = self.answer_id = None
        self._clear_result()

    def cancel(self) -> None:
        if self.state in TERMINAL:
            raise InvalidTransition("state_" + self.state)
        if self.attempt is not None and self.attempt.status == "open":
            self.attempt.status = "abandoned"
        self.state = "cancelled"
        self._clear_result()

    def _clear_result(self) -> None:
        self.camera = None
        self.result = self.result_text = self.observation_id = None
        self.difference_exact = self.count_exact = None

    # Projection helpers -----------------------------------------------------------------------------------------

    def allowed_actions(self, skill: SkillDefinition, r: ClockReading) -> list[str]:
        if self.state in TERMINAL:
            return []
        if self.state == "retry":
            return ["retry", "cancel"]
        if self.state == "teaching":
            kind = self.step_on_screen(skill).kind
            first = {"instruction": "step_done", "count": "start_count", "question": "answer"}[kind]
            return [first, "cancel"]
        attempt = self.attempt
        if attempt is None or not attempt.window.ended(r):
            return ["cancel"]
        count_no = self.count_step(skill).step_no
        done = self._done() | {count_no}
        pending = [s for s in self.steps(skill) if s.step_no > count_no and s.step_no not in done]
        return ["answer", "cancel"] if pending else ["finish", "cancel"]

    def visible_beats(self) -> list[Beat]:
        if self.state == "cancelled" or self.attempt is None:
            return []
        return [self.attempt.beats[k] for k in sorted(self.attempt.beats)]
