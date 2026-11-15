"""The ports this module provides (E32 4.3; SYSTEM 8.1; packs DECISIONS A4, A5), over one snapshot or the caller's
unit of work. Every missing fact stays missing: an unknown mission or supply is Unknown, null or not aboard."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal

from crew_health.guidance.application.ports import Fact, Known, StockFact, Unknown
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot
from crew_health.supplies.application.context import (
    Deps,
    MissionUnknown,
    cover_rows,
    current_mission,
    partition,
)
from crew_health.supplies.application.events import cover_fact
from crew_health.supplies.application.ports import (
    CoverChange,
    CoverFact,
    KitMapContext,
    StockLevel,
    SupplyAddition,
    SupplyNeed,
    SupplyPartition,
)
from crew_health.supplies.application.repository import View
from crew_health.supplies.domain.cover import compute_row
from crew_health.supplies.domain.errors import FieldIssue, SuppliesError, WhatIfInvalid
from crew_health.supplies.domain.model import CoverRow, Partition, SupplyDef
from crew_health.supplies.domain.overlay import added_row
from crew_health.supplies.domain.quantity import boundary_unit, floor_ratio, subtract, within_bounds

PROVIDER = "supplies"
NOT_LISTED = "supply not in the kit map"
NO_MISSION = "no current mission"
NOT_READABLE = "supplies not readable"
OVERLAY_BASIS = "team default"  # INV-S11: a row the what-if adds has the not-aboard basis of INV-S5


def _rows(deps: Deps, view: View, where: Partition) -> tuple[CoverRow, ...]:
    index = deps.indexes.get(view.release)
    heads = deps.repository.heads(view, where, limit=deps.limits.max_supplies)
    return cover_rows(deps, view, index, where, heads)


def cover_facts(
    deps: Deps, view: View, where: SupplyPartition, after_supply_id: str | None, limit: int
) -> tuple[CoverFact, ...]:
    """SupplyCoverFacts.rows: supply_id order, after the cursor, at most min(limit, supplies.cover_batch_rows)."""
    bound = min(limit, deps.limits.cover_batch_rows)
    if bound < 1:
        return ()
    own = Partition(where.mission_id, where.origin_mode, where.environment_id, where.custody_location_id)
    index = deps.indexes.get(view.release)
    rows = sorted(_rows(deps, view, own), key=lambda r: r.supply.supply_id)
    after = [r for r in rows if after_supply_id is None or r.supply.supply_id > after_supply_id]
    return tuple(cover_fact(r, index) for r in after[:bound])


def stock_levels(deps: Deps, environment_id: str) -> Mapping[str, StockLevel]:
    with deps.snapshots.snapshot() as view:
        where = partition(current_mission(deps, view), environment_id)
        return {
            r.supply.supply_id: StockLevel(r.supply.supply_id, r.aboard, r.quantity_now, r.supply.unit)
            for r in _rows(deps, view, where)
        }


def stock_facts(
    deps: Deps, snapshot: ReadSnapshot, location_id: str | None, supplies: Mapping[str, Quantity | None]
) -> Mapping[str, Fact[StockFact]]:
    """guidance StockFacts: the location is the environment until custody locations exist (SYSTEM 8.5)."""
    try:
        facts = current_mission(deps, snapshot)
        where = partition(facts, location_id or facts.active_environment_id)
        rows = {r.supply.supply_id: r for r in _rows(deps, snapshot, where)}
    except MissionUnknown:
        return {key: Unknown(PROVIDER, NO_MISSION) for key in sorted(supplies)}
    except SuppliesError:
        return {key: Unknown(PROVIDER, NOT_READABLE) for key in sorted(supplies)}
    out: dict[str, Fact[StockFact]] = {}
    for key in sorted(supplies):
        row = rows.get(key)
        if row is None:
            out[key] = Unknown(PROVIDER, NOT_LISTED)
            continue
        asked = supplies[key]
        per_use = (
            None
            if asked is None
            else Quantity(asked.value, boundary_unit(asked.unit, row.supply.unit_word, row.supply.unit))
        )
        out[key] = Known(StockFact(row.aboard, row.quantity_now, per_use, row.supply.consumable))
    return out


def aboard(deps: Deps, supply_ids: Collection[str], environment_id: str) -> Mapping[str, bool]:
    """roles SupplyAboardPort: not aboard whenever it is not known to be aboard (fail closed)."""
    try:
        with deps.snapshots.snapshot() as view:
            where = partition(current_mission(deps, view), environment_id)
            rows = {r.supply.supply_id: r for r in _rows(deps, view, where)}
    except SuppliesError:
        return dict.fromkeys(sorted(supply_ids), False)
    return {s: (rows[s].aboard if s in rows else False) for s in sorted(supply_ids)}


def uses_after_one(
    deps: Deps, environment_id: str, needs: Sequence[SupplyNeed], stock: Mapping[str, StockLevel]
) -> Mapping[tuple[str, str], int | None]:
    """R28-20: floor((quantity_now - this need's per_use) / the largest per_use of the supply among `needs`); a
    negative remainder is 0; None when not consumable, not aboard or a value is not set (CONTRACTS 22 rule 6)."""
    with deps.snapshots.snapshot() as view:
        supplies = deps.indexes.get(view.release).supplies
    out: dict[tuple[str, str], int | None] = {}
    for need in needs:
        supply, level = supplies.get(need.supply_id), stock.get(need.supply_id)
        key = (need.step_id, need.supply_id)
        if supply is None or level is None or not supply.consumable or not level.aboard:
            out[key] = None
            continue
        amounts = [
            a for n in needs if n.supply_id == need.supply_id and (a := _amount(n, supply)) is not None
        ]
        now, mine = level.quantity_now, _amount(need, supply)
        if now is None or not amounts or any(a.unit != now.unit for a in amounts):
            out[key] = None
            continue
        rest = now if mine is None else subtract(now, mine)
        out[key] = 0 if rest.value < 0 else floor_ratio(rest, max(amounts, key=lambda a: a.value))
    return out


def _amount(need: SupplyNeed, supply: SupplyDef) -> Quantity | None:
    if need.per_use is None:
        return None
    return Quantity(need.per_use.value, boundary_unit(need.per_use.unit, supply.unit_word, supply.unit))


def cover_changed(
    deps: Deps, ctx: KitMapContext, additions: Sequence[SupplyAddition]
) -> Sequence[CoverChange]:
    """E27's what-if: per addition, in request order and listed even when equal, the cover before and after adding
    it to the stock (INV-S11). Nothing is written."""
    view = ctx.snapshot
    if ctx.release.release_id != view.release.release_id:
        raise WhatIfInvalid(
            (FieldIssue("/release_id", "INVALID_VALUE", "the what-if names another release"),)
        )
    index = deps.indexes.get(view.release)
    _check_additions(deps, index.supplies, additions)
    try:
        where = partition(current_mission(deps, view), ctx.environment_id)
    except MissionUnknown:
        return tuple(CoverChange(a.supply_id, None, None) for a in additions)
    needs = index.needs_in(ctx.environment_id)
    stock = index.kit.stock.get(ctx.environment_id, {})
    out: list[CoverChange] = []
    for i, addition in enumerate(additions):
        supply = index.supplies[addition.supply_id]
        if deps.corrupt.has(where, supply.supply_id):
            out.append(CoverChange(supply.supply_id, None, None))
            continue
        head = deps.repository.head(view, where, supply.supply_id)
        row = stock.get(supply.supply_id)
        amount = Quantity(
            addition.quantity.value, boundary_unit(addition.quantity.unit, supply.unit_word, supply.unit)
        )
        if amount.unit != supply.unit:
            raise WhatIfInvalid((FieldIssue(f"/add_supplies/{i}/quantity", "INVALID_VALUE", "another unit"),))
        args = (head, needs.get(supply.supply_id, ()), index.threshold, ctx.environment_id)
        try:
            before = compute_row(supply, row, *args)
            after = compute_row(supply, added_row(supply, row, amount, OVERLAY_BASIS), *args)
        except OverflowError as error:
            raise WhatIfInvalid(
                (FieldIssue(f"/add_supplies/{i}/quantity", "INVALID_VALUE", "cover past 2^53 uses"),)
            ) from error
        out.append(CoverChange(supply.supply_id, before.cover_uses, after.cover_uses))
    return tuple(out)


def _check_additions(deps: Deps, supplies: Mapping[str, object], additions: Sequence[SupplyAddition]) -> None:
    issues: list[FieldIssue] = []
    seen: set[str] = set()
    for i, addition in enumerate(additions):
        pointer = f"/add_supplies/{i}"
        value: Decimal = addition.quantity.value
        if addition.supply_id in seen:
            issues.append(FieldIssue(pointer + "/supply_id", "INVALID_VALUE", "a supply is added once"))
        seen.add(addition.supply_id)
        if addition.supply_id not in supplies:
            issues.append(FieldIssue(pointer + "/supply_id", "INVALID_VALUE", NOT_LISTED))
        if (
            not value.is_finite()
            or value <= 0
            or not within_bounds(
                value, maximum=deps.limits.max_quantity, max_scale=deps.limits.max_quantity_scale
            )
        ):
            issues.append(FieldIssue(pointer + "/quantity", "INVALID_VALUE", "quantity outside its bounds"))
    if len(additions) > deps.limits.max_supplies:
        issues.append(
            FieldIssue("/add_supplies", "INVALID_VALUE", "more additions than supplies.max_supplies")
        )
    if issues:
        raise WhatIfInvalid(issues)
