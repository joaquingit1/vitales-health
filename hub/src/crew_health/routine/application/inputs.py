from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.uow import UnitOfWork
from crew_health.routine.application.audit import audit
from crew_health.routine.application.ports import AnswerFacts, MissionFacts
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.domain.admission import admissible
from crew_health.routine.domain.audience import members
from crew_health.routine.domain.bindings import (
    AnswerInput,
    EnvironmentInput,
    Firing,
    MissionEventInput,
    ObservationInput,
    OverlayInput,
    TriggerInput,
    WarningClearedInput,
    WarningRaisedInput,
    is_assistant_source,
    match_bindings,
)
from crew_health.routine.domain.cadence import period_days
from crew_health.routine.domain.coalesce import absorbing
from crew_health.routine.domain.errors import ConfigInvalid, Finding, MissionUnavailable
from crew_health.routine.domain.instances import (
    QuestionInstance,
    apply_answer,
    expire,
    pick_instance,
    plan_slots,
)
from crew_health.routine.domain.occurrence import Outcome, complete, miss
from crew_health.routine.domain.override import (
    Override,
    clear_decision,
    days_end_at,
    end,
    reason_text,
    repoint,
    started_by_for,
)
from crew_health.routine.domain.release import (
    AddOccurrences,
    AddQuestions,
    Binding,
    ItemPolicy,
    ObservationOn,
    ReplaceCadence,
    RoutineRelease,
    UntilDays,
    UntilEffectComplete,
    UntilMissionEvent,
    UntilPersonStops,
)
from crew_health.routine.domain.rendering import cadence_text
from crew_health.routine.domain.vocabulary import FINAL_STATES, CauseKind, EndReason, MemberBasis
from crew_health.routine.domain.windows import (
    Window,
    after_walk_window,
    before_walk_window,
    slot_window,
    trigger_window,
)

if TYPE_CHECKING:
    from crew_health.routine.application.engine import RoutineEngine


