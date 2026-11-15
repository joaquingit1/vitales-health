"""Wire shapes of the supplies contract (supply-cover-view, supply-stock, supply-use schemas) with exact numbers:
a Decimal is written as its canonical text, never through a float (E32 3, DD1)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from crew_health.supplies.domain.cover import cover_text
from crew_health.supplies.domain.model import CoverRow, SupplyWords
from crew_health.supplies.domain.quantity import decimal_text

type Json = None | bool | int | str | Decimal | Sequence[Json] | Mapping[str, Json]

NOT_ABOARD_BASIS = "team default"  # INV-S5: the basis a not-aboard row shows (supplies/cover-lunar.json)
_MAX_DEPTH = 16


def instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive instant")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def encode(value: Json, *, sort_keys: bool = False) -> bytes:
    """Compact JSON; Decimal numbers keep their exact text. Bounded depth, no recursion."""
    out: list[str] = []
    stack: list[tuple[Json, int] | str] = [(value, 0)]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        node, depth = item
        if depth > _MAX_DEPTH:
            raise ValueError("JSON nesting past the bound")
        if node is None or isinstance(node, bool | int | str):
            out.append(json.dumps(node))
        elif isinstance(node, Decimal):
            out.append(decimal_text(node))
        elif isinstance(node, Mapping):
            keys = sorted(node) if sort_keys else list(node)
            parts: list[tuple[Json, int] | str] = ["{"]
            for i, key in enumerate(keys):
                parts.append(("," if i else "") + json.dumps(key) + ":")
                parts.append((node[key], depth + 1))
            parts.append("}")
            stack.extend(reversed(parts))
        else:
            seq: list[tuple[Json, int] | str] = ["["]
            for i, element in enumerate(node):
                if i:
                    seq.append(",")
                seq.append((element, depth + 1))
            seq.append("]")
            stack.extend(reversed(seq))
    return "".join(out).encode()


def supply_entry(row: CoverRow, environment_id: str, words: SupplyWords) -> dict[str, Json]:
    stock = row.row
    return {
        "stock": {
            "supply_id": row.supply.supply_id,
            "name": row.supply.name,
            "unit": row.supply.unit_word,
            "consumable": row.supply.consumable,
            "environment_id": environment_id,
            "aboard": row.aboard,
            "quantity_packed": None if stock is None or stock.packed is None else stock.packed.value,
            "used": row.used.value,
            "quantity_now": None if row.quantity_now is None else row.quantity_now.value,
            "basis": NOT_ABOARD_BASIS if stock is None else stock.basis,
            "to_be_set_by": None if stock is None else stock.to_be_set_by,
        },
        "used_by": [
            {
                "plan_id": need.plan_id,
                "step_id": need.step_id,
                "per_use": None if need.per_use is None else need.per_use.value,
            }
            for need in row.used_by
        ],
        "cover_uses": row.cover_uses,
        "cover_text": cover_text(row.cover_text_key, row.cover_uses, words),
        "low": row.low,
    }
