"""E02 4: sign in, authenticate every request, read and end the current session, list the crew.

Sign-in reserves attempt capacity in its own unit of work, verifies the credential outside the writer
and rechecks the principal before issuance. Every request reads the authoritative session row; there is
no permission cache.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from crew_health.identity.application.ports import RosterEntry, View
from crew_health.identity.application.store import Principal
from crew_health.identity.application.support import (
    IdentityDeps,
    audit,
    declaration,
    normalized_credential,
    reference,
)
from crew_health.identity.domain.limits import (
    GLOBAL_KEY,
    Bucket,
    BucketScope,
    admits,
    bucket_keys,
    fresh,
    reclaimable,
    refresh,
    refund,
    reserve,
)
from crew_health.identity.domain.routing import may_record_for
from crew_health.identity.domain.session import (
    MEDICAL_OFFICER_RANKS,
    EndReason,
    Instant,
    PrincipalKind,
    PrincipalState,
    SessionRecord,
    SignInRequest,
    actor_for,
    context_for,
    identity_mismatch,
    is_live,
    oldest_to_end,
    principal_key,
    roles_for,
    unauthenticated,
)
from crew_health.kernel.commands import CommandIdentity, StoredResult, canonical_json, command_identity
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.uow import Actor, ReadSnapshot, Role, SessionContext, UnitOfWork

SIGN_IN_ACTOR = Actor("engine", "identity_sign_in")
_TOKEN_MAX = 128


@dataclass(frozen=True, slots=True)
class SessionView:
    """Everything the Session schema needs, read in one snapshot; the token appears only at issuance."""

    record: SessionRecord
    roles: frozenset[Role]
    actor_display_name: str
    person_display_name: str | None
    token: str | None


@dataclass(frozen=True, slots=True)
class _Reservation:
    principal: Principal | None
    buckets: tuple[Bucket, ...]


class IdentityService:
    def __init__(self, deps: IdentityDeps) -> None:
        self.deps = deps

    # Sign in --------------------------------------------------------------------------------------------

    def sign_in(
        self, request: SignInRequest, credential: str, *, client: str, listener: str, correlation_id: str
    ) -> SessionView:
        kind, person_id = principal_key(request, ground_allowed=self.deps.ground.allowed)
        reservation = self._proven(kind, person_id, credential, client=client, correlation_id=correlation_id)
        return self._issue(reservation, listener=listener, correlation_id=correlation_id)

    def prove(self, principal: Principal, credential: str, *, client: str, correlation_id: str) -> None:
        """Proof of a principal's current credential for a local command, under the sign-in limits."""
        reservation = self._proven(
            principal.kind, principal.person_id, credential, client=client, correlation_id=correlation_id
        )
        proven = reservation.principal
        if proven is None or proven.principal_id != principal.principal_id:
            raise identity_mismatch()
        self._refund(reservation, correlation_id=correlation_id)

    def _proven(
        self, kind: PrincipalKind, person_id: str | None, credential: str, *, client: str, correlation_id: str
    ) -> _Reservation:
        secret = normalized_credential(credential, max_bytes=self.deps.settings.credential_max_bytes)
        reservation = self._reserve(kind, person_id, client=client, correlation_id=correlation_id)
        if (
            not self._verify(reservation, secret, correlation_id=correlation_id)
            or reservation.principal is None
        ):
            raise identity_mismatch()
        return reservation

    def verify_owner(
        self, person_id: str, credential: str, *, client: str, correlation_id: str
    ) -> SessionContext:
        """The same limits, verifier and recheck as sign-in, for a local owner command; no token is issued."""
        deps = self.deps
        kind, subject = principal_key(SignInRequest("crew", person_id), ground_allowed=False)
        reservation = self._proven(kind, subject, credential, client=client, correlation_id=correlation_id)
        principal = reservation.principal
        if principal is None:
            raise identity_mismatch()
        with deps.snapshots.snapshot() as view:
            latest = deps.store.principal(view.connection, principal.principal_id)
            member = self._member(view, latest)
        if (
            latest is None
            or member is None
            or latest.generation != principal.generation
            or not latest.enabled
        ):
            raise identity_mismatch()
        self._refund(reservation, correlation_id=correlation_id)
        return SessionContext(
            deps.ids.new("owner"),
            "crew",
            actor_for("crew", principal.principal_id),
            principal.person_id,
            roles_for("crew", member.medical_officer_rank),
        )

    def _reserve(
        self, kind: PrincipalKind, person_id: str | None, *, client: str, correlation_id: str
    ) -> _Reservation:
        deps = self.deps
        with deps.uows.begin(actor=SIGN_IN_ACTOR, correlation_id=correlation_id) as uow:
            store, connection = deps.store, uow.connection
            principal = (
                store.principal(connection, person_id)
                if person_id is not None
                else store.principal_for_kind(connection, kind)
            )
            if principal is not None and principal.kind != kind:
                principal = None
            now = deps.instant()
            keys = bucket_keys(
                principal_id=None if principal is None else principal.principal_id, client=client
            )
            wanted: tuple[tuple[BucketScope, str], ...] = (
                ("principal", keys.principal),
                ("client", keys.client),
                ("global", GLOBAL_KEY),
            )
            existing = {(b.scope, b.key): b for b in store.buckets(connection, wanted)}
            self._make_room(uow, len([key for key in wanted if key not in existing]), now)
            policy = deps.settings.attempts
            buckets = refresh(
                tuple(existing.get(key) or fresh(key[0], key[1], now) for key in wanted), policy, now
            )
            locked = not admits(buckets, policy)
            stored = buckets if locked else reserve(buckets, policy, now)
            for bucket in stored:
                store.save_bucket(connection, bucket)
        if locked:
            raise identity_mismatch()
        return _Reservation(principal, stored)

    def _make_room(self, uow: UnitOfWork, needed: int, now: Instant) -> None:
        deps, connection = self.deps, uow.connection
        if needed == 0 or deps.store.count_buckets(connection) + needed <= deps.settings.buckets_max:
            return
        for bucket in deps.store.oldest_buckets(connection, limit=deps.settings.sweep_batch):
            if reclaimable(bucket, deps.settings.attempts, now):
                deps.store.delete_bucket(connection, bucket)
        if deps.store.count_buckets(connection) + needed > deps.settings.buckets_max:
            raise identity_mismatch()

    def _verify(self, reservation: _Reservation, secret: str, *, correlation_id: str) -> bool:
        deps = self.deps
        if not deps.slots.semaphore.acquire(timeout=deps.settings.wait_ms / 1000):
            self._refund(reservation, correlation_id=correlation_id)
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)
        try:
            started = deps.clock.monotonic_ns()
            principal = reservation.principal
            usable = principal is not None and principal.enabled and principal.verifier is not None
            verifier = principal.verifier if usable and principal is not None else None
            matched = deps.hasher.verify(verifier, secret)
            elapsed_ms = (deps.clock.monotonic_ns() - started) // 1_000_000
        finally:
            deps.slots.semaphore.release()
        if elapsed_ms > deps.settings.compute_ms:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)
        return usable and matched

    def _refund(self, reservation: _Reservation, *, correlation_id: str) -> None:
        deps = self.deps
        with deps.uows.begin(actor=SIGN_IN_ACTOR, correlation_id=correlation_id) as uow:
            self._refund_in(uow, reservation)

    def _refund_in(self, uow: UnitOfWork, reservation: _Reservation) -> None:
        store, connection = self.deps.store, uow.connection
        keys = tuple((bucket.scope, bucket.key) for bucket in reservation.buckets)
        stored = {(b.scope, b.key): b for b in store.buckets(connection, keys)}
        for reserved in reservation.buckets:
            bucket = stored.get((reserved.scope, reserved.key))
            if bucket is not None:
                store.save_bucket(connection, refund(bucket, reserved))

    def _issue(self, reservation: _Reservation, *, listener: str, correlation_id: str) -> SessionView:
        deps, verified = self.deps, reservation.principal
        if verified is None:
            raise identity_mismatch()
        actor = actor_for(verified.kind, verified.principal_id)
        with deps.uows.begin(actor=actor, correlation_id=correlation_id) as uow:
            connection = uow.connection
            principal = deps.store.principal(connection, verified.principal_id)
            member = self._member(uow, principal)
            if (
                principal is None
                or not principal.enabled
                or principal.generation != verified.generation
                or principal.verifier != verified.verifier
                or (principal.kind == "crew" and member is None)
            ):
                raise identity_mismatch()
            self._refund_in(uow, reservation)
            record, token = self.start(uow, principal, listener=listener)
            roles = roles_for(principal.kind, None if member is None else member.medical_officer_rank)
            return SessionView(record, roles, _actor_name(principal, member), _person_name(member), token)

    def start(self, uow: UnitOfWork, principal: Principal, *, listener: str) -> tuple[SessionRecord, str]:
        deps, connection = self.deps, uow.connection
        now = deps.instant()
        live = deps.store.live_sessions(
            connection,
            principal_id=principal.principal_id,
            now=now.at,
            limit=deps.settings.sessions_max,
        )
        for ended in oldest_to_end(live, per_principal=deps.settings.sessions_per_principal):
            self._end(uow, ended.session_id, "replaced")
        if deps.store.count_live(connection, now=now.at) >= deps.settings.sessions_max:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value, retryable=True)
        token = deps.tokens.new_token()
        record = SessionRecord(
            session_id=deps.ids.new("ses"),
            principal_id=principal.principal_id,
            kind=principal.kind,
            person_id=principal.person_id,
            mission_id=principal.mission_id,
            generation=principal.generation,
            listener=listener,
            process_epoch=now.process_epoch,
            created_at=now.at,
            created_monotonic_ns=now.monotonic_ns,
            expires_at=now.at + deps.settings.limits.lifetime(principal.kind),
            ended_at=None,
            end_reason=None,
        )
        deps.store.insert_session(connection, record, deps.tokens.digest(token))
        reference(uow, record_type="identity.session", record_id=record.session_id, operation="create")
        audit(
            uow,
            deps.ids,
            declaration("identity.session_started"),
            record.session_id,
            {"kind": principal.kind},
        )
        return record, token

    def _end(self, uow: UnitOfWork, session_id: str, reason: EndReason) -> bool:
        deps = self.deps
        if not deps.store.end_session(uow.connection, session_id, at=uow.now, reason=reason):
            return False
        deps.store.bump_access_generation(uow.connection)
        reference(uow, record_type="identity.session", record_id=session_id, operation="version")
        audit(uow, deps.ids, declaration("identity.session_ended"), session_id, {"reason": reason})
        return True

    def end_principal_sessions(self, uow: UnitOfWork, principal_id: str, reason: EndReason) -> int:
        deps = self.deps
        live = deps.store.live_sessions(
            uow.connection, principal_id=principal_id, now=uow.now, limit=deps.settings.sessions_max
        )
        return sum(1 for record in live if self._end(uow, record.session_id, reason))

    # Every request ----------------------------------------------------------------------------------------

    def authenticate(self, token: str | None, *, listener: str) -> SessionContext:
        if token is None or not 1 <= len(token) <= _TOKEN_MAX or not token.isascii():
            raise unauthenticated()
        digest = self.deps.tokens.digest(token)
        with self.deps.snapshots.snapshot() as view:
            record = self.deps.store.session_by_digest(view.connection, digest)
            if record is None:
                raise unauthenticated()
            context = self._live_context(view, record, listener=listener)
        if context is None:
            raise unauthenticated()
        return context

    def is_live(self, view: View, session: SessionContext, *, listener: str) -> bool:
        record = self._record(view, session.session_id)
        context = None if record is None else self._live_context(view, record, listener=listener)
        return context is not None and context == session

    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool:
        """E01 event delivery: the stream's session is still live on the listener that issued it."""
        record = self._record(snapshot, session.session_id)
        return record is not None and self.is_live(snapshot, session, listener=record.listener)

    def _record(self, view: View, session_id: str) -> SessionRecord | None:
        return self.deps.store.session_by_id(view.connection, session_id)

    def _live_context(self, view: View, record: SessionRecord, *, listener: str) -> SessionContext | None:
        principal = self.deps.store.principal(view.connection, record.principal_id)
        member = self._member(view, principal)
        state = (
            None
            if principal is None
            else PrincipalState(
                principal.generation, principal.enabled, principal.kind != "crew" or member is not None
            )
        )
        if not is_live(record, state, now=self.deps.instant(), listener=listener):
            return None
        rank = None if member is None else member.medical_officer_rank
        return context_for(record, roles_for(record.kind, rank))

    def _member(self, view: View, principal: Principal | None) -> RosterEntry | None:
        if principal is None or principal.person_id is None:
            return None
        return self.deps.store.roster_member(view.connection, principal.person_id)

    def current(self, session: SessionContext) -> SessionView:
        with self.deps.snapshots.snapshot() as view:
            record = self.deps.store.session_by_id(view.connection, session.session_id)
            principal = (
                None if record is None else self.deps.store.principal(view.connection, record.principal_id)
            )
            if record is None or principal is None:
                raise unauthenticated()
            member = self._member(view, principal)
        return SessionView(record, session.roles, _actor_name(principal, member), _person_name(member), None)

    def sign_out(self, session: SessionContext, *, key: str, correlation_id: str) -> None:
        deps = self.deps
        command = sign_out_identity(session, key)
        with deps.uows.begin(actor=session.actor, command=command, correlation_id=correlation_id) as uow:
            if deps.ledger.lookup(uow, command) is not None:
                return
            if not self._end(uow, session.session_id, "signed_out"):
                raise unauthenticated()
            receipt = deps.receipts.accepted(uow, command)
            deps.ledger.record(uow, command, StoredResult(receipt, 204, session.session_id, "accepted", None))

    def end_stale_epoch(self, *, correlation_id: str) -> int:
        """S5.10 startup sweep: every session issued by an earlier process epoch ends before SERVE."""
        deps, ended = self.deps, 0
        pages = deps.settings.sessions_max * 64 // deps.settings.sweep_batch + 2
        for _ in range(pages):
            with deps.uows.begin(actor=SIGN_IN_ACTOR, correlation_id=correlation_id) as uow:
                stale = deps.store.stale_epoch_sessions(
                    uow.connection, process_epoch=deps.clock.process_epoch(), limit=deps.settings.sweep_batch
                )
                for session_id in stale:
                    ended += int(self._end(uow, session_id, "restarted"))
            if len(stale) < deps.settings.sweep_batch:
                return ended
        raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)

    # Roster read ------------------------------------------------------------------------------------------

    def crew_page(self, *, after_index: int | None, limit: int) -> tuple[tuple[RosterEntry, ...], bool]:
        if not 1 <= limit <= self.deps.settings.page_max:
            raise ProblemError(ErrorCode.MALFORMED_REQUEST, ErrorCode.MALFORMED_REQUEST.value)
        with self.deps.snapshots.snapshot() as view:
            rows = self.deps.store.roster(view.connection, after_index=after_index, limit=limit + 1)
        return rows[:limit], len(rows) > limit

    def access_generation(self, view: View) -> int:
        return self.deps.store.access_generation(view.connection)

    # Consumer ports: RosterPort, PrincipalDirectory, CaptureEligibility ---------------------------------------

    def members(self, view: View, *, after_index: int | None, limit: int) -> tuple[RosterEntry, ...]:
        return self.deps.store.roster(view.connection, after_index=after_index, limit=limit)

    def member(self, view: View, person_id: str) -> RosterEntry | None:
        return self.deps.store.roster_member(view.connection, person_id)

    def known_recipient(self, view: View, principal_id: str) -> bool:
        principal = self.deps.store.principal(view.connection, principal_id)
        if principal is None or not principal.enabled or principal.kind == "adapter":
            return False
        return principal.kind != "crew" or self._member(view, principal) is not None

    def may_record(self, view: View, session: SessionContext, *, patient_id: str, operator_id: str) -> bool:
        operator = None if session.person_id is None else self.member(view, session.person_id)
        return may_record_for(
            session,
            patient_id=patient_id,
            operator_id=operator_id,
            patient_on_roster=self.member(view, patient_id) is not None,
            officer_now=operator is not None and operator.medical_officer_rank in MEDICAL_OFFICER_RANKS,
        )

    def release_id(self) -> str:
        with self.deps.snapshots.snapshot() as view:
            return view.release.release_id


def sign_out_identity(session: SessionContext, key: str) -> CommandIdentity:
    """The key covers the operation, its target session and the (empty) body (AGENTS 6)."""
    parameters = canonical_json(
        {"operation": "deleteCurrentSession", "target": session.session_id, "body": None}
    )
    return command_identity(
        key=key,
        actor_ref=f"{session.actor.kind}:{session.actor.id}",
        operation="deleteCurrentSession",
        target=session.session_id,
        parameters_json=parameters,
        body_fingerprint=hashlib.sha256(parameters).hexdigest(),
        fingerprint_kind="sha256",
    )


def _actor_name(principal: Principal, member: RosterEntry | None) -> str:
    return member.display_name if member is not None else principal.display_name


def _person_name(member: RosterEntry | None) -> str | None:
    return None if member is None else member.display_name
