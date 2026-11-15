from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic_ns

from crew_health.clock.domain.errors import InvalidClockValue
from crew_health.kernel.clock import require_utc
from crew_health.kernel.ids import require_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SystemClock:
    """The only clock adapter that reads operating-system time."""

    def __init__(
        self,
        *,
        epoch: str,
        offset: Callable[[], timedelta],
        utc_now: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], int] = monotonic_ns,
    ) -> None:
        self.epoch = require_id(epoch)
        self.offset, self.utc_now, self.monotonic = offset, utc_now, monotonic

    def now(self) -> datetime:
        value = self.utc_now()
        require_utc(value)
        try:
            corrected = value + self.offset()
        except OverflowError as error:
            raise InvalidClockValue() from error
        require_utc(corrected)
        return corrected

    def monotonic_ns(self) -> int:
        value = self.monotonic()
        if type(value) is not int or value < 0:
            raise InvalidClockValue()
        return value

    def process_epoch(self) -> str:
        return self.epoch
