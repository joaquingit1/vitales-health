"""Typed domain errors of the warnings engine (E24 3.3, 3.13, 8).

The domain never raises a bare exception across its boundary. The application maps each class
to a problem code through its `code` attribute.
"""

from __future__ import annotations


class RulesDomainError(Exception):
    code: str = "INTERNAL_ERROR"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.code)
        self.detail = detail or self.code


class DefinitionInvalid(RulesDomainError):
    code = "CONFIG_INVALID"


class InvariantViolation(RulesDomainError):
    code = "INTERNAL_ERROR"


class LevelNotAllowed(RulesDomainError):
    code = "LEVEL_NOT_ALLOWED"


class ActionNotAllowed(RulesDomainError):
    code = "ACTION_NOT_ALLOWED"


class InvalidTransition(RulesDomainError):
    code = "INVALID_TRANSITION"


class NotVisible(RulesDomainError):
    code = "NOT_FOUND"


class OriginMismatch(RulesDomainError):
    code = "ORIGIN_MISMATCH"


class InvalidInput(RulesDomainError):
    code = "INVALID_VALUE"
