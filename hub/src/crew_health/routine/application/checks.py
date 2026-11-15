from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, cast

from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.application.ports import DailyCheckSubmitted, ReactionTestRecorded
from crew_health.routine.domain.admission import admissible
from crew_health.routine.domain.occurrence import Outcome, complete
from crew_health.routine.domain.vocabulary import OPEN_STATES, OriginMode

if TYPE_CHECKING:
    from crew_health.routine.application.engine import RoutineEngine
    from crew_health.routine.domain.occurrence import Occurrence


class CheckNotifications:
    def __init__(self, engine: RoutineEngine) -> None:
        self.engine, self.deps = engine, engine.deps

    def submitted(self, uow: UnitOfWork, event: DailyCheckSubmitted) -> None:
        if event.submitted_at > uow.now:
            return
        mission = self.engine.mission(uow)
        if (
            event.origin_mode not in ("LIVE", "REPLAY", "SIM")
            or not admissible(
                mission_mode=mission.mission_mode,
                input_origin=cast(OriginMode, event.origin_mode),
                group_audience=False,
                on_roster=None
                if self.deps.roster is None
                else event.person_id in self.deps.roster.person_ids(),
            ).ok
        ):
            return
        release = self.engine.release(uow)
        if self.engine.day(event.submitted_at)[0] != event.mission_day:
            return
        for occ in self.engine.today_occurrences(uow, event.person_id, event.submitted_at):
            if (
                occ.periodic
                and occ.mission_day == event.mission_day
                and occ.item_id == release.question_carrier_item_id()
            ):
                self.finish(uow, occ, event.submitted_at, event.person_id)
        ask = self.deps.store.get_barrier_ask(uow, event.person_id, event.mission_day)
        if ask is not None and ask[1] == "pending":
            self.deps.store.put_barrier_ask(
                uow, event.person_id, event.mission_day, ask[0], "shown_unanswered", None, event.submitted_at
            )

    def reaction(self, uow: UnitOfWork, event: ReactionTestRecorded) -> None:
        if event.state != "completed" or event.occurrence_id is None:
            return
        occ = self.deps.store.get(uow, event.occurrence_id)
        if occ is not None and occ.person_id == event.person_id:
            self.finish(uow, occ, uow.now, event.person_id)

    def finish(self, uow: UnitOfWork, occ: Occurrence, at: datetime, person: str) -> None:
        if occ.state not in OPEN_STATES:
            return
        until, _ = self.engine.hold(uow, occ, self.engine.release_for(uow, occ.config_release_id), uow.now)
        if occ.due_from is None or at < occ.due_from or (until is not None and at >= until) or at > uow.now:
            return
        occ = replace(occ, due_until=until)
        if self.deps.store.get(uow, occ.occurrence_id) is None and not self.engine.insert(uow, occ):
            return
        self.engine.change(
            uow,
            occ,
            complete(
                occ,
                now=uow.now,
                completed_at=at,
                outcome=Outcome((), (), None),
                actor_kind="person",
                actor_id=person,
                day_start=self.engine.bounds(occ.mission_day, occ.clock_version)[0],
                guidance_release_id=occ.config_release_id,
            ),
        )
