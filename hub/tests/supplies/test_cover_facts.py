"""What supplies hands E24 and guidance: cover batches sorted and bounded (SYSTEM 8.1), a stock revision that moves
with every change of the stock facts (M4), and facts that stay unknown when the place or the ledger is unknown."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from crew_health.contracts_gen.enums import ConfigName
from crew_health.supplies.application.ports import (
    CoverFact,
    SupplyCoverChanged,
    SupplyCoverFacts,
    SupplyPartition,
)

from .harness import (
    SUPPLIES_VALUES,
    Settings,
    Station,
    build_station,
    crew_session,
    example,
    headers,
    release,
    use_body,
)

USES = "/v1/supplies/eye_rinse_water/uses"
LUNAR_SIM = SupplyPartition("mission_1", "lunar_surface", "lunar_surface", "SIM")


def rinse_fact(station: Station) -> CoverFact:
    facts = station.resolver.require(SupplyCoverFacts)  # type: ignore[type-abstract]
    with station.db.snapshot() as view:
        rows = facts.rows(view, partition=LUNAR_SIM, after_supply_id=None, limit=100)
    return next(f for f in rows if f.supply_id == "eye_rinse_water")


def kit_with_rinse(quantity: int) -> object:
    kit = copy.deepcopy(example("kit-map.example.json"))
    assert isinstance(kit, dict)
    for row in kit["stock"]["lunar_surface"]:
        if row["supply_id"] == "eye_rinse_water":
            row["quantity"] = quantity
    return kit


@pytest.mark.trace("M4", "SYSTEM 8.1", "E24 supply_cover_low clear")
def test_a_resupply_release_advances_the_stock_revision(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    station.sessions.by_token["crew"] = crew_session()
    assert station.client.post(USES, headers=headers(), json=use_body(4)).status_code == 201
    before = rinse_fact(station)
    station.set_release(release("release_2", {ConfigName.KIT_MAP: kit_with_rinse(20)}))
    after = rinse_fact(station)
    assert (before.cover_uses, after.cover_uses) == (2, 16)
    assert after.stock_revision > before.stock_revision  # E24 clears only on a newer revision
    assert after.last_use_id == before.last_use_id  # a release change keeps the last-use fields
    assert station.client.post(USES, headers=headers(key="use-key-0002"), json=use_body(1)).status_code == 201
    assert rinse_fact(station).stock_revision > after.stock_revision  # and so does every use


@pytest.mark.trace("m1", "SYSTEM 8.1")
def test_the_use_batch_is_sorted_by_supply_and_split_at_the_batch_setting(tmp_path: Path) -> None:
    values = dict(SUPPLIES_VALUES)
    values["supplies.cover_batch_rows"] = 4
    station = build_station(tmp_path, settings=Settings(values))
    station.sessions.by_token["crew"] = crew_session()
    seen: list[SupplyCoverChanged] = []
    station.db.bus.subscribe(SupplyCoverChanged, lambda uow, e: seen.append(e), name="test.cover")
    assert station.client.post(USES, headers=headers(), json=use_body(1)).status_code == 201
    assert [len(batch.rows) for batch in seen] == [4, 2]
    ids = [row.supply_id for batch in seen for row in batch.rows]
    assert ids == sorted(ids) and len(set(ids)) == 6
    assert {batch.partition for batch in seen} == {LUNAR_SIM}
    assert replace(seen[0], rows=()) == replace(seen[1], rows=())  # one use: one cause, one partition
