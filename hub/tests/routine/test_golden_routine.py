"""Independent routine expectations from ROUTINE.md and the source catalogs."""

import csv
import json
import re
from dataclasses import dataclass
from typing import cast

import pytest

from crew_health.routine.domain.release import RoutineItem

from .support import REPO, release


@dataclass(frozen=True, slots=True)
class SummaryRow:
    anchor: str
    name: str
    default: str
    basis: str
    minutes: int
    environments: tuple[str, ...]


def summary_rows() -> tuple[SummaryRow, ...]:
    text = (REPO / "docs/product/ROUTINE.md").read_text(encoding="utf-8")
    summary = text.split("## Summary\n", 1)[1].split("\n## ", 1)[0]
    rows: list[SummaryRow] = []
    for line in summary.splitlines():
        columns = tuple(part.strip() for part in line.strip("|").split("|"))
        if not line.startswith("|") or not columns[0].isdigit():
            continue
        assert len(columns) == 10, line
        link = re.fullmatch(r"\[([^]]+)\]\(#([^)]+)\)", columns[1])
        assert link is not None, columns[1]
        rows.append(SummaryRow(link[2], link[1], columns[2], columns[3], int(columns[5]), columns[6:10]))
    assert rows, "The independent Summary table must contain routine items"
    assert len({row.anchor for row in rows}) == len(rows)
    return tuple(rows)


def source_item(row: SummaryRow, items: tuple[RoutineItem, ...]) -> RoutineItem:
    schedule = cast(
        dict[str, object],
        json.loads((REPO / "docs/product/routine-schedule.json").read_text(encoding="utf-8")),
    )
    source_items = cast(list[dict[str, object]], schedule["items"])
    matches = {
        str(item["id"])
        for item in source_items
        if item["id"] == row.anchor.replace("-", "_") or item["name"] == row.name
    }
    assert len(matches) == 1, f"Summary anchor {row.anchor!r} must map to exactly one schedule item"
    candidates = tuple(item for item in items if item.item_id in matches)
    assert len(candidates) == 1, f"Summary anchor {row.anchor!r} is absent from the parsed release"
    return candidates[0]


def source_cadence(text: str) -> tuple[str, int | None]:
    """CONTRACTS 15 maps walks, events, phases and continuous wear to distinct words."""
    if text in ("daily", "weekly", "monthly"):
        return text, None
    periodic = re.fullmatch(r"every ([1-9][0-9]*) days", text)
    if periodic is not None:
        return "every_n_days", int(periodic[1])
    if text == "always worn":
        return "continuous", None
    if text in ("as needed", "n/a"):
        return "as_needed", None
    if text == "each spacewalk":
        return "per_eva", None
    if text in ("each surface walk", "after landing") or text.startswith("FD "):
        return "after_event", None
    if text in ("pre-flight", "early flight", "return"):
        return "mission_phase", None
    raise AssertionError(f"Unrecognized independent Summary cadence: {text!r}")


@pytest.mark.trace("T-E25-047", "R43")
@pytest.mark.parametrize("row", summary_rows(), ids=lambda row: row.anchor)
def test_release_matches_independent_routine_summary(row: SummaryRow) -> None:
    item = source_item(row, release().items)
    contract = (REPO / "docs/product/CONTRACTS.md").read_text(encoding="utf-8")
    routine_contract = contract.split("## 15. Routine\n", 1)[1].split("\n## ", 1)[0]
    words = set(re.findall(r"`([a-z_]+)`", routine_contract))
    expected = source_cadence(row.default)
    assert expected[0] in words
    assert (item.default_cadence.word, item.default_cadence.n) == expected
    assert item.minutes == row.minutes
    assert item.cadence_basis == row.basis
    environments = ("station_leo", "transit", "lunar_surface", "mars_surface")
    for environment, text in zip(environments, row.environments, strict=True):
        expected = source_cadence(text)
        assert expected[0] in words
        actual = item.cadence_for(environment)
        assert (actual.word, actual.n) == expected, environment


@pytest.mark.trace("T-E25-047", "R43")
def test_summary_covers_every_release_item_once() -> None:
    items = release().items
    mapped = tuple(source_item(row, items).item_id for row in summary_rows())
    assert len(mapped) == len(set(mapped))
    assert set(mapped) == {item.item_id for item in items}


def _routine_sources() -> dict[str, str]:
    """The `## Sources` table of ROUTINE.md: the research record each routine citation was read from."""
    text = (REPO / "docs/product/ROUTINE.md").read_text(encoding="utf-8")
    table = text.split("\n## Sources\n", 1)[1]
    rows: dict[str, str] = {}
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) >= 2 and re.fullmatch(r"[A-Z]+[0-9]+", cells[0]):
            rows[cells[0]] = cells[1].strip('"')
    return rows


@pytest.mark.trace("T-E25-047", "R43")
def test_every_displayed_source_resolves_to_an_independent_catalog() -> None:
    sources = _routine_sources()
    with (REPO / "docs/product/sources.csv").open(encoding="utf-8", newline="") as source_file:
        catalog = {row["source_id"] for row in csv.DictReader(source_file)} | set(sources)
    nasa = (REPO / "docs/product/NASA-SOURCES.md").read_text(encoding="utf-8")
    for line in nasa.splitlines():
        if line.startswith("| "):
            catalog.update(re.findall(r"\b[A-Z]+[0-9]+\b", line.split("|")[1]))
    parsed = release()
    referenced = {
        source_id
        for item in parsed.items
        for source_id in (*item.source_ids, *(s for trigger in item.triggers for s in trigger.source_ids))
    }
    assert referenced <= catalog, f"Uncatalogued routine source IDs: {sorted(referenced - catalog)}"
    for source_id, title in parsed.sources.items():
        assert sources.get(source_id) == title, source_id
