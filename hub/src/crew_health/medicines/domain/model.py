"""Medicines domain types (E32 3.4 to 3.7): configuration views, the dose request and context, and the proposal."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from crew_health.medicines.domain.units import Amount

type Mode = Literal["LIVE", "REPLAY", "SIM"]
type LotState = Literal["in_specification", "adjust", "do_not_use"]
type Path = Literal["labeled", "labeled_with_estimate", "compensated"]
type AlertLevel = Literal["override_required", "acknowledge", "info"]
type ConfirmBy = Literal["crew_medical_officer", "any_trained_crew"]

REVIEWED = frozenset({"reviewed", "approved"})

REFUSAL_CODES = (
    "no_working_diagnosis",
    "rule_not_found",
    "rule_not_reviewed_live",
    "formulary_mismatch",
    "allergy_list_unknown",
    "patient_context_missing",
    "weight_missing",
    "weight_stale",
    "weight_implausible",
    "weight_change_unconfirmed",
    "unit_incompatible",
    "clock_discontinuity",
    "min_interval_not_passed",
    "course_ended",
    "no_usable_lot",
    "potency_adjustment_disabled",
    "adjustment_not_permitted",
    "no_deliverable_amount",
    "per_administration_max_exceeded",
    "period_max_exceeded",
    "cumulative_max_exceeded",
    "catastrophic_limit",
    "insufficient_stock",
    "history_too_large",
)

ALERT_KINDS = (
    "allergy_same_ingredient",
    "allergy_class",
    "cross_sensitivity",
    "interaction",
    "duplicate_ingredient",
    "duplicate_class",
    "organ_caution",
    "condition_caution",
    "outside_medicine_overlap",
    "potency_shortfall",
    "clock_unverified_after_restart",
    "spaceflight_caution",
    "consult_ground_first",
    "potency_information",
)


# ---- configuration (treatments.json, medicines.json, the pharmacy formulary) ----


@dataclass(frozen=True, slots=True)
class PeriodLimit:
    period_seconds: int
    limit: Amount


@dataclass(frozen=True, slots=True)
class IngredientRule:
    ingredient_id: str
    dose: Amount | None
    dose_per_kg: Amount | None
    max_per_administration: Amount
    max_per_period: tuple[PeriodLimit, ...]
    max_cumulative: Amount | None
    cumulative_scope: Literal["course", "mission"]
    min_interval_seconds: int
    min_effective: Amount | None
    course_seconds: int | None


@dataclass(frozen=True, slots=True)
class DoseRule:
    rule_id: str
    review_state: str
    ingredients: tuple[IngredientRule, ...]
    organ_cautions: tuple[str, ...]
    condition_cautions: tuple[str, ...]
    source_ids: tuple[str, ...]
    pointer: str = ""


@dataclass(frozen=True, slots=True)
class ProductRule:
    product_id: str
    deliverable_increment: Amount
    rounding_tolerance_fraction: Decimal
    high_alert: bool
    confirm_by: ConfirmBy
    consult_ground_first: bool
    therapeutic_classes: tuple[str, ...]
    spaceflight_cautions: tuple[str, ...]
    pointer: str = ""
    # The compendial acceptance range of the labeled amount (lower, upper), as fractions of label claim; None when
    # no source gives it. Within it, the labeled path counts the labeled amount (E32 3.6 correction, brief section 4).
    assay_acceptance: tuple[Decimal, Decimal] | None = None


@dataclass(frozen=True, slots=True)
class FormularyIngredient:
    ingredient_id: str
    strength: Amount  # per one deliverable increment unit (a tablet, a dose, a mL)


@dataclass(frozen=True, slots=True)
class FormularyProduct:
    product_id: str
    name: str
    per: str  # the strength's denominator as a UCUM unit (`{tbl}`, `{dose}`, `mL`)
    ingredients: tuple[FormularyIngredient, ...]
    narrow_therapeutic_index: bool
    potency_adjustment_allowed: bool
    degradant_profile_reviewed: bool


@dataclass(frozen=True, slots=True)
class IndicationLine:
    line: Literal["first_choice", "alternative"]
    product_id: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class Interaction:
    pair: tuple[str, str]
    severity: str
    management_text: str


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    kind: str
    level: AlertLevel
    override_roles: tuple[str, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeightPolicy:
    plausible_min_kg: Decimal
    plausible_max_kg: Decimal
    max_age_seconds: int
    max_change_fraction: Decimal
    change_window_seconds: int


@dataclass(frozen=True, slots=True)
class Treatments:
    version: str
    sha256: str
    example: bool
    review_state: str
    indications: Mapping[str, tuple[IndicationLine, ...]]
    rules: Mapping[str, DoseRule]
    products: Mapping[str, ProductRule]
    weight: WeightPolicy | None
    catastrophic: Mapping[str, Amount]
    allergy_classes: Mapping[str, frozenset[str]]
    cross_sensitivity: tuple[frozenset[str], ...]
    interactions: tuple[Interaction, ...]
    policies: Mapping[str, AlertPolicy]
    context_max_age_seconds: int
    unit_display: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PotencyPolicy:
    mode: Literal["compensate", "information_only"]
    require_measured_assay: bool
    exclude_narrow_therapeutic_index: bool
    exclude_high_alert: bool
    require_degradant_profile_reviewed: bool
    require_measured_storage_history: bool
    require_validated_engine: bool
    validated_engine_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MedicinesPolicy:
    version: str
    sha256: str
    example: bool
    review_state: str
    environment_zones: Mapping[str, tuple[str, ...]]
    proposal_ttl_seconds: int
    max_assessment_age_seconds: int
    potency: PotencyPolicy
    live_authorizations: Mapping[str, str]  # product_id -> authorized_at text


# ---- the dose request and its context (E32 4.3 DoseContext) ----


@dataclass(frozen=True, slots=True)
class Potency:
    low: Decimal  # fractions of label claim
    median: Decimal
    high: Decimal


@dataclass(frozen=True, slots=True)
class LotSummary:
    """The adapter's mapping of one pharmacy LotAssessment (E32 5.3)."""

    run_id: str
    as_of: datetime
    state: LotState
    potency: Mapping[str, Potency]
    adjustment_eligible: bool
    assay_used: bool
    custody_declared_or_gapped: bool
    engine_version: str
    alternative_lot_ids: tuple[str, ...] = ()
    alternative_product_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LotView:
    lot_id: str
    product_id: str
    lot_status: str
    labeled_expiry: datetime
    quantity_now: Decimal  # in the product's deliverable unit
    adverse_inspection: bool
    summary: LotSummary | None


