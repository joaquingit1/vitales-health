from datetime import UTC, datetime

from crew_health.clock.domain.errors import InvalidClockValue
from crew_health.kernel.clock import Hlc, require_utc


def milliseconds(at: datetime) -> int:
    require_utc(at)
    delta = at - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _require_stamp(value: Hlc | None, *, logical_max: int) -> None:
    if value is None:
        return
    if (
        type(value.physical_ms) is not int
        or value.physical_ms < 0
        or type(value.logical) is not int
        or not 0 <= value.logical <= logical_max
        or not value.node_id
        or not value.incarnation_id
    ):
        raise InvalidClockValue()


def advance(
    physical: int,
    local: Hlc | None,
    remote: Hlc | None,
    *,
    node_id: str,
    incarnation_id: str,
    logical_max: int,
) -> Hlc:
    if type(physical) is not int or physical < 0 or not node_id or not incarnation_id:
        raise InvalidClockValue()
    for value in (local, remote):
        _require_stamp(value, logical_max=logical_max)
    left, counter = (local.physical_ms, local.logical) if local is not None else (-1, 0)
    right, remote_counter = (remote.physical_ms, remote.logical) if remote is not None else (-1, 0)
    high = max(physical, left, right)
    if high == left == right:
        logical = max(counter, remote_counter) + 1
    elif high == left:
        logical = counter + 1
    elif high == right:
        logical = remote_counter + 1
    else:
        logical = 0
    if not 0 <= logical <= logical_max or high > 9223372036854775807:
        raise InvalidClockValue()
    return Hlc(high, logical, node_id, incarnation_id)
