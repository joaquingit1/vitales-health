"""E02 composition (SYSTEM 5.12): identity and consent wired into the consumers that own structural copies of
their ports. Each adapter below implements one consumer's port class with E02's real services and is registered
under that consumer's own class, which is the registry key the consumer resolves.

Nothing here decides access on its own. Sessions, roles, roster membership, consent and capture eligibility are
E02's answers; the adapters only translate shapes. Every unknown (an operation absent from the frozen catalog, a
request with no listener, an unknown mission day, a partition E02 cannot route) refuses.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from fastapi import Request

from crew_health.app.assembly import ConfigHttpMetadata, HttpSecurity, RecordHttpServices
from crew_health.app.middleware import Operation, OperationCatalog
from crew_health.app.registry import ModuleRegistry, Resolver
from crew_health.catchup.api.router import RoleGate as CatchupGate
from crew_health.catchup.application.ports import ConsentQueries as CatchupConsent
from crew_health.clock.application.clock import PersistentClocks
from crew_health.consent.application.ports import ClockAdmission, ObservationFacts, TimeAdmission
from crew_health.consent.application.ports import ObservationPartitions as ConsentObservations
from crew_health.consent.application.records import ConsentRecordAuthorization
from crew_health.consent.application.service import ConsentService
from crew_health.contracts_gen.enums import ErrorCode
from crew_health.events.application.delivery import SessionAuthorizer
from crew_health.groundlink.api.router import RoleGate as GroundLinkGate
from crew_health.help.api.router import RoleGate as HelpGate
from crew_health.help.application.ports import RosterPort as HelpRoster
from crew_health.identity.api.router import IdentitySecurity
from crew_health.identity.application.ports import RosterEntry
from crew_health.identity.application.service import IdentityService
from crew_health.kernel.commands import CommandIdentity, ResultVisibility, StoredResult
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.ids import IdFactory
from crew_health.kernel.settings import HubSettings
from crew_health.kernel.units import UnitRegistry, WireUnitConverter
from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork
from crew_health.kitmap.api.router import RequireRoles as KitmapRoles
from crew_health.kitmap.application.ports import RosterPort as KitmapRoster
from crew_health.measurement.application.ports import ConsentQueries as MeasurementConsent
from crew_health.measurement.application.ports import SessionAuthenticator as MeasurementAuthenticator
from crew_health.plans.api.router import RequestAuthenticator as PlansAuthenticator
from crew_health.records.adapters.policies import ReleaseObservationPolicies
from crew_health.records.application.commands import RecordsCommands
from crew_health.records.application.queries import RecordsQueries
from crew_health.records.application.seams import ObservationPolicies, RecordsStore
from crew_health.records.domain.observations import ObservationInput, RecordContext
from crew_health.roles.api.router import RequestAuthenticator as RolesAuthenticator
from crew_health.roles.application.ports import RosterMember as RolesMember
from crew_health.roles.application.ports import RosterPort as RolesRoster
from crew_health.teach.application.ports import OriginMode as TeachOrigin
from crew_health.teach.application.ports import PersonDirectory, PersonRef, RecordRouting
from crew_health.teach.application.ports import RequestAuthenticator as TeachAuthenticator

type View = ReadSnapshot | UnitOfWork


def _refused() -> ProblemError:
    return ProblemError(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)


def _unavailable() -> ProblemError:
    return ProblemError(ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.CAPABILITY_UNAVAILABLE.value)


# Sessions and roles ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationRoles:
    """The frozen contract's role table, the same one E01's middleware enforces (x-roles per operation)."""

    operations: Mapping[str, Operation]

    @classmethod
    def load(cls, contract: Path) -> OperationRoles:
        return _roles(str(contract.resolve()), hashlib.sha256(contract.read_bytes()).hexdigest())

    def admit(self, session: SessionContext, operation_id: str) -> None:
        operation = self.operations.get(operation_id)
        if operation is None or operation.security is None or not session.roles & operation.roles:
            raise _refused()
        if operation.security == "adapterToken" and session.kind != "adapter":
            raise ProblemError(ErrorCode.ADAPTER_NOT_TRUSTED, ErrorCode.ADAPTER_NOT_TRUSTED.value)


@functools.lru_cache(maxsize=4)
def _roles(contract: str, digest: str) -> OperationRoles:
    """One parse per contract content: `digest` keys the cache, so a changed file is parsed again."""
    catalog = OperationCatalog.load(Path(contract))
    return OperationRoles({operation.operation_id: operation for operation in catalog.operations})


@dataclass(frozen=True, slots=True)
class SessionGate:
    """E02 2 `require_roles(operation_id)` in every shape a consumer copied: the request's bearer token to a live
    session on the listener that issued it, the operation's contract roles, then E02's role coherence check.
    Each call reads the session row again; nothing is cached from the middleware's earlier answer."""

    security: IdentitySecurity
    roles: OperationRoles

    def _admitted(self, session: SessionContext, operation_id: str) -> SessionContext:
        self.roles.admit(session, operation_id)
        self.security.authorize(session, operation_id)
        return session

    # catchup, help, groundlink RoleGate
    def session_for(self, request: Request, operation_id: str) -> SessionContext:
        return self._admitted(self.security.authenticate(request), operation_id)

    # plans and roles RequestAuthenticator
    def session(self, request: Request, operation_id: str) -> SessionContext:
        return self.session_for(request, operation_id)

    # kitmap RequireRoles
    def __call__(self, operation_id: str) -> Callable[[Request], SessionContext]:
        def dependency(request: Request) -> SessionContext:
            return self.session_for(request, operation_id)

        return dependency


