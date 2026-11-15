"""Recorded NASA data and the twin's scenario table. The scenario table is data (scenarios.json) with a basis on
every entry; this module only loads and slices it."""

from __future__ import annotations

import csv
import json
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Any

MAX_STEPS = 100000  # bound on one scenario's length, an implementation constant


class TwinError(Exception):
    """A scenario, data file or parameter the twin cannot honour. Raised, never papered over."""


def scenario_table() -> dict[str, Any]:
    text = resources.files("crew_health_sim_habitat").joinpath("scenarios.json").read_text(encoding="utf-8")
    table: dict[str, Any] = json.loads(text)
    return table


def read_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as error:
        raise TwinError("data file unreadable: " + path.name) from error
    if len(rows) > MAX_STEPS * 4:
        raise TwinError("data file above the bound")
    return rows


def decimal(text: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation as error:
        raise TwinError("not a number in recorded data") from error
    if not value.is_finite():
        raise TwinError("non-finite number in recorded data")
    return value


def column(rows: list[dict[str, str]], name: str, where: dict[str, str] | None = None) -> list[Decimal]:
    """Values of one column in file order, optionally only rows whose filter column equals a text. Empty cells
    are skipped, never filled."""
    found: list[Decimal] = []
    for row in rows:
        if where is not None and row.get(where["column"]) != where["equals"]:
            continue
        cell = row.get(name, "")
        if cell.strip() == "":
            continue
        found.append(decimal(cell))
    if not found:
        raise TwinError("no recorded values for " + name)
    return found
