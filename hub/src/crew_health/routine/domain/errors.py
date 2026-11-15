from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Literal["error", "warning"]  # internal enum
    pointer: str
    message: str


class RoutineError(Exception):
    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NotFound(RoutineError):
    def __init__(self) -> None:
        super().__init__("not_found")


class InvalidTransition(RoutineError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("invalid_transition")


class StaleVersion(RoutineError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision
        super().__init__("stale_version")


class InvalidBody(RoutineError):
    def __init__(self, pointer: str, reason: str) -> None:
        self.pointer, self.reason = pointer, reason
        super().__init__("invalid_body")


class ActionNotAllowed(RoutineError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("action_not_allowed")


class Unauthorized(RoutineError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("unauthorized")


class ConfigInvalid(RoutineError):
    def __init__(self, findings: tuple[Finding, ...]) -> None:
        self.findings = findings
        super().__init__("config_invalid")


class DuplicateOverride(RoutineError):
    def __init__(self, override_id: str) -> None:
        self.override_id = override_id
        super().__init__("duplicate_override")


class RecheckRejected(RoutineError):
    def __init__(
        self, code: Literal["item_unknown", "person_mismatch", "not_today", "already_added", "capacity"]
    ) -> None:
        super().__init__(code)


class MissionUnavailable(RoutineError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("mission_unavailable")


class VersionRequired(RoutineError):
    def __init__(self) -> None:
        super().__init__("version_required")


class ConfigVersionMismatch(RoutineError):
    def __init__(self, current_release_id: str) -> None:
        self.current_release_id = current_release_id
        super().__init__("config_version_mismatch")
