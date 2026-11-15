"""Ports E27's kitmap provides (KitGate, KitMapQueries, KitCapabilitiesPort, EquipmentFacts) and consumes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from crew_health.kernel.config import ReleaseHandle
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot, UnitOfWork
from crew_health.kitmap.domain.model import CapabilityWords, Instrument, KitCapability, KitMapDoc

type Snap = UnitOfWork | ReadSnapshot


@dataclass(frozen=True, slots=True)
class KitCheckTemplates:
    can: str
    cannot: str


@dataclass(frozen=True, slots=True)
class KitMapRelease:
    """Everything one computation reads from one configuration release (E27 4.1)."""

    release_id: str
    doc: KitMapDoc
    templates: KitCheckTemplates | None  # ui-copy kit_check_templates; None when the release has no ui-copy
    skill_ids: frozenset[str]  # skills-catalog skill IDs; empty when the release has none
    has_onboard_plans: bool


class KitMapReleaseSource(Protocol):
    def for_release(self, release: ReleaseHandle) -> KitMapRelease: ...


@dataclass(frozen=True, slots=True)
class KitMapContext:
    """Captured once per request: one snapshot, one release, one environment, one instant (KM-I8)."""

    snapshot: Snap
    release: KitMapRelease
    environment_id: str
    at: datetime


# ---- provided ----


@dataclass(frozen=True, slots=True)
class KitCheckFacts:
    """The KitCheck of kit-check.schema.json, as E05 stores it on the request (CONTRACTS 21)."""

    can: bool
    instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]
    cliff_urls: tuple[str, ...]
    text: str
    kit_map_version: str
    environment_id: str


@dataclass(frozen=True, slots=True)
class GateRow:
    condition_id: str
    plan_id: str | None


@dataclass(frozen=True, slots=True)
class GateOutcome:
    kit_check: KitCheckFacts
    capability: CapabilityWords
    rows: tuple[GateRow, ...]  # request order


class KitGate(Protocol):
    """E05 calls it inside its unit of work; raises KitGateInvalid for unknown IDs or a bound exceeded."""

    def check(
        self, uow: UnitOfWork, *, capability_code: str, environment_id: str, condition_ids: Sequence[str]
    ) -> GateOutcome: ...


class KitMapQueries(Protocol):
    """Kit composition for other engines (E28, E32), pinned to the caller's view."""

    def kit_instruments(self, view: Snap, environment_id: str) -> frozenset[str]: ...
    def instrument(self, view: Snap, instrument_id: str) -> Instrument | None: ...


class KitCapabilitiesPort(Protocol):
    """E27 4.2 and E05 2: the release is the snapshot's own (snapshot-bound ReleaseHandle, SYSTEM 5.6, 5.7)."""

    def capabilities(self, snapshot: ReadSnapshot, environment_id: str) -> tuple[KitCapability, ...]: ...


# ---- consumed ----


# Provided by E23 (crew_health.mission); structural copy of E23 4.2 until it is on main.
class MissionStatePort(Protocol):
    def mission_mode(self, uow: Snap | None = None) -> Literal["LIVE", "REPLAY", "SIM"] | None: ...
    def active_environment_id(self, uow: Snap | None = None) -> str | None: ...


# Provided by E02 (crew_health.identity); structural copy of E02 section 2 until it is on main.
class RosterPort(Protocol):
    def person_ids(self) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class SupplyAddition:
    supply_id: str
    quantity: Quantity


@dataclass(frozen=True, slots=True)
class SkillAddition:
    skill_id: str
    person_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanningDelta:
    add_instruments: tuple[str, ...]
    add_supplies: tuple[SupplyAddition, ...]
    add_skills: tuple[SkillAddition, ...]


@dataclass(frozen=True, slots=True)
class PlanColumns:
    plan_id: str | None
    written_for: Literal["condition", "family", "flow"] | None
    plan_aboard: bool


@dataclass(frozen=True, slots=True)
class PlanChange:
    plan_id: str
    condition_id: str | None
    flow_id: str | None
    before_plan_aboard: bool
    before_waits_for_mo: bool
    after_plan_aboard: bool
    after_waits_for_mo: bool


# Implemented by E28 (crew_health.plans); E27 4.1 defines it.
class PlanColumnsPort(Protocol):
    def row_columns(
        self, ctx: KitMapContext, *, kit: frozenset[str], plan_ids: Sequence[str | None]
    ) -> Sequence[PlanColumns]: ...
    def plans_changed(
        self, ctx: KitMapContext, *, base_kit: frozenset[str], delta: PlanningDelta
    ) -> Sequence[PlanChange]: ...


@dataclass(frozen=True, slots=True)
class CoverChange:
    supply_id: str
    from_uses: int | None
    to_uses: int | None


# Implemented by E32 (crew_health.supplies); E27 4.1 defines it.
class CoverPlanningPort(Protocol):
    def cover_changed(
        self, ctx: KitMapContext, *, add_supplies: Sequence[SupplyAddition]
    ) -> Sequence[CoverChange]: ...


# Provided by E10 (crew_health.knowledge); structural copy of E10 4.1 ConditionRegistryPort.active_ids until it is on
# main. Raises when the registry is unavailable (E10 RegistryUnavailable); the release check turns that into a finding.
class ConditionRegistryPort(Protocol):
    def active_ids(self) -> frozenset[str]: ...


# Provided by E05 (crew_health.measurement); structural copy of E27 2 InstrumentReadinessPort until it is on main
# (R27-15). Packed instruments E05 confirms usable now in this snapshot.
class InstrumentReadinessPort(Protocol):
    def usable_now(self, snapshot: Snap, environment_id: str) -> frozenset[str]: ...
