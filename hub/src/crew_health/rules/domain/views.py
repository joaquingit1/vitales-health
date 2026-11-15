"""Engine and projection views (E24 3.10, 3.12, 3.16, 4.3).

`EngineView` is built per input by the application layer and holds only the keys that input can touch.
Projections are computed per session on read and never stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.rules.domain.definitions import WarningDefinition
from crew_health.rules.domain.dwell import DwellMarker
from crew_health.rules.domain.inputs import MissionEventInput
from crew_health.rules.domain.occurrences import Occurrence
from crew_health.rules.domain.vocabulary import Basis, EnvironmentId, Level, OriginMode, WarningAction
from crew_health.rules.domain.warning import TriggerRef, Warning


@dataclass(frozen=True, slots=True)
class LimitFacts:
    """A sensor limit as the active release states it (sensor-limits.json), for raising and pinning."""

    limit_id: str
    sensor_type_id: str
    level: Level
    role: Literal["warning", "display_only"]  # internal enum
    status: Literal["active", "threshold_pending"]  # internal enum
    value: Decimal | None
    dwell_seconds: int
    stale_after_seconds: int
    limit_fingerprint: str
    basis: Basis | None
    clear_basis: Basis | None


@dataclass(frozen=True, slots=True)
class EngineView:
    release_id: str
    environment_id: EnvironmentId
    mission_mode: OriginMode
    now: datetime
    node_id: str
    definitions: tuple[WarningDefinition, ...]
    fingerprints: Mapping[str, str]
    content_hashes: Mapping[str, str]
    limits: Mapping[str, LimitFacts]
    active: Mapping[tuple[str, str], Warning]
    group_active: tuple[Warning, ...]
    dwell: Mapping[str, DwellMarker]
    occurrences: Mapping[str, tuple[Occurrence, ...]]
    latest_clear: Mapping[tuple[str, str], datetime]
    spent: frozenset[tuple[str, str, TriggerRef]]
    clears_frozen: bool
    answer_events: tuple[MissionEventInput, ...] = ()
    clock_regressed: bool = False
    origin_words: Mapping[str, str] | None = None
    duration_words: Mapping[str, str] | None = None
    context_placeholders: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class HoldState:
    since: datetime
    until: datetime | None


@dataclass(frozen=True, slots=True)
class Attention:
    interrupt: bool
    order: int


@dataclass(frozen=True, slots=True)
class ReplicaInfo:
    origin_node_id: str
    age_seconds: int


type DefinitionStatus = Literal["current", "content_changed", "rule_changed", "withdrawn"]  # internal enum
type GuidanceMode = Literal["show", "simulation_only", "unsupported"]  # internal enum


@dataclass(frozen=True, slots=True)
class Projection:
    allowed_actions: tuple[WarningAction, ...]
    served_steps: tuple[str, ...]
    guidance: GuidanceMode
    review_label: Literal["clinically_reviewed", "not_clinically_reviewed"] | None  # internal enum
    definition_status: DefinitionStatus
    held: HoldState | None
    attention: Attention | None
    replica: ReplicaInfo | None
    care_access: bool


@dataclass(frozen=True, slots=True)
class WarningView:
    warning: Warning
    projection: Projection


@dataclass(frozen=True, slots=True)
class HeadlineView:
    text: str
    warning_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HeldSummary:
    since: datetime
    until: datetime | None
    warnings: tuple[WarningView, ...]
