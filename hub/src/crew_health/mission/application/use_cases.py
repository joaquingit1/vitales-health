"""Mission commands UC-M1 to UC-M5 (E23 4.1) on the kernel unit of work.

Each command runs inside the caller's unit of work. Idempotency, receipts, HTTP and the SQLite store are the
platform's (E01 T2); the store here is a protocol. The in-transaction bus publish of EnvironmentActivated,
OverlayChanged, MissionEventRecorded and StateFactChanged is held under R-E23-5: only outbox events, audit,
scheduling and the explicit ActivationParticipant call are made.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel

from crew_health.contracts_gen.enums import EventType
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    EventScope,
    OutboxEvent,
    ReplicationChange,
    UnitOfWork,
)
from crew_health.mission.application.ports import (
    ActivationParticipant,
    MissionEventRecorded,
    OriginMode,
    StateFactChanged,
)
from crew_health.mission.application.queries import (
    MissionIdentity,
    MissionReader,
    event_model,
    overlay_model,
    profile_model,
)
from crew_health.mission.domain import events as event_rules
from crew_health.mission.domain import overlay as overlay_rules
from crew_health.mission.domain import profile as profile_rules
from crew_health.mission.domain import state_facts as fact_rules
from crew_health.mission.domain.config import MissionConfig
from crew_health.mission.domain.errors import (
    InvalidValue,
    NotFound,
    NotInitialized,
    StaleVersion,
    UnauthorizedScope,
)
from crew_health.mission.domain.events import EventRecord, TimeState
from crew_health.mission.domain.overlay import OverlayRecord
from crew_health.mission.domain.profile import ActorRef, ProfileLists, ProfileRecord
from crew_health.mission.domain.state_facts import FactState

ACTIVATE_HANDLER = "mission.activate_profile"
POST_EVA_HANDLER = "mission.end_post_eva"
PROPOSERS = frozenset({"operator", "ground", "crew_medical_officer"})  # proposeEnvironmentChange x-roles
OVERLAY_ENDERS = frozenset({"operator", "crew_medical_officer"})  # any overlay, beyond the owner's own
Scope = Literal["person", "habitat", "crew", "ground", "operator"]


class MissionStore(MissionReader, Protocol):
    """Write side of the owned tables. Implemented by the SQLite adapter (E01 T2)."""

    def create_identity(self, uow: UnitOfWork, identity: MissionIdentity) -> None: ...
    def save_profile(self, uow: UnitOfWork, record: ProfileRecord) -> None: ...
    def save_overlay(self, uow: UnitOfWork, record: OverlayRecord) -> None: ...
    def add_event(self, uow: UnitOfWork, record: EventRecord) -> None: ...
    def save_fact(self, uow: UnitOfWork, fact: FactState) -> None: ...


@dataclass(frozen=True, slots=True)
class Caller:
    """The authenticated session, never the request body (I-M6)."""

    actor: Actor
    person_id: str | None
    roles: frozenset[str]

    @property
    def is_crew(self) -> bool:
        return "crew" in self.roles


@dataclass(frozen=True, slots=True)
class EventTimeFacts:
    """S5.2 normalization of the event's occurred_at, computed at the boundary and passed in."""

    state: TimeState
    effective_at: datetime | None


@dataclass(frozen=True, slots=True)
class Services:
    store: MissionStore
    ids: IdFactory
    config_of: Callable[[ReleaseHandle], MissionConfig]
    lists_of: Callable[[UnitOfWork, MissionConfig, str], ProfileLists]
    roster: Callable[[UnitOfWork], Collection[str]]
    participants: Callable[[], Sequence[ActivationParticipant]]
    day_end: Callable[[datetime], datetime]


def actor_ref(actor: Actor) -> ActorRef:
    return ActorRef(actor.kind, actor.id)


def audience(object_ref: str, name: str) -> Audience:
    return Audience("mission.local", object_ref, AudienceProjectionKey("mission", name, 1), "1")


