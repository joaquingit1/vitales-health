"""Typed errors of the help domain. Each carries one contract error code; the API maps it to the envelope."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

HelpErrorCode = Literal[
    "INVALID_VALUE",
    "INVALID_TRANSITION",
    "SESSION_CLOSED",
    "STALE_VERSION",
    "VERSION_REQUIRED",
    "ACTION_NOT_ALLOWED",
    "UNAUTHORIZED_SCOPE",
    "NOT_FOUND",
    "CONFIG_INVALID",
    "DUPLICATE_COMMAND",
    "INTERNAL_ERROR",
]


class HelpError(Exception):
    """Base of every error the help module raises on purpose."""

    def __init__(
        self,
        code: HelpErrorCode,
        message: str,
        *,
        field: str | None = None,
        field_code: str | None = None,
        current_state: Mapping[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code: HelpErrorCode = code
        self.message = message
        self.field = field
        self.field_code = field_code or code
        self.current_state = dict(current_state) if current_state is not None else None
        self.retryable = retryable


class HelpInvalidValue(HelpError):
    def __init__(self, message: str, *, field: str = "/", code: str = "INVALID_VALUE") -> None:
        super().__init__("INVALID_VALUE", message, field=field, field_code=code)


class HelpLogFull(HelpInvalidValue):
    def __init__(self) -> None:
        super().__init__("the help log reached its size limit", field="/", code="LOG_FULL")


class HelpInvalidTransition(HelpError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_TRANSITION", message)


class HelpSessionClosed(HelpError):
    def __init__(self) -> None:
        super().__init__("SESSION_CLOSED", "the help session is closed")


class HelpStaleVersion(HelpError):
    def __init__(self, current_state: Mapping[str, object]) -> None:
        super().__init__("STALE_VERSION", "the session changed", current_state=current_state)


class HelpVersionRequired(HelpError):
    def __init__(self) -> None:
        super().__init__("VERSION_REQUIRED", "If-Match is required")


class HelpActionNotAllowed(HelpError):
    def __init__(self, message: str) -> None:
        super().__init__("ACTION_NOT_ALLOWED", message)


class HelpUnauthorized(HelpError):
    def __init__(self) -> None:
        super().__init__("UNAUTHORIZED_SCOPE", "not allowed for this session")


class HelpNotFound(HelpError):
    def __init__(self) -> None:
        super().__init__("NOT_FOUND", "not found")


class HelpDuplicateCommand(HelpError):
    def __init__(self) -> None:
        super().__init__("DUPLICATE_COMMAND", "this key was used for another command")


class HelpStorageFault(HelpError):
    """A storage fault: nothing committed; the client keeps the command pending and retries."""

    def __init__(self, message: str) -> None:
        super().__init__("INTERNAL_ERROR", message, retryable=True)


class HelpConfigInvalid(HelpError):
    """A pinned or candidate help document failed the help checks; the last good release stays active."""

    def __init__(self, findings: Sequence[str]) -> None:
        self.findings = tuple(findings)
        super().__init__("CONFIG_INVALID", "; ".join(self.findings[:20]) or "invalid help documents")
