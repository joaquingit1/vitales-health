from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError

if TYPE_CHECKING:
    from crew_health.kernel.units import Quantity, Unit


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    kind: Literal["integer", "quantity", "boolean", "text"]  # internal enum
    unit: Unit | None
    minimum: Decimal | None
    maximum: Decimal | None
    max_text_bytes: int | None
    basis: str

    def __post_init__(self) -> None:
        if not self.key or not self.basis:
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        if any(value is not None and not value.is_finite() for value in (self.minimum, self.maximum)):
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        if self.kind in ("integer", "quantity") and (
            self.minimum is None or self.maximum is None or self.minimum > self.maximum
        ):
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
        if self.kind == "text" and (self.max_text_bytes is None or self.max_text_bytes < 1):
            raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)


class HubSettings(Protocol):
    def integer(self, key: str) -> int: ...
    def quantity(self, key: str) -> Quantity: ...
    def boolean(self, key: str) -> bool: ...
    def text(self, key: str) -> str: ...
