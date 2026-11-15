"""SYSTEM 8.1 pinned templates: words come from the document versions the help session pinned, never the active
release, so preview, press and every later read render the same approved lines."""

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from crew_health.catchup.domain.compose import CatchUpWords, GroundWords
from crew_health.catchup.domain.errors import CatchUpUnavailable
from crew_health.contracts_gen.enums import ConfigName, ErrorCode
from crew_health.contracts_gen.schemas.config_help_presentation_schema import HelpPresentationJson
from crew_health.contracts_gen.schemas.config_ui_copy_schema import UiCopyJson
from crew_health.help.application.ports import ConfigPinView
from crew_health.kernel.config import ConfigReader
from crew_health.kernel.errors import ProblemError

_CACHE_MAX = 8


@dataclass(frozen=True, slots=True)
class Words:
    catch: CatchUpWords
    ground: GroundWords


def pinned_versions(pins: Sequence[ConfigPinView]) -> dict[str, str]:
    """The two documents catch-up reads, by the versions pinned in the help snapshot."""
    versions = {pin.name: pin.version for pin in pins}
    wanted = (ConfigName.UI_COPY.value, ConfigName.HELP_PRESENTATION.value)
    if any(name not in versions for name in wanted):
        raise CatchUpUnavailable()
    return {name: versions[name] for name in wanted}


def pins_key(versions: dict[str, str]) -> str:
    return json.dumps(versions, sort_keys=True, separators=(",", ":"))


def parse_words(ui_raw: bytes, presentation_raw: bytes) -> Words:
    try:
        ui = UiCopyJson.model_validate_json(ui_raw)
        presentation = HelpPresentationJson.model_validate_json(presentation_raw)
    except (ValueError, ValidationError) as error:
        raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value) from error
    catch, ground = ui.catch_up_words, presentation.ground_message
    if catch is None:
        raise ProblemError(ErrorCode.CONFIG_INVALID, ErrorCode.CONFIG_INVALID.value)
    return Words(
        CatchUpWords(
            catch.step_done_line,
            catch.watch_line,
            catch.advice_line,
            catch.nothing_watched,
            ui.designed_states.unknown,
        ),
        GroundWords(ground.header_template, ground.answers_template, ground.crew_template),
    )


class PinnedWords:
    def __init__(self, reader: ConfigReader | None) -> None:
        self._reader = reader
        self._lock = threading.Lock()
        self._cache: dict[str, Words] = {}

    def words(self, versions: dict[str, str]) -> Words:
        reader = self._reader
        if reader is None:
            raise CatchUpUnavailable()
        key = pins_key(versions)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            ui = reader.document(ConfigName.UI_COPY, versions[ConfigName.UI_COPY.value])
            presentation = reader.document(
                ConfigName.HELP_PRESENTATION, versions[ConfigName.HELP_PRESENTATION.value]
            )
        except ProblemError as error:
            raise CatchUpUnavailable() from error
        words = parse_words(ui.raw, presentation.raw)
        with self._lock:
            if len(self._cache) >= _CACHE_MAX:
                del self._cache[next(iter(self._cache))]
            self._cache[key] = words
        return words
