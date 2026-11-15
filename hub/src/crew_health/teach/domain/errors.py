"""Typed domain errors of the teach module (E30 4.7). The API maps each kind to the contract error envelope."""

from __future__ import annotations

from typing import Literal

type ErrorKind = Literal[
    "invalid_value",
    "invalid_transition",
    "context_inapplicable",
    "not_eligible",
    "catalog_shape",
]


class TeachError(Exception):
    """Base of every teach domain error; carries a reason code, never a value, count, answer or name."""

    kind: ErrorKind = "invalid_value"

    def __init__(self, reason: str, *, fields: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


class InvalidValue(TeachError):
    kind: ErrorKind = "invalid_value"


class IncompleteCapture(InvalidValue):
    """A count that failed CR1 to CR4: never a rate, never an Observation (3.3)."""

    def __init__(self, reasons: frozenset[str]) -> None:
        super().__init__("incomplete_capture", fields=tuple(sorted("/beats/" + r for r in reasons)))
        self.reasons = reasons


class InvalidTransition(TeachError):
    kind: ErrorKind = "invalid_transition"


class BeatRefused(InvalidTransition):
    """A refused beat. `recorded` is True when the refusal also marked the attempt incomplete (3.3 steps 3, 4)."""

    def __init__(self, reason: str, *, recorded: bool) -> None:
        super().__init__(reason)
        self.recorded = recorded


class ContextInapplicable(TeachError):
    kind: ErrorKind = "context_inapplicable"


class NotEligible(TeachError):
    kind: ErrorKind = "not_eligible"


class CatalogShapeError(TeachError):
    kind: ErrorKind = "catalog_shape"