def emit(
    uow: UnitOfWork,
    kind: EventType,
    scopes: Sequence[Scope],
    object_ref: str,
    payload: BaseModel,
    *,
    person_id: str | None = None,
    environment_id: str | None = None,
) -> None:
    """One outbox event per C18 scope. The payload is a generated model; private notes are never in it."""
    data = json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()
    for scope in scopes:
        uow.emit(
            OutboxEvent(
                kind,
                uow.now,
                EventScope(scope, person_id if scope == "person" else None, environment_id),
                uow.release.release_id,
                uow.correlation_id,
                data,
                audience(object_ref, kind.value),
            )
        )


def note_audit(uow: UnitOfWork, ids: IdFactory, event_type: str, refs: dict[str, str]) -> None:
    uow.audit(AuditEntry(ids.new("aud"), event_type, 1, uow.actor, refs, uow.correlation_id, None, {}))


def replicate_local(
    uow: UnitOfWork, record_type: str, record_id: str, operation: Literal["create", "version", "append"]
) -> None:
    """Local-only accounting (S5.7 export_projection=None): references only, never fields, notes or person IDs."""
    payload = json.dumps({"id": record_id}, sort_keys=True).encode()
    uow.replicate(
        ReplicationChange(
            None,
            record_type,
            record_id,
            operation,
            payload,
            (),
            uow.release.release_id,
            audience(record_id, record_type),
        )
    )


def put_profile(services: Services, uow: UnitOfWork, record: ProfileRecord) -> None:
    services.store.save_profile(uow, record)
    replicate_local(
        uow, "mission_environment", record.profile_id, "create" if record.revision == 1 else "version"
    )


def put_overlay(services: Services, uow: UnitOfWork, record: OverlayRecord) -> None:
    services.store.save_overlay(uow, record)
    replicate_local(
        uow, "mission_overlay", record.overlay_id, "create" if record.revision == 1 else "version"
    )


def put_event(services: Services, uow: UnitOfWork, record: EventRecord) -> None:
    services.store.add_event(uow, record)
    replicate_local(uow, "mission_event", record.event_id, "append")


def put_fact(services: Services, uow: UnitOfWork, fact: FactState) -> None:
    services.store.save_fact(uow, fact)
    uow.publish(
        StateFactChanged(
            fact.fact,
            fact.value,
            fact.environment_id,
            fact.cause_at,
            fact.cause_event_id,
            fact.origin_mode,  # type: ignore[arg-type]
        )
    )
    replicate_local(uow, "mission_state_fact", fact.fact + ":" + fact.environment_id, "version")


def _identity(services: Services, uow: UnitOfWork) -> MissionIdentity:
    identity = services.store.identity(uow)
    if identity is None:
        raise NotInitialized()
    return identity


def _active(services: Services, uow: UnitOfWork) -> ProfileRecord:
    records = services.store.profiles(uow)
    profile_rules.assert_single_active(records)
    return next(r for r in records if r.state == "active")


def _open(services: Services, uow: UnitOfWork) -> ProfileRecord | None:
    return next((r for r in services.store.profiles(uow) if r.state == "proposed"), None)


# --- UC-M1 to UC-M3 ---------------------------------------------------------------------------------------


def propose_change(
    services: Services,
    uow: UnitOfWork,
    caller: Caller,
    *,
    environment_id: str,
    reason: profile_rules.Reason,
    effective_at: datetime | None,
) -> ProfileRecord:
    if not caller.roles & PROPOSERS:
        raise UnauthorizedScope("role may not propose an environment change")
    identity = _identity(services, uow)
    config = services.config_of(uow.release)
    profile = config.profile(environment_id)
    if profile is None:
        raise InvalidValue("no profile for the environment")
    record = profile_rules.propose(
        profile_id=services.ids.new("env"),
        template=profile.template,
        reason=reason,
        effective_at=effective_at,
        proposed_by=actor_ref(caller.actor),
        now=uow.now,
        mission_mode=identity.mission_mode,
        active=_active(services, uow),
        open_proposal=_open(services, uow),
        confirmations_required=config.confirmations_required,
        release_id=uow.release.release_id,
        lists=services.lists_of(uow, config, environment_id),
    )
    put_profile(services, uow, record)
    emit(
        uow,
        EventType.ENVIRONMENT_CHANGE_PROPOSED,
        ("crew", "operator"),
        record.profile_id,
        profile_model(record, is_crew=False, person_id=None),
    )
    return record


