"""Adapters that satisfy the consumer modules' structural copies of E23's ports (E23 4.2).

Modules import only each other's `application.ports`, so each consumer declared its own copy of MissionStatePort,
MissionFacts, OverlayPort and the rest. Composition registers one of these adapters under each copy; a consumer's
`resolver.optional(...)` then finds a real answer. No adapter holds state: every call reads through the live
repository, so it sees the same facts as E23's own ports and nothing a consumer could change.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime

from crew_health.guidance.application.ports import Known, Unknown
from crew_health.help.application.ports import MissionFacts as HelpMissionFacts
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.measurement.application.ports import MissionFacts as MeasurementMissionFacts
from crew_health.mission.adapters.live_state import LiveMissionState
from crew_health.roles.application.ports import MissionFacts as RolesMissionFacts
from crew_health.roles.application.ports import OverlayFact as RolesOverlayFact

PROVIDER = "environment"


class MeasurementMission:
    """measurement.application.ports.MissionStatePort."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MeasurementMissionFacts | None:
        facts = self._state.current(uow)
        if facts is None:
            return None
        return MeasurementMissionFacts(
            facts.mission_id,
            facts.mission_mode,
            facts.active_environment_id,
            facts.planned_length_days,
            facts.site_node_id,
        )


class HelpMission:
    """help.application.ports.MissionStatePort."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def current(self, uow: ReadSnapshot | None = None) -> HelpMissionFacts | None:
        return self._state.current(uow)


class KitmapMission:
    """kitmap.application.ports.MissionStatePort."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def mission_mode(self, uow: UnitOfWork | ReadSnapshot | None = None) -> str | None:
        return self._state.mission_mode(uow)

    def active_environment_id(self, uow: UnitOfWork | ReadSnapshot | None = None) -> str | None:
        return self._state.active_environment_id(uow)


class RolesMission:
    """roles.application.ports.MissionStatePort."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> RolesMissionFacts | None:
        facts = self._state.current(uow)
        if facts is None:
            return None
        return RolesMissionFacts(
            facts.mission_id,
            facts.mission_mode,
            facts.active_environment_id,
            facts.planned_length_days,
            facts.site_node_id,
        )

    def day_end(self, instant: datetime) -> datetime:
        return self._state.day_end(instant)


class RolesOverlays:
    """roles.application.ports.OverlayPort. `planned_end` is request Q9 of E31 and stays None until E23 has it."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[RolesOverlayFact]]:
        found = self._state.active(person_ids, at)
        return {
            person: tuple(
                RolesOverlayFact(
                    f.overlay_id, f.person_id, f.kind, f.state, f.planned_start, f.started_at, f.ended_at, None
                )
                for f in facts
            )
            for person, facts in found.items()
        }


class RolesLocation:
    """roles.application.ports.LocationPort (S8.5 interim: no per person location)."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def location_of(self, person_id: str, at: datetime) -> str | None:
        return self._state.location_of(person_id, at)


class RolesDayLabel:
    """roles.application.ports.DayLabelPort."""

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def inline_label_at(self, instant: datetime) -> str:
        return self._state.inline_label_at(instant)


class GuidanceEnvironment:
    """guidance.application.ports.EnvironmentFacts under the S8.5 interim: one location per environment.

    A location of None is the active environment's single location. No profile yet, or a location that is not the
    active environment's, is Unknown and the caller fails closed (S8.2).
    """

    def __init__(self, state: LiveMissionState) -> None:
        self._state = state

    def applicable(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, environments: Collection[str]
    ) -> Known[bool] | Unknown:
        active = self._state.active_environment_id(snapshot)
        if active is None:
            return Unknown(PROVIDER, "no_data")
        if patient_location_id is not None and patient_location_id != active:
            return Unknown(PROVIDER, "location_not_in_active_environment")
        return Known(active in environments)
