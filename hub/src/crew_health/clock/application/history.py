from datetime import datetime

from crew_health.clock.application.clock import ClockRepository
from crew_health.clock.domain.corrections import instant
from crew_health.clock.domain.errors import ClockAuthorityDenied
from crew_health.clock.domain.met import bounds, met_label
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MetLabel, MissionClockVersion
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    ReadSnapshotFactory,
    ReplicationChange,
    UnitOfWork,
)


class MissionClockService:
    def __init__(self, *, snapshots: ReadSnapshotFactory, repository: ClockRepository) -> None:
        self.snapshots, self.repository = snapshots, repository

    def version_at(self, at: datetime) -> MissionClockVersion:
        with self.snapshots.snapshot() as snapshot:
            return self.repository.mission_version(snapshot.connection, at=at)

    def met(self, at: datetime, *, version: str | None = None) -> MetLabel:
        with self.snapshots.snapshot() as snapshot:
            selected = self.repository.mission_version(snapshot.connection, at=at, version=version)
        return met_label(at, selected)

    def label(self, at: datetime, *, version: str | None = None) -> tuple[str, int]:
        value = self.met(at, version=version)
        return value.day_label, value.mission_day

    def mission_day(self, at: datetime, *, version: str | None = None) -> int:
        return self.met(at, version=version).mission_day

    def day_bounds(self, mission_day: int, *, version: str) -> tuple[datetime, datetime]:
        with self.snapshots.snapshot() as snapshot:
            selected = self.repository.mission_version(snapshot.connection, at=snapshot.now, version=version)
        return bounds(mission_day, selected)

    def append(self, uow: UnitOfWork, value: MissionClockVersion, *, ids: IdFactory) -> None:
        if uow.actor.kind != "operator":
            raise ClockAuthorityDenied()
        self.repository.append_mission_version(uow.connection, value)
        reference = ids.derived("missionclock", value.clock_version)
        payload = canonical_json(
            {
                "clock_version": value.clock_version,
                "generation": value.generation,
                "epoch": instant(value.epoch),
                "effective_from": instant(value.effective_from),
                "effective_until": None if value.effective_until is None else instant(value.effective_until),
                "day_label_template": value.day_label_template,
            }
        )
        uow.replicate(
            ReplicationChange(
                None,
                "clock.mission_version",
                reference,
                "append",
                payload,
                (),
                uow.release.release_id,
                Audience("operator", reference, AudienceProjectionKey("clock", "history", 1), "1"),
            )
        )
        uow.audit(
            AuditEntry(
                ids.new("audit"),
                "clock.mission_version",
                1,
                uow.actor,
                {"clock_record": reference},
                uow.correlation_id,
                None,
                {},
            )
        )
