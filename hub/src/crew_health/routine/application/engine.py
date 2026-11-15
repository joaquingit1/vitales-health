from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.clock import TimeInput
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import StorageUnavailable
from crew_health.kernel.scheduler import AbsoluteDeadline, ElapsedDeadline, ScheduledJob
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
from crew_health.routine.application.ports import AnswerFacts, MissionFacts
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.application.timers import TimerKind, deadline_kind
from crew_health.routine.domain.bindings import OverlayInput, TriggerInput
from crew_health.routine.domain.cadence import (
    CadenceCandidate,
    EffectiveCadence,
    effective_cadence,
    next_periodic_day,
    period_days,
)
from crew_health.routine.domain.errors import (
    ConfigInvalid,
    EnvironmentAnchorUnavailable,
    Finding,
    MissionClockUnset,
    MissionUnavailable,
)
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
        from crew_health.routine.application.activations import CadenceActivations

        self.activations = CadenceActivations(self)

    def release(self, view: UnitOfWork | ReadSnapshot) -> RoutineRelease:
        release = self.deps.releases.release(view.release)
        if release.words is None:
            self.deps.health.mark_degraded("routine", "CONFIG_INVALID", view.now)
        return release

    def release_for(self, view: UnitOfWork | ReadSnapshot, release_id: str) -> RoutineRelease:
        if view.release.release_id == release_id:
            return self.release(view)
        release = self.deps.releases.pinned(release_id)
        if release is None:
            raise ConfigInvalid(
                (Finding("error", "/config_release_id", "Pinned routine release is unavailable"),)
            )
        return release

    def pinned_for_deadline(self, uow: UnitOfWork, release_id: str) -> RoutineRelease | None:
        """AGENTS 6 and PLAN row 28: open work keeps its pinned release. When that pin cannot be read (no
        ConfigReleaseReader, or an evicted cache after a restart) the caller keeps the stored deadline and routine is
        marked degraded; the active release never recomputes a hold under open work."""
        try:
            return self.release_for(uow, release_id)
        except ConfigInvalid:
            self.deps.health.mark_degraded("routine", "CONFIG_INVALID", uow.now)
            return None

    def day(self, at: datetime) -> tuple[int, str]:
        version = self.deps.mission_clock.version_at(at).clock_version
        return self.deps.mission_clock.mission_day(at, version=version), version

    def bounds(self, day: int, version: str) -> tuple[datetime, datetime]:
        return self.deps.mission_clock.day_bounds(day, version=version)

    def mission(self, view: UnitOfWork | ReadSnapshot) -> MissionFacts:
        port = self.deps.mission
        facts = None if port is None else port.current(view)
        if facts is None:
            # A missing capability (E23 not installed, or no mission yet), not a fault: the caller refuses with
            # CAPABILITY_UNAVAILABLE and health stays ok (SYSTEM 5.10).
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
        candidates += tuple(
            CadenceCandidate(a.activation_id, a.cadence, a.started_at, item.cadence_basis, item.source_ids)
            for a in self.activations.active(view, person_id)
            if a.item_id == item.item_id
            and (a.ends_at is None or view.now < a.ends_at or not self.trusted(view))
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
        self,
        uow: UnitOfWork,
        key: str,
        handler: str,
        at: datetime,
        payload: dict[str, str],
        *,
        kind: TimerKind = "absolute",
    ) -> None:
        """Every routine timer goes through here; `kind` is its PLAN row 34 class (application/timers.py)."""
        deadline: AbsoluteDeadline | ElapsedDeadline = AbsoluteDeadline(at)
        # While the shared clock is frozen the kernel cannot normalize an anchor (clock.py normalize) and nothing
        # recomputes the job after the sweep, so an elapsed timer set then would never fall due. Its handler
        # rechecks the trust anyway (no miss counts and no tightening ends on unknown time), so it keeps its
        # instant until the clock is trusted again.
        if kind == "elapsed" and self.trusted(uow):
            anchor = self.hub_time(uow)
            if anchor is not None:
                deadline = ElapsedDeadline(anchor, max(at - uow.now, timedelta(0)))
        generation = self.deps.store.deadline_generation(uow, key)
        generation = uow.schedule(
            ScheduledJob(key, handler, deadline, payload, None), expected_generation=generation
        )
        self.deps.store.set_deadline_generation(uow, key, generation)

    def hub_time(self, uow: UnitOfWork) -> TimeInput | None:
        if self.deps.hub_clock is None:
            return None
        facts = self.deps.mission.current(uow) if self.deps.mission is not None else None
        return self.deps.hub_clock.hub_time(uow, "SIM" if facts is None else facts.mission_mode)

    def lateness(self, uow: UnitOfWork, job: ScheduledJob) -> timedelta | None:
        """How late an elapsed timer runs, measured through the shared clock corrections; an unknown hub time is
        unbounded lateness, so it never counts a miss. None for an absolute timer (`counted` uses the wall clock)."""
        if not isinstance(job.deadline, ElapsedDeadline):
            return None
        now = self.hub_time(uow)
        elapsed = None if now is None else self.deps.corrections.elapsed(uow, job.deadline.anchor, now)
        return timedelta.max if elapsed is None else elapsed - job.deadline.duration

    def cancel(self, uow: UnitOfWork, key: str) -> None:
        generation = self.deps.store.deadline_generation(uow, key)
        if generation is not None:
            uow.cancel_schedule(key, expected_generation=generation)

    def reschedule(self, uow: UnitOfWork, occ: Occurrence) -> None:
        key = "routine.occ." + occ.occurrence_id
        if occ.state in FINAL_STATES:
            self.cancel(uow, key)
            return
        release = self.pinned_for_deadline(uow, occ.config_release_id)
        if release is None:
            # M1: the stored deadline stands. A held occurrence (no deadline) stays held until its pin is readable.
            if occ.due_until is not None:
                self.schedule_deadline(uow, occ)
            return
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
            self.schedule_deadline(uow, occ)

    def schedule_deadline(self, uow: UnitOfWork, occ: Occurrence) -> None:
        if occ.due_until is None:
            return
        self.schedule(
            uow,
            "routine.occ." + occ.occurrence_id,
            "routine.occurrence",
            occ.due_until,
            {"occurrence_id": occ.occurrence_id},
            kind=deadline_kind(occ),
        )

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
        anchor = self.anchor_day(view, release, mission.active_environment_id)
        if anchor is None:
            raise EnvironmentAnchorUnavailable()
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
                    period=period, anchor_day=anchor, last_final_day=last, today=day, today_has_periodic=False
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
                        "override"
                        if effective.governing_override_id
                        and not effective.governing_override_id.startswith("rca_")
                        else "cadence",
                        mission,
                        release,
                        periodic=True,
                        override_id=effective.governing_override_id
                        if effective.governing_override_id
                        and not effective.governing_override_id.startswith("rca_")
                        else None,
                    )
                )
        return tuple(result)

    def anchor_day(
        self, view: UnitOfWork | ReadSnapshot, release: RoutineRelease, environment_id: str
    ) -> int | None:
        if release.calendar.anchor == "mission_start":
            return 1
        day = self.deps.store.environment_day(view, environment_id)
        if day is None:
            self.deps.health.mark_degraded("routine", "ENVIRONMENT_ANCHOR_UNAVAILABLE", view.now)
        return day

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
        try:
            projected = self.periodic(view, person_id, day, self.release(view), self.mission(view))
        except EnvironmentAnchorUnavailable:
            projected = ()
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
        self.deps.store.record_occurrence_mission(uow, occ.occurrence_id, self.mission(uow).mission_id)
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

    def close_predecessor(self, uow: UnitOfWork, older: Occurrence) -> None:
        """B1: the successor rule closes, uncounted, only work its own deadline has not decided: an occurrence still
        inside its window, or one a hold extended (design 3.7 and test 109: sleep over an all-day item carries it
        past midnight). An open predecessor whose absolute deadline has passed is a deadline miss, counted by the
        same rule as its own deadline job."""
        if older.due_until is not None and older.due_until <= uow.now and deadline_kind(older) == "absolute":
            self.deadline_miss(uow, older)
            return
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

    def close_due(self, uow: UnitOfWork, person_id: str) -> None:
        """B1: a day boundary is also the deadline of every all-day occurrence, and the scheduler runs the jobs due
        at one instant in key order (routine.day sorts before routine.occ). So the day job first decides every
        passed absolute deadline exactly as the occurrence's own job would, whatever order the jobs run in. Needs
        no mission, roster or release: misses are recorded with every optional provider absent (AGENTS 1 rule 3)."""
        for occ in self.open_occurrences(uow, person_id):
            if occ.due_until is not None and occ.due_until <= uow.now and deadline_kind(occ) == "absolute":
                self.deadline_miss(uow, occ)

    def materialize(self, uow: UnitOfWork, person_id: str, day: int) -> None:
        self.close_due(uow, person_id)
        mission, release = self.mission(uow), self.release(uow)
        try:
            periodic = self.periodic(uow, person_id, day, release, mission)
        except EnvironmentAnchorUnavailable:
            periodic = ()
        for occ in periodic:
            for older in self.earlier_periodic(uow, person_id, occ.item_id, day):
                self.close_predecessor(uow, older)
            self.insert(uow, occ)
        self.inputs.each_day(uow, person_id, day)
        self.offer_barrier(uow, person_id)

    def change(
        self,
        uow: UnitOfWork,
        old: Occurrence,
        result: tuple[Occurrence, TransitionRecord],
        *,
        deadline: bool = False,
    ) -> Occurrence:
        new, transition = result
        self.deps.store.update(uow, new, expected_revision=old.revision)
        self.deps.store.append_transition(uow, transition)
        self.emit_occurrence(uow, new, cached_only=deadline)
        self.reschedule(uow, new)
        if new.state in FINAL_STATES:
            self.inputs.effect_complete(uow, new.occurrence_id)
            if new.state == "missed":
                self.barrier_after_miss(uow, new.person_id, deadline=deadline)
        return new

    def barrier_after_miss(self, uow: UnitOfWork, person_id: str, *, deadline: bool) -> None:
        """m1: the deadline path opens no configuration document (AGENTS 1 rule 3), so it offers the barrier
        question from the cached active release only. A cold cache is no longer silent: routine.barrier turns
        degraded and the person's next day job offers the question (`day_barrier`)."""
        release = self.deps.releases.cached(uow.release.release_id)
        if deadline and release is None:
            self.deps.health.mark_degraded("routine.barrier", "CONFIG_INVALID", uow.now)
            return
        try:
            self.offer_barrier(uow, person_id, release=release)
        except ConfigInvalid:
            self.deps.health.mark_degraded("routine.barrier", "CONFIG_INVALID", uow.now)

    def day_barrier(self, uow: UnitOfWork, person_id: str) -> None:
        """m1 retry: the day job loads the active release and offers a barrier question a cold deadline deferred."""
        self.offer_barrier(uow, person_id)
        self.deps.health.mark_ok("routine.barrier")

    def trusted(self, view: UnitOfWork | ReadSnapshot) -> bool:
        """The shared clock trust (SYSTEM 5.2, PersistentClocks.clearing_allowed): False after a regression until
        the correction and its sweep, and at first boot until the hub's first sweep. Routine keeps none of its own."""
        if self.deps.clock_trust is None:
            return False
        return self.deps.clock_trust.clearing_allowed(view)

    def counted(self, uow: UnitOfWork, until: datetime, *, late: timedelta | None = None) -> bool:
        """A miss counts only when it is decided inside routine.miss_grace_ms of its deadline on a trusted clock.
        `late` is an elapsed timer's lateness through the clock corrections (`lateness`); otherwise the wall clock."""
        lateness = uow.now - until if late is None else late
        return lateness <= timedelta(milliseconds=self.deps.settings.miss_grace_ms) and self.trusted(uow)

    def deadline_miss(self, uow: UnitOfWork, occ: Occurrence, *, late: timedelta | None = None) -> None:
        """The one place a deadline miss is decided, by the occurrence's own job or by the day job (B1)."""
        if occ.due_until is None:
            return
        self.change(
            uow,
            occ,
            miss(
                occ,
                now=uow.now,
                counted=self.counted(uow, occ.due_until, late=late),
                reason="deadline",
                actor_id=uow.actor.id,
            ),
            deadline=True,
        )

    def occurrence_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        occ = self.deps.store.get(uow, job.payload["occurrence_id"])
        if occ is None or occ.state in FINAL_STATES:
            return
        # Overlay handling persists each hold and wake deadline in its own transaction.
        # The durable deadline is sufficient even after a cold restart with invalid config.
        if occ.due_until is None:
            return
        late = self.lateness(uow, job)
        if late is None and uow.now < occ.due_until:
            self.schedule_deadline(uow, occ)
            return
        self.deadline_miss(uow, occ, late=late)

    def day_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        person = job.payload["person_id"]
        try:
            with uow.savepoint("routine_day"):
                self.close_due(uow, person)
        except StorageUnavailable:
            raise
        except Exception as error:  # noqa: BLE001  # contained to this person's day job, reported below
            self.day_failed(uow, "close_due", error)
        try:
            with uow.savepoint("routine_materialize"):
                self.materialize(uow, person, self.day(uow.now)[0])
                self.day_barrier(uow, person)
        except StorageUnavailable:
            raise
        except Exception as error:  # noqa: BLE001  # contained to this person's day job, reported below
            self.day_failed(uow, "materialize", error)
        self.ensure_day_job(uow, person)

    def day_failed(self, uow: UnitOfWork, step: str, error: Exception) -> None:
        """m2: one person's failing day job rolls back only its own savepoint and stays visible, so the deadlines
        of everyone else in the shared dispatch batch still commit. Storage errors are never contained. No mission
        facts or no mission clock yet is missing context, not a fault (SYSTEM 5.10): nothing to create, health ok."""
        if isinstance(error, MissionUnavailable | MissionClockUnset):
            logging.getLogger("crew_health.routine").info(
                "routine day job %s: %s", step, type(error).__name__
            )
            return
        code = "CONFIG_INVALID" if isinstance(error, ConfigInvalid) else "EVALUATION_UNAVAILABLE"
        logging.getLogger("crew_health.routine").error(
            "routine day job %s failed: %s", step, type(error).__name__
        )
        self.deps.health.mark_degraded("routine", code, uow.now)

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

    def offer_barrier(
        self, uow: UnitOfWork, person_id: str, *, release: RoutineRelease | None = None
    ) -> None:
        release = self.release(uow) if release is None else release
        summary = self.miss_summary(uow, person_id, release, uow.now)
        day = self.day(uow.now)[0]
        if summary.barrier_question_due and self.deps.store.get_barrier_ask(uow, person_id, day) is None:
            self.deps.store.put_barrier_ask(uow, person_id, day, summary.item_ids, "pending", None, uow.now)

    def emit_occurrence(self, uow: UnitOfWork, occ: Occurrence, *, cached_only: bool = False) -> None:
        from crew_health.routine.application.views import occurrence_view

        try:
            if cached_only and self.deps.releases.cached(occ.config_release_id) is None:
                raise ConfigInvalid(())
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
        for field in ("skip", "outcome"):
            value = body.get(field)
            if isinstance(value, dict):
                value["note"] = None
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
            release = self.deps.releases.cached(override.config_release_id)
            if release is None or release.words is None:
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
        """STATE_SWEEP: load the active release first (AGENTS 3 rule 3), whatever seams are absent, so the deadline
        path, which opens no configuration document, finds it cached; a cold cache there is a fault (m1)."""
        try:
            with self.deps.snapshots.snapshot() as view:
                self.release(view)
        except ConfigInvalid:
            self.deps.health.mark_degraded("routine", "CONFIG_INVALID", self.deps.clock.now())
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
            except (MissionUnavailable, MissionClockUnset) as error:
                # Missing context, not a fault: the next start after the mission is set materializes the day.
                logging.getLogger("crew_health.routine").info(
                    "routine startup sweep: %s", type(error).__name__
                )
            except Exception as error:
                logging.getLogger("crew_health.routine").error(
                    "routine startup sweep failed: %s", type(error).__name__
                )
                self.deps.health.mark_degraded("routine", "EVALUATION_UNAVAILABLE", self.deps.clock.now())

    def apply_input(self, uow: UnitOfWork, inp: TriggerInput) -> None:
        self.inputs.apply(uow, inp)

    def on_answer(self, uow: UnitOfWork, answer: AnswerFacts, mission_id: str) -> None:
        self.inputs.on_answer(uow, answer, mission_id)

    def override_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.inputs.override_job(uow, job)

    def instance_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        self.inputs.instance_job(uow, job)

    def environment_activated(self, uow: UnitOfWork) -> None:
        if self.deps.roster is not None:
            for member in tuple(self.deps.roster.crew(uow.now))[: self.deps.settings.max_roster]:
                self.materialize(uow, member.person_id, self.day(uow.now)[0])

    def release_activated(self, uow: UnitOfWork) -> None:
        self.release(uow)
