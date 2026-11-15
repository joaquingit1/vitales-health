"""Known-outcome replay (AGENTS: replay known NASA outcomes as model validation) for medicines and supplies.

Case iss-ijv-thrombosis-anticoagulant-supply (catalog N42, NEJM 2020 letter; N41, JAMA Netw Open 2019): an ISS
astronaut with an internal jugular vein thrombosis was treated with enoxaparin 1.5 mg/kg once daily from 20 vials of
300 mg; the dose was reduced to 1 mg/kg on day 33 "to extend therapy until oral apixaban could be delivered", and
apixaban arrived on day 42. The case is qualitative: the body weight is not published, so the test states what the
product must say for any weight (cover from the documented stock and regimen, a low warning while the cover is short
of the resupply, the documented reduction bridging it) and what it says without a weight (cover unknown, never a
default weight; a per-kilogram dose refuses `weight_missing`). Every number comes from the case files."""

from __future__ import annotations

import csv
import json
from decimal import Decimal

import pytest

from crew_health.kernel.units import Quantity, Unit
from crew_health.medicines.domain.engine import propose
from crew_health.supplies.domain.cover import compute_row
from crew_health.supplies.domain.model import CoverRow, Need, StockRow, SupplyDef, Threshold

from .harness import REPO
from .kit import ask
from .test_engine import _weighted

CASE = REPO / "docs/product/nasa-data/cases/iss-ijv-thrombosis-anticoagulant-supply"
MG = Unit("{mg}")
ENOXAPARIN = SupplyDef("enoxaparin", "enoxaparin", "mg", MG, True)


def _facts() -> dict[str, dict[str, str]]:
    with (CASE / "anticoagulant_stock_and_regimen.csv").open(encoding="utf-8", newline="") as handle:
        return {row["item"]: row for row in csv.DictReader(handle)}


def _cover(stock_mg: Decimal, daily_mg: Decimal | None, days_to_resupply: int) -> CoverRow:
    """One use is one daily dose, so the cover is in days; the warning threshold is the days left to resupply."""
    return compute_row(
        ENOXAPARIN,
        StockRow("enoxaparin", Quantity(stock_mg, MG), "case", None, "/case"),
        None,
        (
            Need(
                "ijv",
                "daily_dose",
                "enoxaparin",
                None if daily_mg is None else Quantity(daily_mg, MG),
                "/case",
            ),
        ),
        Threshold(days_to_resupply, frozenset({"station_leo"})),
        "station_leo",
    )


@pytest.mark.trace("KO-iss-ijv-thrombosis-anticoagulant-supply", "N41", "N42", "INV-S6", "INV-S7", "INV-S8")
def test_the_documented_anticoagulant_supply_constraint_surfaces_as_the_supply_cover_finding() -> None:
    outcome = json.loads((CASE / "outcome.json").read_text(encoding="utf-8"))
    assert outcome["case_type"] == "qualitative" and outcome["environment_id"] == "station_leo"
    facts = _facts()
    vials = Decimal(facts["onboard_stock"]["value"])
    per_vial = Decimal(facts["onboard_stock_per_vial"]["value"])
    full = Decimal(facts["dose_start"]["value"])  # mg/kg/d
    reduced = Decimal(facts["dose_reduced"]["value"])
    reduced_day = int(facts["dose_reduced"]["day_after_diagnosis"])
    resupply_day = int(
        facts["switch"]["day_after_diagnosis"]
    )  # apixaban on board: enoxaparin no longer needed
    stock = vials * per_vial
    assert (stock, full, reduced, reduced_day, resupply_day) == (
        Decimal(6000),
        Decimal("1.5"),
        Decimal(1),
        33,
        42,
    )

    # No weight: the daily amount is unknown, so the cover is unknown, never computed from an assumed weight, and an
    # unknown cover never raises or clears the warning (INV-S6, INV-S8); the dose path asks for the weight.
    unknown = _cover(stock, None, resupply_day)
    assert (unknown.cover_uses, unknown.cover_text_key, unknown.low) == (None, "cover_unknown", False)
    weighted, _ = _weighted()
    refusal = propose(ask("acetaminophen_325_tab", "ICL63"), weighted)
    assert refusal.refusal is not None and refusal.refusal.code == "weight_missing"

    # With a weight: the full regimen falls short of the resupply above 6000 / (1.5 x 42) = 95.2 kg, and the
    # documented day-33 reduction (33 days at 1.5 plus 9 at 1 mg/kg = 58.5 mg/kg) bridges it up to 102.6 kg.
    for kg, short in ((Decimal(70), False), (Decimal(95), False), (Decimal(100), True), (Decimal(110), True)):
        start = _cover(stock, full * kg, resupply_day)
        assert start.cover_uses == int(stock / (full * kg)) and start.low is short, kg
    kg = Decimal(100)
    left = stock - full * kg * reduced_day
    after = _cover(left, reduced * kg, resupply_day - reduced_day)
    assert (after.cover_uses, after.low) == (10, False)  # 1050 mg at 100 mg/d covers the 9 days to day 42
    kept = _cover(left, full * kg, resupply_day - reduced_day)
    assert (kept.cover_uses, kept.low) == (7, True)  # without the reduction the stock ends on day 40
