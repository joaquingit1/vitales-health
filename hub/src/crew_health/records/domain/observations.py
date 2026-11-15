from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.kernel.clock import NormalizedTime, TimeInput, require_utc
from crew_health.kernel.errors import ErrorCode
from crew_health.kernel.units import ConvertedQuantity, Quantity, Unit, WireQuantity
from crew_health.records.domain.errors import RecordError

type OriginMode = Literal["LIVE", "REPLAY", "SIM"]
type AcquisitionMethod = Literal[
    "self_report",
    "manual_instrument",
    "device",
    "camera_estimate",
    "crewmate_palpation",
    "imported_report",
    "clinician_entry",
    "derived",
]
type QualityStatus = Literal["accepted", "rejected", "uncertain", "not_assessed"]


@dataclass(frozen=True, slots=True)
class RecordContext:
    patient_id: str
    operator_id: str
    mission_id: str
    origin_mode: OriginMode
    observed: TimeInput
    care_basis_ref: str


@dataclass(frozen=True, slots=True)
class ObservationInput:
    patient_id: str
    episode_id: str | None
    code: str
    original: WireQuantity
    observed_at: datetime
    origin_mode: OriginMode
    acquisition_method: AcquisitionMethod
    device_id: str | None
    context: tuple[tuple[str, str], ...]
    body_region: str | None
    supersedes: str | None
    measurement_request_id: str | None


@dataclass(frozen=True, slots=True)
class ObservationPolicy:
    code: str
    version: str
    source_ref: str
    canonical_unit: Unit
    accepted_wire_units: frozenset[str]
    methods: frozenset[AcquisitionMethod]
    minimum: Decimal
    maximum: Decimal
    saturation_minimum: Decimal
    saturation_maximum: Decimal
    stuck_count: int
    max_change_per_second: Decimal
    manual_checks: bool

    def validate(self) -> None:
        numbers = (
            self.minimum,
            self.maximum,
            self.saturation_minimum,
            self.saturation_maximum,
            self.max_change_per_second,
        )
        if (
            not self.source_ref
            or not self.version
            or not self.code
            or not self.accepted_wire_units
            or not self.methods
            or any(not number.is_finite() for number in numbers)
            or not self.minimum <= self.saturation_minimum < self.saturation_maximum <= self.maximum
            or self.stuck_count < 2
            or self.stuck_count > 1000
            or self.max_change_per_second <= 0
            or not self.manual_checks
        ):
            raise RecordError(ErrorCode.CONTEXT_INAPPLICABLE)


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    status: QualityStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    patient_id: str
    operator_id: str
    mission_id: str
    episode_id: str | None
    code: str
    quantity: Quantity
    conversion: ConvertedQuantity
    observed: TimeInput
    normalized: NormalizedTime
    entered_at: datetime
    received_at: datetime
    origin_mode: OriginMode
    acquisition_method: AcquisitionMethod
    quality_status: QualityStatus
    quality_rule_version: str
    quality_reasons: tuple[str, ...]
    device_id: str | None
    context: tuple[tuple[str, str], ...]
    body_region: str | None
    supersedes: str | None
    release_id: str
    proof_ref: str | None
    measurement_request_id: str | None
    care_basis_ref: str


def assess_quality(
    policy: ObservationPolicy,
    quantity: Quantity,
    observed_at: datetime,
    previous: tuple[tuple[datetime, Quantity], ...],
) -> QualityAssessment:
    policy.validate()
    require_utc(observed_at)
    if quantity.unit != policy.canonical_unit:
        raise RecordError(ErrorCode.INVALID_UNIT)
    reasons: list[str] = []
    if quantity.value < policy.minimum or quantity.value > policy.maximum:
        reasons.append("out_of_range")
    if quantity.value <= policy.saturation_minimum or quantity.value >= policy.saturation_maximum:
        reasons.append("saturation")
    if any(last.unit != quantity.unit for _, last in previous):
        # History in another unit cannot run the stuck and rate checks, and a reading those checks cannot clear
        # is never trusted: it is rejected and kept, with range and saturation reasons intact (AGENTS 2).
        return QualityAssessment("rejected", tuple(reasons) + ("history_unit_mismatch",))
    if len(previous) >= policy.stuck_count - 1 and all(
        item[1] == quantity for item in previous[: policy.stuck_count - 1]
    ):
        reasons.append("stuck_value")
    if previous:
        at, last = previous[0]
        require_utc(at)
        elapsed = Decimal(str((observed_at - at).total_seconds()))
        if elapsed > 0 and abs(quantity.value - last.value) / elapsed > policy.max_change_per_second:
            reasons.append("rate_of_change")
    return QualityAssessment("rejected" if reasons else "accepted", tuple(reasons))
