"""Mission reads: wire mappers and the port implementations of E23 4.2 (UC-Q).

The repository protocol is implemented by the SQLite adapter, which waits on E01 T2. Reads are bound to the
caller's snapshot or unit of work; only a call with no view opens its own snapshot (E23 4.2).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.contracts_gen.schemas.activity_overlay_schema import ActivityOverlay as OverlayModel
from crew_health.contracts_gen.schemas.environment_profile_schema import (
    EnvironmentProfile as EnvironmentProfileModel,
)
from crew_health.contracts_gen.schemas.mission_event_schema import MissionEvent as MissionEventModel
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.met import MissionClock
from crew_health.kernel.uow import ReadSnapshotFactory, UnitOfWork
from crew_health.mission.application.ports import (
    MissionFacts,
    OriginMode,
    OverlayFact,
    ReturnOption,
    Snap,
)
from crew_health.mission.domain import day as day_rules
from crew_health.mission.domain import overlay as overlay_rules
from crew_health.mission.domain.errors import IntegrityViolation
from crew_health.mission.domain.events import EventRecord
from crew_health.mission.domain.overlay import OverlayRecord
from crew_health.mission.domain.profile import ProfileRecord, allowed_actions, assert_single_active
from crew_health.mission.domain.state_facts import FactState


@dataclass(frozen=True, slots=True)
class MissionIdentity:
    mission_id: str
    mission_mode: OriginMode
    planned_length_days: int | None
    site_node_id: str
    created_at: datetime


class MissionReader(Protocol):
    """Read side of the owned tables (E23 5.1). Every method takes the caller's view."""

    def identity(self, view: Snap) -> MissionIdentity | None: ...
    def profile(self, view: Snap, profile_id: str) -> ProfileRecord | None: ...
    def profiles(self, view: Snap) -> Sequence[ProfileRecord]: ...  # bounded by mission.page_max
    def overlay(self, view: Snap, overlay_id: str) -> OverlayRecord | None: ...
    def overlays_of(self, view: Snap, person_ids: Collection[str]) -> Sequence[OverlayRecord]: ...
    def events(
        self, view: Snap, *, since: datetime, after_event_id: str | None, limit: int
    ) -> Sequence[EventRecord]: ...
    def fact_states(self, view: Snap) -> Mapping[str, FactState]: ...


