from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from crew_health.kernel.scheduler import ScheduledJob
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.routine.application.audit import audit
from crew_health.routine.application.scans import bounded_pages
from crew_health.routine.domain.activation import CadenceActivation
from crew_health.routine.domain.bindings import Firing, MissionEventInput, WarningClearedInput
from crew_health.routine.domain.errors import ConfigInvalid, Finding
from crew_health.routine.domain.release import (
    ReplaceCadence,
    UntilDays,
    UntilMissionEvent,
    UntilWarningCleared,
)
from crew_health.routine.domain.vocabulary import EndReason

if TYPE_CHECKING:
    from crew_health.routine.application.engine import RoutineEngine
    from crew_health.routine.application.ports import MissionFacts
    from crew_health.routine.domain.release import RoutineRelease


class CadenceActivations:
    def __init__(self, engine: RoutineEngine) -> None:
        self.engine, self.deps = engine, engine.deps

    def active(
        self, view: UnitOfWork | ReadSnapshot, person: str | None = None
    ) -> tuple[CadenceActivation, ...]:
        return bounded_pages(
            lambda size, offset: self.deps.store.active_activations(
                view, size, offset=offset, person_id=person
            ),
            page=self.deps.settings.page_limit,
            maximum=self.deps.settings.max_bindings * (1 if person else self.deps.settings.max_roster),
        )

    def start(
        self,
        uow: UnitOfWork,
        firing: Firing,
        person: str,
        mission: MissionFacts,
        release: RoutineRelease,
        previous_warning_id: str | None = None,
    ) -> None:
        binding = firing.binding
        if not isinstance(binding.effect, ReplaceCadence):
            return
        until = binding.until
        if not isinstance(until, UntilDays | UntilMissionEvent | UntilWarningCleared):
            raise ConfigInvalid((Finding("error", "/until", "Invalid cadence activation end"),))
        identifier = self.deps.ids.derived(
            "rca", binding.binding_id, firing.source_kind, firing.source_ref, person
        )
        if self.existing(uow, firing, person, identifier, previous_warning_id):
            return
        if len(self.active(uow, person)) >= self.deps.settings.max_bindings:
            audit(self.deps, uow, "capacity", firing.source_kind)
            return
        start, finish = self.engine.bounds(*self.engine.day(firing.source_at))
        ends_at = firing.source_at + until.days * (finish - start) if isinstance(until, UntilDays) else None
        activation = CadenceActivation(
            identifier,
            1,
            binding.binding_id,
            binding.item_id,
            person,
            binding.effect.cadence,
            firing.source_at,
            "days"
            if isinstance(until, UntilDays)
            else "mission_event"
            if isinstance(until, UntilMissionEvent)
            else "warning_cleared",
            ends_at,
            "active",
            None,
            None,
            firing.source_kind,
            firing.source_ref,
            firing.source_at,
            firing.origin_mode or mission.mission_mode,
            release.release_id,
        )
        if not self.deps.store.insert_activation(uow, activation):
            return
        if ends_at is not None:
            self.engine.schedule(
                uow,
                "routine.rca." + identifier,
                "routine.activation",
                ends_at,
                {"activation_id": identifier},
                kind="elapsed",
            )
        if ends_at is None or uow.now < ends_at:
            for occ in self.engine.periodic(uow, person, self.engine.day(uow.now)[0], release, mission):
                if occ.item_id == binding.item_id:
                    self.engine.insert(uow, occ)

    def existing(
        self, uow: UnitOfWork, firing: Firing, person: str, identifier: str, previous_warning_id: str | None
    ) -> bool:
        binding = firing.binding
        if (
            self.deps.store.get_activation(uow, identifier) is not None
            or self.deps.store.activation_from_source(
                uow, binding.binding_id, firing.source_kind, firing.source_ref, person
            )
            is not None
        ):
            return True
        if firing.source_kind == "warning" and previous_warning_id is not None:
            old = self.deps.store.activation_from_source(
                uow, binding.binding_id, "warning", previous_warning_id, person
            )
            if old is not None and old.state == "active" and firing.source_at >= old.source_at:
                self.deps.store.update_activation(
                    uow,
                    replace(
                        old,
                        revision=old.revision + 1,
                        source_ref=firing.source_ref,
                        source_at=firing.source_at,
                    ),
                    expected_revision=old.revision,
                )
                return True
        return False

    def end(self, uow: UnitOfWork, activation: CadenceActivation, reason: EndReason) -> None:
        changed = activation.end(at=uow.now, reason=reason)
        self.deps.store.update_activation(uow, changed, expected_revision=activation.revision)
        self.engine.cancel(uow, "routine.rca." + activation.activation_id)

    def job(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        activation = self.deps.store.get_activation(uow, job.payload["activation_id"])
        if activation is None or activation.state != "active" or activation.ends_at is None:
            return
        if not self.engine.trusted(uow):
            # PLAN row 34: unknown time never ends a tightening; look again after the grace.
            at = uow.now + timedelta(milliseconds=self.deps.settings.miss_grace_ms)
        elif uow.now >= activation.ends_at:
            self.end(uow, activation, "days_elapsed")
            return
        else:
            at = activation.ends_at
        self.engine.schedule(uow, job.key, job.handler, at, dict(job.payload), kind="elapsed")

    def event(self, uow: UnitOfWork, inp: MissionEventInput | WarningClearedInput) -> None:
        for activation in self.active(uow):
            if isinstance(inp, WarningClearedInput):
                if (
                    activation.ends_kind == "warning_cleared"
                    and activation.source_ref == inp.warning_id
                    and inp.cleared_at >= activation.started_at
                    and inp.clear_reason not in ("superseded", "config_changed")
                ):
                    self.end(
                        uow,
                        activation,
                        "environment_changed"
                        if inp.clear_reason == "environment_changed"
                        else "warning_cleared",
                    )
            elif activation.ends_kind == "mission_event" and inp.occurred_at >= activation.started_at:
                release = self.engine.release_for(uow, activation.config_release_id)
                binding = next((b for b in release.bindings if b.binding_id == activation.binding_id), None)
                if (
                    binding is not None
                    and isinstance(binding.until, UntilMissionEvent)
                    and inp.event_type in binding.until.event_types
                ):
                    self.end(uow, activation, "mission_event")
