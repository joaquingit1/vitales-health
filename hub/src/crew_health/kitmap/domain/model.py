"""E27 3.1: kit map domain types. Pure, frozen, no framework imports.

Vocabulary orders (body regions, environments) are passed in from the generated contract enums by the application
layer; the domain never spells them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

type Operator = Literal["self", "crewmate", "crew_medical_officer"]  # internal enum, config-kit-map schema


class ConditionState(StrEnum):
    MEASURED_HERE = "measured_here"
    REPORT_ONLY = "report_only"
    NEEDS_TEST_NOT_ABOARD = "needs_test_not_aboard"


STATE_ORDER: Final[tuple[ConditionState, ...]] = tuple(ConditionState)
CAMERA_ESTIMATE: Final = "camera_estimate"


@dataclass(frozen=True, slots=True)
class Instrument:
    id: str
    name: str
    needs_text: str
    capability_codes: tuple[str, ...]
    method: str


@dataclass(frozen=True, slots=True)
class CapabilityWords:
    code: str
    label: str
    body_region: str | None
    operator_requirement: Operator
    expected_duration_seconds: int | None
    basis: str
    components: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfirmingTest:
    index: int
    name: str
    test_id: str | None
    instruments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConditionRow:
    condition_id: str
    name: str
    body_regions: tuple[str, ...]
    tests: tuple[ConfirmingTest, ...]
    cliff_url: str
    report_only_findings: tuple[str, ...]
    review_state: Literal["unreviewed", "reviewed", "rejected"]
    written_by: str | None
    checked_by: str | None
    plan_id: str | None
    ground_needs_json: str | None  # canonical JSON of the form copy, served as is
    treatment_scope_json: str | None
    rtdc_json: str | None
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupplyDef:
    supply_id: str
    name: str
    unit: str
    consumable: bool


@dataclass(frozen=True, slots=True)
class KitMapDoc:
    version: str  # SHA-256 of the raw bytes
    label: str
    example: bool
    notice: str | None
    instruments: tuple[Instrument, ...]
    capabilities: tuple[CapabilityWords, ...]
    kits: Mapping[str, frozenset[str]]
    candidates: tuple[str, ...]
    candidate_supplies: tuple[str, ...]
    candidate_skills: tuple[str, ...]
    supplies: Mapping[str, SupplyDef]
    conditions: tuple[ConditionRow, ...]  # document order, rejected rows already dropped

    def instrument(self, instrument_id: str) -> Instrument | None:
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                return instrument
        return None


@dataclass(frozen=True, slots=True)
class TestEvaluation:
    index: int
    instruments: tuple[str, ...]
    needs_no_instrument: bool
    fully_aboard: bool
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConditionEvaluation:
    row: ConditionRow
    state: ConditionState
    tests: tuple[TestEvaluation, ...]
    missing_instruments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StateCounts:
    measured_here: int
    report_only: int
    needs_test_not_aboard: int
    total: int


@dataclass(frozen=True, slots=True)
class RegionSummary:
    region: str
    counts: StateCounts
    marks: tuple[ConditionState, ...]


@dataclass(frozen=True, slots=True)
class GateResult:
    capability_code: str
    environment_id: str
    can: bool
    instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]
    cliff_urls: tuple[str, ...]
    template_key: Literal["can", "cannot"]
    instrument_words: tuple[str, ...]
    kit_map_version: str


@dataclass(frozen=True, slots=True)
class KitCapability:
    code: str
    label: str
    body_region: str | None
    operator_requirement: Operator
    expected_duration_seconds: int | None
    methods: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    missing_instrument_ids: tuple[str, ...]
    applicable_environments: tuple[str, ...]
    kit_status: Literal["available", "unavailable"]
    kit_reason: Literal["not_in_kit"] | None
    components: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Finding:
    """A release check finding with its JSON pointer into kit-map.json."""

    check_id: str
    pointer: str
    message: str


class KitMapError(Exception):
    """Base of typed kit map domain errors."""


class UnknownCapabilityCode(KitMapError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnknownConditionId(KitMapError):
    def __init__(self, index: int, condition_id: str) -> None:
        super().__init__(condition_id)
        self.index = index
        self.condition_id = condition_id


class DocumentInvalid(KitMapError):
    def __init__(self, findings: tuple[Finding, ...]) -> None:
        super().__init__("; ".join(f"{f.check_id} {f.pointer}" for f in findings[:8]))
        self.findings = findings
