"""E02 4 consent commands and queries.

Grant and revoke run in the caller's unit of work with reference-only audit. A finite grant schedules its
absolute expiry in the same unit of work. Queries read only the caller's snapshot and deny at or after
expiry even before the scheduler runs. A clock correction or regression fences every finite grant in the
clock's own unit of work, and startup fences grants issued by an earlier process.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from crew_health.consent.application.ports import View
from crew_health.consent.application.store import ConsentStore
from crew_health.consent.domain.grants import (
    POLICY_VERSION,
    Grant,
    GrantRequest,
    Issue,
    Reading,
    allowed_fields,
    ended,
    grants_now,
    invalid,
    owner,
    refused,
    validate_request,
)
from crew_health.identity.application.ports import PrincipalDirectory, RosterPort
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


@dataclass(frozen=True, slots=True)
class ConsentLimits:
    active_per_subject_max: int
    fields_max: int

    def __post_init__(self) -> None:
        if not 1 <= self.active_per_subject_max <= 256 or not 1 <= self.fields_max <= 256:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)


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


class ConsentService:
    def __init__(self, deps: ConsentDeps) -> None:
        self.deps = deps

    # Queries (ConsentQueries) ---------------------------------------------------------------------------

    def allows(self, view: View, *, subject_person_id: str, recipient: str, scope: tuple[str, str]) -> bool:
        return any(
            grants_now(grant, self._reading(view))
            for grant in self._candidates(view, subject_person_id, recipient, scope)
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
    ) -> frozenset[str]:
        reading = self._reading(view)
        released: frozenset[str] = frozenset()
        for grant in self._candidates(view, subject_person_id, recipient, scope):
            released |= allowed_fields(grant, reading, action=action, requested=requested)
        return released

    def _candidates(
        self, view: View, subject_person_id: str, recipient: str, scope: tuple[str, str]
    ) -> tuple[Grant, ...]:
        purpose, object_ref = scope
        if not subject_person_id or not recipient or not purpose or not object_ref:
            return ()
        if self.deps.roster.member(view, subject_person_id) is None:
            return ()
        if not self.deps.directory.known_recipient(view, recipient):
            return ()
        return self.deps.store.matching(
            view.connection,
            subject_person_id=subject_person_id,
            recipient=recipient,
            purpose=purpose,
            object_ref=object_ref,
            limit=self.deps.limits.active_per_subject_max,
        )

    def _reading(self, view: View) -> Reading:
        clock = self.deps.clock
        return Reading(view.now, clock.process_epoch(), clock.monotonic_ns())

    # Owner commands -------------------------------------------------------------------------------------

    def grant(self, session: SessionContext, request: GrantRequest, *, correlation_id: str) -> Grant:
        deps = self.deps
        subject = owner(session)
        with deps.uows.begin(actor=session.actor, correlation_id=correlation_id) as uow:
            value = validate_request(session, request, now=uow.now, fields_max=deps.limits.fields_max)
            if deps.roster.member(uow, subject) is None:
                raise refused()
            if not deps.directory.known_recipient(uow, value.recipient):
                raise invalid("recipient")
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
        self, session: SessionContext, grant_id: str, *, expected_revision: int, correlation_id: str
    ) -> Grant:
        """Revocation is final for a grant ID and is always available, even at capacity."""
        deps = self.deps
        subject = owner(session)
        with deps.uows.begin(actor=session.actor, correlation_id=correlation_id) as uow:
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
            self._audit(
                uow, "consent.ended", "consent.grant", grant_id, {"state": "revoked"}, operation="version"
            )
        return revoked

    def own_grants(self, session: SessionContext) -> tuple[Grant, ...]:
        subject = owner(session)
        with self.deps.snapshots.snapshot() as view:
            return self.deps.store.for_subject(
                view.connection, subject, limit=self.deps.limits.active_per_subject_max * 4
            )

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
