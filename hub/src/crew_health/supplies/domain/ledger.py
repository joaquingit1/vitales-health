"""Use admission (E32 3.3 INV-S9, INV-S10, INV-S12, INV-S14): no stored use makes a set quantity_now negative."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from crew_health.kernel.units import Quantity
from crew_health.supplies.domain.cover import quantity_now
from crew_health.supplies.domain.errors import SupplyBelowZero, UnitMismatch
from crew_health.supplies.domain.model import Admitted, Head, StockRow, SupplyDef
from crew_health.supplies.domain.quantity import add, subtract, zero


def admit(supply: SupplyDef, row: StockRow | None, head: Head | None, quantity: Quantity) -> Admitted:
    """One use of `quantity` against the partition head.

    Refuses with SupplyBelowZero when the supply has no stock row here or when quantity_now minus the use is below
    zero; with quantity_now not set the use is admitted and only `used` grows (INV-S9). Units never coerce."""
    if quantity.unit != supply.unit:
        raise UnitMismatch(quantity.unit.code, supply.unit.code)
    if quantity.value <= 0:
        raise ValueError("a use quantity is positive")
    used = head.used if head is not None else zero(supply.unit)
    if row is None:
        raise SupplyBelowZero(supply.supply_id, None, quantity)
    now = quantity_now(row, used)
    if now is not None and subtract(now, quantity).value < 0:
        raise SupplyBelowZero(supply.supply_id, now, quantity)
    seq = head.seq + 1 if head is not None else 1
    return Admitted(supply.supply_id, quantity, seq, add(used, quantity))


@dataclass(frozen=True, slots=True)
class StepUse:
    supply: SupplyDef
    quantity: Quantity  # the pinned per-use amount


def admit_step(
    uses: Sequence[StepUse],
    rows: Mapping[str, StockRow],
    heads: Mapping[str, Head],
) -> tuple[Admitted, ...]:
    """Every consumable pinned need of a step, in supply order; any one below zero admits none (INV-S10).

    Two needs of one supply in one step accumulate against the same head, so their sum is what is checked."""
    admitted: list[Admitted] = []
    running: dict[str, Head] = dict(heads)
    for use in sorted(uses, key=lambda u: u.supply.supply_id):
        supply_id = use.supply.supply_id
        result = admit(use.supply, rows.get(supply_id), running.get(supply_id), use.quantity)
        running[supply_id] = Head(result.seq, result.used_after, None, None)
        admitted.append(result)
    return tuple(admitted)


def check_chain(
    entries: Sequence[tuple[int, Quantity, Quantity]], previous: tuple[int, Quantity] | None
) -> tuple[int, Quantity]:
    """Contiguity and running totals of one partition, oldest first (INV-S12). Each entry is
    (seq, quantity, used_after). Returns the last (seq, used_after); raises ValueError at the first break."""
    seq, used = previous if previous is not None else (0, None)
    for entry_seq, amount, used_after in entries:
        expected = amount if used is None else add(used, amount)
        if entry_seq != seq + 1 or amount.value <= 0 or expected != used_after:
            raise ValueError("ledger chain broken")
        seq, used = entry_seq, used_after
    if used is None:
        raise ValueError("empty chain")
    return seq, used
