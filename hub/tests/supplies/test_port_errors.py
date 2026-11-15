"""Errors that cross a provided port are the port errors of supplies/application/ports.py, importable by consumers
and rendered by them as the contract envelope (M3; AGENTS 3), and `drawn`, the pure draw E28 and medicines use."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health.contracts_gen.enums import ErrorCode
from crew_health.kernel.errors import ProblemError
from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor
from crew_health.kitmap.application import ports as kitmap_ports
from crew_health.supplies.application.ports import (
    CoverPlanningPort,
    StockBelowZero,
    StockLevel,
    StockUnitMismatch,
    SupplyInputInvalid,
    SupplyLedgerPort,
    SupplyNeed,
    SupplyPortError,
    drawn,
)

from .harness import build_station

RINSE = Unit("{rinse}")
AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
amounts = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal(1000), places=6)


def q(value: object, unit: Unit = RINSE) -> Quantity:
    return Quantity(Decimal(str(value)), unit)


@dataclass(frozen=True)
class _Release:
    release_id: str


@pytest.mark.trace("M3", "E32 6.1 WhatIfInvalid", "SYSTEM modules-independent")
def test_an_invalid_what_if_raises_a_port_error_kitmap_renders_as_422(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    planning = station.resolver.require(CoverPlanningPort)  # type: ignore[type-abstract]
    unlisted = kitmap_ports.SupplyAddition("not_in_the_kit_map", Quantity(Decimal(1), Unit("{x}")))
    with station.db.snapshot() as view:
        context = kitmap_ports.KitMapContext(view, _Release("release_1"), "station_leo", AT)  # type: ignore[arg-type]
        with pytest.raises(ProblemError) as raised:  # kitmap's what-if route catches exactly this type
            planning.cover_changed(context, add_supplies=[unlisted])
    assert isinstance(raised.value, SupplyInputInvalid)
    assert raised.value.code == ErrorCode.INVALID_VALUE and raised.value.status == 422
    assert raised.value.field_errors[0].field == "/add_supplies/0/supply_id"


@pytest.mark.trace("M3", "INV-S10", "E32 4.2")
def test_a_step_below_zero_raises_the_importable_stock_below_zero(tmp_path: Path) -> None:
    station = build_station(tmp_path)
    ledger = station.resolver.require(SupplyLedgerPort)  # type: ignore[type-abstract]
    too_much = SupplyNeed("plan_eye_injury", "eye_rinse", "eye_rinse_water", q(7))
    with pytest.raises(StockBelowZero) as raised:
        with station.db.begin(actor=Actor("person", "p"), correlation_id="cor-step") as uow:
            ledger.record_step_uses(
                uow,
                environment_id="lunar_surface",
                plan_run_id="run_1",
                step_id="eye_rinse",
                occurrence=1,
                pinned_release_id="release_1",
                uses=[too_much],
                actor=Actor("person", "p"),
                at=AT,
            )
    error = raised.value
    assert error.code == "SUPPLY_BELOW_ZERO" and error.status == 422
    assert (error.supply_id, error.quantity_now, error.requested) == ("eye_rinse_water", q(6), q(7))
    assert station.rows("SELECT count(*) FROM supplies_use") == [(0,)]


@pytest.mark.trace("M3", "drawn")
def test_drawn_subtracts_exactly_and_refuses_unknown_other_units_and_below_zero() -> None:
    level = StockLevel("eye_rinse_water", True, q("6"), RINSE)
    assert drawn(level, q("2.5")) == StockLevel("eye_rinse_water", True, q("3.5"), RINSE)
    assert drawn(level, q(6)) == StockLevel("eye_rinse_water", False, q(0), RINSE)  # none left: not aboard
    for refused in (
        StockLevel("eye_rinse_water", True, None, RINSE),  # unknown never admits a draw
        StockLevel("eye_rinse_water", False, q(3), RINSE),
    ):
        with pytest.raises(StockBelowZero):
            drawn(refused, q(1))
    with pytest.raises(StockBelowZero) as below:
        drawn(level, q("6.000001"))
    assert below.value.quantity_now == q(6) and below.value.requested == q("6.000001")
    with pytest.raises(StockUnitMismatch) as other:
        drawn(level, q(1, Unit("{towel}")))
    assert other.value.code == "INVALID_UNIT" and isinstance(other.value, SupplyPortError)
    with pytest.raises(SupplyInputInvalid):
        drawn(level, q(0))


@pytest.mark.trace("M3", "INV-S9", "rule 5")
@settings(max_examples=200, deadline=None)
@given(
    now=st.one_of(st.none(), st.decimals(min_value=0, max_value=1000, places=6)),
    aboard=st.booleans(),
    amount=amounts,
)
def test_drawn_never_yields_a_negative_quantity(now: Decimal | None, aboard: bool, amount: Decimal) -> None:
    level = StockLevel("eye_rinse_water", aboard, None if now is None else q(now), RINSE)
    try:
        after = drawn(level, q(amount))
    except StockBelowZero:
        assert now is None or not aboard or now - amount < 0
        return
    assert after.quantity_now is not None and after.quantity_now.value >= 0
    assert now is not None and after.quantity_now == q(now - amount)
    assert after.aboard is (after.quantity_now.value > 0)
