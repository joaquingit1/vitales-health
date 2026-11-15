"""Computed stock and cover (CONTRACTS 25; E32 3.3 INV-S2 to INV-S8). Nothing here is ever typed by a person."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from crew_health.kernel.units import Quantity
from crew_health.supplies.domain.errors import UnitMismatch
from crew_health.supplies.domain.model import (
    CoverRow,
    CoverTextKey,
    Head,
    Need,
    StockRow,
    SupplyDef,
    SupplyWords,
    Threshold,
)
from crew_health.supplies.domain.quantity import MAX_COVER, floor_ratio, same_unit, subtract, zero


def quantity_now(row: StockRow | None, used: Quantity) -> Quantity | None:
    """packed minus used when a row exists and packed is set; None otherwise (INV-S3)."""
    if row is None or row.packed is None:
        return None
    return subtract(row.packed, used)


def is_aboard(row: StockRow | None, now: Quantity | None) -> bool:
    """A stock row exists and quantity_now is not set or above zero (INV-S4)."""
    return row is not None and (now is None or now.value > 0)


def largest_per_use(supply: SupplyDef, needs: Sequence[Need]) -> Quantity | None:
    """The largest set per-use amount among the applicable needs; None when none is set."""
    best: Quantity | None = None
    for need in needs:
        if need.per_use is None:
            continue
        if need.per_use.unit != supply.unit:
            raise UnitMismatch(need.per_use.unit.code, supply.unit.code)
        if best is None or need.per_use.value > best.value:
            best = need.per_use
    return best


def cover_uses(supply: SupplyDef, aboard: bool, now: Quantity | None, per_use: Quantity | None) -> int | None:
    """floor(quantity_now / largest per_use) when consumable, aboard and both set; None otherwise (INV-S6).
    A null stock or per-use gives a null cover, never zero (SYSTEM 8.1)."""
    if not supply.consumable or not aboard or now is None or per_use is None:
        return None
    same_unit(now, per_use)
    if now.value < 0:
        raise ValueError("negative quantity_now reached cover")
    uses = floor_ratio(now, per_use)
    if uses > MAX_COVER:
        raise OverflowError("cover past the exact integer range")
    return uses


def cover_text_key(aboard: bool, consumable: bool, has_need: bool, uses: int | None) -> CoverTextKey:
    """First match of INV-S7."""
    if not aboard:
        return "not_aboard"
    if not consumable:
        return "not_consumable"
    if not has_need:
        return "not_used"
    if uses is None:
        return "cover_unknown"
    return "cover"


def is_low(uses: int | None, threshold: Threshold | None, environment_id: str) -> bool:
    """INV-S8: the threshold is set, applies here, the cover is known and below it. A null cover is never low."""
    if threshold is None or threshold.below_uses is None or environment_id not in threshold.environments:
        return False
    return uses is not None and uses < threshold.below_uses


def cover_text(key: CoverTextKey, uses: int | None, words: SupplyWords) -> str:
    """The ui-copy words of one key; `{n}` is the only value the cover template takes."""
    if key == "cover":
        return words.cover.replace("{n}", str(uses))
    return {
        "cover_unknown": words.cover_unknown,
        "not_used": words.not_used,
        "not_aboard": words.not_aboard,
        "not_consumable": words.not_consumable,
    }[key]


def compute_row(
    supply: SupplyDef,
    row: StockRow | None,
    head: Head | None,
    needs: Sequence[Need],
    threshold: Threshold | None,
    environment_id: str,
) -> CoverRow:
    used = head.used if head is not None else zero(supply.unit)
    same_unit(used, Quantity(Decimal(0), supply.unit))
    now = quantity_now(row, used)
    aboard = is_aboard(row, now)
    per_use = largest_per_use(supply, needs)
    uses = cover_uses(supply, aboard, now, per_use)
    key = cover_text_key(aboard, supply.consumable, bool(needs), uses)
    return CoverRow(
        supply=supply,
        row=row,
        used=used,
        quantity_now=now,
        aboard=aboard,
        used_by=tuple(needs),
        per_use=per_use,
        cover_uses=uses,
        cover_text_key=key,
        low=is_low(uses, threshold, environment_id),
        head=head,
    )
