from __future__ import annotations

from crew_health.kernel.errors import ErrorCode, ProblemError


class RecordError(ProblemError):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code, code.value)


class PolicyMissing(RecordError):
    """No observation policy covers this code under the pinned release: the value is unknown, never accepted."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.CONTEXT_INAPPLICABLE)
