"""Typed errors of the habitat simulator."""


class SimulatorError(Exception):
    """Base class of every error this package raises."""


class ConfigError(SimulatorError):
    """A configuration document is missing, malformed or inconsistent."""


class NonFiniteValueError(SimulatorError):
    """A computed quantity is NaN or infinite; it is never emitted."""


class DatasetError(SimulatorError):
    """A replay dataset is missing or does not match its recorded checksum."""


class HubRejectedError(SimulatorError):
    """The hub refused a batch with a status that retrying cannot fix."""

    def __init__(self, status: int, code: str | None, detail: str) -> None:
        super().__init__(f"hub refused the batch: HTTP {status} {code or ''} {detail}".strip())
        self.status = status
        self.code = code


class HubUnavailableError(SimulatorError):
    """The hub stayed unreachable for longer than the configured outage bound."""
