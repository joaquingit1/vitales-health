"""Domain invariant failures are typed problems that carry stable detail codes only."""

from crew_health.kernel.errors import ErrorCode, ProblemError

# Invariants a release can break (a malformed catalog policy) are configuration problems; the rest are internal.
_CONFIGURATION = frozenset({"trend_days_invalid", "baseline_policy_invalid", "comparison_band_invalid"})


class TrendsInvariant(ProblemError):
    def __init__(self, reason: str) -> None:
        code = ErrorCode.CONFIG_INVALID if reason in _CONFIGURATION else ErrorCode.INTERNAL_ERROR
        super().__init__(code, code.value)
        self.reason = reason
