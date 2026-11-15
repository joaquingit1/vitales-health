"""Feed documents (E23 5.2): validated against feeds.schema.json, then mapped to immutable `Feed` values.

A feed file reads only from its own directory (the fixture or eval copy of the data), never from docs/product, and
only under an explicit demo profile. An invalid document is rejected whole; nothing is partly accepted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from crew_health_adapter_habitat.errors import FeedInvalid, ProfileRefused
from crew_health_adapter_habitat.ports import CabinPressure, Criteria, Origin, ReplayInfo, parse_decimal

MAX_FEED_FILE_BYTES = 4 * 1024 * 1024  # implementation bound on a feed document
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "feeds.schema.json"


@dataclass(frozen=True, slots=True)
class Pace:
    mode: Literal["burst", "fixed", "recorded"]
    interval_s: float = 0.0
    speedup: float = 0.0


@dataclass(frozen=True, slots=True)
class Columns:
    time: str
    value: str
    unit: str


@dataclass(frozen=True, slots=True)
class RowFilter:
    column: str
    equals: str


@dataclass(frozen=True, slots=True)
class Feed:
    feed_id: str
    kind: Literal["replay", "sim"]
    adapter_id: str
    channel_id: str
    unit: str
    pace: Pace
    replay: ReplayInfo | None
    file: Path | None
    columns: Columns | None
    row_filter: RowFilter | None
    carry_naive: bool
    window_from: str | None
    window_to: str | None
    sim: Mapping[str, Any] | None
    cabin_pressure: CabinPressure | None
    criteria: Criteria | None
    basis: Mapping[str, Any]

    @property
    def origin(self) -> Origin:
        return "REPLAY" if self.kind == "replay" else "SIM"


def require_demo_profile(profile: str | None) -> None:
    """Demo replay and sim refuse unless the deployment settings say profile demo (E23 5.2, T-E23-084)."""
    if profile != "demo":
        raise ProfileRefused("deployment profile is not demo")


def load_feed_document(path: Path, schema_path: Path = SCHEMA_PATH) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise FeedInvalid("feed or schema unreadable") from error
    if len(raw) > MAX_FEED_FILE_BYTES:
        raise FeedInvalid("feed document above the byte bound")
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise FeedInvalid("feed document is not JSON") from error
    problems = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: list(e.path))
    if problems:
        pointer = "/" + "/".join(str(p) for p in problems[0].path)
        raise FeedInvalid(f"{len(problems)} schema findings, first at {pointer}")
    found: Mapping[str, Any] = document
    return found


def load_feeds(path: Path, *, profile: str | None, schema_path: Path = SCHEMA_PATH) -> tuple[Feed, ...]:
    require_demo_profile(profile)
    document = load_feed_document(path, schema_path)
    base = path.resolve().parent
    feeds = tuple(_feed(entry, base) for entry in document["feeds"])
    ids = [f.feed_id for f in feeds]
    if len(set(ids)) != len(ids):
        raise FeedInvalid("duplicate feed_id")
    return feeds


def select_feed(feeds: tuple[Feed, ...], kind: str, feed_id: str | None) -> Feed:
    chosen = [f for f in feeds if f.kind == kind and (feed_id is None or f.feed_id == feed_id)]
    if feed_id is None and len(chosen) != 1:
        raise FeedInvalid("name one feed with --feed-id")
    if not chosen:
        raise FeedInvalid("no such feed of that kind")
    return chosen[0]


def _feed(entry: Mapping[str, Any], base: Path) -> Feed:
    kind = entry["kind"]
    file = _inside(base, entry["file"]) if "file" in entry else None
    columns = Columns(**entry["columns"]) if "columns" in entry else None
    unit = columns.unit if columns is not None else entry["unit"]
    filt = entry.get("row_filter")
    return Feed(
        feed_id=entry["feed_id"],
        kind=kind,
        adapter_id=entry["adapter_id"],
        channel_id=entry["channel_id"],
        unit=unit,
        pace=_pace(entry["pace"]),
        replay=ReplayInfo(entry["dataset"], entry["time_basis"]) if kind == "replay" else None,
        file=file,
        columns=columns,
        row_filter=RowFilter(filt["column"], filt["equals"]) if filt else None,
        carry_naive=entry.get("naive_time") == "carry_as_dataset_clock",
        window_from=entry.get("from"),
        window_to=entry.get("to"),
        sim=_sim(entry.get("sim"), base),
        cabin_pressure=_pressure(entry.get("cabin_pressure")),
        criteria=_criteria(entry.get("criteria")),
        basis=entry["basis"],
    )


def _inside(base: Path, name: str) -> Path:
    target = (base / name).resolve()
    if base not in target.parents:
        raise FeedInvalid("feed file outside the feed directory")
    return target


def _pace(raw: Mapping[str, Any]) -> Pace:
    return Pace(raw["mode"], float(raw.get("interval_s", 0)), float(raw.get("speedup", 0)))


def _sim(raw: Mapping[str, Any] | None, base: Path) -> Mapping[str, Any] | None:
    if raw is None:
        return None
    if raw["type"] == "twin":
        return {**raw, "data_dir": str(_inside_dir(base, raw["data_dir"]))}
    return raw


def _inside_dir(base: Path, name: str) -> Path:
    target = (base / name).resolve()
    if target != base and base not in target.parents:
        raise FeedInvalid("twin data directory outside the feed directory")
    return target


def _pressure(raw: Mapping[str, Any] | None) -> CabinPressure | None:
    if raw is None:
        return None
    value = parse_decimal(str(raw["value"]))
    if value is None or value <= 0:
        raise FeedInvalid("cabin pressure is not a positive finite number")
    return CabinPressure(value, raw["basis"])


def _criteria(raw: Mapping[str, Any] | None) -> Criteria | None:
    if raw is None:
        return None

    def dec(key: str) -> Decimal | None:
        return None if key not in raw else parse_decimal(str(raw[key]))

    stuck = raw.get("stuck")
    return Criteria(
        basis=raw["basis"]["text"],
        minimum=dec("min"),
        maximum=dec("max"),
        saturation=tuple(
            d for d in (parse_decimal(str(v)) for v in raw.get("saturation", ())) if d is not None
        ),
        stuck_count=None if stuck is None else int(stuck["count"]),
        stuck_span_s=None if stuck is None else parse_decimal(str(stuck["span_s"])),
        max_rate_per_s=dec("max_rate_per_s"),
    )
