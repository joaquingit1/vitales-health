"""E02 4 consent commands and queries.

Grant and revoke run in the caller's unit of work with reference-only audit. A finite grant schedules its
absolute expiry in the same unit of work. Queries read only the caller's snapshot and deny at or after
expiry even before the scheduler runs. A clock correction or regression fences every finite grant in the
clock's own unit of work, and startup fences grants issued by an earlier process.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from crew_health.consent.application.ports import (
    ObservationPartitions,
    Partition,
    TimeAdmission,
    View,
)
from crew_health.consent.application.store import ConsentStore
from crew_health.consent.domain.grants import (
    POLICY_VERSION,
    Admission,
    Grant,
    GrantRequest,
    Issue,
    Reading,
    allowed_fields,
    ended,
    grants_now,
    invalid,
    matches,
    owner,
    refused,
    validate_request,
)
from crew_health.identity.application.ports import (
    AccessGenerations,
    OwnerProof,
    OwnerVerification,
    PrincipalDirectory,
    RosterPort,
    SessionReader,
)
from crew_health.kernel.audit import AuditEntry
from crew_health.kernel.clock import Clock, ClockCorrected, ClockRegressed
from crew_health.kernel.commands import canonical_json
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.scheduler import AbsoluteDeadline, ScheduledJob
from crew_health.kernel.uow import (
    Actor,
    Audience,
    AudienceProjectionKey,
    ReadSnapshotFactory,
    ReplicationChange,
    SessionContext,
    UnitOfWork,
    UnitOfWorkFactory,
)

EXPIRY_HANDLER = "consent.expire"
RECORD_PURPOSE = "records.read"
"""The purpose of a grant over one stored observation; its object facts come from ObservationPartitions."""
LOCAL_AUDIENCE = "consent.local"
_PROJECTION = AudienceProjectionKey("consent", "reference", 1)
_STARTUP_ACTOR = Actor("engine", "consent_startup")
STATE_VALUES: frozenset[str | int | bool] = frozenset({"revoked", "expired", "fenced"})


@dataclass(frozen=True, slots=True)
class ConsentAuditDeclaration:
    event_type: str
    reference_field: str
    reference_type: str
    metadata: Mapping[str, frozenset[str | int | bool]]


AUDIT_DECLARATIONS: tuple[ConsentAuditDeclaration, ...] = (
    ConsentAuditDeclaration("consent.granted", "grant", "consent.grant", {}),
    ConsentAuditDeclaration("consent.ended", "grant", "consent.grant", {"state": STATE_VALUES}),
    ConsentAuditDeclaration("consent.fenced", "fence", "consent.fence", {}),
)


ACTIVE_CEILING = 256
"""Upper bound of consent.active_per_subject_max: no subject ever holds more active grants than this."""
FIELDS_CEILING = 256


@dataclass(frozen=True, slots=True)
class ConsentLimits:
    active_per_subject_max: int
    fields_max: int

    def __post_init__(self) -> None:
        if (
            not 1 <= self.active_per_subject_max <= ACTIVE_CEILING
            or not 1 <= self.fields_max <= FIELDS_CEILING
        ):
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)


@dataclass(frozen=True, slots=True)
class OwnGrants:
    """Every active grant of the owner, then the newest ended ones; `older` says earlier ended grants exist."""

    grants: tuple[Grant, ...]
    older: bool


def _complete(grants: tuple[Grant, ...]) -> tuple[Grant, ...]:
    """Active grants are read whole: more than the ceiling is corrupt state, never a truncated answer."""
    if len(grants) > ACTIVE_CEILING:
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
    return grants


@dataclass(slots=True)
class ConsentBindings:
    """Composer facts bound late (S5.12): the durable clock admission and stored observation facts. Each one
    absent fails closed: finite grants authorize nothing and no record grant is made or matched."""

    time: TimeAdmission | None = None
    observations: ObservationPartitions | None = None


class ObservedWall:
    """The latest wall time this process has read for a consent decision. Process memory, never a snapshot
    write: an expiry one read observed stays observed for every later read of this process epoch, and finite
    grants never outlive the epoch that issued them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: datetime | None = None

    def observe(self, at: datetime) -> datetime:
        with self._lock:
            if self._latest is None or at > self._latest:
                self._latest = at
            return self._latest


type Owner = OwnerProof | SessionContext
"""A credential proof for a local owner command, or a live crew session."""


@dataclass(frozen=True, slots=True)
class ConsentDeps:
    uows: UnitOfWorkFactory
    snapshots: ReadSnapshotFactory
    clock: Clock
    ids: IdFactory
    store: ConsentStore
    roster: RosterPort
    directory: PrincipalDirectory
    limits: ConsentLimits
    sessions: SessionReader
    owners: OwnerVerification
    generations: AccessGenerations
    bound: ConsentBindings = field(default_factory=ConsentBindings)
    observed: ObservedWall = field(default_factory=ObservedWall)