@dataclass(frozen=True, slots=True)
class PastAdministration:
    administration_id: str
    at: datetime
    rule_id: str | None
    lineage_id: str | None
    accounting: Mapping[str, Amount]  # per ingredient, in the strength unit


@dataclass(frozen=True, slots=True)
class ContextPart:
    state: str
    items: tuple[Mapping[str, object], ...]
    recorded_at: datetime
    statement_id: str


@dataclass(frozen=True, slots=True)
class MedicationContext:
    allergies: ContextPart | None = None
    other_medicines: ContextPart | None = None
    renal: ContextPart | None = None
    hepatic: ContextPart | None = None
    conditions: ContextPart | None = None


@dataclass(frozen=True, slots=True)
class WeightObservation:
    observation_id: str
    kg: Decimal
    at: datetime


@dataclass(frozen=True, slots=True)
class WorkingDiagnosis:
    diagnosis_id: str
    condition_id: str
    lineage_id: str


@dataclass(frozen=True, slots=True)
class DoseRequest:
    subject_person_id: str
    diagnosis: WorkingDiagnosis | None
    product_id: str
    lot_id: str | None
    labeled_only: bool


@dataclass(frozen=True, slots=True)
class DoseContext:
    now: datetime
    mode: Mode
    treatments: Treatments
    policy: MedicinesPolicy
    formulary: Mapping[str, FormularyProduct]
    lots: tuple[LotView, ...]
    history: tuple[PastAdministration, ...]
    context: MedicationContext
    weights: tuple[WeightObservation, ...] = ()
    clock_discontinuity: bool = False
    max_window_rows: int = 10000


# ---- the engine's output ----


@dataclass(frozen=True, slots=True)
class TraceEntry:
    step: str
    source: str  # "treatments.json@<version>#/<pointer>", "record:<id>", "assessment:<run_id>", "computed"
    value_text: str


@dataclass(frozen=True, slots=True)
class Alert:
    alert_id: str
    kind: str
    level: AlertLevel
    text: str
    reason_codes: tuple[str, ...]
    override_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngredientLine:
    ingredient_id: str
    target: Amount
    accounting: Amount
    expected_median: Amount | None
    expected_low: Amount | None
    remaining_allowance: Amount


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str
    detail_text: str
    lot_ids: tuple[str, ...] = ()
    product_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Proposal:
    outcome: Literal["proposal", "refusal"]
    product_id: str
    rule_id: str | None
    lot_id: str | None
    amount: Amount | None
    amount_text: str | None
    ingredients: tuple[IngredientLine, ...]
    path: Path
    potency_status: Literal["estimated", "stale", "none"]
    assessment_run_id: str | None
    intervals: Mapping[str, Potency]
    refusal: Refusal | None
    alerts: tuple[Alert, ...]
    trace: tuple[TraceEntry, ...]
    next_dose_earliest_at: datetime | None = None
    live_authorized_at: str | None = None
    review_state: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)