def iso(value: datetime) -> str:
    return (
        value.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if value.microsecond else value.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _opt(value: datetime | None) -> str | None:
    return None if value is None else iso(value)


def profile_model(record: ProfileRecord, *, is_crew: bool, person_id: str | None) -> EnvironmentProfileModel:
    lists = record.lists
    return EnvironmentProfileModel.model_validate(
        {
            "profile_id": record.profile_id,
            "revision": record.revision,
            "environment_id": record.environment_id,
            "state": record.state,
            "label": record.label,
            "place": record.place,
            "eva_word": record.eva_word,
            "reason": record.reason,
            "proposed_by": {"kind": record.proposed_by.kind, "id": record.proposed_by.id},
            "proposed_at": iso(record.proposed_at),
            "effective_at": _opt(record.effective_at),
            "confirmations_required": record.confirmations_required,
            "confirmations": [{"person_id": c.person_id, "at": iso(c.at)} for c in record.confirmations],
            "rejected_by": None
            if record.rejected_by is None
            else {"kind": record.rejected_by.kind, "id": record.rejected_by.id},
            "activated_at": _opt(record.activated_at),
            "superseded_at": _opt(record.superseded_at),
            "environment_day_start": _opt(record.environment_day_start),
            "config_release_id": record.config_release_id,
            "hazard_ids": list(lists.hazard_ids),
            "limit_ids": list(lists.limit_ids),
            "warning_definition_ids": list(lists.warning_definition_ids),
            "sensor_type_ids": list(lists.sensor_type_ids),
            "sensor_channel_ids": list(lists.sensor_channel_ids),
            "routine_item_ids": list(lists.routine_item_ids),
            "ground": {
                "delay_text": record.ground_delay_text,
                "simulated_one_way_delay_seconds": record.ground_delay_seconds,
            },
            "allowed_actions": list(allowed_actions(record, is_crew=is_crew, person_id=person_id)),
        }
    )


def overlay_model(record: OverlayRecord) -> OverlayModel:
    return OverlayModel.model_validate(
        {
            "overlay_id": record.overlay_id,
            "revision": record.revision,
            "person_id": record.person_id,
            "overlay": record.kind,
            "state": record.state,
            "planned_start": _opt(record.planned_start),
            "started_at": _opt(record.started_at),
            "ended_at": _opt(record.ended_at),
            "source": {
                "kind": record.source_kind,
                "ref_id": record.source_ref,
            },
            "location": record.location,
        }
    )


def event_model(record: EventRecord) -> MissionEventModel:
    return MissionEventModel.model_validate(
        {
            "event_id": record.event_id,
            "event_type": record.event_type,
            "occurred_at": iso(record.occurred_at),
            "recorded_at": iso(record.recorded_at),
            "recorded_by": {"kind": record.recorded_by_kind, "id": record.recorded_by_id},
            "origin_mode": record.origin_mode,
            "environment_id": record.environment_id,
            "person_ids": list(record.person_ids),
            "location": record.location,
            "note": record.note,
        }
    )


def _active_profile(reader: MissionReader, view: Snap) -> ProfileRecord:
    records = reader.profiles(view)
    assert_single_active(records)  # a missing or doubled active record after init is an integrity failure
    return next(r for r in records if r.state == "active")


@dataclass(frozen=True, slots=True)
class MissionState:
    """MissionStatePort, OverlayPort, LocationPort and the day label port over one repository."""

    reader: MissionReader
    snapshots: ReadSnapshotFactory
    return_options: Mapping[str, ReturnOption | None]  # per environment, from the active release
    day_end_of: MissionClock
    day_label_for: Callable[[ProfileRecord], day_rules.DayLabelFacts]  # through the record's own release pin

    def _facts(self, view: Snap) -> MissionFacts | None:
        identity = self.reader.identity(view)
        if identity is None:
            return None  # before mission init every answer is None, never a guess
        active = _active_profile(self.reader, view)
        return MissionFacts(
            identity.mission_id,
            identity.mission_mode,
            active.environment_id,
            identity.planned_length_days,
            identity.site_node_id,
        )

    def current(self, uow: Snap | None = None) -> MissionFacts | None:
        if uow is not None:
            return self._facts(uow)
        with self.snapshots.snapshot() as snapshot:
            return self._facts(snapshot)

    def mission_mode(self, uow: Snap | None = None) -> OriginMode | None:
        facts = self.current(uow)
        return None if facts is None else facts.mission_mode

    def active_environment_id(self, uow: Snap | None = None) -> str | None:
        facts = self.current(uow)
        return None if facts is None else facts.active_environment_id

    def overlays(self, uow: UnitOfWork, person_id: str, since: datetime) -> Sequence[OverlayFact]:
        stored = self.reader.overlays_of(uow, (person_id,))
        records = overlay_rules.intervals(stored, {r.kind for r in stored}, since, uow.now)
        return tuple(_fact(r) for r in records)

    def return_option(self, uow: Snap | None, environment_id: str) -> ReturnOption | None:
        return self.return_options.get(environment_id)

    def active(self, person_ids: Collection[str], at: datetime) -> Mapping[str, Sequence[OverlayFact]]:
        with self.snapshots.snapshot() as snapshot:
            records = self.reader.overlays_of(snapshot, person_ids)
        result: dict[str, list[OverlayFact]] = {p: [] for p in person_ids}
        for r in records:
            if r.started_at is not None and r.started_at <= at and (r.ended_at is None or at < r.ended_at):
                result[r.person_id].append(_fact(r))
        return result

    def intervals(
        self, person_id: str, kinds: Collection[str], since: datetime, until: datetime, limit: int
    ) -> Sequence[OverlayFact]:
        with self.snapshots.snapshot() as snapshot:
            records = overlay_rules.intervals(
                self.reader.overlays_of(snapshot, (person_id,)), kinds, since, until
            )
        if len(records) > limit:
            raise ProblemError(
                ErrorCode.PAYLOAD_TOO_LARGE, "overlay rows above the bound"
            )  # never a truncated list as complete
        return tuple(_fact(r) for r in records)

    def location_of(self, person_id: str, at: datetime) -> str | None:
        return None  # S8.5 interim: one location per environment, no per-person location until R-SYS-6

    def day_end(self, instant: datetime) -> datetime:
        number = self.day_end_of.mission_day(instant)
        version = self.day_end_of.version_at(instant).clock_version
        return self.day_end_of.day_bounds(number, version=version)[1]

    def _period(self, record: ProfileRecord) -> day_rules.ProfilePeriod:
        if record.activated_at is None or record.environment_day_start is None:
            raise IntegrityViolation("activated profile without its activation instants")
        return day_rules.ProfilePeriod(
            record.environment_id,
            record.activated_at,
            record.superseded_at,
            record.environment_day_start,
            self.day_label_for(record),
        )

    def inline_label_at(self, instant: datetime, *, unknown: str) -> str | None:
        with self.snapshots.snapshot() as snapshot:
            identity = self.reader.identity(snapshot)
            if identity is None:
                return None
            history = tuple(
                self._period(r) for r in self.reader.profiles(snapshot) if r.activated_at is not None
            )
        return day_rules.inline_label_at(
            history,
            instant,
            mission_day_at=self.day_end_of.mission_day,
            total=identity.planned_length_days,
            unknown=unknown,
        )


def _fact(record: OverlayRecord) -> OverlayFact:
    return OverlayFact(
        record.overlay_id,
        record.person_id,
        record.kind,
        record.state,
        record.planned_start,
        record.started_at,
        record.ended_at,
    )
