"""Replay of recorded NASA data (docs/product/nasa-data) as REPLAY readings.

Rows are read as recorded: original timestamps (read as UTC, as the feed's time_basis says),
original values and units, no interpolation and no unit conversion. A row that cannot be parsed
is counted and skipped; nothing is substituted for it. The file's SHA-256 must match the value
recorded in feeds.json (taken from the dataset's provenance file) before any row is read.
"""

import csv
import hashlib
import json
import math
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from crew_health_sim_habitat.config import load_json
from crew_health_sim_habitat.errors import ConfigError, DatasetError
from crew_health_sim_habitat.feeder import Feeder, drain, wait_for_room
from crew_health_sim_habitat.payload import WireReading
from crew_health_sim_habitat.units import Quantity
from crew_health_sim_habitat.wiring import Dataset, ReplayFeed

REPLAY_ORIGIN = "REPLAY"
_HASH_CHUNK = 1 << 16


@dataclass(frozen=True, slots=True)
class ReplayRow:
    row_number: int
    observed_at: datetime
    quantity: Quantity


@dataclass(slots=True)
class ReplayCounts:
    rows_read: int = 0
    rows_rejected: int = 0
    rows_filtered: int = 0


def verify_dataset(root: Path, dataset: Dataset) -> Path:
    path = root / dataset.path
    if not path.is_file():
        raise DatasetError(f"dataset {dataset.dataset_id!r} not found at {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    if digest.hexdigest() != dataset.sha256:
        raise DatasetError(f"dataset {dataset.dataset_id!r} does not match its recorded SHA-256")
    return path


def _parse(row: dict[str, str], feed: ReplayFeed) -> tuple[datetime, float] | None:
    try:
        moment = datetime.strptime(row[feed.time_column], feed.time_format).replace(tzinfo=UTC)
        number = float(row[feed.value_column])
    except KeyError, ValueError, TypeError:
        return None
    if not math.isfinite(number):
        return None
    return moment, number


def read_rows(
    path: Path, feed: ReplayFeed, counts: ReplayCounts, columns: tuple[str, ...] | None = None
) -> Iterator[ReplayRow]:
    """Stream the feed's rows in file order; row_number is the 1-based data row in the file.

    columns names the fields of a file with no header row (the dataset's `columns`).
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, fieldnames=list(columns) if columns is not None else None)
        for index, row in enumerate(reader, start=1):
            if feed.filter_column is not None and row.get(feed.filter_column) != feed.filter_equals:
                counts.rows_filtered += 1
                continue
            parsed = _parse(row, feed)
            if parsed is None:
                counts.rows_rejected += 1
                continue
            counts.rows_read += 1
            yield ReplayRow(index, parsed[0], Quantity(parsed[1], feed.unit))


def dataset_rows(root: Path, dataset: Dataset, feed: ReplayFeed, counts: ReplayCounts) -> Iterator[ReplayRow]:
    """Check the dataset's SHA-256, then stream the feed's rows with the dataset's column names."""
    path = verify_dataset(root, dataset)
    yield from read_rows(path, feed, counts, dataset.columns)


def to_wire(row: ReplayRow, feed: ReplayFeed, quality_status: str) -> WireReading:
    if feed.channel_id is None:
        raise DatasetError(f"replay feed {feed.feed_id!r} has no hub channel")
    return WireReading(
        channel_id=feed.channel_id,
        quantity=row.quantity,
        observed_at=row.observed_at,
        origin_mode=REPLAY_ORIGIN,
        quality_status=quality_status,
        replay_dataset=feed.dataset_label,
        replay_time_basis=feed.time_basis,
        cabin_pressure_mmhg=feed.cabin_pressure_mmhg,
        cabin_pressure_basis=None if feed.cabin_pressure_mmhg is None else "assumed",
    )


class ReplayCursor:
    """How many rows of each replay the hub has accepted, written atomically after every delivered batch.

    A restart resumes after those rows instead of sending the dataset again under new sequences. Rows are
    offered in file order and batches leave in order, so the accepted rows are always a prefix. Only a crash
    between the hub's commit and this write can repeat rows, at most one batch.
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._rows: dict[str, int] = {}
        if path is not None and path.exists():
            rows = load_json(path).get("rows")
            if not isinstance(rows, dict) or not all(isinstance(v, int) and v >= 0 for v in rows.values()):
                raise ConfigError(f"replay cursor {path} is malformed")
            self._rows = {str(k): int(v) for k, v in rows.items()}

    @staticmethod
    def key(feed: ReplayFeed, dataset: Dataset) -> str:
        return f"{feed.feed_id}:{dataset.sha256}"

    def delivered(self, key: str) -> int:
        return self._rows.get(key, 0)

    def advance(self, key: str, rows: int) -> None:
        if rows <= self._rows.get(key, 0):
            return
        self._rows[key] = rows
        if self._path is None:
            return
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump({"rows": dict(sorted(self._rows.items()))}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._path)


@dataclass(frozen=True, slots=True)
class ReplayRun:
    resumed_after: int
    offered: int


def feed_replay(
    rows: Iterable[ReplayRow],
    feed: ReplayFeed,
    quality_status: str,
    feeder: Feeder,
    cursor: tuple[ReplayCursor, str],
    sleep: Callable[[float], None],
    gap_s: float = 0.0,
) -> ReplayRun:
    """Offer the rows the hub has not accepted yet, in order, and drain; the cursor follows each delivery."""
    store, key = cursor
    resumed_after = store.delivered(key)
    offered = 0

    def delivered() -> None:
        store.advance(key, resumed_after + offered - feeder.pending)

    for index, row in enumerate(rows):
        if index < resumed_after:
            continue
        wait_for_room(feeder, sleep, delivered)
        feeder.offer(to_wire(row, feed, quality_status))
        offered += 1
        if (gap_s or feeder.pending >= feeder.settings.batch_max_readings) and feeder.pump():
            delivered()
        if gap_s:
            sleep(gap_s)
    drain(feeder, sleep, delivered)
    return ReplayRun(resumed_after, offered)
