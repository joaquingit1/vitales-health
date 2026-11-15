"""Consumer ports of the identity module (E02 2), free of identity domain imports.

Other modules import only this file (SYSTEM 6.4); the module's own outbound ports are in `store.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork

type View = ReadSnapshot | UnitOfWork
type Partition = tuple[str, Literal["LIVE", "REPLAY", "SIM"]]
"""A mission and its origin namespace: the partition every record, grant and roster belongs to."""


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """A current roster member. No health, location or participation fact lives here."""

    person_id: str
    display_name: str
    roster_index: int
    on_roster_from_day: int
    medical_officer_rank: int | None
    on_roster_until_day: int | None = None


class RosterPort(Protocol):
    """Current roster in the caller's snapshot: a removed member, or one outside its mission-day interval, is
    never on it. An unknown mission day makes nobody current (E02 3.1)."""

    def members(self, view: View, *, after_index: int | None, limit: int) -> tuple[RosterEntry, ...]: ...
    def member(self, view: View, person_id: str) -> RosterEntry | None: ...
    def partition(self, view: View) -> Partition | None: ...


class PrincipalDirectory(Protocol):
    """Whether an ID names a current recipient: a roster member or an enabled provisioned principal."""

    def known_recipient(self, view: View, principal_id: str) -> bool: ...


class SessionReader(Protocol):
    """Liveness and access generation for consumers that hold a SessionContext (S8.3)."""

    def is_live(self, view: View, session: SessionContext, *, listener: str) -> bool: ...
    def alive(self, snapshot: View, session: SessionContext) -> bool: ...
    def access_generation(self, view: View) -> int: ...


class SessionLiveness(Protocol):
    """A held session is still live in the caller's snapshot, on the listener that issued it."""

    def is_live(self, view: View, session: SessionContext) -> bool: ...


class AccessGenerations(Protocol):
    """E02 4: the durable access generation. Any revocation of a session, roster membership or consent grant
    moves it forward in the revoking unit of work; a delivery stamped with an older generation is discarded."""

    def bump(self, uow: UnitOfWork) -> int: ...
    def access_generation(self, view: View) -> int: ...


class CaptureEligibility(Protocol):
    """E02 3.4: self capture, or an acting medical officer's manual capture for a named roster member."""

    def may_record(
        self, view: View, session: SessionContext, *, patient_id: str, operator_id: str
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class OwnerProof:
    """An owner proven by credential for one local owner command: no session row and no token.

    `generation` is the credential generation the proof verified. Every command that acts on the proof checks,
    inside its own unit of work, that the generation is still current, so a credential changed while the
    command waited cannot act (E02 4)."""

    session: SessionContext
    generation: int


@dataclass(frozen=True, slots=True)
class OperatorProof:
    """The operator at the hub console. `generation` is the operator credential generation proven under the
    sign-in limits, or None for the first-start console before that principal has any credential."""

    actor: Actor
    generation: int | None


class OwnerVerification(Protocol):
    def verify_owner(
        self, person_id: str, credential: str, *, client: str, correlation_id: str
    ) -> OwnerProof: ...
    def owner_current(self, view: View, proof: OwnerProof) -> bool: ...


@dataclass(frozen=True, slots=True)
class HubProfile:
    """Composer fact: a ground or demo hub admits ground sign-in (E02 7). Absent, ground is refused."""

    ground_sign_in: bool


@dataclass(frozen=True, slots=True)
class IdentityPorts:
    """The concrete registry key under which identity offers its consumer ports (S5.12)."""

    roster: RosterPort
    directory: PrincipalDirectory
    sessions: SessionReader
    capture: CaptureEligibility
    owners: OwnerVerification
    generations: AccessGenerations
