from __future__ import annotations

from collections.abc import Sequence

from crew_health_bench.domain.cases import RevealTable, SimAnswer, SimMeasurement
from crew_health_bench.domain.text import normalized


class LookupSimulator:
    def __init__(self, table: RevealTable) -> None:
        self._table = table
        self.simulator_unmatched = 0

    def answer(self, question_text: str, choices: Sequence[str]) -> SimAnswer:
        question = normalized(question_text)
        for row in self._table.questions:
            if any(normalized(phrase) in question for phrase in row.any_of) and not any(
                normalized(phrase) in question for phrase in row.none_of
            ):
                return row.answer
        self.simulator_unmatched += 1
        return SimAnswer("unknown")

    def measure(self, capability_code: str) -> SimMeasurement:
        for row in self._table.capabilities:
            if row.capability_code == capability_code:
                return row.answer
        return SimMeasurement("unavailable")