def act_on_change(
    services: Services,
    uow: UnitOfWork,
    caller: Caller,
    *,
    profile_id: str,
    action: Literal["confirm", "reject"],
    expected_revision: int,
) -> ProfileRecord:
    if not caller.is_crew or caller.person_id is None:
        raise UnauthorizedScope("only a crew session with a person may confirm or reject")
    _identity(services, uow)
    record = services.store.profile(uow, profile_id)
    if record is None:
        raise NotFound(profile_id)
    profile_rules.require_revision(record, expected_revision)
    if action == "reject":
        rejected = profile_rules.reject(record, actor_ref(caller.actor))
        put_profile(services, uow, rejected)
        emit(
            uow,
            EventType.ENVIRONMENT_CHANGE_REJECTED,
            ("crew", "operator"),
            profile_id,
            profile_model(rejected, is_crew=False, person_id=None),
        )
        return rejected
    confirmed = profile_rules.confirm(record, caller.person_id, uow.now)
    put_profile(services, uow, confirmed)
    if not profile_rules.due(confirmed, uow.now):
        _schedule_if_waiting(uow, confirmed)
        return confirmed
    return activate(services, uow, profile_id)


def _schedule_if_waiting(uow: UnitOfWork, record: ProfileRecord) -> None:
    """The count is reached but effective_at is later: an absolute deadline, never shifted by a clock fix."""
    if len(record.confirmations) >= record.confirmations_required and record.effective_at is not None:
        uow.schedule(
            ScheduledJob(
                "mission.activate." + record.profile_id,
                ACTIVATE_HANDLER,
                AbsoluteDeadline(record.effective_at),
                {"profile_id": record.profile_id},
                None,
            ),
            expected_generation=None,
        )


def activate(services: Services, uow: UnitOfWork, profile_id: str) -> ProfileRecord:
    """E23 3.3 activation: supersede and activate, recompute the lists from the active release, emit the
    outbox event first, then call the sensors participant after every in-transaction handler."""
    record = services.store.profile(uow, profile_id)
    if record is None:
        raise NotFound(profile_id)
    config = services.config_of(uow.release)
    superseded, activated = profile_rules.activate(
        _active(services, uow),
        record,
        now=uow.now,
        release_id=uow.release.release_id,
        lists=services.lists_of(uow, config, record.environment_id),
    )
    put_profile(services, uow, superseded)
    put_profile(services, uow, activated)
    model = profile_model(activated, is_crew=False, person_id=None)
    emit(
        uow,
        EventType.ENVIRONMENT_ACTIVATED,
        ("crew", "ground", "operator"),
        profile_id,
        model,
        environment_id=activated.environment_id,
    )
    note_audit(
        uow,
        services.ids,
        "mission.environment_activated",
        {"profile_id": profile_id, "superseded_profile_id": superseded.profile_id},
    )
    for participant in services.participants():
        participant.on_activated(uow, model)
    return activated


def activate_due(services: Services, uow: UnitOfWork, job: ScheduledJob) -> None:
    """Scheduled handler. A stale or repeated job does nothing; only a still due proposal activates."""
    record = services.store.profile(uow, job.payload["profile_id"])
    if record is not None and profile_rules.due(record, uow.now):
        activate(services, uow, record.profile_id)


# --- UC-M4 overlays ---------------------------------------------------------------------------------------


def _person_overlays(services: Services, uow: UnitOfWork, person_id: str) -> Sequence[OverlayRecord]:
    return services.store.overlays_of(uow, (person_id,))


