from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class BackendStatus:
    ready: bool
    reason: str | None
    model_id: str
    revision: str
    sha256: str | None


class ModelBackend(Protocol):
    def status(self) -> BackendStatus: ...

    def predict(
        self, context: Sequence[Decimal], horizon: int, low: Decimal, high: Decimal
    ) -> list[tuple[Decimal, Decimal, Decimal]]: ...
