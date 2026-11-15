"""Supplies domain types (E32 3.2 with SYSTEM 8.1 partitions). Pure values: no I/O, no clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crew_health.kernel.units import Quantity, Unit

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]  # internal enum (common.schema origin_mode)
type CoverTextKey = Literal[
    "cover", "cover_unknown", "not_used", "not_aboard", "not_consumable"
]  # internal enum
ORIGIN_MODES: tuple[OriginMode, ...] = ("LIVE", "REPLAY", "SIM")


@dataclass(frozen=True, slots=True)
class SupplyDef:
    supply_id: str
    name: str
    unit_word: str  # the kit-map word the API shows
    unit: Unit  # its UCUM annotation unit `{word}`
    consumable: bool


@dataclass(frozen=True, slots=True)
class StockRow:
    supply_id: str
    packed: Quantity | None  # cumulative quantity packed into the environment; None: not set
    basis: str
    to_be_set_by: str | None
    pointer: str  # JSON pointer into kit-map.json, provenance of the row


@dataclass(frozen=True, slots=True)
class Need:
    """One supply need of one plan step (SupplyNeed of the ports, plus provenance)."""

    plan_id: str
    step_id: str
    supply_id: str
    per_use: Quantity | None
    pointer: str  # JSON pointer into onboard-plans.json


@dataclass(frozen=True, slots=True)
class Threshold:
    """The supply_cover_low definition of warning-rules.json (Q19: inactive while below_uses is null)."""

    below_uses: int | None
    environments: frozenset[str]


@dataclass(frozen=True, slots=True)
class SupplyWords:
    """ui-copy supply_words: every display string of the cover view comes from here."""

    cover: str
    cover_unknown: str
    not_used: str
    not_aboard: str
    not_consumable: str


@dataclass(frozen=True, slots=True)
class Partition:
    """The ledger partition (DD4 and SYSTEM 8.1 SupplyPartition): one mission, mode, environment and custody site."""

    mission_id: str
    origin_mode: OriginMode
    environment_id: str
    custody_location_id: str


@dataclass(frozen=True, slots=True)
class Head:
    """The latest use of one supply in one partition: its running total is the partition's `used` (DD2)."""

    seq: int
    used: Quantity
    last_use_id: str | None
    last_use_at: datetime | None


@dataclass(frozen=True, slots=True)
class Admitted:
    """A use the ledger may append: its partition sequence and running total after it."""

    supply_id: str
    quantity: Quantity
    seq: int
    used_after: Quantity


@dataclass(frozen=True, slots=True)
class CoverRow:
    supply: SupplyDef
    row: StockRow | None
    used: Quantity
    quantity_now: Quantity | None
    aboard: bool
    used_by: tuple[Need, ...]
    per_use: Quantity | None  # the largest applicable per-use amount, the cover divisor
    cover_uses: int | None
    cover_text_key: CoverTextKey
    low: bool
    head: Head | None  # evidence for E24: last use ID and its observation time