class ConsentService:
    def __init__(self, deps: ConsentDeps) -> None:
        self.deps = deps

    # Queries (ConsentQueries) ---------------------------------------------------------------------------

    def allows(
        self,
        view: View,
        *,
        subject_person_id: str,
        recipient: str,
        scope: tuple[str, str],
        partition: Partition | None = None,
    ) -> bool:
        reading = self._reading(view)
        return any(
            grants_now(grant, reading)
            for grant in self._candidates(view, subject_person_id, recipient, scope, partition)
        )

    def fields(
        self,
        view: View,
        *,
        subject_person_id: str,
        recipient: str,
        scope: tuple[str, str],
        action: str,
        requested: frozenset[str],
        partition: Partition | None = None,
    ) -> frozenset[str]:
        reading = self._reading(view)
        released: frozenset[str] = frozenset()
        for grant in self._candidates(view, subject_person_id, recipient, scope, partition):
            released |= allowed_fields(grant, reading, action=action, requested=requested)
        return released

    def _candidates(
        self,
        view: View,
        subject_person_id: str,
        recipient: str,
        scope: tuple[str, str],
        partition: Partition | None,
    ) -> tuple[Grant, ...]:
        purpose, object_ref = scope
        if not subject_person_id or not recipient or not purpose or not object_ref:
            return ()
        target = self.deps.roster.partition(view) if partition is None else partition
        if target is None:
            return ()
        if self.deps.roster.member(view, subject_person_id) is None:
            return ()
        if not self.deps.directory.known_recipient(view, recipient):
            return ()
        found = _complete(
            self.deps.store.matching(
                view.connection,
                subject_person_id=subject_person_id,
                recipient=recipient,
                purpose=purpose,
                object_ref=object_ref,
                mission_id=target[0],
                origin_mode=target[1],
                limit=ACTIVE_CEILING + 1,
            )
        )
        return tuple(
            grant
            for grant in found
            if matches(
                grant,
                subject_person_id=subject_person_id,
                recipient=recipient,
                purpose=purpose,
                object_ref=object_ref,
                partition=target,
            )
        )

    def _reading(self, view: View) -> Reading:
        clock, time = self.deps.clock, self.deps.bound.time
        state = None if time is None else time.admission(view)
        admission = None if state is None else Admission(state.high_water, state.frozen)
        return Reading(
            view.now,
            clock.process_epoch(),
            clock.monotonic_ns(),
            admission,
            self.deps.observed.observe(view.now),
        )

    # Owner proof ---------------------------------------------------------------------------------------

    def _owner_session(self, value: Owner) -> SessionContext:
        return value.session if isinstance(value, OwnerProof) else value

    def _require_owner(self, view: View, value: Owner) -> str:
        """The owner is rechecked in the command's own view: a credential changed, a member removed or a
        session ended while the command waited refuses it (E02 4)."""
        subject = owner(self._owner_session(value))
        current = (
            self.deps.owners.owner_current(view, value)
            if isinstance(value, OwnerProof)
            else self.deps.sessions.alive(view, value)
        )
        if not current:
            raise refused()
        return subject

    def _require_object(self, view: View, subject: str, value: GrantRequest) -> None:
        """A grant is validated against the authoritative facts of its object: a stored observation of the
        subject for a record grant, else the hub roster's mission and origin (E02 3.3)."""
        terms = (value.mission_id, value.origin_mode)
        if value.purpose == RECORD_PURPOSE:
            records = self.deps.bound.observations
            facts = None if records is None else records.observation(view, value.object_ref)
            if facts is None or facts.patient_id != subject or (facts.mission_id, facts.origin_mode) != terms:
                raise invalid("object_ref")
        elif self.deps.roster.partition(view) != terms:
            raise invalid("mission_id")

    # Owner commands -------------------------------------------------------------------------------------

    def grant(self, owner_value: Owner, request: GrantRequest, *, correlation_id: str) -> Grant:
        deps = self.deps
        session = self._owner_session(owner_value)
        owner(session)
        with deps.uows.begin(actor=session.actor, correlation_id=correlation_id) as uow:
            subject = self._require_owner(uow, owner_value)
            value = validate_request(session, request, now=uow.now, fields_max=deps.limits.fields_max)
            if deps.roster.member(uow, subject) is None:
                raise refused()
            if not deps.directory.known_recipient(uow, value.recipient):
                raise invalid("recipient")
            self._require_object(uow, subject, value)
            if deps.store.active_count(uow.connection, subject) >= deps.limits.active_per_subject_max:
                raise ProblemError(ErrorCode.INVALID_TRANSITION, ErrorCode.INVALID_TRANSITION.value)
            grant_id = deps.ids.new("cns")
            generation = None
            if value.valid_until is not None:
                generation = uow.schedule(
                    ScheduledJob(
                        "consent.expiry." + grant_id,
                        EXPIRY_HANDLER,
                        AbsoluteDeadline(value.valid_until),
                        {"grant_id": grant_id, "revision": "1"},
                        None,
                    ),
                    expected_generation=None,
                )
            issue = Issue(uow.now, deps.clock.process_epoch(), deps.clock.monotonic_ns())
            grant = Grant(
                grant_id,
                1,
                session.actor.id,
                subject,
                value,
                POLICY_VERSION,
                issue,
                generation,
                "active",
                None,
            )
            deps.store.insert(uow.connection, grant)
            self._audit(uow, "consent.granted", "consent.grant", grant_id, {}, operation="create")
        return grant

    def revoke(
        self, owner_value: Owner, grant_id: str, *, expected_revision: int, correlation_id: str
    ) -> Grant:
        """Revocation is final for a grant ID and is always available, even at capacity. It moves the access
        generation in the same unit of work, so buffered deliveries stamped before it are discarded."""
        deps = self.deps
        session = self._owner_session(owner_value)
        owner(session)
        with deps.uows.begin(actor=session.actor, correlation_id=correlation_id) as uow:
            subject = self._require_owner(uow, owner_value)
            grant = deps.store.grant(uow.connection, grant_id)
            if grant is None or grant.subject_person_id != subject:
                raise ProblemError(ErrorCode.NOT_FOUND, ErrorCode.NOT_FOUND.value)
            if grant.revision != expected_revision:
                raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
            revoked = ended(grant, "revoked", uow.now)
            if grant.schedule_generation is not None:
                uow.cancel_schedule(
                    "consent.expiry." + grant_id, expected_generation=grant.schedule_generation
                )
            if not deps.store.save_terminal(uow.connection, revoked, expected_revision=grant.revision):
                raise ProblemError(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)
            deps.generations.bump(uow)
            self._audit(
                uow, "consent.ended", "consent.grant", grant_id, {"state": "revoked"}, operation="version"
            )
        return revoked

    def own_grants(self, owner_value: Owner) -> OwnGrants:
        """All of the owner's active grants and one page of the newest ended ones, newest first."""
        page = self.deps.limits.active_per_subject_max
        store = self.deps.store
        with self.deps.snapshots.snapshot() as view:
            subject = self._require_owner(view, owner_value)
            active = store.for_subject(view.connection, subject, active=True, limit=ACTIVE_CEILING + 1)
            ended = store.for_subject(view.connection, subject, active=False, limit=page + 1)
        return OwnGrants(_complete(active) + ended[:page], len(ended) > page)

    # Time ----------------------------------------------------------------------------------------------

    def expire(self, uow: UnitOfWork, job: ScheduledJob) -> None:
        """S5.10 handler in the scheduler's unit of work; a stale or duplicate callback changes nothing."""
        grant_id, revision = job.payload.get("grant_id"), job.payload.get("revision")
        if grant_id is None or revision is None or not revision.isdigit():
            return
        grant = self.deps.store.grant(uow.connection, grant_id)
        if grant is None or grant.state != "active" or grant.revision != int(revision):
            return
        until = grant.request.valid_until
        if until is None or uow.now < until:
            return
        if self.deps.store.save_terminal(
            uow.connection, ended(grant, "expired", uow.now), expected_revision=grant.revision
        ):
            self.deps.generations.bump(uow)
            self._audit(
                uow, "consent.ended", "consent.grant", grant_id, {"state": "expired"}, operation="version"
            )

    def fence_on_correction(self, uow: UnitOfWork, event: ClockCorrected) -> None:
        self._fence(uow, keep_epoch=None)

    def fence_on_regression(self, uow: UnitOfWork, event: ClockRegressed) -> None:
        self._fence(uow, keep_epoch=None)

    def fence_earlier_processes(self, *, correlation_id: str) -> int:
        """Startup (STATE_SWEEP): finite grants issued by an earlier process end before the hub serves."""
        with self.deps.uows.begin(actor=_STARTUP_ACTOR, correlation_id=correlation_id) as uow:
            return self._fence(uow, keep_epoch=self.deps.clock.process_epoch())

    def _fence(self, uow: UnitOfWork, *, keep_epoch: str | None) -> int:
        count = self.deps.store.fence_finite(uow.connection, at=uow.now, keep_epoch=keep_epoch)
        if count:
            self.deps.generations.bump(uow)
            fence_id = self.deps.ids.new("fence")
            self._audit(uow, "consent.fenced", "consent.fence", fence_id, {}, operation="create")
        return count

    def _audit(
        self,
        uow: UnitOfWork,
        event_type: str,
        record_type: str,
        record_id: str,
        metadata: Mapping[str, str | int | bool],
        *,
        operation: str,
    ) -> None:
        declaration = next(value for value in AUDIT_DECLARATIONS if value.event_type == event_type)
        uow.replicate(
            ReplicationChange(
                None,
                record_type,
                record_id,
                "create" if operation == "create" else "version",
                canonical_json({"record_id": record_id}),
                (),
                uow.release.release_id,
                Audience(LOCAL_AUDIENCE, record_id, _PROJECTION, "1"),
            )
        )
        uow.audit(
            AuditEntry(
                self.deps.ids.new("audit"),
                event_type,
                1,
                uow.actor,
                {declaration.reference_field: record_id},
                uow.correlation_id,
                None,
                metadata,
            )
        )
