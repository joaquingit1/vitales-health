from crew_health.kernel.errors import ErrorCode, ProblemError


class ConfigInvalid(ProblemError):
    def __init__(self, message: str = "configuration validation failed") -> None:
        super().__init__(ErrorCode.CONFIG_INVALID, message)