def _emit_overlay(uow: UnitOfWork, record: OverlayRecord) -> None:
    emit(
        uow,
        EventType.OVERLAY_CHANGED,
        ("person", "crew"),
        record.overlay_id,
        overlay_model(record),
        person_id=record.person_id,
    )


def _schedule_post_eva(services: Services, uow: UnitOfWork, record: OverlayRecord) -> None:
    if record.kind == overlay_rules.POST_EVA and record.started_at is not None:
        ends = overlay_rules.post_eva_end(services.day_end(record.started_at))
        uow.schedule(
            ScheduledJob(
                "mission.post_eva." + record.overlay_id,
                POST_EVA_HANDLER,
                AbsoluteDeadline(ends),
                {"overlay_id": record.overlay_id},
                None,
            ),
            expected_generation=None,
        )


def create_overlay(
    services: Services,
    uow: UnitOfWork,
    caller: Caller,
    *,
    kind: str,
    planned_start: datetime | None,
    started_at: datetime | None,
    location: str | None,
) -> OverlayRecord:
    if caller.person_id is None:
        raise InvalidValue("an overlay belongs to a person: no direct operator creation (R-E23-7)")
    identity_active = _active(services, uow)
    config = services.config_of(uow.release)
    profile = config.profile(identity_active.environment_id)
    if profile is None:
        raise InvalidValue("no profile for the active environment")
    owned = _person_overlays(services, uow, caller.person_id)
    record = overlay_rules.create(
        overlay_id=services.ids.new("ovl"),
        person_id=caller.person_id,
        kind=kind,
        kinds_in_profile=profile.overlays,
        planned_start=planned_start,
        started_at=started_at,
        location=location,
        source_kind="person",
        source_ref=None,
        active_kinds=set(overlay_rules.by_kind(owned)),
        release_id=uow.release.release_id,
    )
    put_overlay(services, uow, record)
    _schedule_post_eva(services, uow, record)
    _emit_overlay(uow, record)
    return record


def end_overlay(
    services: Services,
    uow: UnitOfWork,
    caller: Caller,
    *,
    overlay_id: str,
    ended_at: datetime,
    expected_revision: int,
) -> OverlayRecord:
    record = services.store.overlay(uow, overlay_id)
    if record is None:
        raise NotFound(overlay_id)
    if record.person_id != caller.person_id and not caller.roles & OVERLAY_ENDERS:
        raise UnauthorizedScope("not your overlay")
    if record.revision != expected_revision:
        raise StaleVersion(overlay_id)
    ended = overlay_rules.end(record, ended_at)  # an end uses the overlay's own pinned definition (I-O5)
    put_overlay(services, uow, ended)
    _emit_overlay(uow, ended)
    return ended


def end_post_eva(services: Services, uow: UnitOfWork, job: ScheduledJob) -> None:
    """Absolute day-end deadline: ends the post_eva overlay at that instant if it is still active."""
    record = services.store.overlay(uow, job.payload["overlay_id"])
    if record is not None and record.state == "active":
        ended = overlay_rules.end(record, services.day_end(record.started_at or uow.now))
        put_overlay(services, uow, ended)
        _emit_overlay(uow, ended)


# --- UC-M5 mission events ---------------------------------------------------------------------------------


def _last_effect(records: Sequence[OverlayRecord], kind: str) -> tuple[datetime | None, str | None]:
    latest: tuple[datetime | None, str | None] = (None, None)
    for r in records:
        if r.kind != kind or r.last_event_at is None:
            continue
        if latest[0] is None or (r.last_event_at, r.last_event_id or "") > (latest[0], latest[1] or ""):
            latest = (r.last_event_at, r.last_event_id)
    return latest


