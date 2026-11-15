from datetime import datetime, timedelta
from string import Formatter

from crew_health.clock.domain.errors import InvalidClockValue, MissingMissionClock
from crew_health.kernel.clock import require_utc
from crew_health.kernel.met import MetLabel, MissionClockVersion


def validate_version(value: MissionClockVersion) -> None:
    if (
        not value.clock_version
        or type(value.generation) is not int
        or value.generation <= 0
        or (value.effective_until is not None and value.effective_until <= value.effective_from)
        or not value.day_label_template
    ):
        raise MissingMissionClock()
    try:
        fields = tuple(Formatter().parse(value.day_label_template))
    except ValueError as error:
        raise MissingMissionClock() from error
    if any(
        field is not None and (field != "day" or spec or conversion) for _, field, spec, conversion in fields
    ):
        raise MissingMissionClock()


def met_label(at: datetime, version: MissionClockVersion) -> MetLabel:
    require_utc(at)
    validate_version(version)
    seconds = (at - version.epoch) // timedelta(seconds=1)
    day, remainder = divmod(seconds, 86400)
    if not 0 <= day <= 999:
        raise InvalidClockValue()
    hour, remainder = divmod(remainder, 3600)
    minute, second = divmod(remainder, 60)
    return MetLabel(
        f"{day:03}/{hour:02}:{minute:02}:{second:02}",
        day + 1,
        version.day_label_template.format(day=day + 1),
        version.clock_version,
    )


def bounds(mission_day: int, version: MissionClockVersion) -> tuple[datetime, datetime]:
    validate_version(version)
    if type(mission_day) is not int or not 1 <= mission_day <= 1000:
        raise InvalidClockValue()
    try:
        start = version.epoch + timedelta(days=mission_day - 1)
        return start, start + timedelta(days=1)
    except OverflowError as error:
        raise InvalidClockValue() from error
