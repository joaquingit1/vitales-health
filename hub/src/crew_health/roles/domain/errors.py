"""Typed errors of the roles domain and application (E31 design 6.2); the API maps each to the envelope."""

from __future__ import annotations

from collections.abc import Mapping


class RolesError(Exception):
    """Base of every typed roles error."""


class RulesInvalid(RolesError):
    """A configuration document the roles module reads failed a check; the release is rejected."""

    def __init__(self, findings: tuple[tuple[str, str], ...]) -> None:
        super().__init__("roles configuration invalid")
        self.findings = findings


class ContextNotFound(RolesError):
    pass


class RoleNotFound(RolesError):
    pass


class DrillNotFound(RolesError):
    pass


class NotVisible(RolesError):
    pass


class NotAPerson(RolesError):
    pass


class NotAssignee(RolesError):
    pass


class HumanOnly(RolesError):
    """Only a person's or the station's own session may act on a role; the assistant never can."""


class RevisionRequired(RolesError):
    pass


class StaleRevision(RolesError):
    def __init__(self, current: Mapping[str, object]) -> None:
        super().__init__("stale revision")
        self.current = current


class NotCurrent(RolesError):
    def __init__(self, current: Mapping[str, object] | None) -> None:
        super().__init__("assignment is not current")
        self.current = current


class ContextClosed(RolesError):
    def __init__(self, kind: str, current: Mapping[str, object]) -> None:
        super().__init__("context closed")
        self.kind = kind
        self.current = current


class KeyReused(RolesError):
    pass


class NotEligible(RolesError):
    def __init__(self, rule_id: str, text: str) -> None:
        super().__init__(rule_id)
        self.rule_id = rule_id
        self.text = text


class InvalidDrill(RolesError):
    def __init__(self, pointer: str, message: str) -> None:
        super().__init__(message)
        self.pointer = pointer
        self.message = message


class SuppliesNotAboard(RolesError):
    def __init__(self, supply_ids: tuple[str, ...]) -> None:
        super().__init__("supplies not aboard")
        self.supply_ids = supply_ids


class TooManyOpenDrills(RolesError):
    def __init__(self, limit: int) -> None:
        super().__init__("too many open drills")
        self.limit = limit


class OriginMismatch(RolesError):
    pass


class NotRunning(RolesError):
    def __init__(self, current: Mapping[str, object]) -> None:
        super().__init__("drill not running")
        self.current = current


class BadParticipants(RolesError):
    pass


class PortUnavailable(RolesError):
    """A consumed port timed out, raised or returned an invalid answer; never read as an answer."""

    def __init__(self, port: str) -> None:
        super().__init__(port)
        self.port = port
