from __future__ import annotations

from crew_health.kernel.evaluation import EvaluatorComputationFailure


class SensorDomainError(Exception):
    """Typed failure of a sensor rule. The code is internal; the application layer maps it."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail

    code: str
    detail: str


class InvalidValue(SensorDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("INVALID_VALUE", detail)


class InvalidUnit(SensorDomainError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("INVALID_UNIT", detail)


class WindowScanBound(SensorDomainError):
    """A window would read more rows than the bound. Never evaluated partially (E23 3.7)."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("WINDOW_SCAN_BOUND", detail)


class ConfigInconsistent(SensorDomainError):
    """Validated configuration contradicts itself at run time. The loader should have rejected it."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("CONFIG_INVALID", detail)


class SensorComputationFailure(EvaluatorComputationFailure):
    """The only failure that S5.10.2 isolates: a non-finite or overflowing computation."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("NON_FINITE_COMPUTATION")
        self.detail = detail

    detail: str
