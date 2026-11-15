"""Typed errors of the supplies domain (E32 6.1 table). The API maps each to the contract envelope."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from crew_health.kernel.units import Quantity


@dataclass(frozen=True, slots=True)
class FieldIssue:
    field: str  # JSON pointer into the request or document
    code: str
    message: str


class SuppliesError(Exception):
    """Base of every typed supplies error; never raised itself."""


class SupplyNotFound(SuppliesError):
    def __init__(self, supply_id: str) -> None:
        super().__init__("unknown supply")
        self.supply_id = supply_id


class UseInvalid(SuppliesError):
    def __init__(self, issues: Sequence[FieldIssue]) -> None:
        super().__init__("supply use invalid")
        self.issues = tuple(issues)


class SupplyBelowZero(SuppliesError):
    """A use would take quantity_now below zero, or the supply has no stock row here (INV-S9, INV-S14)."""

    def __init__(self, supply_id: str, quantity_now: Quantity | None, requested: Quantity) -> None:
        super().__init__("supply below zero")
        self.supply_id, self.quantity_now, self.requested = supply_id, quantity_now, requested


class UnitMismatch(SuppliesError):
    def __init__(self, left: str, right: str) -> None:
        super().__init__("unit mismatch")
        self.left, self.right = left, right


class ReleaseMismatch(SuppliesError):
    def __init__(self, active_release_id: str) -> None:
        super().__init__("configuration release mismatch")
        self.active_release_id = active_release_id


class ReferenceNotVisible(SuppliesError):
    """The referenced plan run or help session is not visible to the session."""


class WhatIfInvalid(SuppliesError):
    def __init__(self, issues: Sequence[FieldIssue]) -> None:
        super().__init__("what-if invalid")
        self.issues = tuple(issues)


class DocumentInvalid(SuppliesError):
    """A configuration document E32 reads does not have the shape this module needs."""

    def __init__(self, document: str, pointer: str, message: str) -> None:
        super().__init__(message)
        self.document, self.pointer = document, pointer


class LedgerCorrupt(SuppliesError):
    """The stored ledger breaks an invariant (contiguity, running totals); never repaired silently."""

    def __init__(self, partition: str) -> None:
        super().__init__("ledger corrupt")
        self.partition = partition
