"""Consumer ports of the identity module (E02 2), free of identity domain imports.

Other modules import only this file (SYSTEM 6.4); the module's own outbound ports are in `store.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from crew_health.kernel.uow import ReadSnapshot, SessionContext, UnitOfWork

type View = ReadSnapshot | UnitOfWork


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """A current roster member. No health, location or participation fact lives here."""

    person_id: str
    display_name: str
    roster_index: int
    on_roster_from_day: int
    medical_officer_rank: int | None


class RosterPort(Protocol):
    """Current roster in the caller's snapshot; a removed member is never on it."""

    def members(self, view: View, *, after_index: int | None, limit: int) -> tuple[RosterEntry, ...]: ...
    def member(self, view: View, person_id: str) -> RosterEntry | None: ...


class PrincipalDirectory(Protocol):
    """Whether an ID names a current recipient: a roster member or an enabled provisioned principal."""

    def known_recipient(self, view: View, principal_id: str) -> bool: ...


class SessionReader(Protocol):
    """Liveness and access generation for consumers that hold a SessionContext (S8.3)."""

    def is_live(self, view: View, session: SessionContext, *, listener: str) -> bool: ...
    def alive(self, snapshot: ReadSnapshot, session: SessionContext) -> bool: ...
    def access_generation(self, view: View) -> int: ...


class CaptureEligibility(Protocol):
    """E02 3.4: self capture, or an acting medical officer's manual capture for a named roster member."""

    def may_record(
        self, view: View, session: SessionContext, *, patient_id: str, operator_id: str
    ) -> bool: ...


class OwnerVerification(Protocol):
    """An owner proven by credential for a local owner command; no session row and no token."""

    def verify_owner(
        self, person_id: str, credential: str, *, client: str, correlation_id: str
    ) -> SessionContext: ...


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