class RoutineInputs:
    def __init__(self, engine: RoutineEngine) -> None:
        self.engine, self.deps = engine, engine.deps

    def apply(self, uow: UnitOfWork, inp: TriggerInput) -> None:
        try:
            mission = self.engine.mission(uow)
        except MissionUnavailable:
            audit(self.deps, uow, "mission_unknown")
            return
        if is_assistant_source(inp):
            audit(self.deps, uow, "assistant_input_ignored", "warning")
            return
        release = self.engine.release(uow)
        if isinstance(inp, EnvironmentInput):
            if not self.deps.store.record_environment_day(
                uow, inp.environment_id, inp.activated_at, self.engine.day(inp.activated_at)[0]
            ):
                return
        if isinstance(inp, ObservationInput) and inp.quality_status == "accepted":
            for binding in release.bindings:
                if (
                    isinstance(binding.on, ObservationOn)
                    and binding.on.code == inp.code
                    and binding.on.unit != inp.unit
                ):
                    audit(self.deps, uow, "binding_unit_mismatch", "observation")
        if isinstance(inp, WarningClearedInput):
            self.clear_warning(uow, inp)
            return
        if isinstance(inp, OverlayInput):
            if not self.deps.store.upsert_overlay(uow, inp):
                return
            self.overlay(uow, inp, mission, release)
        if isinstance(inp, MissionEventInput):
            self.end_on_event(uow, inp)
        for firing in match_bindings(release, inp, mission.active_environment_id):
            self.fire(uow, firing, inp, mission, release)
        if isinstance(inp, WarningRaisedInput) and self.deps.warnings is not None:
            self.deps.warnings.attach_routine_overrides(
                uow, inp.warning_id, tuple(o.override_id for o in self.from_warning(uow, inp.warning_id))
            )
        if isinstance(inp, EnvironmentInput):
            self.engine.environment_activated(uow)

    def walking(self, uow: UnitOfWork, roster: tuple[str, ...], at: datetime) -> frozenset[str]:
        return frozenset(
            p
            for p in roster
            if any(
                o.kind == "eva"
                and o.started_at is not None
                and o.started_at <= at
                and (o.ended_at is None or at < o.ended_at)
                for o in self.deps.store.overlays_for(uow, p, at, self.deps.settings.max_overlay_intervals)
            )
        )

    def audience(
        self, uow: UnitOfWork, firing: Firing, mission: MissionFacts
    ) -> tuple[tuple[str, MemberBasis], ...] | None:
        roster = (
            None
            if self.deps.roster is None
            else tuple(m.person_id for m in self.deps.roster.crew(firing.source_at))
        )
        if roster is not None and len(roster) > self.deps.settings.max_roster:
            audit(self.deps, uow, "roster_unavailable", firing.source_kind)
            return None
        if roster is None:
            audit(self.deps, uow, "roster_unavailable", firing.source_kind)
            return None
        origin = firing.origin_mode or mission.mission_mode
        admission = admissible(
            mission_mode=mission.mission_mode,
            input_origin=origin,
            group_audience=firing.binding.audience != "person",
            on_roster=None,
        )
        if not admission.ok:
            audit(self.deps, uow, admission.code or "origin_rejected", firing.source_kind)
            return None
        if roster is not None and any(p not in roster for p in firing.subject_ids):
            audit(self.deps, uow, "not_on_roster", firing.source_kind)
            return None
        walking = self.walking(uow, roster, firing.source_at)
        locations = self.deps.locations
        location_of = None if locations is None else lambda p: locations.location_of(p, firing.source_at)
        result = members(
            firing.binding.audience,
            subject_ids=firing.subject_ids,
            roster=roster,
            location_of=location_of,
            source_location=firing.location,
            walking=walking,
        )
        if result is None or (firing.binding.audience == "person" and not result):
            audit(self.deps, uow, "roster_unavailable", firing.source_kind)
            return None
        return result

    def fire(
        self,
        uow: UnitOfWork,
        firing: Firing,
        inp: TriggerInput,
        mission: MissionFacts,
        release: RoutineRelease,
    ) -> None:
        binding = firing.binding
        if binding.kind == "cadence_event" and not isinstance(
            binding.effect, AddOccurrences | ReplaceCadence
        ):
            audit(self.deps, uow, "unsupported_binding", firing.source_kind)
            return
        audience = self.audience(uow, firing, mission)
        if audience is None:
            return
        if binding.kind == "cadence_event":
            for person, _ in audience:
                if isinstance(binding.effect, ReplaceCadence):
                    self.engine.activations.start(
                        uow,
                        firing,
                        person,
                        mission,
                        release,
                        inp.previous_warning_id if isinstance(inp, WarningRaisedInput) else None,
                    )
                else:
                    self.occurrence_slots(uow, firing, person, None, mission, release)
            return
        groups = (
            tuple(((p, basis),) for p, basis in audience) if binding.audience == "person" else (audience,)
        )
        for group in groups:
            override = self.create(uow, firing, inp, group, mission, release)
            if override is None:
                continue
            for person in override.member_ids:
                self.apply_effect(uow, override, firing, person, mission, release)

    def from_warning(self, uow: UnitOfWork, warning_id: str, *, active: bool = True) -> tuple[Override, ...]:
        fetch = (
            self.deps.store.active_overrides_from_warning
            if active
            else self.deps.store.overrides_from_warning
        )
        return bounded_pages(
            lambda size, offset: fetch(uow, warning_id, size, offset=offset),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_bindings * self.deps.settings.max_roster,
        )

    def repoint_previous(
        self, uow: UnitOfWork, inp: WarningRaisedInput, binding: Binding, person: str | None
    ) -> bool:
        if inp.previous_warning_id is None:
            return False
        for old in self.from_warning(uow, inp.previous_warning_id):
            if (
                old.binding_id == binding.binding_id
                and old.person_id == person
                and old.audience == binding.audience
            ):
                changed = repoint(old, inp.warning_id)
                self.deps.store.update_override(uow, changed, expected_revision=old.revision)
                self.engine.emit_override(uow, changed)
                return True
        return False

    def create(
        self,
        uow: UnitOfWork,
        firing: Firing,
        inp: TriggerInput,
        group: tuple[tuple[str, MemberBasis], ...],
        mission: MissionFacts,
        release: RoutineRelease,
    ) -> Override | None:
        binding = firing.binding
        person = group[0][0] if binding.audience == "person" else None
        key = person or binding.audience
        identifier = self.deps.ids.derived(
            "rov", binding.binding_id, firing.source_kind, firing.source_ref, key
        )
        if self.deps.store.get_override(uow, identifier) is not None:
            return None
        if isinstance(inp, WarningRaisedInput):
            prior = self.from_warning(uow, inp.warning_id, active=False)
            if any(
                o.binding_id == binding.binding_id
                and o.person_id == person
                and o.audience == binding.audience
                for o in prior
            ):
                return None
        if isinstance(inp, WarningRaisedInput) and inp.previous_warning_id is not None:
            if self.repoint_previous(uow, inp, binding, person):
                return None
        if any(
            self.deps.store.count_active_overrides_for(uow, p)
            >= self.deps.settings.max_active_overrides_per_person
            for p, _ in group
        ):
            audit(self.deps, uow, "capacity", firing.source_kind)
            self.deps.health.mark_degraded("routine", "ROUTINE_CAPACITY", uow.now)
            return None
        override = self.new_override(
            uow,
            firing,
            group,
            mission,
            release,
            identifier,
            person,
            inp.raised_by if isinstance(inp, WarningRaisedInput) else "rule",
        )
        if not self.deps.store.insert_override(uow, override):
            return None
        self.engine.emit_override(uow, override)
        if override.ends_at is not None:
            self.engine.schedule(
                uow,
                "routine.ovr." + identifier,
                "routine.override",
                override.ends_at,
                {"override_id": identifier, "kind": "ends"},
                kind="elapsed",
            )
        return override

    def new_override(
        self,
        uow: UnitOfWork,
        firing: Firing,
        group: tuple[tuple[str, MemberBasis], ...],
        mission: MissionFacts,
        release: RoutineRelease,
        identifier: str,
        person: str | None,
        raised_by: str,
    ) -> Override:
        binding = firing.binding
        item = release.item(binding.item_id)
        if item is None or binding.trigger_index is None:
            raise ConfigInvalid((Finding("error", "/bindings", "Unknown trigger item"),))
        trigger = item.triggers[binding.trigger_index]
        until = binding.until
        ends_kind: Literal["days", "person_stops", "effect_complete", "mission_event", "warning_cleared"] = (
            "days"
            if isinstance(until, UntilDays)
            else "person_stops"
            if isinstance(until, UntilPersonStops)
            else "effect_complete"
            if isinstance(until, UntilEffectComplete)
            else "mission_event"
            if isinstance(until, UntilMissionEvent)
            else "warning_cleared"
        )
        start, finish = self.engine.bounds(*self.engine.day(firing.source_at))
        ends_at = (
            days_end_at(firing.source_at, until.days, finish - start)
            if isinstance(until, UntilDays)
            else None
        )
        words = release.words
        rendered = (
            trigger.new_cadence.word
            if words is None
            else cadence_text(trigger.new_cadence, words, words.overlay_words.get("eva"))
        )
        return Override(
            identifier,
            1,
            item.item_id,
            person,
            binding.audience,
            binding.binding_id,
            trigger.index,
            trigger.text,
            started_by_for(raised_by),
            firing.source_kind,
            firing.source_ref,
            trigger.new_cadence,
            rendered,
            reason_text(trigger.text),
            "active",
            firing.source_at,
            ends_kind,
            ends_at,
            words.unknown if words else "",
            until.role if isinstance(until, UntilPersonStops) else None,
            None,
            None,
            trigger.basis,
            trigger.source_ids,
            release.release_id,
            tuple(p for p, _ in group),
            dict(group),
            firing.origin_mode or mission.mission_mode,
            firing.source_at,
            (firing.source_ref,) if firing.source_kind == "warning" else (),
            binding.effect,
            False,
            None,
        )

    def apply_effect(
        self,
        uow: UnitOfWork,
        override: Override,
        firing: Firing,
        person: str,
        mission: MissionFacts,
        release: RoutineRelease,
    ) -> None:
        effect = override.effect
        if isinstance(effect, ReplaceCadence):
            item = release.item(override.item_id)
            if item is None:
                return
            new = period_days(effect.cadence, release.calendar)
            base = period_days(item.cadence_for(mission.active_environment_id), release.calendar)
            if new is not None and (base is None or new <= base):
                # Materialize without re-entering each-day question expansion.
                for occ in self.engine.periodic(uow, person, self.engine.day(uow.now)[0], release, mission):
                    if occ.item_id == item.item_id:
                        for old in self.engine.earlier_periodic(uow, person, item.item_id, occ.mission_day):
                            self.engine.close_predecessor(uow, old)
                        self.engine.insert(uow, occ)
            else:
                window = trigger_window(
                    firing.source_at,
                    release.policy(item.item_id),
                    self.engine.bounds(*self.engine.day(firing.source_at))[1],
                )
                self.added(
                    uow,
                    person,
                    item.item_id,
                    window,
                    "override:" + override.override_id + ":at_trigger",
                    "override",
                    override,
                    mission,
                    release,
                )
        elif isinstance(effect, AddOccurrences):
            self.occurrence_slots(uow, firing, person, override, mission, release)
        else:
            self.questions(uow, override, person, release, effect, firing.source_at)

    def added(
        self,
        uow: UnitOfWork,
        person: str,
        item_id: str,
        window: Window | None,
        key: str,
        kind: CauseKind,
        override: Override | None,
        mission: MissionFacts,
        release: RoutineRelease,
        *,
        repeat: bool = False,
    ) -> str | None:
        if window is None or uow.now >= window.due_until:
            audit(self.deps, uow, "late_input", override.source_kind if override else "overlay")
            return None
        opened = self.engine.open_occurrences(uow, person)
        candidates = tuple(
            o
            for o in opened
            if not self.deps.store.instances_for_occurrence(
                uow, o.occurrence_id, self.deps.settings.max_slots_per_binding
            )
        )
        target = (
            None
            if repeat
            else absorbing(
                candidates,
                item_id=item_id,
                person_id=person,
                due_from=window.due_from,
                due_until=window.due_until,
                repeat=repeat,
            )
        )
        if target is None:
            occ = self.engine.make_occurrence(
                uow,
                person,
                item_id,
                window,
                key,
                kind,
                mission,
                release,
                override_id=None if override is None else override.override_id,
                binding_id=None if override is None else override.binding_id,
                started_by=None if override is None else override.started_by,
            )
            if override is not None:
                occ = replace(occ, origin_mode=override.source_origin_mode)
            if self.engine.insert(uow, occ) or self.deps.store.get(uow, occ.occurrence_id) is not None:
                target = occ.occurrence_id
        if override is not None and target is not None:
            self.deps.store.add_target(uow, override.override_id, "occurrence", target)
        return target

    def occurrence_slots(
        self,
        uow: UnitOfWork,
        firing: Firing,
        person: str,
        override: Override | None,
        mission: MissionFacts,
        release: RoutineRelease,
    ) -> None:
        effect = firing.binding.effect
        if not isinstance(effect, AddOccurrences):
            return
        day, version = self.engine.day(firing.source_at)
        start, finish = self.engine.bounds(day, version)
        prefix = (
            "override:" + override.override_id
            if override
            else "cadence_event:" + firing.binding.binding_id + ":" + firing.source_ref
        )
        kind: CauseKind = "override" if override else "cadence_event"
        policy = release.policy(firing.binding.item_id)
        if effect.at_trigger:
            self.added(
                uow,
                person,
                firing.binding.item_id,
                trigger_window(firing.source_at, policy, finish),
                prefix + ":at_trigger",
                kind,
                override,
                mission,
                release,
            )
        for index, (offset, minute) in enumerate(effect.times):
            day_start, day_end = self.engine.bounds(day + offset, version)
            self.added(
                uow,
                person,
                firing.binding.item_id,
                trigger_window(day_start + timedelta(minutes=minute), policy, day_end),
                prefix + f":t{index}",
                kind,
                override,
                mission,
                release,
            )
        if effect.every_minutes is not None and override is not None:
            # One shared next-slot job for all frozen members, never a timer per member.
            self.engine.schedule(
                uow,
                "routine.every." + override.override_id,
                "routine.override",
                firing.source_at + timedelta(minutes=effect.every_minutes),
                {"override_id": override.override_id, "kind": "every", "slot": "1"},
                kind="elapsed",
            )

    def questions(
        self,
        uow: UnitOfWork,
        override: Override,
        person: str,
        release: RoutineRelease,
        effect: AddQuestions,
        at: datetime,
        *,
        daily: bool = False,
    ) -> None:
        ids = None if release.question_sets is None else release.question_sets.get(effect.question_set_id)
        if ids is None:
            audit(self.deps, uow, "question_set_unknown", override.source_kind)
            return
        for slot, start, finish in plan_slots(
            source_at=at,
            repeat_after_minutes=effect.repeat_after_minutes,
            day_end_of=lambda t: self.engine.bounds(*self.engine.day(t))[1],
        ):
            periodic = self.deps.store.periodic_on_day(
                uow, person, override.item_id, self.engine.day(start)[0]
            )
            needs_carrier = slot != "initial" or (periodic is not None and periodic.state in FINAL_STATES)
            if needs_carrier:
                window = trigger_window(start, release.policy(override.item_id), finish)
                if window is None:
                    continue
                finish = window.due_until
            if uow.now >= finish:
                audit(self.deps, uow, "late_input", override.source_kind)
                continue
            day = self.engine.day(start)[0]
            if daily:
                slot = f"day:{day}:" + slot
            identifier = self.deps.ids.derived(
                "rq", override.override_id, person, effect.question_set_id, slot
            )
            if self.deps.store.get_instance(uow, identifier) is not None:
                continue
            carrier = self.question_carrier(uow, override, person, release, slot, start, finish)
            if needs_carrier and carrier is None:
                continue
            instance = QuestionInstance(
                identifier,
                override.override_id,
                person,
                effect.question_set_id,
                slot,
                ids,
                start,
                finish,
                {},
                "open",
                1,
            )
            if self.deps.store.insert_instance(uow, instance, carrier_day=day, carrier_occurrence_id=carrier):
                self.deps.store.add_target(uow, override.override_id, "instance", identifier)
                self.engine.schedule(
                    uow, "routine.qi." + identifier, "routine.instance", finish, {"instance_id": identifier}
                )

    def question_carrier(
        self,
        uow: UnitOfWork,
        override: Override,
        person: str,
        release: RoutineRelease,
        slot: str,
        start: datetime,
        finish: datetime,
    ) -> str | None:
        day = self.engine.day(start)[0]
        periodic = self.deps.store.periodic_on_day(uow, person, override.item_id, day)
        initial = slot == "initial" or slot.endswith(":initial")
        if initial and (periodic is None or periodic.state not in FINAL_STATES):
            return None
        window = trigger_window(start, release.policy(override.item_id), finish)
        return self.added(
            uow,
            person,
            override.item_id,
            window,
            f"override:{override.override_id}:q:{slot}",
            "override",
            override,
            self.engine.mission(uow),
            release,
            repeat=True,
        )

    def each_day(self, uow: UnitOfWork, person: str, day: int) -> None:
        for override in self.engine.overrides_for(uow, person):
            if isinstance(override.effect, AddQuestions) and override.effect.attach_to == "each_occurrence":
                if day <= self.engine.day(override.source_at)[0]:
                    continue
                release = self.engine.pinned_for_deadline(uow, override.config_release_id)
                if release is None:
                    continue
                start = self.engine.bounds(day, self.engine.day(uow.now)[1])[0]
                self.questions(uow, override, person, release, override.effect, start, daily=True)

    def clear_warning(self, uow: UnitOfWork, inp: WarningClearedInput) -> None:
        self.engine.activations.event(uow, inp)
        for override in self.from_warning(uow, inp.warning_id):
            decision = clear_decision(override, warning_id=inp.warning_id, clear_reason=inp.clear_reason)
            if decision in ("end", "environment_changed"):
                self.at_clear(uow, override, inp.cleared_at)
                self.end(
                    uow,
                    override,
                    "environment_changed" if decision == "environment_changed" else "warning_cleared",
                    inp.cleared_at,
                )
            elif decision == "await":
                changed = replace(override, revision=override.revision + 1, awaiting_disposition=True)
                self.deps.store.update_override(uow, changed, expected_revision=override.revision)
                self.engine.emit_override(uow, changed)

    def at_clear(self, uow: UnitOfWork, override: Override, at: datetime) -> None:
        if not isinstance(override.effect, AddOccurrences) or not override.effect.at_clear:
            return
        release = self.engine.release_for(uow, override.config_release_id)
        mission = self.engine.mission(uow)
        for person in override.member_ids:
            self.added(
                uow,
                person,
                override.item_id,
                trigger_window(
                    at, release.policy(override.item_id), self.engine.bounds(*self.engine.day(at))[1]
                ),
                f"override:{override.override_id}:at_clear",
                "override",
                override,
                mission,
                release,
                repeat=True,
            )

    def end(self, uow: UnitOfWork, override: Override, reason: EndReason, at: datetime) -> Override:
        changed = end(override, at=at, reason=reason)
        self.deps.store.update_override(uow, changed, expected_revision=override.revision)
        self.engine.cancel(uow, "routine.ovr." + override.override_id)
        self.engine.cancel(uow, "routine.every." + override.override_id)
        self.engine.emit_override(uow, changed)
        return changed

    def effect_complete(self, uow: UnitOfWork, target_id: str) -> None:
        overrides = bounded_pages(
            lambda size, offset: self.deps.store.overrides_targeting(uow, target_id, size, offset=offset),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_bindings * self.deps.settings.max_roster,
        )
        for override in overrides:
            if override.state != "active" or override.ends_kind != "effect_complete":
                continue
            if self.targets_final(uow, override):
                self.end(uow, override, "effect_complete", uow.now)

    def targets_final(self, uow: UnitOfWork, override: Override) -> bool:
        page = self.deps.settings.page_limit
        maximum = (
            self.deps.settings.max_roster
            * (self.deps.settings.max_slots_per_binding + 1)
            * self.deps.settings.history_days
        )
        seen = False
        for offset in range(0, maximum, page):
            targets = self.deps.store.targets_of(uow, override.override_id, page, offset=offset)
            for kind, identifier in targets:
                seen = True
                if kind == "occurrence":
                    occ = self.deps.store.get(uow, identifier)
                    if occ is None or occ.state not in FINAL_STATES:
                        return False
                else:
                    instance = self.deps.store.get_instance(uow, identifier)
                    if instance is None or instance.state == "open":
                        return False
            if len(targets) < page:
                return seen
        audit(self.deps, uow, "capacity", override.source_kind)
        self.deps.health.mark_degraded("routine", "ROUTINE_CAPACITY", uow.now)
        return False

    def end_on_event(self, uow: UnitOfWork, inp: MissionEventInput) -> None:
        mission = self.engine.mission(uow)
        if not admissible(
            mission_mode=mission.mission_mode,
            input_origin=inp.origin_mode,
            group_audience=True,
            on_roster=None,
        ).ok:
            return
        self.engine.activations.event(uow, inp)
        for override in self.engine.all_overrides(uow):
            if override.ends_kind != "mission_event" or inp.occurred_at < override.started_at:
                continue
            release = self.engine.release_for(uow, override.config_release_id)
            binding = next((b for b in release.bindings if b.binding_id == override.binding_id), None)
            if (
                binding is not None
                and isinstance(binding.until, UntilMissionEvent)
                and inp.event_type in binding.until.event_types
            ):
                self.end(uow, override, "mission_event", inp.occurred_at)

    def override_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        override = self.deps.store.get_override(uow, job.payload["override_id"])
        if override is None or override.state != "active":
            return
        if job.payload["kind"] == "every":
            self.every(uow, override, int(job.payload["slot"]))
        elif override.ends_at is not None:
            if not self.engine.trusted(uow):
                # PLAN row 34: unknown time never ends a tightening; look again after the grace.
                self.engine.schedule(
                    uow,
                    job.key,
                    job.handler,
                    uow.now + timedelta(milliseconds=self.deps.settings.miss_grace_ms),
                    dict(job.payload),
                    kind="elapsed",
                )
            elif uow.now >= override.ends_at:
                self.end(uow, override, "days_elapsed", uow.now)
            else:
                self.engine.schedule(
                    uow, job.key, job.handler, override.ends_at, dict(job.payload), kind="elapsed"
                )

    def every(self, uow: UnitOfWork, override: Override, slot: int) -> None:
        effect = override.effect
        if not isinstance(effect, AddOccurrences) or effect.every_minutes is None:
            return
        window = slot_window(override.started_at, slot, effect.every_minutes)
        release = self.engine.release_for(uow, override.config_release_id)
        mission = self.engine.mission(uow)
        for person in override.member_ids:
            self.added(
                uow,
                person,
                override.item_id,
                window,
                f"override:{override.override_id}:every:{slot}",
                "override",
                override,
                mission,
                release,
                repeat=True,
            )
        elapsed = (uow.now - override.started_at) // timedelta(minutes=effect.every_minutes)
        following = max(slot + 1, elapsed + 1)
        self.engine.schedule(
            uow,
            "routine.every." + override.override_id,
            "routine.override",
            override.started_at + timedelta(minutes=following * effect.every_minutes),
            {"override_id": override.override_id, "kind": "every", "slot": str(following)},
            kind="elapsed",
        )

    def on_answer(self, uow: UnitOfWork, answer: AnswerFacts, mission_id: str) -> None:
        mission = self.engine.mission(uow)
        if mission_id != mission.mission_id:
            audit(self.deps, uow, "foreign_mission", "answer")
            return
        if (
            answer.origin_mode not in ("LIVE", "REPLAY", "SIM")
            or not admissible(
                mission_mode=mission.mission_mode,
                input_origin=answer.origin_mode,
                group_audience=False,
                on_roster=None
                if self.deps.roster is None
                else answer.patient_id in self.deps.roster.person_ids(),
            ).ok
        ):
            audit(self.deps, uow, "origin_rejected", "answer")
            return
        instances = self.engine.open_instances(uow, answer.patient_id)
        instance = pick_instance(instances, answer.question_id, answer.answered_at)
        if instance is not None:
            changed = apply_answer(
                instance,
                question_id=answer.question_id,
                answer_id=answer.answer_id,
                answered_at=answer.answered_at,
            )
            if changed is not None:
                self.deps.store.update_instance(uow, changed, expected_revision=instance.revision)
                if changed.state == "answered":
                    self.finish_instance(uow, changed, answer.answered_at)
        release = self.engine.release(uow)
        day = self.engine.day(answer.answered_at)[0]
        ask = self.deps.store.get_barrier_ask(uow, answer.patient_id, day)
        if answer.question_id == release.misses.question_id and ask is not None and ask[1] == "pending":
            self.deps.store.put_barrier_ask(
                uow, answer.patient_id, day, ask[0], "answered", answer.answer_id, answer.answered_at
            )
        self.apply(
            uow,
            AnswerInput(
                answer.answer_id,
                answer.patient_id,
                answer.question_id,
                answer.presence,
                answer.answered_at,
                answer.origin_mode,
            ),
        )

    def instance_job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        instance = self.deps.store.get_instance(uow, job.payload["instance_id"])
        if instance is None or instance.state != "open":
            return
        changed = expire(instance, now=uow.now)
        if changed == instance:
            self.engine.schedule(uow, job.key, job.handler, instance.due_until, dict(job.payload))
            return
        self.deps.store.update_instance(uow, changed, expected_revision=instance.revision)
        self.finish_instance(uow, changed, uow.now)

    def finish_instance(self, uow: UnitOfWork, instance: QuestionInstance, at: datetime) -> None:
        self.engine.cancel(uow, "routine.qi." + instance.instance_id)
        carrier = self.deps.store.instance_carrier(uow, instance.instance_id)
        occ = None if carrier is None or carrier[1] is None else self.deps.store.get(uow, carrier[1])
        if occ is not None and occ.state not in FINAL_STATES:
            if instance.state == "answered":
                start = self.engine.bounds(occ.mission_day, occ.clock_version)[0]
                result = complete(
                    occ,
                    now=uow.now,
                    completed_at=at,
                    outcome=Outcome((), tuple(instance.answers.values()), None),
                    actor_kind="engine",
                    actor_id=uow.actor.id,
                    day_start=start,
                    guidance_release_id=occ.config_release_id,
                )
            else:
                result = miss(occ, now=uow.now, counted=False, reason=instance.state, actor_id=uow.actor.id)
            self.engine.change(uow, occ, result)
        self.effect_complete(uow, instance.instance_id)

    def walk_window(self, inp: OverlayInput, policy: ItemPolicy) -> tuple[Window | None, str] | None:
        if (
            inp.state == "planned"
            and inp.planned_start is not None
            and policy.per_eva in ("before_start", "both")
        ):
            start = self.engine.bounds(*self.engine.day(inp.planned_start))[0]
            window = before_walk_window(inp.planned_start, inp.started_at, policy, start)
            return window, "before"
        elif inp.state == "ended" and inp.ended_at is not None and policy.per_eva in ("after_end", "both"):
            window = after_walk_window(
                inp.ended_at, policy, self.engine.bounds(*self.engine.day(inp.ended_at))[1]
            )
            return window, "after"
        else:
            return None

    def overlay(
        self, uow: UnitOfWork, inp: OverlayInput, mission: MissionFacts, release: RoutineRelease
    ) -> None:
        for occ in self.engine.open_occurrences(uow, inp.person_id):
            if (
                inp.kind == "eva"
                and inp.state == "active"
                and occ.cause_key == f"per_eva:{inp.overlay_id}:before"
            ):
                deadline = inp.started_at or uow.now
                self.engine.change(
                    uow,
                    occ,
                    miss(
                        occ,
                        now=uow.now,
                        counted=self.engine.counted(uow, deadline),
                        reason="walk_started",
                        actor_id=uow.actor.id,
                    ),
                )
            else:
                self.engine.reschedule(uow, occ)
        if inp.kind != "eva":
            return
        for item in release.items:
            effective = self.engine.effective(
                uow, inp.person_id, item, release, mission.active_environment_id
            )
            policy = release.policy(item.item_id)
            if effective.cadence.word != "per_eva" or policy is None:
                continue
            choice = self.walk_window(inp, policy)
            if choice is None:
                continue
            window, edge = choice
            self.added(
                uow,
                inp.person_id,
                item.item_id,
                window,
                f"per_eva:{inp.overlay_id}:{edge}",
                "per_eva",
                None,
                mission,
                release,
            )
