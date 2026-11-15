"""Ports of the consent module (SYSTEM 4.2 row `consent`).

`ConsentQueries` is what owning modules call inside their own snapshot. It never writes, schedules or
opens a transaction; a true answer is necessary but not sufficient, and the owner still projects fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork
type Partition = tuple[str, Literal["LIVE", "REPLAY", "SIM"]]
"""The mission and origin namespace of the object a query is about."""


class ConsentQueries(Protocol):
    """`partition` is the mission and origin of the object. Omitted, it is the hub roster's partition; a grant
    made for another mission or origin never matches, and an unknown partition matches nothing."""

    def allows(
        self,
        view: View,
        *,
        subject_person_id: str,
        recipient: str,
        scope: tuple[str, str],
        partition: Partition | None = None,
    ) -> bool: ...
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
    ) -> frozenset[str]: ...


class RecordSubject(Protocol):
    """The subject and the person who entered a record: two facts of E01's RecordContext and record."""

    @property
    def patient_id(self) -> str: ...
    @property
    def operator_id(self) -> str: ...


class ObservationFacts(Protocol):
    """The authoritative facts of one stored observation that consent matches a grant against."""

    @property
    def patient_id(self) -> str: ...
    @property
    def mission_id(self) -> str: ...
    @property
    def origin_mode(self) -> str: ...


class ObservationPartitions(Protocol):
    """A stored observation's subject, mission and origin. Consent may not import E01's records store, so the
    composer binds it; absent, no record grant is made and no non-subject read is released."""

    def observation(self, view: View, observation_id: str) -> ObservationFacts | None: ...


@dataclass(frozen=True, slots=True)
class ClockAdmission:
    """The durable clock state visible in a view: the last committed write time and whether clearing is frozen
    (a regression, or a first start before the operator confirmed time)."""

    high_water: datetime
    frozen: bool


class TimeAdmission(Protocol):
    """The durable clock admission visible in a view (SYSTEM 5.2 clock state), bound by the composer.
    None means unknown: a finite grant then authorizes nothing (R-E02-21)."""

    def admission(self, view: View) -> ClockAdmission | None: ...


@dataclass(frozen=True, slots=True)
class ConsentPorts:
    """The concrete registry key under which consent offers `ConsentQueries` (S5.12)."""

    queries: ConsentQueries
