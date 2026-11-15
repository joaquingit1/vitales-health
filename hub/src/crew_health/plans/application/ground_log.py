# mypy: disable-error-code="type-abstract"
# The registry resolves ports by Protocol class, as SYSTEM 5.12 requires.
"""The sole ground-link consumer in plans; link facts affect the log line and audit only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from crew_health.kernel.errors import StorageUnavailable
from crew_health.plans.application.release import UiCopy
from crew_health.plans.application.world_errors import internal
from crew_health.plans.domain.run import LinkAtOpen


# Provided by E29 (crew_health.catchup); structural copy until it is on main.
@dataclass(frozen=True, slots=True)
class LinkSnapshot:
    status: Literal["up", "delayed", "down"]
    next_contact_at: datetime | None
    one_way_delay: timedelta | None
    simulated: bool
    observed_at: datetime


# Provided by E29 (crew_health.catchup); structural copy until it is on main.
class GroundLinkReader(Protocol):
    def current(self) -> LinkSnapshot: ...


# Provided by E29 (crew_health.catchup); structural copy until it is on main.
class InTimeWords(Protocol):
    def render(self, now: datetime, until: datetime) -> str: ...


class OptionalResolver(Protocol):
    def optional[P](self, port: type[P]) -> P | None: ...


def resolve(resolver: OptionalResolver) -> GroundLine:
    return GroundLine(resolver.optional(GroundLinkReader), resolver.optional(InTimeWords))


def text(copy: UiCopy, section: str, key: str) -> str:
    value = copy.text(section, key)
    if not value:
        raise internal()
    return value


class GroundLine:
    def __init__(self, reader: GroundLinkReader | None, in_time: InTimeWords | None) -> None:
        self.reader, self.in_time = reader, in_time

    def _current(self) -> LinkSnapshot | None:
        if self.reader is None:
            return None
        try:
            return self.reader.current()
        except StorageUnavailable, OSError:
            raise
        except Exception:
            return None

    def line(self, ui_copy: UiCopy, now: datetime) -> tuple[str, datetime | None]:
        snapshot = self._current()
        if snapshot is not None and snapshot.status == "up":
            return text(ui_copy, "plan_lines", "ground_log_up"), None
        if snapshot is not None and snapshot.next_contact_at is not None and self.in_time is not None:
            try:
                words = self.in_time.render(now, snapshot.next_contact_at)
            except StorageUnavailable, OSError:
                raise
            except Exception:
                return text(ui_copy, "designed_states", "unknown"), None
            template = text(ui_copy, "plan_lines", "ground_log_down")
            origin = text(ui_copy, "origin_words", "SIM" if snapshot.simulated else "LIVE")
            return ui_copy.render(
                template, {"in_time": words, "origin_word": origin}
            ), snapshot.next_contact_at
        return text(ui_copy, "designed_states", "unknown"), None

    def at_open(self) -> LinkAtOpen:
        snapshot = self._current()
        if snapshot is None:
            return LinkAtOpen("down", "unknown", None)
        return LinkAtOpen(
            snapshot.status, None if snapshot.status == "up" else "unknown", snapshot.next_contact_at
        )