@dataclass(frozen=True, slots=True)
class HeaderGate:
    """The same gate for consumers that pass only the request headers (teach, measurement)."""

    security: IdentitySecurity
    roles: OperationRoles

    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext:
        session = self.security.authenticate_headers(headers)
        self.roles.admit(session, operation_id)
        self.security.authorize(session, operation_id)
        return session


@dataclass(frozen=True, slots=True)
class MeasurementGate:
    """measurement's SessionAuthenticator: None for not signed in (401), a raised refusal for a wrong role."""

    gate: HeaderGate

    def authenticate(self, headers: Mapping[str, str], operation_id: str) -> SessionContext | None:
        try:
            session = self.gate.security.authenticate_headers(headers)
        except ProblemError as error:
            if error.code == ErrorCode.UNAUTHENTICATED:
                return None
            raise
        self.gate.roles.admit(session, operation_id)
        self.gate.security.authorize(session, operation_id)
        return session


@dataclass(frozen=True, slots=True)
class LiveSessions:
    """E01 event delivery's SessionAuthorizer: the stream's session is still live in the delivery snapshot."""

    identity: IdentityService

    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool:
        return self.identity.alive(snapshot, session)


@dataclass(frozen=True, slots=True)
class ReplayVisibility:
    """A stored command result replays only to the actor that issued it, while that actor's session is live.
    The result body is then read again under the reader's current authorization by the owning route."""

    identity: IdentityService

    def authorize(
        self, snapshot: View, session: SessionContext, identity: CommandIdentity, result: StoredResult
    ) -> None:
        if session.actor.id != identity.actor_ref or not self.identity.alive(snapshot, session):
            raise _refused()


# Roster ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HelpRosterAdapter:
    identity: IdentityService

    def crew(self, at: datetime) -> Sequence[RosterEntry]:
        return self.identity.crew_at(at)


@dataclass(frozen=True, slots=True)
class RolesRosterAdapter:
    identity: IdentityService

    def crew(self, at: datetime) -> Sequence[RolesMember]:
        return tuple(
            RolesMember(entry.person_id, entry.display_name, entry.roster_index, entry.medical_officer_rank)
            for entry in self.identity.crew_at(at)
        )


@dataclass(frozen=True, slots=True)
class KitmapRosterAdapter:
    identity: IdentityService

    def person_ids(self) -> frozenset[str]:
        return frozenset(entry.person_id for entry in self.identity.crew_at())


@dataclass(frozen=True, slots=True)
class PeopleAdapter:
    """teach PersonDirectory: a current roster member, else None (unknown and not current are the same)."""

    identity: IdentityService

    def person(self, person_id: str) -> PersonRef | None:
        with self.identity.deps.snapshots.snapshot() as view:
            entry = self.identity.member(view, person_id)
        return None if entry is None else PersonRef(entry.person_id, entry.display_name, True)


