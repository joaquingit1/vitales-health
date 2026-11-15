"""Review item 2: the eye rinse declares the water it needs, so absent water can never make it available.

Golden, checked against the sources and not against config: NASA's Burn Chemical Eye CliFF (catalog RS33,
docs/product/cliff/forms/ICL19.json page 1) says "Immediate eye irrigation therapy is the most important initial
management of chemical eye burns"; RS30 (NHS, non-NASA, used only for what the form does not state) gives clean
water for at least 20 minutes. So the rinse step needs clean rinse water, and without it the step is not runnable.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from crew_health.kernel.units import Quantity, Unit
from crew_health.plans.domain.availability import EnvContext, Person, StockLevel, assess_step, runnable_now
from crew_health.plans.domain.model import Plan, SupplyInfo, parse_document

ROOT = Path(__file__).resolve().parents[3]
RINSE_STEPS = ("eyi_rinse", "eyi_rinse_weightless")
WATER = "eye_rinse_water"
ENVIRONMENTS = ("station_leo", "transit", "lunar_surface", "mars_surface")
CREW = (Person("mara", "Mara", 1, None, True, {}), Person("tomas", "Tomas", 2, 1, True, {}))


def shipped_plan(supplies: dict[str, SupplyInfo]) -> Plan:
    raw = json.loads((ROOT / "config/onboard-plans.json").read_text(encoding="utf-8"))
    plan = parse_document(raw, document_sha256="0" * 64, supplies=supplies).plan("plan_eye_injury")
    assert plan is not None
    return plan


def context(environment: str, stock: dict[str, StockLevel]) -> EnvContext:
    return EnvContext(environment, frozenset(), stock, CREW, "mara", {}, False)


@pytest.mark.trace("EI3", "R50", "V83", "T28-02")
def test_rinse_declares_water_with_an_unknown_quantity_and_a_nasa_source() -> None:
    plan = shipped_plan({})
    for step_id in RINSE_STEPS:
        step = plan.step(step_id)
        assert step is not None
        (need,) = [n for n in step.needs.supplies if n.supply_id == WATER]
        assert need.per_use is None
    rinse = plan.step("eyi_rinse")
    assert rinse is not None and "RS33" in rinse.source_ids


@pytest.mark.trace("EI3", "R50", "V83", "T28-02")
def test_absent_or_uncatalogued_water_never_makes_the_rinse_available() -> None:
    uncatalogued = shipped_plan({})
    catalogued = shipped_plan({WATER: SupplyInfo(WATER, Unit("{rinse}"), True)})
    water = {WATER: StockLevel(WATER, True, Quantity(Decimal(1), Unit("{rinse}")))}
    for environment in ENVIRONMENTS:
        for plan, stock in ((uncatalogued, water), (catalogued, {}), (uncatalogued, {})):
            step = plan.step("eyi_rinse")
            assert step is not None
            assessment = assess_step(step, plan, context(environment, stock))
            assert not runnable_now(assessment), (environment, stock)
            assert WATER in assessment.missing_supplies
    step = catalogued.step("eyi_rinse")
    assert step is not None
    assert runnable_now(assess_step(step, catalogued, context("lunar_surface", water)))
