from collections.abc import Callable

from crew_health.routine.domain.errors import ConfigInvalid, Finding


def bounded_pages[T](fetch: Callable[[int, int], tuple[T, ...]], *, page: int, maximum: int) -> tuple[T, ...]:
    result: list[T] = []
    for offset in range(0, maximum + 1, page):
        rows = fetch(min(page, maximum - offset + 1), offset)
        result.extend(rows)
        if len(result) > maximum:
            raise ConfigInvalid(
                (Finding("error", "/routine/capacity", "Routine scan exceeds its operational bound"),)
            )
        if len(rows) < min(page, maximum - offset + 1):
            return tuple(result)
    return tuple(result)
