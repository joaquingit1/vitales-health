"""The stock overlay of a planning what-if (E32 3.3 INV-S11): packed grows, used never changes, null stays null."""

from __future__ import annotations

from crew_health.kernel.units import Quantity
from crew_health.supplies.domain.model import StockRow, SupplyDef
from crew_health.supplies.domain.quantity import add

OVERLAY_POINTER = "what-if"


def added_row(supply: SupplyDef, row: StockRow | None, amount: Quantity, basis: str) -> StockRow:
    """A row with packed q becomes q + a; a null packed stays null; no row becomes a row with packed a."""
    if amount.value <= 0:
        raise ValueError("an added quantity is above zero")
    if row is None:
        return StockRow(supply.supply_id, Quantity(amount.value, supply.unit), basis, None, OVERLAY_POINTER)
    if row.packed is None:
        return row
    return StockRow(row.supply_id, add(row.packed, amount), row.basis, row.to_be_set_by, row.pointer)
