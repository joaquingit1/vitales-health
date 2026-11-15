"""Environment profile state machine (E23 3.3, invariants I-M1 to I-M6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from crew_health.mission.domain.errors import (
    ConfirmationRequired,
    IntegrityViolation,
    InvalidTransition,
    InvalidValue,
    StaleVersion,
)

State = Literal["proposed", "active", "superseded", "rejected"]
Reason = Literal["mission_start", "phase_plan", "operator", "ground", "demo_switch"]
OriginMode = Literal["LIVE", "REPLAY", "SIM"]
DEMO_MODES: tuple[OriginMode, ...] = ("SIM", "REPLAY")  # I-M4


@dataclass(frozen=True, slots=True)
class ActorRef:
    kind: str
    id: str


@dataclass(frozen=True, slots=True)
class Confirmation:
    person_id: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ProfileLists:
    hazard_ids: tuple[str, ...]
    limit_ids: tuple[str, ...]
    warning_definition_ids: tuple[str, ...]
    sensor_type_ids: tuple[str, ...]
    sensor_channel_ids: tuple[str, ...]
    routine_item_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileTemplate:
    """What a profile of one environment says, read from the active release (never from code)."""

    environment_id: str
    label: str
    place: str
    eva_word: str
    ground_delay_text: str
    ground_delay_seconds: int


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    profile_id: str
    revision: int
    environment_id: str
    state: State
    label: str
    place: str
    eva_word: str
    reason: Reason
    proposed_by: ActorRef
    proposed_at: datetime
    effective_at: datetime | None
    confirmations_required: int
    confirmations: tuple[Confirmation, ...]
    rejected_by: ActorRef | None
    activated_at: datetime | None
    superseded_at: datetime | None
    environment_day_start: datetime | None
    config_release_id: str
    proposal_release_id: str  # I-M3: the policy pin of open work
    lists: ProfileLists
    ground_delay_text: str
    ground_delay_seconds: int


def _next(record: ProfileRecord) -> ProfileRecord:
    return replace(record, revision=record.revision + 1)


def start_profile(
    *,
    profile_id: str,
    template: ProfileTemplate,
    actor: ActorRef,
    now: datetime,
    confirmations_required: int,
    release_id: str,
    lists: ProfileLists,
) -> ProfileRecord:
    """Mission start (E23 3.3): reason mission_start, empty confirmations, active at the unit of work's now."""
    return ProfileRecord(
        profile_id,
        1,
        template.environment_id,
        "active",
        template.label,
        template.place,
        template.eva_word,
        "mission_start",
        actor,
        now,
        now,
        confirmations_required,
        (),
        None,
        now,
        None,
        now,
        release_id,
        release_id,
        lists,
        template.ground_delay_text,
        template.ground_delay_seconds,
    )


def propose(
    *,
    profile_id: str,
    template: ProfileTemplate,
    reason: Reason,
    effective_at: datetime | None,
    proposed_by: ActorRef,
    now: datetime,
    mission_mode: OriginMode,
    active: ProfileRecord,
    open_proposal: ProfileRecord | None,
    confirmations_required: int,
    release_id: str,
    lists: ProfileLists,
) -> ProfileRecord:
    """I-M4 demo_switch only in SIM or REPLAY, I-M5 not the active environment, I-M2 one open proposal.

    I-M6: `proposed_by` is the session actor chosen by the caller, never a field of the request body.
    """
    if reason == "demo_switch" and mission_mode not in DEMO_MODES:
        raise InvalidValue("demo_switch needs a SIM or REPLAY mission")
    if reason == "mission_start":
        raise InvalidValue("mission_start is created only by mission init")
    if template.environment_id == active.environment_id:
        raise InvalidValue("the environment is already active")
    if open_proposal is not None:
        raise InvalidTransition("a change is already open")
    return ProfileRecord(
        profile_id,
        1,
        template.environment_id,
        "proposed",
        template.label,
        template.place,
        template.eva_word,
        reason,
        proposed_by,
        now,
        effective_at,
        confirmations_required,
        (),
        None,
        None,
        None,
        None,
        release_id,
        release_id,
        lists,
        template.ground_delay_text,
        template.ground_delay_seconds,
    )


def confirm(record: ProfileRecord, person_id: str, at: datetime) -> ProfileRecord:
    """Count a person once. A repeat while the count is not reached is C1: CONFIRMATION_REQUIRED."""
    if record.state != "proposed":
        raise InvalidTransition("only a proposed change can be confirmed")
    if any(c.person_id == person_id for c in record.confirmations):
        raise ConfirmationRequired(record.profile_id)
    return replace(_next(record), confirmations=(*record.confirmations, Confirmation(person_id, at)))


def reject(record: ProfileRecord, by: ActorRef) -> ProfileRecord:
    if record.state != "proposed":
        raise InvalidTransition("only a proposed change can be rejected")
    return replace(_next(record), state="rejected", rejected_by=by)


def due(record: ProfileRecord, now: datetime) -> bool:
    """I-M3: the count is reached and now is at or after effective_at (when set)."""
    if record.state != "proposed" or len(record.confirmations) < record.confirmations_required:
        return False
    return record.effective_at is None or now >= record.effective_at


def activate(
    active: ProfileRecord, proposed: ProfileRecord, *, now: datetime, release_id: str, lists: ProfileLists
) -> tuple[ProfileRecord, ProfileRecord]:
    """Supersede the active record and activate the proposal at now (E23 3.3 step 1 and 2)."""
    if active.state != "active":
        raise IntegrityViolation("no active record to supersede")
    if not due(proposed, now):
        raise InvalidTransition("the change is not due")
    superseded = replace(_next(active), state="superseded", superseded_at=now)
    activated = replace(
        _next(proposed),
        state="active",
        activated_at=now,
        environment_day_start=now,
        effective_at=now if proposed.effective_at is None else proposed.effective_at,
        config_release_id=release_id,
        lists=lists,
    )
    return superseded, activated


def require_revision(record: ProfileRecord, expected: int) -> None:
    if record.revision != expected:
        raise StaleVersion(record.profile_id)


def assert_single_active(records: Sequence[ProfileRecord], *, initialized: bool = True) -> None:
    """I-M1: exactly one active record in every committed state after mission init; at most one proposed."""
    active = [r for r in records if r.state == "active"]
    proposed = [r for r in records if r.state == "proposed"]
    if initialized and len(active) != 1:
        raise IntegrityViolation(f"{len(active)} active profiles")
    if len(proposed) > 1:
        raise IntegrityViolation("more than one open proposal")


def allowed_actions(record: ProfileRecord, *, is_crew: bool, person_id: str | None) -> tuple[str, ...]:
    """`confirm` and `reject` for a proposed record to a crew session with a person; nothing otherwise."""
    if record.state != "proposed" or not is_crew or person_id is None:
        return ()
    counted = any(c.person_id == person_id for c in record.confirmations)
    return ("reject",) if counted else ("confirm", "reject")
