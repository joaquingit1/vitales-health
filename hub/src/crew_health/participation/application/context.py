"""What every use case shares: the stores and ports it works with, the active rules, the mission partition and the
audit of a participation event (ids and enumerated words only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.commands import CommandReceipts, IdempotencyStore, canonical_json
from crew_health.kernel.config import ConfigName
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.met import MissionClock
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshotFactory,
    ReplicationChange,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.participation.application.errors import no_mission
from crew_health.participation.application.ports import ParticipationRoster, RecordOrigins, View
from crew_health.participation.application.settings import Limits
from crew_health.participation.application.store import ParticipationStore
from crew_health.participation.domain.rules import ParticipationRules, parse_rules
from crew_health.participation.domain.types import ViewOrigin

ENGINE = Actor("engine", "participation")
LOCAL_AUDIENCE = "participation.local"
_PROJECTION = AudienceProjectionKey("participation", "reference", 1)


@dataclass(frozen=True, slots=True)
class Deps:
    uows: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    ids: IdFactory
    clock: MissionClock
    roster: ParticipationRoster
    origins: RecordOrigins
    commands: IdempotencyStore
    receipts: CommandReceipts
    limits: Limits
    store: ParticipationStore


def rules_of(view: View) -> ParticipationRules:
    document = view.release.document(ConfigName.PARTICIPATION_RULES)
    parsed: object = json.loads(document.raw)
    if not isinstance(parsed, dict):
        raise ValueError("participation-rules is not an object")
    return parse_rules(parsed)


def rules_version(view: View) -> str:
    return view.release.version_of(ConfigName.PARTICIPATION_RULES)


def view_origin(namespace: str) -> ViewOrigin:
    """DE22-6: LIVE for a LIVE mission, SIM otherwise. No participation view is ever REPLAY."""
    return "LIVE" if namespace == "LIVE" else "SIM"


def partition_of(deps: Deps, view: View) -> tuple[str, ViewOrigin]:
    found = deps.roster.partition(view)
    if found is None:
        raise no_mission()
    return found[0], view_origin(found[1])


def today_of(deps: Deps, at: datetime) -> tuple[int, str]:
    version = deps.clock.version_at(at).clock_version
    return deps.clock.mission_day(at, version=version), version


def reference(uow: UnitOfWork, record_type: str, record_id: str) -> None:
    """Makes `record_id` a platform reference so an audit entry may name it (SYSTEM 5.8); never visible to a session."""
    uow.replicate(
        ReplicationChange(
            None,
            record_type,
            record_id,
            "version",
            canonical_json({"record_id": record_id}),
            (),
            uow.release.release_id,
            Audience(LOCAL_AUDIENCE, record_id, _PROJECTION, "1"),
        )
    )


def audit(
    deps: Deps,
    uow: UnitOfWork,
    event_type: str,
    field: str,
    record_type: str,
    record_id: str,
    metadata: dict[str, str | int | bool] | None = None,
) -> None:
    reference(uow, record_type, record_id)
    uow.audit(
        AuditEntry(
            deps.ids.new("audit"),
            event_type,
            1,
            uow.actor,
            {field: record_id},
            uow.correlation_id,
            None,
            metadata or {},
        )
    )
