from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.clock import ClockCorrected, ClockRegressed
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReadSnapshot,
    SessionContext,
    UnitOfWork,
)
from crew_health.routine.application.audit import audit
from crew_health.routine.application.deps import RoutineDeps
from crew_health.routine.application.ports import AnswerAccepted, MissionFacts
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.domain.bindings import OverlayInput, TriggerInput
from crew_health.routine.domain.cadence import (
    CadenceCandidate,
    EffectiveCadence,
    effective_cadence,
    next_periodic_day,
    period_days,
)
from crew_health.routine.domain.errors import ConfigInvalid, Finding, MissionUnavailable
from crew_health.routine.domain.hold import Interval, held_kind_at, held_until
from crew_health.routine.domain.instances import QuestionInstance
from crew_health.routine.domain.misses import MissesSummary, MissFact, summarize
from crew_health.routine.domain.occurrence import (
    Occurrence,
    TransitionRecord,
    display_state,
    extend_for_hold,
    miss,
)
from crew_health.routine.domain.override import Override
from crew_health.routine.domain.release import ReplaceCadence, RoutineItem, RoutineRelease
from crew_health.routine.domain.vocabulary import FINAL_STATES, CauseKind, Overlay
from crew_health.routine.domain.windows import Window, policy_window


class RoutineEngine:
    def __init__(self, deps: RoutineDeps) -> None:
        self.deps = deps
        from crew_health.routine.application.inputs import RoutineInputs

        self.inputs = RoutineInputs(self)

    def release(self, view: UnitOfWork | ReadSnapshot) -> RoutineRelease:
        return self.deps.releases.release(view.release)

    def release_for(self, view: UnitOfWork | ReadSnapshot, release_id: str) -> RoutineRelease:
        if view.release.release_id == release_id:
            return self.release(view)
        release = self.deps.releases.pinned(release_id)
        if release is None:
            raise ConfigInvalid(
                (Finding("error", "/config_release_id", "Pinned routine release is unavailable"),)
            )
        return release

    def release_for_deadline(self, uow: UnitOfWork, release_id: str) -> RoutineRelease:
        """Holds and misses never fail on presentation: an unreadable pin falls back to the active release (its
        suppressed overlays decide holds) and marks routine degraded, so a deadline is still recorded."""
        try:
            return self.release_for(uow, release_id)
        except ConfigInvalid:
            self.deps.health.mark_degraded("routine", "CONFIG_INVALID", uow.now)
            return self.release(uow)

    def day(self, at: datetime) -> tuple[int, str]:
        version = self.deps.mission_clock.version_at(at).clock_version
        return self.deps.mission_clock.mission_day(at, version=version), version

    def bounds(self, day: int, version: str) -> tuple[datetime, datetime]:
        return self.deps.mission_clock.day_bounds(day, version=version)

    def mission(self, view: UnitOfWork | ReadSnapshot) -> MissionFacts:
        port = self.deps.mission
        facts = None if port is None else port.current(view)
        if facts is None:
            self.deps.health.mark_degraded("routine", "MISSION_UNAVAILABLE", view.now)
            raise MissionUnavailable("mission facts are absent")
        return facts

    def open_occurrences(self, view: UnitOfWork | ReadSnapshot, person_id: str) -> tuple[Occurrence, ...]:
        maximum = (
            self.deps.settings.max_open_per_person
            + self.deps.settings.max_assistant_rechecks_per_person_day * self.deps.settings.history_days
        )
        return bounded_pages(
            lambda size, offset: self.deps.store.open_for_person(view, person_id, size, offset=offset),
            page=self.deps.settings.page_limit,
            maximum=maximum,
        )

    def overrides_for(self, view: UnitOfWork | ReadSnapshot, person_id: str) -> tuple[Override, ...]:
        return bounded_pages(
            lambda size, offset: self.deps.store.active_overrides_for(view, person_id, size, offset=offset),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_active_overrides_per_person,
        )

    def all_overrides(self, view: UnitOfWork | ReadSnapshot) -> tuple[Override, ...]:
        return bounded_pages(
            lambda size, offset: self.deps.store.active_overrides(view, size, offset=offset),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_roster * self.deps.settings.max_active_overrides_per_person,
        )

    def open_instances(self, view: UnitOfWork | ReadSnapshot, person_id: str) -> tuple[QuestionInstance, ...]:
        maximum = (
            self.deps.settings.max_active_overrides_per_person
            * (self.deps.settings.max_slots_per_binding + 1)
            * self.deps.settings.history_days
        )
        return bounded_pages(
            lambda size, offset: self.deps.store.open_instances_for_person(
                view, person_id, size, offset=offset
            ),
            page=self.deps.settings.page_limit,
            maximum=maximum,
        )

    def effective(
        self,
        view: UnitOfWork | ReadSnapshot,
        person_id: str,
        item: RoutineItem,
        release: RoutineRelease,
        environment_id: str,
    ) -> EffectiveCadence:
        candidates = tuple(
            CadenceCandidate(o.override_id, o.new_cadence, o.started_at, o.basis, o.source_ids)
            for o in self.overrides_for(view, person_id)
            if o.item_id == item.item_id and isinstance(o.effect, ReplaceCadence)
        )
        return effective_cadence(
            item.cadence_for(environment_id),
            item.cadence_basis,
            item.source_ids,
            candidates,
            release.calendar,
        )

    def hold_rows(
        self, view: UnitOfWork | ReadSnapshot, occ: Occurrence, release: RoutineRelease
    ) -> tuple[OverlayInput, ...]:
        maximum = self.deps.settings.max_overlay_intervals + 1
        page = min(self.deps.settings.page_limit, maximum)
        excluded = (
            occ.cause_key.split(":")[1]
            if occ.cause_key.startswith("per_eva:") and occ.cause_key.endswith(":before")
            else None
        )
        result: list[OverlayInput] = []
        for offset in range(0, maximum, page):
            size = min(page, maximum - offset)
            rows = self.deps.store.overlays_for(
                view,
                occ.person_id,
                occ.due_from or view.now,
                size,
                offset=offset,
                started_only=True,
                kinds=release.suppressed_overlays,
                exclude_overlay_id=excluded,
            )
            result.extend(rows)
            if len(rows) < size:
                break
        return tuple(result)

    def hold(
        self, view: UnitOfWork | ReadSnapshot, occ: Occurrence, release: RoutineRelease, now: datetime
    ) -> tuple[datetime | None, Overlay | None]:
        if occ.due_from is None or occ.base_due_until is None:
            return occ.due_until, None
        rows = self.hold_rows(view, occ, release)
        if len(rows) > self.deps.settings.max_overlay_intervals:
            return None, release.suppressed_overlays[0] if release.suppressed_overlays else None
        intervals = tuple(
            Interval(o.started_at, o.ended_at, o.kind, o.overlay_id)
            for o in rows
            if o.kind in release.suppressed_overlays
            and o.started_at is not None
            and not (occ.cause_key == f"per_eva:{o.overlay_id}:before")
        )
        return held_until(occ.due_from, occ.base_due_until, intervals), held_kind_at(
            now, occ.due_from, intervals, release.suppressed_overlays
        )

    def schedule(
        self, uow: UnitOfWork, key: str, handler: str, at: datetime, payload: dict[str, str]
    ) -> None:
        generation = self.deps.store.deadline_generation(uow, key)
        generation = uow.schedule(
            ScheduledJob(key, handler, AbsoluteDeadline(at), payload, None), expected_generation=generation
        )
        self.deps.store.set_deadline_generation(uow, key, generation)

    def cancel(self, uow: UnitOfWork, key: str) -> None:
        generation = self.deps.store.deadline_generation(uow, key)
        if generation is not None:
            uow.cancel_schedule(key, expected_generation=generation)

    def reschedule(self, uow: UnitOfWork, occ: Occurrence) -> None:
        key = "routine.occ." + occ.occurrence_id
        if occ.state in FINAL_STATES:
            self.cancel(uow, key)
            return
        release = self.release_for_deadline(uow, occ.config_release_id)
        until, held = self.hold(uow, occ, release, uow.now)
        if (
            until is None
            and len(self.hold_rows(uow, occ, release)) > self.deps.settings.max_overlay_intervals
        ):
            audit(self.deps, uow, "overlay_capacity", "overlay")
        if until != occ.due_until:
            changed = extend_for_hold(occ, until)
            self.deps.store.update(uow, changed, expected_revision=occ.revision)
            self.deps.store.append_transition(
                uow,
                TransitionRecord(
                    changed.occurrence_id,
                    changed.revision,
                    occ.state,
                    changed.state,
                    uow.now,
                    "engine",
                    uow.actor.id,
                    "hold_recomputed",
                    None,
                ),
            )
            self.emit_occurrence(uow, changed)
            occ = changed
        if held is not None or until is None:
            self.cancel(uow, key)
        else:
            self.schedule(uow, key, "routine.occurrence", until, {"occurrence_id": occ.occurrence_id})

    def ensure_day_job(self, uow: UnitOfWork, person_id: str) -> None:
        day, version = self.day(uow.now)
        self.schedule(
            uow,
            "routine.day." + person_id,
            "routine.day",
            self.bounds(day + 1, version)[0],
            {"person_id": person_id},
        )

    def periodic(
        self,
        view: UnitOfWork | ReadSnapshot,
        person_id: str,
        day: int,
        release: RoutineRelease,
        mission: MissionFacts,
    ) -> tuple[Occurrence, ...]:
        if release.calendar.anchor != "mission_start":
            raise ConfigInvalid(
                (
                    Finding(
                        "error", "/calendar/anchor", "environment_start requires E23 environment start day"
                    ),
                )
            )
        _, version = self.day(view.now)
        start, end = self.bounds(day, version)
        result: list[Occurrence] = []
        for item in release.items:
            effective = self.effective(view, person_id, item, release, mission.active_environment_id)
            period = period_days(effective.cadence, release.calendar)
            if (
                period is None
                or self.deps.store.periodic_on_day(view, person_id, item.item_id, day) is not None
            ):
                continue
            last = self.deps.store.latest_periodic_final_day(view, person_id, item.item_id)
            if (
                next_periodic_day(
                    period=period, anchor_day=1, last_final_day=last, today=day, today_has_periodic=False
                )
                != day
            ):
                continue
            window = policy_window(release.policy(item.item_id), start, end)
            if window is not None and view.now < window.due_until:
                result.append(
                    self.make_occurrence(
                        view,
                        person_id,
                        item.item_id,
                        window,
                        "cadence",
                        "override" if effective.governing_override_id else "cadence",
                        mission,
                        release,
                        periodic=True,
                        override_id=effective.governing_override_id,
                    )
                )
        return tuple(result)

    def make_occurrence(
        self,
        view: UnitOfWork | ReadSnapshot,
        person_id: str,
        item_id: str,
        window: Window,
        cause_key: str,
        cause_kind: CauseKind,
        mission: MissionFacts,
        release: RoutineRelease,
        *,
        periodic: bool = False,
        override_id: str | None = None,
        binding_id: str | None = None,
        started_by: str | None = None,
        insight_id: str | None = None,
    ) -> Occurrence:
        day, version = self.day(window.due_from)
        occ = Occurrence(
            self.deps.ids.derived("ro", person_id, item_id, str(day), cause_key),
            1,
            person_id,
            item_id,
            mission.active_environment_id,
            day,
            cause_kind,
            cause_key,
            periodic,
            override_id,
            binding_id,
            started_by,
            insight_id,
            "later" if view.now < window.due_from else "due",
            window.due_from,
            window.due_until,
            window.due_until,
            window.kind,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            release.release_id,
            mission.mission_mode,
            version,
            view.now,
        )
        until, held = self.hold(view, occ, release, view.now)
        state, _ = display_state(occ, now=view.now, today=self.day(view.now)[0], held_kind=held)
        return replace(occ, state=state, due_until=until)

    def today_occurrences(
        self, view: UnitOfWork | ReadSnapshot, person_id: str, now: datetime
    ) -> tuple[Occurrence, ...]:
        day, _ = self.day(now)
        stored = bounded_pages(
            lambda size, offset: self.deps.store.for_person_days(
                view, person_id, day, day, size, offset=offset
            ),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_open_per_person * self.deps.settings.history_days,
        )
        open_ = self.open_occurrences(view, person_id)
        projected = self.periodic(view, person_id, day, self.release(view), self.mission(view))
        return tuple({o.occurrence_id: o for o in (*stored, *open_, *projected)}.values())

    def insert(self, uow: UnitOfWork, occ: Occurrence) -> bool:
        if self.deps.store.get(uow, occ.occurrence_id) is not None:
            return False
        if (
            occ.cause_kind != "assistant"
            and self.deps.store.count_open_nonassistant(uow, occ.person_id)
            >= self.deps.settings.max_open_per_person
        ):
            audit(self.deps, uow, "capacity")
            self.deps.health.mark_degraded("routine", "ROUTINE_CAPACITY", uow.now)
            return False
        if not self.deps.store.insert(uow, occ):
            return False
        self.deps.store.append_transition(
            uow,
            TransitionRecord(
                occ.occurrence_id, 1, None, occ.state, uow.now, uow.actor.kind, uow.actor.id, None, None
            ),
        )
        self.emit_occurrence(uow, occ)
        self.reschedule(uow, occ)
        return True

    def earlier_periodic(
        self, view: UnitOfWork | ReadSnapshot, person_id: str, item_id: str, day: int
    ) -> tuple[Occurrence, ...]:
        return tuple(
            o
            for o in self.open_occurrences(view, person_id)
            if o.item_id == item_id and o.periodic and o.mission_day < day
        )

    def materialize(self, uow: UnitOfWork, person_id: str, day: int) -> None:
        mission, release = self.mission(uow), self.release(uow)
        for occ in self.periodic(uow, person_id, day, release, mission):
            for older in self.earlier_periodic(uow, person_id, occ.item_id, day):
                self.change(
                    uow,
                    older,
                    miss(
                        older,
                        now=uow.now,
                        counted=False,
                        reason="superseded_by_next_slot",
                        actor_id=uow.actor.id,
                    ),
                )
            self.insert(uow, occ)
        self.inputs.each_day(uow, person_id, day)
        self.offer_barrier(uow, person_id)

    def change(
        self, uow: UnitOfWork, old: Occurrence, result: tuple[Occurrence, TransitionRecord]
    ) -> Occurrence:
        new, transition = result
        self.deps.store.update(uow, new, expected_revision=old.revision)
        self.deps.store.append_transition(uow, transition)
        self.emit_occurrence(uow, new)
        self.reschedule(uow, new)
        if new.state in FINAL_STATES:
            self.inputs.effect_complete(uow, new.occurrence_id)
            if new.state == "missed":
                self.offer_barrier(uow, new.person_id)
        return new

    def counted(self, uow: UnitOfWork, until: datetime) -> bool:
        return (uow.now - until) <= timedelta(
            milliseconds=self.deps.settings.miss_grace_ms
        ) and self.deps.store.clock_suspect_since(uow) is None

    def occurrence_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        occ = self.deps.store.get(uow, job.payload["occurrence_id"])
        if occ is None or occ.state in FINAL_STATES:
            return
        self.reschedule(uow, occ)
        occ = self.deps.store.get(uow, occ.occurrence_id)
        if occ is None or occ.due_until is None:
            return
        _, held = self.hold(uow, occ, self.release_for_deadline(uow, occ.config_release_id), uow.now)
        if held is not None or uow.now < occ.due_until:
            return
        self.change(
            uow,
            occ,
            miss(
                occ,
                now=uow.now,
                counted=self.counted(uow, occ.due_until),
                reason="deadline",
                actor_id=uow.actor.id,
            ),
        )

    def day_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        person = job.payload["person_id"]
        self.materialize(uow, person, self.day(uow.now)[0])
        self.ensure_day_job(uow, person)

    def miss_summary(
        self, view: UnitOfWork | ReadSnapshot, person_id: str, release: RoutineRelease, now: datetime
    ) -> MissesSummary:
        day = self.day(now)[0]
        facts = bounded_pages(
            lambda size, offset: self.deps.store.misses_since(
                view, person_id, max(1, day - self.deps.settings.history_days + 1), size, offset=offset
            ),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_open_per_person * self.deps.settings.history_days,
        )
        periods = {i.item_id: period_days(i.default_cadence, release.calendar) for i in release.items}
        return summarize(
            tuple(MissFact(o.item_id, o.mission_day, bool(o.miss_counted)) for o in facts),
            release.misses,
            periods,
            self.deps.store.latest_barrier_day(view, person_id),
        )

    def offer_barrier(self, uow: UnitOfWork, person_id: str) -> None:
        release = self.release(uow)
        summary = self.miss_summary(uow, person_id, release, uow.now)
        day = self.day(uow.now)[0]
        if summary.barrier_question_due and self.deps.store.get_barrier_ask(uow, person_id, day) is None:
            self.deps.store.put_barrier_ask(uow, person_id, day, summary.item_ids, "pending", None, uow.now)

    def emit_occurrence(self, uow: UnitOfWork, occ: Occurrence) -> None:
        from crew_health.routine.application.views import occurrence_view

        try:
            release = self.release_for(uow, occ.config_release_id)
            if release.words is None:
                raise ConfigInvalid(())
            session = SessionContext(
                "routine_projection", "crew", Actor("person", occ.person_id), occ.person_id, frozenset()
            )
            body = occurrence_view(self.deps, self, uow, occ, session, uow.now)
        except ConfigInvalid:
            self.deps.health.mark_degraded("routine", "CONFIG_INVALID", uow.now)
            return
        self.emit(
            uow,
            EventType.ROUTINE_OCCURRENCE_CHANGED,
            occ.occurrence_id,
            "routine.occurrence",
            EventScope("person", occ.person_id, occ.environment_id),
            occ.config_release_id,
            body,
        )

    def emit_override(self, uow: UnitOfWork, override: Override) -> None:
        from crew_health.routine.application.views import override_view

        try:
            if self.release_for(uow, override.config_release_id).words is None:
                raise ConfigInvalid(())
        except ConfigInvalid:
            self.deps.health.mark_degraded("routine", "CONFIG_INVALID", uow.now)
            return
        session = SessionContext("routine_projection", "station", uow.actor, override.person_id, frozenset())
        body = override_view(override, session)
        self.emit(
            uow,
            EventType.ROUTINE_OVERRIDE_CHANGED,
            override.override_id,
            "routine.override",
            EventScope("person" if override.audience == "person" else "crew", override.person_id, None),
            override.config_release_id,
            body,
        )

    def emit(
        self,
        uow: UnitOfWork,
        event_type: EventType,
        ref: str,
        rule: str,
        scope: EventScope,
        release_id: str,
        body: dict[str, object],
    ) -> None:
        uow.emit(
            OutboxEvent(
                event_type,
                uow.now,
                scope,
                release_id,
                uow.correlation_id,
                canonical_json(body),
                Audience(rule, ref, AudienceProjectionKey("routine", event_type.value, 1), "1"),
            )
        )

    def startup_sweep(self) -> None:
        if self.deps.roster is None:
            return
        for member in tuple(self.deps.roster.members())[: self.deps.settings.max_roster]:
            try:
                with self.deps.uow.begin(
                    actor=Actor("engine", "routine"), correlation_id=self.deps.ids.new("cor")
                ) as uow:
                    self.materialize(uow, member.person_id, self.day(uow.now)[0])
                    self.ensure_day_job(uow, member.person_id)
                    for occ in self.open_occurrences(uow, member.person_id):
                        self.reschedule(uow, occ)
            except Exception as error:
                logging.getLogger("crew_health.routine").error(
                    "routine startup sweep failed: %s", type(error).__name__
                )
                self.deps.health.mark_degraded(
                    "routine",
                    "MISSION_UNAVAILABLE"
                    if isinstance(error, MissionUnavailable)
                    else "EVALUATION_UNAVAILABLE",
                    self.deps.clock.now(),
                )

    def apply_input(self, uow: UnitOfWork, inp: TriggerInput) -> None:
        self.inputs.apply(uow, inp)

    def on_answer(self, uow: UnitOfWork, event: AnswerAccepted) -> None:
        self.inputs.on_answer(uow, event)

    def override_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.inputs.override_job(uow, job)

    def instance_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.inputs.instance_job(uow, job)

    def on_clock_regressed(self, uow: UnitOfWork, event: ClockRegressed) -> None:
        previous = self.deps.store.clock_suspect_since(uow)
        self.deps.store.set_clock_suspect(
            uow, max(previous, event.observed_at) if previous else event.observed_at
        )

    def on_clock_corrected(self, uow: UnitOfWork, event: ClockCorrected) -> None:
        suspect = self.deps.store.clock_suspect_since(uow)
        if suspect is not None and event.detected_at >= suspect:
            self.deps.store.set_clock_suspect(uow, None)

    def environment_activated(self, uow: UnitOfWork) -> None:
        if self.deps.roster is not None:
            for member in tuple(self.deps.roster.crew(uow.now))[: self.deps.settings.max_roster]:
                self.materialize(uow, member.person_id, self.day(uow.now)[0])

    def release_activated(self, uow: UnitOfWork) -> None:
        self.release(uow)