@dataclass(frozen=True, slots=True)
class DemoOperatorRecord:
    """A demo hub's separate record for the presenter's LIVE readings (VALIDATION 3, DEMO-PLAN 5 point 1): while
    `persona_person_id` is the subject, a reading of origin `origin` goes to `record_id`, never to the persona's
    roster record. Only a demo profile provides it; a station hub has none."""

    persona_person_id: str
    record_id: str
    origin: TeachOrigin


@dataclass(frozen=True, slots=True)
class RoutingAdapter:
    """teach RecordRouting (E30 X10): a current member's record lives in the roster's origin namespace. Another
    origin routes only to a demo hub's demo-operator record for that persona; anything else is refused as
    unavailable (docs/requests/E02.md R-E02-33)."""

    identity: IdentityService
    demo: DemoOperatorRecord | None = None

    def _origin(self, person_id: str) -> TeachOrigin:
        with self.identity.deps.snapshots.snapshot() as view:
            entry = self.identity.member(view, person_id)
            partition = self.identity.partition(view)
        if entry is None or partition is None:
            raise _unavailable()
        return partition[1]

    def record_origin(self, person_id: str) -> TeachOrigin:
        return self._origin(person_id)

    def record_for(self, person_id: str, origin: TeachOrigin) -> str:
        if self._origin(person_id) == origin:
            return person_id
        demo = self.demo
        if demo is not None and demo.persona_person_id == person_id and demo.origin == origin:
            return demo.record_id
        raise _unavailable()


# Consent -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsentAdapter:
    """catchup and measurement ConsentQueries: E02's answer for objects of the hub roster's mission and origin.

    A grant must hold in the caller's own snapshot and, when that is a read snapshot, again in the latest committed
    state (docs/requests/E02.md R-E02-34): a revocation, sign out or roster removal committed after the caller's
    snapshot opened refuses the read, as ConsentRecordAuthorization.require_read does for records. A unit of work
    already holds the writer, so its view is the latest state."""

    consent: ConsentService

    def allows(
        self, view: ReadSnapshot, *, subject_person_id: str, recipient: str, scope: tuple[str, str]
    ) -> bool:
        if not self.consent.allows(
            view, subject_person_id=subject_person_id, recipient=recipient, scope=scope
        ):
            return False
        if callable(getattr(view, "replicate", None)):
            return True
        with self.consent.deps.snapshots.snapshot() as latest:
            return self.consent.allows(
                latest, subject_person_id=subject_person_id, recipient=recipient, scope=scope
            )


@dataclass(frozen=True, slots=True)
class ObservationsAdapter:
    """Consent's ObservationPartitions from E01's records store: a stored observation's subject and partition."""

    records: RecordsStore

    def observation(self, view: View, observation_id: str) -> ObservationFacts | None:
        return self.records.observation(view, observation_id)


@dataclass(frozen=True, slots=True)
class ClockAdmissionAdapter:
    """Consent's TimeAdmission from E01's durable clock state in the same view."""

    clocks: PersistentClocks

    def admission(self, view: View) -> ClockAdmission | None:
        state = self.clocks.repository.state(view.connection)
        return None if state is None else ClockAdmission(state.last_committed_at, state.frozen)


# Records -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnboundCapture:
    """records' SubjectBinding for an HTTP recordObservation. Binding a body to a mission partition and to the
    clock segment it was observed on needs the producer evidence of R-E01-17, which no HTTP body carries, so a
    capture through this route refuses. Producers (measurement, teach) append through records' own producer."""

    def bind(self, uow: UnitOfWork, session: SessionContext, value: ObservationInput) -> RecordContext:
        raise _unavailable()


def record_services(resolver: Resolver) -> RecordHttpServices:
    """E01's observation routes over E02's real record authorization (consent and identity, read fence
    included), records' release-bound policies and store, and replay only to the issuing live session."""
    store = resolver.require(RecordsStore)  # type: ignore[type-abstract]
    authorization = resolver.require(ConsentRecordAuthorization)
    commands = RecordsCommands(
        repository=store,
        ids=resolver.require(IdFactory),  # type: ignore[type-abstract]
        units=resolver.require(UnitRegistry),  # type: ignore[type-abstract]
        converter=resolver.require(WireUnitConverter),  # type: ignore[type-abstract]
        authorization=authorization,
        binding=UnboundCapture(),
        policies=resolver.require(ObservationPolicies),  # type: ignore[type-abstract]
        clocks=resolver.require(PersistentClocks),
    )
    queries = RecordsQueries(store, authorization, resolver.require(HubSettings))  # type: ignore[type-abstract]
    return RecordHttpServices(commands, queries, ReplayVisibility(resolver.require(IdentityService)))


