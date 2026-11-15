from crew_health.events.domain.errors import failure
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.uow import EventType


def cursor(value: str | None, *, retained: int) -> int:
    if value is None:
        return retained
    if not value or len(value) > 19 or not value.isascii() or not value.isdecimal():
        raise failure(ErrorCode.MALFORMED_REQUEST)
    sequence = int(value)
    if sequence > 9223372036854775807:
        raise failure(ErrorCode.MALFORMED_REQUEST)
    if sequence < retained:
        raise failure(ErrorCode.CURSOR_EXPIRED)
    return sequence


def select_cursor(after: str | None, last_event_id: str | None) -> str | None:
    if after is not None and last_event_id is not None and after != last_event_id:
        raise failure(ErrorCode.MALFORMED_REQUEST)
    return after if after is not None else last_event_id


def event_types(value: str | None) -> frozenset[EventType] | None:
    if value is None:
        return None
    if len(value) > sum(len(item.value) + 1 for item in EventType):
        raise failure(ErrorCode.MALFORMED_REQUEST)
    try:
        result = frozenset(EventType(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise failure(ErrorCode.MALFORMED_REQUEST) from error
    return result
