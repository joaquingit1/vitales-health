"""Dependencies of the roles use cases and the read of the world they decide on (roster, overlays, practice)."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock
from crew_health.kernel.commands import IdempotencyStore
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.storage import StorageSession
from crew_health.kernel.uow import (
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    UnitOfWork,
    UnitOfWorkFactory,
)
from crew_health.roles.application.config import ConfigCache
from crew_health.roles.application.ports import (
    DayLabelPort,
    HelpSessionReader,
    LocationPort,
    MissionFacts,
    MissionStatePort,
    OverlayPort,
    PlanRunReader,
    RosterMember,
    RosterPort,
    SupplyAboardPort,
)
from crew_health.roles.application.repository import RolesRepository
from crew_health.roles.application.settings import RolesSettings
from crew_health.roles.domain.engine import Person
from crew_health.roles.domain.errors import PortUnavailable
from crew_health.roles.domain.freshness import SkillRecord, skill_record
from crew_health.roles.domain.rules import RolesConfig

type Origin = Literal["LIVE", "SIM"]


@dataclass(frozen=True, slots=True)
class Deps:
    uow: UnitOfWorkFactory
    repository: Callable[[StorageSession], RolesRepository]
    clock: Clock
    ids: IdFactory
    settings: RolesSettings
    idempotency: IdempotencyStore
    roster: RosterPort
    overlays: OverlayPort
    mission: MissionStatePort
    day_labels: DayLabelPort
    help_sessions: HelpSessionReader
    locations: LocationPort | None = None
    plan_runs: PlanRunReader | None = None
    supplies: SupplyAboardPort | None = None
    config_reader: ConfigReader | None = None
    configs: ConfigCache | None = None

    def config_cache(self) -> ConfigCache:
        if self.configs is None:
            raise PortUnavailable("config_cache")
        return self.configs


def call[T](port: str, fn: Callable[[], T]) -> T:
    """Every consumed port call: any failure is PortUnavailable, never an answer (design 4.2)."""
    try:
        return fn()
    except PortUnavailable:
        raise
    except Exception as error:
        raise PortUnavailable(port) from error


def practice_origin(mission: MissionFacts) -> Origin:
    """The mission's practice mode: LIVE in a LIVE mission, otherwise SIM; REPLAY never creates a practice."""
    return "LIVE" if mission.mission_mode == "LIVE" else "SIM"


@dataclass(frozen=True, slots=True)
class World:
    at: datetime
    mission: MissionFacts
    roster: tuple[RosterMember, ...]
    people: tuple[Person, ...]
    locations: Mapping[str, str | None]

    def name(self, person_id: str) -> str | None:
        return next((m.display_name for m in self.roster if m.person_id == person_id), None)

    def member(self, person_id: str) -> RosterMember | None:
        return next((m for m in self.roster if m.person_id == person_id), None)


def read_mission(deps: Deps) -> MissionFacts:
    mission = call("mission", lambda: deps.mission.current())
    if mission is None:
        raise PortUnavailable("mission")
    return mission


def _location(locator: LocationPort, person_id: str, at: datetime) -> str | None:
    return call("locations", lambda: locator.location_of(person_id, at))


def read_world(deps: Deps, at: datetime) -> World:
    mission = read_mission(deps)
    roster = tuple(call("roster", lambda: tuple(deps.roster.crew(at))))
    if len(roster) > deps.settings.roster_max or len({m.person_id for m in roster}) != len(roster):
        raise PortUnavailable("roster")
    ids = [m.person_id for m in roster]
    overlays = call("overlays", lambda: deps.overlays.active(ids, at))
    locator = deps.locations
    locations: dict[str, str | None] = {}
    if locator is not None:
        for pid in ids:
            locations[pid] = _location(locator, pid, at)
    people: list[Person] = []
    for m in roster:
        active = [f for f in overlays.get(m.person_id, ()) if f.state == "active"]
        walks = [f for f in active if f.kind == "eva"]
        ends = [f.planned_end for f in walks if f.planned_end is not None]
        people.append(
            Person(
                person_id=m.person_id,
                display_name=m.display_name,
                roster_index=m.roster_index,
                officer_rank=m.medical_officer_rank,
                on_eva=bool(walks),
                asleep=any(f.kind == "sleep" for f in active),
                eva_until=max(ends) if len(ends) == len(walks) and ends else None,
            )
        )
    return World(at, mission, roster, tuple(people), locations)


def record_for(
    repo: RolesRepository,
    cfg: RolesConfig,
    person_id: str,
    skill_id: str,
    mission: MissionFacts,
    at: datetime,
) -> SkillRecord:
    skill = cfg.skills[skill_id]
    origin = practice_origin(mission)
    latest = repo.latest_practice(person_id, skill_id, mission.mission_id, origin, at)
    preflight = repo.latest_practice(person_id, skill_id, mission.mission_id, origin, at, source="preflight")
    found = [p for p in (latest, preflight) if p is not None]
    return skill_record(person_id, skill_id, found, skill.freshness, cfg.calendar, at)


def holders(
    repo: RolesRepository,
    cfg: RolesConfig,
    mission: MissionFacts,
    person_ids: Sequence[str],
    skill_ids: Collection[str],
    at: datetime,
) -> dict[str, dict[str, datetime]]:
    found: dict[str, dict[str, datetime]] = {}
    for skill_id in sorted(set(skill_ids)):
        found[skill_id] = {}
        if skill_id not in cfg.skills:
            continue
        for pid in person_ids:
            record = record_for(repo, cfg, pid, skill_id, mission, at)
            if record.is_holder and record.last is not None:
                found[skill_id][pid] = record.last.practiced_at
    return found


def label_at(deps: Deps) -> Callable[[datetime], str | None]:
    def label(instant: datetime) -> str | None:
        try:
            return deps.day_labels.inline_label_at(instant)
        except Exception:
            return None  # renders the designed unknown word

    return label


def emit(
    uow: UnitOfWork,
    event_type: EventType,
    payload: Mapping[str, object],
    *,
    scope: EventScope,
    ref: str,
    rule: str | None = None,
) -> None:
    uow.emit(
        OutboxEvent(
            type=event_type,
            occurred_at=uow.now,
            scope=scope,
            config_release_id=uow.release.release_id,
            correlation_id=uow.correlation_id,
            data_json=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            audience=Audience(
                rule=rule if rule is not None else "roles." + scope.kind,
                object_ref=ref,
                projection=AudienceProjectionKey("roles", event_type.value, 1),
                policy_version="1",
            ),
        )
    )


CREW = EventScope("crew", None, None)


def audit(
    uow: UnitOfWork,
    deps: Deps,
    event_type: str,
    refs: Mapping[str, str],
    meta: Mapping[str, str | int | bool],
) -> None:
    uow.audit(
        AuditEntry(
            event_id=deps.ids.new("aud"),
            event_type=event_type,
            event_version=1,
            actor=uow.actor,
            refs=refs,
            correlation_id=uow.correlation_id,
            causation_id=None,
            safe_metadata=meta,
        )
    )
