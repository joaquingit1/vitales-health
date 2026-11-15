"""Public ports of crew_health.supplies (SYSTEM 4.2 row `supplies`, 8.1; E32 4.3) and the ports it consumes.

This file imports nothing from supplies.domain, so other modules may import it (packs DECISIONS A4): SupplyNeed and
StockLevel are owned here; SupplyPartition, CoverFact, SupplyCoverChanged and SupplyCoverFacts are SYSTEM 8.1's
canonical definitions, verbatim."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import ClassVar, Literal, Protocol

from crew_health.kernel.units import Quantity, Unit
from crew_health.kernel.uow import Actor, ReadSnapshot, SessionContext, UnitOfWork

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]  # internal enum

# ---- provided: E28 (plans), SYSTEM 4.2 row 106, E28 4.1 and R28-20 ----


@dataclass(frozen=True, slots=True)
class SupplyNeed:
    plan_id: str
    step_id: str
    supply_id: str
    per_use: Quantity | None


@dataclass(frozen=True, slots=True)
class StockLevel:
    supply_id: str
    aboard: bool
    quantity_now: Quantity | None
    unit: Unit


class StockReader(Protocol):
    def stock(self, environment_id: str) -> Mapping[str, StockLevel]: ...


class SupplyCoverPort(Protocol):
    """R28-20: per (step_id, supply_id) of `needs`, the cover in uses after one more run (CONTRACTS 22 rule 6)."""

    def uses_after_one(
        self, environment_id: str, needs: Sequence[SupplyNeed], stock: Mapping[str, StockLevel]
    ) -> Mapping[tuple[str, str], int | None]: ...


class SupplyLedgerPort(Protocol):
    """Called inside E28's unit of work on step done; raises SupplyBelowZero or UnitMismatch and writes nothing."""

    def record_step_uses(
        self,
        uow: UnitOfWork,
        *,
        environment_id: str,
        plan_run_id: str,
        step_id: str,
        occurrence: int,
        pinned_release_id: str,
        uses: Sequence[SupplyNeed],
        actor: Actor,
        at: datetime,
    ) -> Sequence[str]: ...


class StockUnitMismatch(ValueError):
    """with_added met an addition in another unit than the stock level (no coercion, AGENTS rule 8)."""


def with_added(
    stock: Mapping[str, StockLevel], additions: Sequence[tuple[str, Quantity]]
) -> Mapping[str, StockLevel]:
    """The hypothetical stock of a planning what-if (E32 3.3 INV-S11), pure: a set quantity grows by the addition,
    a null quantity stays null, a supply with no stock gets the added amount. Nothing is ever lowered.
    The added supply is aboard afterwards only when its resulting quantity is above zero or still unset."""
    out = dict(stock)
    with localcontext() as ctx:
        ctx.prec = 34
        for supply_id, amount in additions:
            if not amount.value.is_finite() or amount.value <= 0:
                raise ValueError("an added quantity is above zero")
            level = out.get(supply_id)
            if level is None:
                out[supply_id] = StockLevel(supply_id, True, amount, amount.unit)
                continue
            if level.unit != amount.unit:
                raise StockUnitMismatch(f"{supply_id}: {amount.unit.code} added to {level.unit.code}")
            if level.quantity_now is None and level.aboard:
                continue
            base = level.quantity_now.value if level.quantity_now is not None else Decimal(0)
            total = Quantity(base + amount.value, level.unit)
            out[supply_id] = StockLevel(supply_id, total.value > 0, total, level.unit)
    return out


# ---- provided: E24 (rules), SYSTEM 8.1 verbatim ----


@dataclass(frozen=True, slots=True)
class SupplyPartition:
    mission_id: str
    environment_id: str
    custody_location_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]  # internal enum


@dataclass(frozen=True, slots=True)
class CoverFact:
    supply_id: str
    supply_name: str
    quantity_now: Quantity | None
    per_use: Quantity | None
    cover_uses: int | None
    quantity_text: str
    per_use_text: str | None
    last_use_id: str | None
    last_use_at: datetime | None
    release_id: str
    stock_revision: int
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupplyCoverChanged:
    event_type: ClassVar[str] = "supplies.cover_changed"
    partition: SupplyPartition
    cause: Literal["use", "stock_edit", "release_activation", "custody_transfer", "recovery"]  # internal enum
    cause_ref: str
    observed_at: datetime
    rows: tuple[CoverFact, ...]


class SupplyCoverFacts(Protocol):
    def rows(
        self,
        view: UnitOfWork | ReadSnapshot,
        *,
        partition: SupplyPartition,
        after_supply_id: str | None,
        limit: int,
    ) -> tuple[CoverFact, ...]: ...


# ---- consumed ----


# Provided by E23 (crew_health.mission); structural copy until it is on main (the roles copy, SYSTEM 4.4 row 151).
@dataclass(frozen=True, slots=True)
class MissionFacts:
    mission_id: str
    mission_mode: OriginMode
    active_environment_id: str
    planned_length_days: int | None
    site_node_id: str


# Provided by E23 (crew_health.mission); structural copy until it is on main.
class MissionStatePort(Protocol):
    def current(self, uow: UnitOfWork | ReadSnapshot | None = None) -> MissionFacts | None: ...
    def day_end(self, instant: datetime) -> datetime: ...


# Provided by E28 (crew_health.plans); structural copy of E28 4.2 PlanRunReaderService.context until it is on main.
@dataclass(frozen=True, slots=True)
class RunContext:
    visible: bool
    plan_id: str
    environment_id: str
    origin_mode: OriginMode
    pinned_needs: Mapping[str, tuple[SupplyNeed, ...]]  # step_id: the needs of the run's pinned release


# Provided by E28 (crew_health.plans); structural copy until it is on main.
class PlanRunReader(Protocol):
    def context(self, session: SessionContext, plan_run_id: str) -> RunContext | None: ...


# Defined by E27 (crew_health.kitmap.application.ports); structural copy because that file imports kitmap.domain
# (packs DECISIONS A1). Names and parameters equal main's text; a test compares them with inspect.signature.
class KitMapReleaseRef(Protocol):
    @property
    def release_id(self) -> str: ...


class KitMapContext(Protocol):
    @property
    def snapshot(self) -> UnitOfWork | ReadSnapshot: ...
    @property
    def release(self) -> KitMapReleaseRef: ...
    @property
    def environment_id(self) -> str: ...
    @property
    def at(self) -> datetime: ...


class SupplyAddition(Protocol):
    @property
    def supply_id(self) -> str: ...
    @property
    def quantity(self) -> Quantity: ...


@dataclass(frozen=True, slots=True)
class CoverChange:
    supply_id: str
    from_uses: int | None
    to_uses: int | None


class CoverPlanningPort(Protocol):
    def cover_changed(
        self, ctx: KitMapContext, *, add_supplies: Sequence[SupplyAddition]
    ) -> Sequence[CoverChange]: ...
