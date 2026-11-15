"""SYSTEM 8.2 public API of `crew_health.guidance`.

The 8.2 value types (model.py) and the pure `decide` (rule.py, no I/O) are exported here under their 8.2 names;
consumers import them from this module. The fact provider protocols are consumer owned (SYSTEM 5.12): each provider is optional, and an absent or failing provider yields a typed Unknown.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from crew_health.guidance.application.model import ContentRef as ContentRef
from crew_health.guidance.application.model import Fact as Fact
from crew_health.guidance.application.model import GuidanceDecision as GuidanceDecision
from crew_health.guidance.application.model import GuidanceFacts as GuidanceFacts
from crew_health.guidance.application.model import GuidanceRequest as GuidanceRequest
from crew_health.guidance.application.model import Known as Known
from crew_health.guidance.application.model import OperatorFact as OperatorFact
from crew_health.guidance.application.model import Requirements as Requirements
from crew_health.guidance.application.model import StockFact as StockFact
from crew_health.guidance.application.model import Unknown as Unknown
from crew_health.guidance.application.rule import apply_substitute as apply_substitute
from crew_health.guidance.application.rule import decide as decide
from crew_health.kernel.units import Quantity
from crew_health.kernel.uow import ReadSnapshot


class GuidanceEligibility(Protocol):
    def evaluate(self, snapshot: ReadSnapshot, request: GuidanceRequest) -> GuidanceDecision: ...


@dataclass(frozen=True, slots=True)
class ContentFactSet:
    """What the content provider knows about one ContentRef at its pinned release (R-E10-17 interim shape)."""

    source_and_hash_valid: Fact[bool]
    purpose_allowed: Fact[bool]
    clinical_review: Literal["clinically_reviewed", "not_clinically_reviewed"]  # internal enum
    withdrawn: Fact[bool]
    simulation_only: Fact[bool]
    pinned_release_matches: bool
    environments: Fact[frozenset[str]]
    substitute: ContentRef | None
    substitute_requirements: Requirements | None


# Consumer-owned protocol, implemented by E10 (crew_health.knowledge); shape pending R-E10-17.
class ContentFacts(Protocol):
    def content(
        self, snapshot: ReadSnapshot, content: ContentRef, *, purpose: str, release_id: str
    ) -> ContentFactSet: ...


# Provided by E23 (crew_health.mission); structural copy of E23 4.2 until it is on main.
class EnvironmentFacts(Protocol):
    def applicable(
        self, snapshot: ReadSnapshot, patient_location_id: str | None, environments: Collection[str]
    ) -> Fact[bool]: ...


# Consumer-owned protocol, implemented by E27 (crew_health.kitmap): instruments aboard where the patient is.
class EquipmentFacts(Protocol):
    def instruments_here(
        self, snapshot: ReadSnapshot, patient_location_id: str | None
    ) -> Fact[frozenset[str]]: ...


# Consumer-owned protocol, implemented by E31 (crew_health.roles): the roster with presence and skill freshness.
class SkillFacts(Protocol):
    def operators(
        self, snapshot: ReadSnapshot, subject_person_id: str | None, patient_location_id: str | None
    ) -> Fact[tuple[OperatorFact, ...]]: ...


# Consumer-owned protocol, implemented by E32 (crew_health.supplies): one Fact per requested supply.
class StockFacts(Protocol):
    def stock(
        self,
        snapshot: ReadSnapshot,
        patient_location_id: str | None,
        supplies: Mapping[str, Quantity | None],
    ) -> Mapping[str, Fact[StockFact]]: ...