def _apply_overlay_effects(
    services: Services,
    uow: UnitOfWork,
    event: EventRecord,
    kinds: tuple[str | None, str | None],
    release_id: str,
    at: datetime,
) -> None:
    starts, ends = kinds
    for person_id in event.person_ids:
        owned = _person_overlays(services, uow, person_id)
        active = overlay_rules.by_kind(owned)
        touched = [k for k in (starts, ends) if k is not None]
        if not all(
            overlay_rules.event_is_newer(*_last_effect(owned, k), at, event.event_id) for k in touched
        ):
            continue  # an older event changes nothing; it stays recorded
        effects, skipped = overlay_rules.plan_event_effects(starts, ends, set(active))
        if skipped:
            conflicting = next((active[k].overlay_id for k in touched if k in active), "none")
            note_audit(
                uow,
                services.ids,
                "mission.overlay_effect_skipped",
                {"event_id": event.event_id, "overlay_id": conflicting},
            )
            continue
        for effect in effects:
            _apply_effect(services, uow, event, person_id, effect, active, release_id, at)


def _apply_effect(
    services: Services,
    uow: UnitOfWork,
    event: EventRecord,
    person_id: str,
    effect: overlay_rules.OverlayEffect,
    active: Mapping[str, OverlayRecord],
    release_id: str,
    at: datetime,
) -> None:
    current = active.get(effect.kind)
    if effect.action == "end" and current is not None:
        record = replace(overlay_rules.end(current, at), last_event_at=at, last_event_id=event.event_id)
    else:
        record = replace(
            overlay_rules.create(
                overlay_id=services.ids.new("ovl"),
                person_id=person_id,
                kind=effect.kind,
                kinds_in_profile={effect.kind},
                planned_start=None,
                started_at=at,
                location=event.location,
                source_kind="mission_event",
                source_ref=event.event_id,
                active_kinds=(),
                release_id=release_id,
            ),
            last_event_at=at,
            last_event_id=event.event_id,
        )
    put_overlay(services, uow, record)
    _schedule_post_eva(services, uow, record)
    _emit_overlay(uow, record)


def record_event(
    services: Services,
    uow: UnitOfWork,
    caller: Caller,
    *,
    event_type: str,
    occurred_at: datetime,
    person_ids: Sequence[str],
    location: str | None,
    note: str | None,
    origin_mode: OriginMode,
    time: EventTimeFacts,
) -> EventRecord:
    """Check, store (append only), then apply overlay and fact effects only for a trusted newer time."""
    identity = _identity(services, uow)
    config = services.config_of(uow.release)
    facts = event_rules.check_record(
        event_type=event_type,
        types=config.event_types,
        roles=caller.roles,
        mission_mode=identity.mission_mode,
        body_origin=origin_mode,
        person_ids=person_ids,
        roster=services.roster(uow),
    )
    active = _active(services, uow)
    record = EventRecord(
        services.ids.new("mce"),
        event_type,
        occurred_at,
        uow.now,
        caller.actor.kind,
        caller.actor.id,
        origin_mode,
        active.environment_id,
        tuple(person_ids),
        location,
        time.state,
        time.effective_at,
        note,
    )
    put_event(services, uow, record)
    if record.orders_state and time.effective_at is not None:
        _apply_overlay_effects(
            services,
            uow,
            record,
            (facts.starts_overlay, facts.ends_overlay),
            uow.release.release_id,
            time.effective_at,
        )
        for changed in fact_rules.apply_event(
            config.state_facts,
            services.store.fact_states(uow),
            event_type=event_type,
            environment_id=active.environment_id,
            at=time.effective_at,
            event_id=record.event_id,
            origin_mode=origin_mode,
        ):
            put_fact(services, uow, changed)
    kind = config.event_type(event_type)
    uow.publish(
        MissionEventRecorded(
            record.event_id,
            event_type,
            event_type if kind is None else kind.label,
            occurred_at,
            time.effective_at,
            record.recorded_at,
            origin_mode,
            record.environment_id,
            tuple(person_ids),
            location,
        )
    )
    emit(
        uow,
        EventType.MISSION_EVENT_RECORDED,
        ("crew", "operator"),
        record.event_id,
        event_model(replace(record, note=None)),
    )  # the note never leaves its owning store
    return record
