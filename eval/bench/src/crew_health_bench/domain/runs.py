from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.kernel.clock import require_utc
from crew_health.kernel.units import Quantity, Unit

from crew_health_bench.domain.documents import integer, number, object_map, text, text_list
from crew_health_bench.domain.errors import BenchError

RunState = Literal[
    "planned", "generating", "generated", "grading", "graded", "reported", "aborted", "invalidated"
]
_CASE_STATUSES = ("completed", "failed_generation", "timeout", "harness_error", "not_run")
_TRANSITIONS = (
    ("", "planned"),
    ("planned", "generating"),
    ("generating", "generated"),
    ("generated", "grading"),
    ("grading", "graded"),
    ("graded", "reported"),
    ("graded", "invalidated"),
    ("reported", "invalidated"),
)


def require_transition(previous: str, target: str) -> None:
    if (previous, target) not in _TRANSITIONS:
        if target != "aborted" or previous not in ("planned", "generating", "generated", "grading", "graded"):
            raise BenchError("invalid_transition")


@dataclass(frozen=True, slots=True)
class Generation:
    case_id: str
    repeat: int
    status: str
    started_at: datetime
    finished_at: datetime
    process_epoch: str
    wall: Quantity
    output_sha256: str
    output_ref: str
    trace_ref: str
    model_calls: int | None

    def __post_init__(self) -> None:
        require_utc(self.started_at)
        require_utc(self.finished_at)
        if (
            not self.case_id
            or self.repeat < 0
            or self.status not in _CASE_STATUSES
            or not self.process_epoch
            or self.wall.unit.code != "s"
            or self.wall.value < 0
            or (self.model_calls is not None and self.model_calls < 0)
        ):
            raise BenchError("case_invalid")

    def record(self, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "case_id": self.case_id,
            "repeat": self.repeat,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "process_epoch": self.process_epoch,
            "wall": {"value": float(self.wall.value), "unit": "s"},
            "output_sha256": self.output_sha256,
            "output_ref": self.output_ref,
            "trace_ref": self.trace_ref,
            "model_calls": self.model_calls,
        }

    @classmethod
    def from_record(cls, row: dict[str, object]) -> Generation:
        wall = object_map(row.get("wall"))
        if "model_calls" not in row:
            raise BenchError("case_invalid")
        try:
            return cls(
                text(row.get("case_id")),
                integer(row.get("repeat")),
                text(row.get("status")),
                datetime.fromisoformat(text(row.get("started_at"))),
                datetime.fromisoformat(text(row.get("finished_at"))),
                text(row.get("process_epoch")),
                Quantity(number(wall.get("value")), Unit(text(wall.get("unit")))),
                text(row.get("output_sha256")),
                text(row.get("output_ref")),
                text(row.get("trace_ref")),
                None if row["model_calls"] is None else integer(row["model_calls"]),
            )
        except ValueError as error:
            raise BenchError("case_invalid", exit_code=6) from error


@dataclass(frozen=True, slots=True)
class Grade:
    case_id: str
    repeat: int
    outcomes: tuple[tuple[str, str, tuple[str, ...]], ...]
    official_score: Decimal | None
    grader_calls: int
    grader_errors: int

    def __post_init__(self) -> None:
        if (
            not self.case_id
            or self.repeat < 0
            or self.grader_calls < 0
            or self.grader_errors < 0
            or any(
                outcome not in ("pass", "fail", "not_applicable", "not_graded")
                for _, outcome, _ in self.outcomes
            )
        ):
            raise BenchError("case_invalid")
        if self.official_score is not None and not math.isfinite(self.official_score):
            raise BenchError("case_invalid")

    def record(self, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "case_id": self.case_id,
            "repeat": self.repeat,
            "outcomes": [
                {"check": check, "outcome": outcome, "evidence": list(evidence)}
                for check, outcome, evidence in self.outcomes
            ],
            "official_score": float(self.official_score) if self.official_score is not None else None,
            "grader_calls": self.grader_calls,
            "grader_errors": self.grader_errors,
        }

    @classmethod
    def from_record(cls, row: dict[str, object]) -> Grade:
        outcomes = row.get("outcomes")
        if not isinstance(outcomes, list):
            raise BenchError("case_invalid", exit_code=6)
        checks: list[tuple[str, str, tuple[str, ...]]] = []
        for item in outcomes:
            value = object_map(item)
            checks.append(
                (text(value.get("check")), text(value.get("outcome")), text_list(value.get("evidence")))
            )
        score = row.get("official_score")
        return cls(
            text(row.get("case_id")),
            integer(row.get("repeat")),
            tuple(checks),
            number(score) if score is not None else None,
            integer(row.get("grader_calls")),
            integer(row.get("grader_errors")),
        )
