"""E02 3 roster facts: stable IDs, stored roster order and unique medical officer ranks per mission."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from crew_health.identity.domain.session import PrincipalKind
from crew_health.kernel.errors import ErrorCode, FieldError, ProblemError
from crew_health.kernel.ids import require_id

type Namespace = Literal["LIVE", "REPLAY", "SIM"]
NAMESPACES: tuple[Namespace, ...] = ("LIVE", "REPLAY", "SIM")
_DISPLAY_NAME_MAX_BYTES = 256


def invalid(field: str) -> ProblemError:
    return ProblemError(
        ErrorCode.INVALID_VALUE,
        ErrorCode.INVALID_VALUE.value,
        field_errors=(FieldError(field, ErrorCode.INVALID_VALUE.value, ErrorCode.INVALID_VALUE.value),),
    )


def display_name(value: str, field: str) -> str:
    """A shown name: nonempty, bounded, no control characters; never an identifier substitute."""
    if not value or not value.strip() or len(value.encode("utf-8")) > _DISPLAY_NAME_MAX_BYTES:
        raise invalid(field)
    if any(ord(character) < 32 or 127 <= ord(character) < 160 for character in value):
        raise invalid(field)
    return value


@dataclass(frozen=True, slots=True)
class RosterMember:
    person_id: str
    display_name: str
    roster_index: int
    on_roster_from_day: int
    on_roster_until_day: int | None
    medical_officer_rank: int | None


@dataclass(frozen=True, slots=True)
class ProvisionedPrincipal:
    principal_id: str
    kind: PrincipalKind
    display_name: str


@dataclass(frozen=True, slots=True)
class RosterBootstrap:
    mission_id: str
    namespace: Namespace
    members: tuple[RosterMember, ...]
    principals: tuple[ProvisionedPrincipal, ...]


def validate_bootstrap(value: RosterBootstrap, *, roster_max: int, day_span_max: int) -> RosterBootstrap:
    """Whole-document checks before any write; one failure rejects the document and keeps prior state."""
    require_id(value.mission_id)
    if not 1 <= len(value.members) <= roster_max:
        raise invalid("members")
    _members(value.members, day_span_max=day_span_max)
    _principals(value.principals, frozenset(member.person_id for member in value.members))
    return value


def _members(members: Sequence[RosterMember], *, day_span_max: int) -> None:
    ids: set[str] = set()
    indexes: set[int] = set()
    ranks: set[int] = set()
    for position, member in enumerate(members):
        prefix = f"members/{position}"
        require_id(member.person_id)
        display_name(member.display_name, prefix + "/display_name")
        if member.person_id in ids or member.roster_index in indexes or member.roster_index < 0:
            raise invalid(prefix)
        ids.add(member.person_id)
        indexes.add(member.roster_index)
        if member.on_roster_until_day is not None and not (
            0 <= member.on_roster_until_day - member.on_roster_from_day < day_span_max
        ):
            raise invalid(prefix + "/on_roster_until_day")
        rank = member.medical_officer_rank
        if rank is not None and (rank < 1 or rank in ranks):
            raise invalid(prefix + "/medical_officer_rank")
        if rank is not None:
            ranks.add(rank)


def _principals(principals: Sequence[ProvisionedPrincipal], person_ids: frozenset[str]) -> None:
    ids: set[str] = set(person_ids)
    kinds: set[str] = set()
    for position, principal in enumerate(principals):
        prefix = f"principals/{position}"
        require_id(principal.principal_id)
        display_name(principal.display_name, prefix + "/display_name")
        if principal.kind == "crew" or principal.principal_id in ids:
            raise invalid(prefix)
        if principal.kind != "adapter" and principal.kind in kinds:
            raise invalid(prefix + "/kind")
        ids.add(principal.principal_id)
        kinds.add(principal.kind)
