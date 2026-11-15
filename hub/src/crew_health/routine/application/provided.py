from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal

from crew_health.kernel.uow import Actor, SessionContext, UnitOfWork
from crew_health.routine.application.audit import audit
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.engine import RoutineEngine
from crew_health.routine.application.ports import (
    AddedQuestion,
    DrillOccurrence,
    OccurrenceRef,
    TodayRoutinePart,
)
from crew_health.routine.domain.errors import RecheckRejected, Unauthorized
from crew_health.routine.domain.occurrence import Occurrence, Outcome, complete, display_state
from crew_health.routine.domain.vocabulary import FINAL_STATES
from crew_health.routine.domain.windows import trigger_window


def ref(occ: Occurrence) -> OccurrenceRef:
    return OccurrenceRef(occ.occurrence_id, occ.item_id, occ.state, occ.due_from, occ.due_until)


class RoutineToday:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def due_today(self, person_id: str, at: datetime) -> tuple[OccurrenceRef, ...]:
        with self.deps.snapshots.snapshot() as view:
            result = []
            for occ in self.engine.today_occurrences(view, person_id, at):
                release = self.engine.release_for(view, occ.config_release_id)
                until, held = self.engine.hold(view, occ, release, at)
                state, _ = display_state(occ, now=at, today=self.engine.day(at)[0], held_kind=held)
                if state == "due":
                    result.append(OccurrenceRef(occ.occurrence_id, occ.item_id, state, occ.due_from, until))
            return tuple(result)

    def today_summary(self, session: SessionContext, at: datetime) -> TodayRoutinePart:
        if session.person_id is None:
            raise Unauthorized("person_required")
        with self.deps.snapshots.snapshot() as view:
            states: list[tuple[str, str]] = []
            today = self.engine.day(at)[0]
            for occ in self.engine.today_occurrences(view, session.person_id, at):
                release = self.engine.release_for(view, occ.config_release_id)
                _, held = self.engine.hold(view, occ, release, at)
                state, _ = display_state(occ, now=at, today=today, held_kind=held)
                states.append((state, occ.occurrence_id))
            due, later = sum(s == "due" for s, _ in states), sum(s == "later" for s, _ in states)
            return TodayRoutinePart(
                due,
                later,
                sum(s == "done" for s, _ in states),
                due + later,
                tuple(i for s, i in states if s in ("due", "later")),
            )


class DailyCheckQuestions:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def added_questions(self, person_id: str, mission_day: int, at: datetime) -> tuple[AddedQuestion, ...]:
        with self.deps.snapshots.snapshot() as view:
            result: list[AddedQuestion] = []
            for instance in self.engine.open_instances(view, person_id):
                carrier = self.deps.store.instance_carrier(view, instance.instance_id)
                if (
                    carrier is None
                    or carrier[0] != mission_day
                    or not instance.due_from <= at < instance.due_until
                ):
                    continue
                override = self.deps.store.get_override(view, instance.override_id)
                cause: Literal["warning", "overlay"] = (
                    "overlay" if override is not None and override.source_kind == "overlay" else "warning"
                )
                repeat = (
                    None
                    if override is None or instance.slot == "initial"
                    else int((instance.due_from - override.source_at).total_seconds() // 60)
                )
                result.extend(
                    AddedQuestion(q, cause, repeat)
                    for q in instance.question_ids
                    if q not in instance.answers
                )
            ask = self.deps.store.get_barrier_ask(view, person_id, mission_day)
            if ask is not None and ask[1] == "pending":
                result.append(AddedQuestion(self.engine.release(view).misses.question_id, "miss", None))
            return tuple(result)


class RoutineCompletion:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def drill_occurrence(self, occurrence_id: str) -> DrillOccurrence | None:
        with self.deps.snapshots.snapshot() as view:
            occ = self.deps.store.get(view, occurrence_id)
            if occ is None or occ.state == "not_due":
                return None
            item = self.engine.release_for(view, occ.config_release_id).item(occ.item_id)
            if item is None:
                return None
            return DrillOccurrence(occ.occurrence_id, occ.person_id, occ.item_id, item.skill_ids, occ.state)

    def complete(self, occurrence_id: str, completed_at: datetime, actor: Actor) -> bool:
        if actor.kind == "assistant":
            raise Unauthorized("human_required")
        with self.deps.uow.begin(actor=actor, correlation_id=self.deps.ids.new("cor")) as uow:
            occ = self.deps.store.get(uow, occurrence_id)
            if occ is None or occ.state in FINAL_STATES or occ.state == "not_due":
                return False
            until, _ = self.engine.hold(
                uow, occ, self.engine.release_for(uow, occ.config_release_id), uow.now
            )
            occ = replace(occ, due_until=until)
            result = complete(
                occ,
                now=uow.now,
                completed_at=completed_at,
                outcome=Outcome((), (), None),
                actor_kind=actor.kind,
                actor_id=actor.id,
                day_start=self.engine.bounds(occ.mission_day, occ.clock_version)[0],
                guidance_release_id=occ.config_release_id,
            )
            self.engine.change(uow, occ, result)
            return True


class AssistantRechecks:
    def __init__(self, deps: RoutineDeps, engine: RoutineEngine) -> None:
        self.deps, self.engine = deps, engine

    def reject(
        self,
        uow: UnitOfWork,
        code: Literal["item_unknown", "person_mismatch", "not_today", "already_added", "capacity"],
    ) -> None:
        audit(self.deps, uow, "assistant_rejected")
        raise RecheckRejected(code)

    def add_recheck(
        self, uow: UnitOfWork, *, person_id: str, item_id: str, insight_id: str, due_from: datetime
    ) -> OccurrenceRef:
        release = self.engine.release(uow)
        if release.item(item_id) is None:
            self.reject(uow, "item_unknown")
        if self.deps.roster is None or person_id not in self.deps.roster.person_ids():
            self.reject(uow, "person_mismatch")
        today, version = self.engine.day(uow.now)
        start, finish = self.engine.bounds(today, version)
        if not start <= due_from < finish:
            self.reject(uow, "not_today")
        if self.deps.store.get_assistant_by_insight(uow, insight_id) is not None:
            self.reject(uow, "already_added")
        if (
            self.deps.store.count_assistant_for_day(uow, person_id, today)
            >= self.deps.settings.max_assistant_rechecks_per_person_day
        ):
            self.reject(uow, "capacity")
        window = trigger_window(due_from, release.policy(item_id), self.engine.bounds(today, version)[1])
        if window is None or uow.now >= window.due_until:
            self.reject(uow, "not_today")
            raise RecheckRejected("not_today")
        occ = self.engine.make_occurrence(
            uow,
            person_id,
            item_id,
            window,
            "assistant:" + insight_id,
            "assistant",
            self.engine.mission(uow),
            release,
            insight_id=insight_id,
            started_by="assistant",
        )
        if not self.engine.insert(uow, occ):
            self.reject(uow, "already_added")
        return ref(occ)
