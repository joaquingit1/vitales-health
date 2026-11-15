"""Typed domain errors of the participation module (E22). Reasons carry codes and ids, never a value or a count of
anything private."""

from __future__ import annotations

from typing import Literal

type ErrorKind = Literal[
    "invalid_basis",
    "invalid_rules",
    "view_bound_exceeded",
    "invalid_correction",
    "unauthorized_scope",
    "aggregate_only",
    "invalid_consent",
]


class ParticipationError(Exception):
    kind: ErrorKind = "invalid_basis"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InvalidBasis(ParticipationError):
    kind: ErrorKind = "invalid_basis"


class InvalidRules(ParticipationError):
    kind: ErrorKind = "invalid_rules"


class ViewBoundExceeded(ParticipationError):
    kind: ErrorKind = "view_bound_exceeded"


class InvalidCorrection(ParticipationError):
    kind: ErrorKind = "invalid_correction"


class UnauthorizedScope(ParticipationError):
    kind: ErrorKind = "unauthorized_scope"


class AggregateOnly(ParticipationError):
    kind: ErrorKind = "aggregate_only"


class InvalidConsent(ParticipationError):
    kind: ErrorKind = "invalid_consent"
