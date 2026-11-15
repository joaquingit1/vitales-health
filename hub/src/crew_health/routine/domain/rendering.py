import re
from collections.abc import Mapping

from crew_health.routine.domain.release import Cadence, RoutineWords
from crew_health.routine.domain.vocabulary import OccurrenceState


def render(template: str, values: Mapping[str, str], unknown: str) -> str:
    names = re.findall(r"\{([^{}]+)\}", template)
    if any(name not in values for name in names):
        return unknown
    return re.sub(r"\{([^{}]+)\}", lambda m: values[m.group(1)], template)


def capitalize_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def cadence_text(cadence: Cadence, words: RoutineWords, eva_word: str | None) -> str:
    values = {
        k: str(v)
        for k, v in (
            ("n", cadence.n),
            ("eva_word", eva_word),
            ("event", cadence.event),
            ("phase", cadence.phase),
        )
        if v is not None
    }
    text = render(words.cadence_words.get(cadence.word, words.unknown), values, words.unknown)
    if text == words.unknown:
        return words.unknown
    return capitalize_first(text) + (", " + cadence.note if cadence.note else "")


def hhmm(met: str) -> str:
    from crew_health.routine.domain.errors import InvalidBody

    if re.fullmatch(r"\d+/\d{2}:[0-5]\d:[0-5]\d", met) is None:
        raise InvalidBody("/met", "invalid_met")
    return met.split("/", 1)[1][:5]


def end_text_key(state: OccurrenceState, *, done_today: bool, specific: bool, held: bool) -> str:
    if state == "done":
        return "done_today" if done_today else "done_earlier"
    if state in ("skipped", "missed"):
        return state
    if held:
        return "suppressed"
    if state == "later":
        return "later"
    if state == "due":
        return "due_now" if specific else "due_today"
    return "not_due"
