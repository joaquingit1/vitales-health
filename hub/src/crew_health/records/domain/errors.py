from __future__ import annotations

from crew_health.kernel.errors import ErrorCode, ProblemError


class RecordError(ProblemError):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code, code.value)
