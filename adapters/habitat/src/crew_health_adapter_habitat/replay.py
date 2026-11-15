"""REPLAY: a recorded file named by a feed entry, with its original dates (E23 5.2, D14).

The dataset time goes out as `observed_at` exactly as recorded. A time without a zone is carried as the dataset's
own clock under the feed's declared `time_basis`; it is never silently called UTC, and a feed that does not declare
the rule rejects such rows. A row that cannot be parsed is rejected with a code and counted; no value is filled.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from crew_health_adapter_habitat.errors import FeedInvalid, SourceUnavailable
from crew_health_adapter_habitat.feeds import Columns, Feed
from crew_health_adapter_habitat.ports import (
    Quantity,
    Settings,
    SourceItem,
    SourceRow,
    parse_decimal,
)

MAX_REPLAY_FILE_BYTES = 64 * 1024 * 1024  # implementation bound on one recorded file


def parse_dataset_time(text: str, *, carry_naive: bool) -> datetime | None:
    """The instant of a recorded timestamp, or None. A zone-free time needs the feed's declared carry rule."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC) if carry_naive else None
    return moment.astimezone(UTC)


class CsvReplaySource:
    """Rows of one CSV column pair, in file order. Positions are 1-based data row numbers, stable across runs."""

    def __init__(self, feed: Feed, settings: Settings) -> None:
        if feed.file is None or feed.columns is None:
            raise FeedInvalid("replay feed needs file and columns")
        self.feed, self.settings, self.carry_naive = feed, settings, feed.carry_naive
        self.lines = _read_lines(feed.file)
        self.header = _header(self.lines, feed.columns, feed.row_filter.column if feed.row_filter else None)
        self.start = self._bound(feed.window_from)
        self.stop = self._bound(feed.window_to)

    def _bound(self, text: str | None) -> datetime | None:
        if text is None:
            return None
        moment = parse_dataset_time(text, carry_naive=self.carry_naive)
        if moment is None:
            raise FeedInvalid("window bound is not a time")
        return moment

    def items(self, after: int) -> Iterator[SourceItem]:
        for position in range(max(after, 0) + 1, len(self.lines)):
            item = self._item(position)
            if item is not None:
                yield item

    def _item(self, position: int) -> SourceItem | None:
        line = self.lines[position]
        if len(line.encode("utf-8")) > self.settings.row_max_bytes:
            return SourceItem(position, None, "ROW_TOO_LARGE")
        fields = _fields(line)
        if fields is None or len(fields) != len(self.header.names):
            return SourceItem(position, None, "MALFORMED_ROW")
        if (
            self.feed.row_filter is not None
            and fields[self.header.filter_index] != self.feed.row_filter.equals
        ):
            return None
        moment = parse_dataset_time(fields[self.header.time_index], carry_naive=self.carry_naive)
        if moment is None:
            return SourceItem(position, None, "BAD_TIME")
        if (self.start is not None and moment < self.start) or (self.stop is not None and moment > self.stop):
            return None
        value = parse_decimal(fields[self.header.value_index])
        if value is None:
            return SourceItem(position, None, "BAD_VALUE")
        return SourceItem(position, SourceRow(self.feed.channel_id, Quantity(value, self.feed.unit), moment))


class _Header:
    def __init__(self, names: list[str], columns: Columns, filter_column: str | None) -> None:
        self.names = names
        try:
            self.time_index = names.index(columns.time)
            self.value_index = names.index(columns.value)
            self.filter_index = names.index(filter_column) if filter_column is not None else -1
        except ValueError as error:
            raise FeedInvalid("a declared column is not in the file header") from error


def _read_lines(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_REPLAY_FILE_BYTES:
            raise FeedInvalid("recorded file above the byte bound")
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SourceUnavailable("recorded file unreadable") from error
    except UnicodeDecodeError as error:
        raise FeedInvalid("recorded file is not UTF-8") from error
    return text.splitlines()


def _fields(line: str) -> list[str] | None:
    try:
        rows = list(csv.reader([line]))
    except csv.Error:
        return None
    return rows[0] if len(rows) == 1 else None


def _header(lines: list[str], columns: Columns, filter_column: str | None) -> _Header:
    if not lines:
        raise FeedInvalid("recorded file is empty")
    names = _fields(lines[0])
    if names is None:
        raise FeedInvalid("recorded file header is not CSV")
    return _Header(names, columns, filter_column)
