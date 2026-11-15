"""The supply catalog in config/kit-map.json (E32 8.2, owned keys `supplies` and `stock`): every supply a plan aboard
names is listed (S-L5), every unit maps to a UCUM annotation (S-L7), and no packed quantity is entered without a
source. The eye supplies are E28's plan_eye_injury needs and its return stage items: eye_rinse_water (RS33 p. 1;
RS08 p. 558), eye_shield (RS32 p. 8; RS08 pp. 590, 988), eye_wash_set (RS08 pp. 558, 1002) and towels (RS08 p. 558);
the provenance is in docs/build/E32/research/supply-catalog.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_health.supplies.domain.document import read_kit_supplies, read_plans, resolve_needs

CONFIG = Path(__file__).resolve().parents[3] / "config"


@pytest.mark.trace("E32-8.2", "S-L5", "S-L7", "E28-14c")
def test_every_supply_a_plan_names_is_catalogued_with_a_valid_unit_and_no_unsourced_stock() -> None:
    kit, issues = read_kit_supplies(json.loads((CONFIG / "kit-map.json").read_text(encoding="utf-8")))
    assert issues == ()
    catalog = {s.supply_id: s for s in kit.supplies}
    assert {"eye_rinse_water", "eye_shield", "eye_wash_set", "towels"} <= set(catalog)
    assert catalog["eye_wash_set"].consumable is False and catalog["eye_rinse_water"].consumable is True
    plans, plan_issues = read_plans(json.loads((CONFIG / "onboard-plans.json").read_text(encoding="utf-8")))
    _, need_issues = resolve_needs(plans, catalog)
    assert plan_issues == () and need_issues == ()
    stock = json.loads((CONFIG / "kit-map.json").read_text(encoding="utf-8"))["stock"]
    assert all(
        row["quantity"] is None or row["basis"] == "team default" for rows in stock.values() for row in rows
    )
