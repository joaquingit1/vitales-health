"""Ports of the consent module (SYSTEM 4.2 row `consent`).

`ConsentQueries` is what owning modules call inside their own snapshot. It never writes, schedules or
opens a transaction; a true answer is necessary but not sufficient, and the owner still projects fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from crew_health.kernel.uow import ReadSnapshot, UnitOfWork

type View = ReadSnapshot | UnitOfWork


class ConsentQueries(Protocol):
    def allows(
        self, view: View, *, subject_person_id: str, recipient: str, scope: tuple[str, str]
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
    ) -> frozenset[str]: ...


class RecordSubject(Protocol):
    """The subject and the person who entered a record: two facts of E01's RecordContext and record."""

    @property
    def patient_id(self) -> str: ...
    @property
    def operator_id(self) -> str: ...


class AuthoredRecords(Protocol):
    """Who entered a stored observation. Consent may not import E01's records store, so the composer binds it."""

    def observation(self, view: View, observation_id: str) -> RecordSubject | None: ...


@dataclass(frozen=True, slots=True)
class ConsentPorts:
    """The concrete registry key under which consent offers `ConsentQueries` (S5.12)."""

    queries: ConsentQueries
