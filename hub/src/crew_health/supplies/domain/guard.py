"""Activation guard S-A1 to S-A3 (E32 3.3): a new release never makes stock negative and never reinterprets the
ledger. Pure: the caller reads the ledger facts in the activation transaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from crew_health.kernel.units import Quantity
from crew_health.supplies.domain.document import Issue, KitSupplies


@dataclass(frozen=True, slots=True)
class UsedTotal:
    """The running total of one supply in one ledger partition (any mission mode)."""

    environment_id: str
    origin_mode: str
    supply_id: str
    used: Quantity


def guard_findings(
    candidate: KitSupplies,
    registered_units: Mapping[str, str],
    totals: Sequence[UsedTotal],
) -> tuple[Issue, ...]:
    """S-A1 a registered unit changes, or a supply with uses turns non consumable; S-A2 a supply with uses loses its
    entry or its stock row there; S-A3 a set packed quantity is below `used` of any partition."""
    issues: list[Issue] = []
    supplies = {s.supply_id: s for s in candidate.supplies}
    for supply_id, unit_code in sorted(registered_units.items()):
        supply = supplies.get(supply_id)
        if supply is not None and supply.unit.code != unit_code:
            issues.append(Issue("S-A1", "kit-map", "/supplies", f"{supply_id}: unit differs from its uses"))
    for total in sorted(totals, key=lambda t: (t.environment_id, t.supply_id, t.origin_mode)):
        if total.used.value <= 0:
            continue
        supply = supplies.get(total.supply_id)
        if supply is None:
            issues.append(Issue("S-A2", "kit-map", "/supplies", f"{total.supply_id}: used supply removed"))
            continue
        if not supply.consumable:
            issues.append(
                Issue("S-A1", "kit-map", "/supplies", f"{total.supply_id}: used supply not consumable")
            )
        row = candidate.stock.get(total.environment_id, {}).get(total.supply_id)
        if row is None:
            issues.append(
                Issue(
                    "S-A2",
                    "kit-map",
                    f"/stock/{total.environment_id}",
                    f"{total.supply_id}: stock row removed",
                )
            )
            continue
        if row.packed is None:
            continue
        if row.packed.unit != total.used.unit or row.packed.value < total.used.value:
            issues.append(Issue("S-A3", "kit-map", row.pointer, f"{total.supply_id}: packed below used"))
    return tuple(issues)
