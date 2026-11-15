from crew_health.kernel.errors import ErrorCode, ProblemError


class KnowledgeInvalid(ProblemError):
    def __init__(self, reason: str) -> None:
        super().__init__(ErrorCode.INVALID_VALUE, reason)


class RegistryUnavailable(ProblemError):
    def __init__(self, reason: str = "registry_unavailable") -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, reason, retryable=True)