# Registration ------------------------------------------------------------------------------------------------


def installed(registry: ModuleRegistry) -> bool:
    providers: Mapping[type[object], object] = getattr(registry, "providers", {})
    return IdentityService in providers and ConsentService in providers


def compose(
    registry: ModuleRegistry,
    *,
    contract: Path,
    metadata: Callable[[IdentityService], ConfigHttpMetadata],
    demo: DemoOperatorRecord | None = None,
) -> None:
    """Registers every E02 adapter under its consumer's real port class. Call it from `configure`, after the
    modules registered and before any fail-closed stand-in; it does nothing when identity or consent is absent."""
    if not installed(registry):
        return
    roles = OperationRoles.load(contract)

    def identity(resolver: Resolver) -> IdentityService:
        return resolver.require(IdentityService)

    def gate(resolver: Resolver) -> SessionGate:
        return SessionGate(resolver.require(IdentitySecurity), roles)

    def headers(resolver: Resolver) -> HeaderGate:
        return HeaderGate(resolver.require(IdentitySecurity), roles)

    def consent(resolver: Resolver) -> ConsentAdapter:
        return ConsentAdapter(resolver.require(ConsentService))

    on_identity = (IdentityService,)
    on_security = (IdentitySecurity,)
    entries: tuple[tuple[object, Callable[[Resolver], object], tuple[type[object], ...]], ...] = (
        (HttpSecurity, lambda r: r.require(IdentitySecurity), on_security),
        (SessionAuthorizer, lambda r: LiveSessions(identity(r)), on_identity),
        (ConfigHttpMetadata, lambda r: metadata(identity(r)), on_identity),
        (CatchupGate, gate, on_security),
        (HelpGate, gate, on_security),
        (GroundLinkGate, gate, on_security),
        (KitmapRoles, gate, on_security),
        (PlansAuthenticator, gate, on_security),
        (RolesAuthenticator, gate, on_security),
        (TeachAuthenticator, headers, on_security),
        (MeasurementAuthenticator, lambda r: MeasurementGate(headers(r)), on_security),
        (HelpRoster, lambda r: HelpRosterAdapter(identity(r)), on_identity),
        (RolesRoster, lambda r: RolesRosterAdapter(identity(r)), on_identity),
        (KitmapRoster, lambda r: KitmapRosterAdapter(identity(r)), on_identity),
        (PersonDirectory, lambda r: PeopleAdapter(identity(r)), on_identity),
        (RecordRouting, lambda r: RoutingAdapter(identity(r), demo), on_identity),
        (CatchupConsent, consent, (ConsentService,)),
        (MeasurementConsent, consent, (ConsentService,)),
        (
            ConsentObservations,
            lambda r: ObservationsAdapter(r.require(RecordsStore)),  # type: ignore[type-abstract]
            (cast(type[object], RecordsStore),),
        ),
        (
            ObservationPolicies,
            lambda r: ReleaseObservationPolicies.packaged(r.require(UnitRegistry)),  # type: ignore[type-abstract]
            (cast(type[object], UnitRegistry),),
        ),
        (
            RecordHttpServices,
            record_services,
            (
                ConsentRecordAuthorization,
                IdentityService,
                PersistentClocks,
                cast(type[object], RecordsStore),
                cast(type[object], ObservationPolicies),
                cast(type[object], WireUnitConverter),
                cast(type[object], UnitRegistry),
                cast(type[object], IdFactory),
                cast(type[object], HubSettings),
            ),
        ),
        (
            TimeAdmission,
            lambda r: ClockAdmissionAdapter(r.require(PersistentClocks)),
            (PersistentClocks,),
        ),
    )
    providers: Mapping[type[object], object] = getattr(registry, "providers", {})
    for port, factory, requires in entries:
        key = cast(type[object], port)
        # A port another composer already bound keeps its provider; a missing dependency leaves the consumer's
        # own fail-closed stand-in in place.
        if key not in providers and all(dependency in providers for dependency in requires):
            registry.provide(key, factory, requires=requires)


def visibility(identity: IdentityService) -> ResultVisibility:
    return ReplayVisibility(identity)
