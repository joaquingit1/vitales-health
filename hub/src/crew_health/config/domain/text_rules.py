from __future__ import annotations

import re
from dataclasses import dataclass

from crew_health.config.domain.errors import ConfigInvalid


@dataclass(frozen=True, slots=True)
class AmountRules:
    max_text_bytes: int
    max_matches: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_text_bytes <= 8388608 or not 1 <= self.max_matches <= 10000:
            raise ConfigInvalid("text rule bounds invalid")

    def find(self, text: str) -> tuple[tuple[int, int], ...]:
        if len(text.encode("utf-8")) > self.max_text_bytes:
            raise ConfigInvalid("text byte bound exceeded")
        pattern = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|mL|IU|units)\b", re.IGNORECASE)
        spans: list[tuple[int, int]] = []
        for match in pattern.finditer(text):
            if len(spans) >= self.max_matches:
                raise ConfigInvalid("text match bound exceeded")
            spans.append(match.span())
        return tuple(spans)
