from __future__ import annotations


class MissionDomainError(Exception):
    """Typed failure of a mission rule. The code spells a contract error code; the API layer maps it."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail

    code: str
    detail: str


class InvalidValue(MissionDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("INVALID_VALUE", detail)


class InvalidTransition(MissionDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("INVALID_TRANSITION", detail)


class StaleVersion(MissionDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("STALE_VERSION", detail)


class UnauthorizedScope(MissionDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("UNAUTHORIZED_SCOPE", detail)


class OriginMismatch(MissionDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("ORIGIN_MISMATCH", detail)


class ConfirmationRequired(MissionDomainError):
    """C1: a repeat confirm while the change still waits for another crew member (E23 3.3)."""

    def __init__(self, profile_id: str) -> None:
        super().__init__("CONFIRMATION_REQUIRED", profile_id)
        self.profile_id = profile_id

    profile_id: str


class IntegrityViolation(MissionDomainError):
    """A stored state breaks an invariant such as I-M1. Never defaulted, never repaired silently."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("INTEGRITY_VIOLATION", detail)


class NotFound(MissionDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("NOT_FOUND", detail)


class NotInitialized(MissionDomainError):
    """Before mission init mission commands and aggregate queries fail retryably (E23 3.3)."""

    def __init__(self) -> None:
        super().__init__("INTERNAL_ERROR", "mission not initialized")
