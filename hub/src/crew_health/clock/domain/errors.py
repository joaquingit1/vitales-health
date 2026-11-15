from crew_health.kernel.errors import ErrorCode, ProblemError


class InvalidClockValue(ProblemError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.INVALID_VALUE, ErrorCode.INVALID_VALUE.value)


class ClockHistoryConflict(ProblemError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.STALE_VERSION, ErrorCode.STALE_VERSION.value)


class MissingMissionClock(ProblemError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)


class ClockAuthorityDenied(ProblemError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.UNAUTHORIZED_SCOPE, ErrorCode.UNAUTHORIZED_SCOPE.value)
