"""E02 3.1 and 3.2: principals, roles, lifetimes and session liveness over injected time.

Pure functions over typed facts. Wall time and monotonic readings are supplied by the caller from the
S5.2 Clock; monotonic readings are compared only inside one process epoch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.errors import ErrorCode, ProblemError
from crew_health.kernel.ids import require_id
from crew_health.kernel.uow import Actor, Role, SessionContext

type SignInKind = Literal["crew", "station", "ground", "operator", "editor"]
type PrincipalKind = Literal["crew", "station", "ground", "operator", "editor", "adapter"]
type EndReason = Literal[
    "signed_out", "replaced", "credential_changed", "member_removed", "restarted", "revoked"
]

SIGN_IN_KINDS: tuple[SignInKind, ...] = ("crew", "station", "ground", "operator", "editor")
PRINCIPAL_KINDS: tuple[PrincipalKind, ...] = (*SIGN_IN_KINDS, "adapter")
END_REASONS: tuple[EndReason, ...] = (
    "signed_out",
    "replaced",
    "credential_changed",
    "member_removed",
    "restarted",
    "revoked",
)
MEDICAL_OFFICER_RANKS = frozenset({1, 2})
_NS_PER_SECOND = 1_000_000_000


def identity_mismatch() -> ProblemError:
    """The one generic sign-in refusal: it never says which part was wrong (E02 3.2)."""
    return ProblemError(ErrorCode.IDENTITY_MISMATCH, ErrorCode.IDENTITY_MISMATCH.value)


def unauthenticated() -> ProblemError:
    return ProblemError(ErrorCode.UNAUTHENTICATED, ErrorCode.UNAUTHENTICATED.value)


@dataclass(frozen=True, slots=True)
class Instant:
    """One reading of the S5.2 clock: wall time, monotonic nanoseconds and the process epoch."""

    at: datetime
    monotonic_ns: int
    process_epoch: str

    def __post_init__(self) -> None:
        require_utc(self.at)
        require_id(self.process_epoch)
        if self.monotonic_ns < 0:
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


@dataclass(frozen=True, slots=True)
class SessionLimits:
    """Validated S5.11 lifetimes. Person, operator, editor and ground sessions are capped at the idle lock."""

    idle_lock: timedelta
    station: timedelta
    adapter: timedelta

    def __post_init__(self) -> None:
        if min(self.idle_lock, self.station, self.adapter) <= timedelta(0):
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)

    def lifetime(self, kind: PrincipalKind) -> timedelta:
        if kind == "station":
            return self.station
        if kind == "adapter":
            return self.adapter
        return self.idle_lock


@dataclass(frozen=True, slots=True)
class SignInRequest:
    kind: SignInKind
    person_id: str | None


def principal_key(request: SignInRequest, *, ground_allowed: bool) -> tuple[PrincipalKind, str | None]:
    """Which principal a sign-in names. Crew needs a person ID; every other kind refuses one (E02 6).

    The second value is the person ID for crew and None for the single provisioned principal of other
    kinds. Ground signs in only where the hub profile allows it.
    """
    if request.kind == "crew":
        if request.person_id is None:
            raise identity_mismatch()
        return "crew", require_id(request.person_id)
    if request.person_id is not None:
        raise identity_mismatch()
    if request.kind == "ground" and not ground_allowed:
        raise identity_mismatch()
    return request.kind, None


def roles_for(kind: PrincipalKind, medical_officer_rank: int | None) -> frozenset[Role]:
    """Roles come only from the principal kind and the current roster rank, never from the caller."""
    if kind == "crew":
        if medical_officer_rank in MEDICAL_OFFICER_RANKS:
            return frozenset({Role.CREW, Role.CREW_MEDICAL_OFFICER})
        return frozenset({Role.CREW})
    single: dict[PrincipalKind, Role] = {
        "station": Role.STATION,
        "ground": Role.GROUND,
        "operator": Role.OPERATOR,
        "editor": Role.EDITOR,
        "adapter": Role.ADAPTER,
    }
    return frozenset({single[kind]})


def actor_for(kind: PrincipalKind, principal_id: str) -> Actor:
    """Editor is an attributed operator actor holding the editor role (S5.7); no new actor kind."""
    require_id(principal_id)
    actors: dict[PrincipalKind, Actor] = {
        "crew": Actor("person", principal_id),
        "station": Actor("station", principal_id),
        "ground": Actor("ground", principal_id),
        "operator": Actor("operator", principal_id),
        "editor": Actor("operator", principal_id),
        "adapter": Actor("adapter", principal_id),
    }
    return actors[kind]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    principal_id: str
    kind: PrincipalKind
    person_id: str | None
    mission_id: str
    generation: int
    listener: str
    process_epoch: str
    created_at: datetime
    created_monotonic_ns: int
    expires_at: datetime
    ended_at: datetime | None
    end_reason: EndReason | None

    def __post_init__(self) -> None:
        require_utc(self.created_at)
        require_utc(self.expires_at)
        if self.ended_at is not None:
            require_utc(self.ended_at)
        if self.expires_at <= self.created_at or (self.ended_at is None) != (self.end_reason is None):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)
        if (self.kind == "crew") != (self.person_id is not None):
            raise ProblemError(ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.value)


@dataclass(frozen=True, slots=True)
class PrincipalState:
    """The authoritative principal facts read in the same snapshot as the session row."""

    generation: int
    enabled: bool
    on_roster: bool


def is_live(record: SessionRecord, principal: PrincipalState | None, *, now: Instant, listener: str) -> bool:
    """E02 3.1: a session is usable only while every fact holds; any unknown fact refuses.

    The wire expiry is an absolute upper bound and the lifetime is also an elapsed timer in the issuing
    process epoch, so a backward wall-clock step never extends a session and a restart ends it.
    """
    if record.ended_at is not None or principal is None:
        return False
    if not principal.enabled or not principal.on_roster or principal.generation != record.generation:
        return False
    if record.listener != listener or record.process_epoch != now.process_epoch:
        return False
    elapsed = now.monotonic_ns - record.created_monotonic_ns
    lifetime = record.expires_at - record.created_at
    if elapsed < 0 or elapsed >= _nanoseconds(lifetime):
        return False
    return record.created_at <= now.at < record.expires_at


def context_for(record: SessionRecord, roles: frozenset[Role]) -> SessionContext:
    if not roles:
        raise unauthenticated()
    return SessionContext(
        record.session_id,
        record.kind,
        actor_for(record.kind, record.principal_id),
        record.person_id,
        roles,
    )


def oldest_to_end(live: tuple[SessionRecord, ...], *, per_principal: int) -> tuple[SessionRecord, ...]:
    """Sessions ended to admit one more: the oldest first, in stable created time and ID order."""
    if per_principal < 1:
        raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
    ordered = sorted(live, key=lambda value: (value.created_at, value.session_id))
    excess = len(ordered) - per_principal + 1
    return tuple(ordered[:excess]) if excess > 0 else ()


def _nanoseconds(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * _NS_PER_SECOND + value.microseconds * 1000
