"""Immutable protocol readings and the family decoder port."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .ieee11073 import DeviceDateTime, Special

ProtocolUnit = str


@dataclass(frozen=True, slots=True)
class Component:
    field: str
    value: Decimal | None
    missing: Special | None
    unit: ProtocolUnit

    def __post_init__(self) -> None:
        if (self.value is None) == (self.missing is None):
            raise ValueError("exactly_one_of_value_or_missing")
        if self.value is not None and not self.value.is_finite():
            raise ValueError("non_finite_component")


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    characteristic: str
    components: tuple[Component, ...]
    status: frozenset[str]
    device_time: DeviceDateTime | None
    device_clock_set: bool | None
    user_index: int | None
    site_code: int | None


class Decoder(Protocol):
    @property
    def family(self) -> str: ...

    @property
    def characteristics(self) -> frozenset[str]: ...

    def decode(self, characteristic: str, payload: bytes) -> DecodedFrame: ...
