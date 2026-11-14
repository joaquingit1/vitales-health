"""SYSTEM 8.2 value types, copied verbatim; application/ports.py re-exports them at the 8.2 path.

They live in the application layer, not in a domain package: the generated import contracts forbid any other module
from reaching `guidance.domain`, even through `application.ports`, so consumers (E26, E27, E30) could not import them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from crew_health.kernel.units import Quantity


@dataclass(frozen=True, slots=True)
class ContentRef:
    kind: Literal[
        "flow_step", "plan_step", "teach_step", "passage", "pathway_card", "prepare_step"
    ]  # internal enum
    id: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class Requirements:
    instruments: frozenset[str]
    supplies: Mapping[str, Quantity | None]
    skills: frozenset[str]
    operator: Literal["any_crew", "skill", "crew_medical_officer"]  # internal enum
    operator_skill_id: str | None


@dataclass(frozen=True, slots=True)
class GuidanceRequest:
    content: ContentRef
    purpose: str
    release_id: str
    origin_mode: Literal["LIVE", "REPLAY", "SIM"]  # internal enum
    subject_person_id: str | None
    patient_location_id: str | None
    requirements: Requirements
    fixed_first_screen: bool
    proposed_operator_id: str | None


@dataclass(frozen=True, slots=True)
class Known[T]:
    value: T


@dataclass(frozen=True, slots=True)
class Unknown:
    provider: str
    reason: str


type Fact[T] = Known[T] | Unknown


@dataclass(frozen=True, slots=True)
class StockFact:
    aboard: bool
    quantity_now: Quantity | None
    per_use: Quantity | None
    consumable: bool


@dataclass(frozen=True, slots=True)
class OperatorFact:
    person_id: str
    roster_order: int
    awake: bool
    on_site: bool
    on_eva: bool
    officer_rank: int | None
    skill_freshness: Mapping[str, Literal["fresh", "due", "stale"]]  # internal enum


@dataclass(frozen=True, slots=True)
class GuidanceFacts:
    source_and_hash_valid: Fact[bool]
    purpose_allowed: Fact[bool]
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]  # internal enum
    withdrawn: Fact[bool]
    simulation_only: Fact[bool]
    pinned_release_matches: bool
    environment_applicable: Fact[bool]
    instruments_here: Fact[frozenset[str]]
    stock: Mapping[str, Fact[StockFact]]
    operators: Fact[tuple[OperatorFact, ...]]
    substitute: ContentRef | None


@dataclass(frozen=True, slots=True)
class GuidanceDecision:
    outcome: Literal["show", "show_with_substitute", "not_here", "not_supported"]  # internal enum
    availability: Literal[
        "aboard", "substituted", "not_aboard", "needs_skill_holder", "unknown"
    ]  # internal enum
    executable: bool
    who_can: tuple[str, ...]
    reasons: tuple[str, ...]
    unknown: tuple[str, ...]
    label: Literal["live", "replay", "simulation_only"]  # internal enum
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]  # internal enum
    substitute: ContentRef | None
