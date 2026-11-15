from typing import Literal

from crew_health.kernel.errors import (
    ErrorCode,
    ProblemError,
)


def fail(code: ErrorCode) -> ProblemError:
    return ProblemError(code, code.value)


class StorageConstraintViolated(ProblemError):
    """A write broke a schema rule (append-only trigger abort, CHECK, UNIQUE, FOREIGN KEY). The store is intact, so
    this is not StorageUnavailable("corrupt"), and retrying the same write breaks the same rule: not retryable."""

    def __init__(self) -> None:
        self.cause: Literal["constraint"] = "constraint"
        super().__init__(ErrorCode.INTERNAL_ERROR, self.cause, retryable=False)
