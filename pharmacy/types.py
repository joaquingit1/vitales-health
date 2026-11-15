"""Types shared by every stage of the potency engine (docs/ENGINE-SPEC.md section 2).

Two families of types live here:

* Configuration documents are JSON-shaped, so they are ``TypedDict`` classes that mirror
  the JSON Schemas in ``contracts/pharmacy`` key for key. A document that passed
  ``pharmacy.config.load_document`` may be cast to its TypedDict without copying.
* Runtime inputs and engine outputs are frozen dataclasses with plain floats, strings and
  tuples so they serialize to JSON with ``to_json`` and hash canonically. Numpy arrays are
  used only inside algorithms, never in these types.

Units are carried in field names (``_k``, ``_s``, ``_percent``, ``_gy``, ``_per_s``).
Times are UTC: every instant carries an ISO 8601 string ending in ``Z`` and the float
seconds since the Unix epoch; ``iso_to_seconds`` and ``seconds_to_iso`` convert.
"""

import dataclasses
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None
type JSONObject = dict[str, JSONValue]

# =========================================================================================
# Enumerations
# =========================================================================================
Basis = Literal["sourced", "derived", "team_default"]
ReviewState = Literal["draft", "reviewed", "approved"]
DistributionFamily = Literal["normal", "lognormal", "truncated_normal", "fixed"]
Quantity = Literal[
    "temperature_k", "relative_humidity", "dose_rate_gy_per_s", "illuminance_lux", "uv_w_per_m2"
]
QUANTITIES: tuple[Quantity, ...] = (
    "temperature_k",
    "relative_humidity",
    "dose_rate_gy_per_s",
    "illuminance_lux",
    "uv_w_per_m2",
)
CANONICAL_UNITS: Mapping[Quantity, tuple[str, ...]] = {
    "temperature_k": ("K", "degC"),
    "relative_humidity": ("percent",),
    "dose_rate_gy_per_s": ("Gy/s",),
    "illuminance_lux": ("lx",),
    "uv_w_per_m2": ("W/m2",),
}
"""Accepted units per quantity. The first is canonical; ``degC`` is converted with
T_K = value + 273.15 (constants.ZERO_CELSIUS_K) before anything else happens."""

DoseFormFamily = Literal["solid", "solution", "suspension", "semisolid"]
RateLawKind = Literal[
    "F0", "F1", "F2", "Fn", "R2", "R3", "A2", "A3", "A4", "An", "B1", "D1", "D2", "D3", "D4"
]
MoistureModelKind = Literal["none", "sealed_constant", "first_order_exchange"]
ZoneKind = Literal["sensed", "declared"]
GapFillKind = Literal["worst_case", "last_plus_bound"]
ProjectionKind = Literal["repeat_last_window", "declared_constant"]
SegmentRegime = Literal["nominal", "frozen", "phase_change"]
CoverageState = Literal["measured", "held", "gap_filled", "declared", "not_applicable"]

SourceLayer = Literal["measured_model", "computed", "class_prior"]
EstimateBasis = Literal["measured_model", "assay_updated", "computed", "class_prior"]
ESTIMATE_BASIS_STRENGTH: Mapping[EstimateBasis, int] = {
    "measured_model": 4,
    "assay_updated": 3,
    "computed": 2,
    "class_prior": 1,
}
"""Strength order of the estimate bases (higher is stronger). ``estimate_basis`` is the
weakest contributing layer (docs/ENGINE-SPEC.md section 9.6)."""

Mechanism = Literal[
    "hydrolysis",
    "autoxidation",
    "s_oxidation",
    "enediol_oxidation",
    "racemization",
    "photolysis",
    "radiolysis",
]
Driver = Literal["thermal", "light", "radiation"]
PathwayRole = Literal["destructive", "racemization"]
ComputedMethodKind = Literal[
    "hammett", "taft", "evans_polanyi_bde", "anchor_only", "g_value_direct", "g_value_indirect", "none"
]
ParameterName = Literal[
    "ln_k_ref_per_s",
    "ea_j_per_mol",
    "b_per_percent_rh",
    "ln_k_vis_per_lux_h",
    "ln_k_uv_per_w_h_per_m2",
    "ln_k_dose_per_gy",
    "ln_k_dose_frozen_per_gy",
    "ln_frozen_rate_multiplier",
]
"""``ln_k_dose_frozen_per_gy`` is the direct-effect constant a solution or suspension uses in
frozen segments (spec 6.3); it shares the variate of ``ln_k_dose_per_gy`` (spec 9.7)."""
ToxicityFlag = Literal["toxic", "not_toxic", "unknown"]
LotState = Literal["in_specification", "adjust", "do_not_use"]
LOT_STATE_RANK: Mapping[LotState, int] = {"in_specification": 0, "adjust": 1, "do_not_use": 2}

EstimateFlag = Literal[
    "outside_validated_range",
    "coverage_low",
    "computed_only",
    "mc_not_converged",
    "assay_model_conflict",
    "assay_reanchored",
    "inspection_override",
    "regime_switch",
    "radiosensitive_dose_exceeded",
    "degradant_profile_not_reviewed",
]
ReasonCode = Literal[
    "within_specification",
    "below_lower_limit",
    "above_upper_limit",
    "degradant_above_limit",
    "degradant_unidentified_above_limit",
    "inspection_found_change",
    "adjustment_not_allowed_by_product",
    "fixed_dose_device",
    "toxic_degradant",
    "estimate_outside_validated_range",
    "estimate_coverage_low",
    "degradant_profile_not_reviewed",
    "computed_only",
    "assay_model_conflict",
    "regime_switch",
    "radiosensitive_dose_exceeded",
    "mc_not_converged",
    "past_labeled_expiry",
]
ReasonSeverity = Literal["blocking", "warning", "info"]
InspectionFinding = Literal[
    "no_change",
    "color_change",
    "cracking",
    "crystals",
    "cloudiness",
    "precipitate",
    "particles",
    "swollen_package",
    "phase_separation",
    "other",
]
AssayMeasurand = Literal["total", "active"]
AssaySite = Literal["onboard", "ground"]
ForecastStatus = Literal["already_crossed", "within_horizon", "beyond_horizon"]
PlacardKind = Literal[
    "temperature_range",
    "humidity_range",
    "time_extrapolation",
    "regime_frozen",
    "regime_phase_change",
    "coverage",
    "monte_carlo",
    "assay_conflict",
]

# =========================================================================================
# Time helpers (implemented; pure)
# =========================================================================================


def iso_to_seconds(t_utc: str) -> float:
    """Convert an ISO 8601 UTC instant (``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``) to float seconds
    since the Unix epoch. Raises ``ValueError`` if the string has no ``Z`` suffix or no
    explicit UTC offset of zero."""
    if not t_utc.endswith("Z"):
        raise ValueError(f"timestamp must be UTC with a Z suffix: {t_utc!r}")
    parsed = datetime.fromisoformat(t_utc[:-1] + "+00:00")
    if parsed.utcoffset() is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"timestamp must be UTC: {t_utc!r}")
    return parsed.timestamp()


def seconds_to_iso(t_s: float) -> str:
    """Convert float seconds since the Unix epoch to ISO 8601 UTC with microsecond
    precision and a ``Z`` suffix (for example ``2027-03-01T00:00:00.000000Z``)."""
    if not math.isfinite(t_s):
        raise ValueError(f"non-finite time: {t_s!r}")
    return datetime.fromtimestamp(t_s, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# =========================================================================================
# Configuration documents (TypedDict mirrors of contracts/pharmacy/*.schema.json)
# =========================================================================================


class SourcedValue(TypedDict):
    """A number with its provenance. ``to_be_set_by`` names who must replace a placeholder."""

    value: float
    unit: str
    basis: Basis
    source_ids: list[str]
    to_be_set_by: str | None
    note: str


class Prior(TypedDict):
    """A parameter distribution with provenance.

    normal: value ~ N(mu, sigma^2). truncated_normal: the same restricted to [lower, upper]
    (either bound may be null). lognormal: ln(value) ~ N(mu, sigma^2), so ``mu`` and
    ``sigma`` are in natural-log units and ``unit`` names the unit of the value itself.
    fixed: value = mu, sigma must be 0.
    """

    family: DistributionFamily
    mu: float
    sigma: float
    lower: float | None
    upper: float | None
    unit: str
    basis: Basis
    source_ids: list[str]
    to_be_set_by: str | None
    note: str


class Provenance(TypedDict):
    basis: Basis
    source_ids: list[str]
    to_be_set_by: str | None
    note: str


class CalibrationStatistic(TypedDict):
    """One named numerical result of an offline calibration, with an explicit unit."""

    name: str
    value: float
    unit: str


class CalibrationProvenance(Provenance):
    """Reproducible derivation of a prior or multiplier (spec 9.5).

    Rows are dataset row identifiers, not list offsets. Statistics have unique names.
    Absence of this record means no reproducible calibration has been recorded.
    """

    method: str
    vector_id: str
    rows: list[int]
    statistics: list[CalibrationStatistic]
    engine_version: str


class DocumentHeader(TypedDict):
    document_id: str
    schema: str
    version: str
    review_state: ReviewState
    notice: str


class SourceEntry(TypedDict):
    source_id: str
    citation: str
    url: str | None
    status: str
    note_file: str


class SourcesDoc(DocumentHeader):
    sources: list[SourceEntry]


# --- engine-settings.json -------------------------------------------------------------------
class ReferenceConditions(TypedDict):
    temperature_k: SourcedValue
    relative_humidity_percent: SourcedValue


class MktWindow(TypedDict):
    window_id: str
    duration_s: SourcedValue


class MktSettings(TypedDict):
    activation_energy_j_per_mol: SourcedValue
    windows: list[MktWindow]


class MonteCarloSettings(TypedDict):
    coverage_probability: SourcedValue
    n_dig: SourcedValue
    max_batches: SourcedValue
    min_batches: SourcedValue
    reported_quantiles: list[float]
    rng: Literal["numpy.PCG64"]


class DecisionSettings(TypedDict):
    lower_quantile: SourcedValue
    upper_quantile: SourcedValue
    degradant_quantile: SourcedValue


class UpdateSettings(TypedDict):
    ess_floor: SourcedValue
    conflict_tail_probability: SourcedValue
    fallback: Literal["reanchor"]
    ground_inter_laboratory_sd_percent: SourcedValue
    onboard_method_sd_percent: SourcedValue


class ExtrapolationSettings(TypedDict):
    ln_k_sd_per_kelvin: SourcedValue
    ln_k_sd_per_percent_rh: SourcedValue
    flag_share_threshold: SourcedValue
    max_time_extrapolation_ratio: SourcedValue


class BasisSettings(TypedDict):
    share_threshold: SourcedValue


class RateLawDefault(TypedDict):
    dose_form_family: DoseFormFamily
    kind: RateLawKind
    basis: Basis
    source_ids: list[str]
    to_be_set_by: str | None
    note: str


class ForecastSettings(TypedDict):
    default_projection: ProjectionKind
    window_s: SourcedValue
    horizon_s: SourcedValue
    time_tolerance_s: SourcedValue


class RadiationSettings(TypedDict):
    include_in_potency: bool
    dose_rate_safety_factor: SourcedValue
    radiosensitive_dose_threshold_gy: SourcedValue


class ComputedLayerSettings(TypedDict):
    rdkit_embed_seed: SourcedValue
    xtb_method: Literal["GFN2-xTB"]
    xtb_cache_tblite_version: str
    xtb_accuracy: SourcedValue
    xtb_electronic_temperature_k: SourcedValue
    xtb_n_conformers: SourcedValue
    xtb_spin_polarization_scale: SourcedValue
    optimizer_gtol_eh_per_bohr: SourcedValue
    optimizer_max_steps: SourcedValue
    same_molecule_sd_log10: SourcedValue
    unparameterized_substituent_sd_log10: SourcedValue
    ortho_substituent_sd_log10: SourcedValue
    heteroaromatic_ring_sd_log10: SourcedValue


class NumericsSettings(TypedDict):
    series_relative_tolerance: SourcedValue
    series_max_terms: SourcedValue
    inverse_absolute_tolerance: SourcedValue
    small_alpha_series_threshold: SourcedValue
    worker_threads: NotRequired[SourcedValue]
    compression_relative_tolerance: NotRequired[SourcedValue]


class ValidatedRangeSettings(TypedDict):
    temperature_below_min_margin_k: SourcedValue


class Q3bThreshold(SourcedValue):
    """Threshold and rounding precision in its stated unit (MDS04, spec 11.6)."""

    decimals: int


class Q3bBand(TypedDict):
    """First matching upper dose bound wins; null is the unbounded last band."""

    dose_upper_mg: SourcedValue | None
    upper_inclusive: bool
    percent: Q3bThreshold
    tdi: Q3bThreshold | None


class Q3bSettings(TypedDict):
    reporting: list[Q3bBand]
    identification: list[Q3bBand]
    qualification: list[Q3bBand]


class EngineSettingsDoc(DocumentHeader):
    reference_conditions: ReferenceConditions
    mean_kinetic_temperature: MktSettings
    monte_carlo: MonteCarloSettings
    decision: DecisionSettings
    update: UpdateSettings
    extrapolation: ExtrapolationSettings
    basis: BasisSettings
    rate_law_defaults: list[RateLawDefault]
    forecast: ForecastSettings
    radiation: RadiationSettings
    computed_layer: ComputedLayerSettings
    numerics: NumericsSettings
    validated_range: NotRequired[ValidatedRangeSettings]
    q3b: NotRequired[Q3bSettings]


# --- reaction-classes.json ---------------------------------------------------------------------
class ValidatedRangeDoc(TypedDict):
    temperature_min_k: float
    temperature_max_k: float
    rh_min_percent: float
    rh_max_percent: float
    basis: Basis
    source_ids: list[str]
    to_be_set_by: str | None
    note: str


class CalibrationDatasetRef(TypedDict):
    vector_id: str
    rows: list[int] | None
    note: str


class TotalRatePrior(TypedDict):
    dose_form_family: DoseFormFamily
    ln_k_ref_per_s: Prior
    calibration: CalibrationDatasetRef
    temperature_k: SourcedValue
    temperature_sd_k: SourcedValue
    rh_percent: SourcedValue
    rh_sd_percent: SourcedValue
    calibration_provenance: NotRequired[CalibrationProvenance | None]


class Anchor(TypedDict):
    anchor_id: str
    dose_form_family: DoseFormFamily
    smiles: str
    ln_k_per_s: Prior
    temperature_k: SourcedValue
    temperature_sd_k: SourcedValue
    rh_percent: SourcedValue
    ph: SourcedValue | None
    reference_study_id: str | None
    calibration: CalibrationDatasetRef | None
    measured_degradant_ids: NotRequired[list[str]]
    storage_zone_id: str
    package_id: str | None
    duration_days: SourcedValue | None
    storage_context: NotRequired[Provenance]


class ComputedMethod(TypedDict):
    kind: ComputedMethodKind
    series_ids: list[str]
    method_error_sd_log10: SourcedValue


class FamilyPriors(TypedDict):
    """Class priors for one dose-form family. ``ln_k_ref_per_s`` is used only by classes
    outside the product total-rate split (role racemization); destructive thermal classes
    take ln k from ``total_rate_priors`` (spec 9.5) and leave it null."""

    dose_form_family: DoseFormFamily
    ln_k_ref_per_s: Prior | None
    ea_j_per_mol: Prior | None
    b_per_percent_rh: Prior | None
    frozen_rate_multiplier: Prior | None


class LightPriors(TypedDict):
    k_vis_per_lux_h: Prior
    k_uv_per_w_h_per_m2: Prior


class ClassDegradant(TypedDict):
    degradant_id: str
    name: str
    smiles: str | None
    molar_mass_g_per_mol: SourcedValue | None
    yield_mol_per_mol: SourcedValue
    toxicity_flag: ToxicityFlag
    basis: Basis
    source_ids: list[str]
    note: str


class ReactionClassDoc(TypedDict):
    class_id: str
    name: str
    mechanism: Mechanism
    driver: Driver
    role: PathwayRole
    smarts: list[str] | None
    site_atom_map: int | None
    requires_defined_stereocenter: bool
    applies_to_families: list[DoseFormFamily]
    computed_method: ComputedMethod
    anchors: list[Anchor]
    priors: list[FamilyPriors]
    light_priors: LightPriors | None
    degradant: ClassDegradant | None
    validated_range: ValidatedRangeDoc
    basis: Basis
    source_ids: list[str]
    note: str


class ReactionClassesDoc(DocumentHeader):
    total_rate_priors: list[TotalRatePrior]
    classes: list[ReactionClassDoc]


# --- lfer-constants.json -------------------------------------------------------------------------
class HammettSubstituent(TypedDict):
    substituent_id: str
    fragment_smiles: str
    sigma_meta: SourcedValue
    sigma_para: SourcedValue


class HammettSeries(TypedDict):
    series_id: str
    class_id: str
    ring_attachment_map: int
    rho: SourcedValue | None
    reaction: str


class TaftSubstituent(TypedDict):
    substituent_id: str
    fragment_smiles: str
    sigma_star: SourcedValue
    steric_es: SourcedValue


class TaftSeries(TypedDict):
    series_id: str
    class_id: str
    substituent_attachment_map: int
    rho_star: SourcedValue | None
    delta: SourcedValue | None
    reaction: str


class EvansPolanyiEntry(TypedDict):
    class_id: str
    alpha: SourcedValue
    max_abs_delta_bde_kj_per_mol: SourcedValue
    bond: Literal["C-H", "O-H", "N-H"]
    reference_smiles: str
    reference_note: str


class LferConstantsDoc(DocumentHeader):
    hammett_substituents: list[HammettSubstituent]
    hammett_series: list[HammettSeries]
    taft_substituents: list[TaftSubstituent]
    taft_series: list[TaftSeries]
    evans_polanyi: list[EvansPolanyiEntry]


# --- radiation-chemistry.json ---------------------------------------------------------------------
class IndirectEffect(TypedDict):
    g_max_mol_per_j: Prior
    half_scavenging_concentration_mol_per_kg: SourcedValue
    full_scavenging_concentration_mol_per_kg: SourcedValue


class UpperBound(TypedDict):
    dose_form_family: DoseFormFamily
    k_per_gy: SourcedValue


class RadiationChemistryDoc(DocumentHeader):
    direct_effect_g_loss_mol_per_j: Prior
    indirect_effect: IndirectEffect
    frozen_solution_uses_direct_effect: Provenance
    upper_bounds: list[UpperBound]


# --- packages.json -------------------------------------------------------------------------------
class SorbingComponent(TypedDict):
    name: str
    dry_mass_g: SourcedValue
    sorption_slope_mg_per_g_per_percent_rh: SourcedValue


class MoistureModelDoc(TypedDict):
    kind: MoistureModelKind
    sealed_rh_percent: SourcedValue | None
    mvtr_mg_per_day: SourcedValue | None
    mvtr_test_temperature_k: SourcedValue | None
    mvtr_test_delta_rh_percent: SourcedValue | None
    permeation_activation_energy_j_per_mol: SourcedValue | None
    headspace_capacity_mg_per_percent_rh: SourcedValue | None
    contents: list[SorbingComponent]
    initial_internal_rh_percent: SourcedValue | None


class PackageDoc(TypedDict):
    package_id: str
    name: str
    kind: Literal["blister", "bottle", "bag", "vial", "syringe", "auto_injector", "ampoule", "tube"]
    description: str
    protective: bool
    light_transmission_visible: SourcedValue
    light_transmission_uv: SourcedValue
    moisture_model: MoistureModelDoc
    basis: Basis
    source_ids: list[str]
    note: str


class PackagesDoc(DocumentHeader):
    packages: list[PackageDoc]


# --- storage-zones.json ----------------------------------------------------------------------------
class SensorSpecDoc(TypedDict):
    sensor_id: str
    quantity: Quantity
    unit: str
    calibration_offset: SourcedValue
    calibration_standard_uncertainty: SourcedValue
    resolution: SourcedValue
    max_hold_s: SourcedValue


class DeclaredConditions(TypedDict):
    temperature_k: Prior | None
    relative_humidity_percent: Prior | None
    dose_rate_gy_per_s: Prior | None
    illuminance_lux: Prior | None
    uv_w_per_m2: Prior | None


class GapBounds(TypedDict):
    temperature_k: SourcedValue | None
    relative_humidity_percent: SourcedValue | None
    dose_rate_gy_per_s: SourcedValue | None
    illuminance_lux: SourcedValue | None
    uv_w_per_m2: SourcedValue | None


class GapPolicy(TypedDict):
    kind: GapFillKind
    bounds: GapBounds


class WorstCase(TypedDict):
    temperature_max_k: SourcedValue
    temperature_min_k: SourcedValue
    rh_max_percent: SourcedValue
    rh_min_percent: SourcedValue
    dose_rate_max_gy_per_s: SourcedValue
    illuminance_max_lux: SourcedValue
    uv_max_w_per_m2: SourcedValue


class ForecastProjectionDoc(TypedDict):
    kind: ProjectionKind
    window_s: SourcedValue | None


class StorageZoneDoc(TypedDict):
    zone_id: str
    name: str
    kind: ZoneKind
    environment_profile_id: str | None
    location_note: str
    shielding_note: str
    sensors: list[SensorSpecDoc]
    declared_conditions: DeclaredConditions
    light_exposure: bool
    gap_policy: GapPolicy
    gap_limit_s: SourcedValue
    min_coverage_fraction: SourcedValue
    worst_case: WorstCase
    time_above_thresholds_k: list[SourcedValue]
    rate_multiplier: Prior
    rate_multiplier_calibration: NotRequired[CalibrationProvenance | None]
    forecast_projection: ForecastProjectionDoc
    basis: Basis
    source_ids: list[str]
    note: str


class StorageZonesDoc(DocumentHeader):
    zones: list[StorageZoneDoc]


# --- formulary.json -----------------------------------------------------------------------------------
class Strength(TypedDict):
    value: float
    unit: str
    per: str


class Specification(TypedDict):
    lower_percent: SourcedValue
    upper_percent: SourcedValue


class ProductDegradant(TypedDict):
    degradant_id: str
    name: str
    smiles: str | None
    formed_by_class_id: str
    yield_mol_per_mol: SourcedValue
    molar_mass_g_per_mol: SourcedValue | None
    limit_percent: SourcedValue | None
    toxicity_flag: ToxicityFlag
    basis: Basis
    source_ids: list[str]
    note: str


class RacemizationSpec(TypedDict):
    initial_active_fraction: SourcedValue


class ActiveIngredientDoc(TypedDict):
    ingredient_id: str
    name: str
    smiles: str
    pubchem_cid: int
    molar_mass_g_per_mol: SourcedValue
    salt_form_note: str
    strength: Strength
    concentration_mol_per_kg: SourcedValue | None
    specification: Specification
    degradants: list[ProductDegradant]
    racemization: RacemizationSpec | None
    source_ids: list[str]


class FormularyMembership(TypedDict):
    pack: str | None
    basis: Basis
    source_ids: list[str]
    note: str


class PotencyAdjustment(TypedDict):
    allowed: bool
    reason: str | None


class RegimeThresholds(TypedDict):
    phase_change_temperature_k: SourcedValue | None
    deliquescence_rh_percent: SourcedValue | None


class ProductDoc(TypedDict):
    product_id: str
    name: str
    dose_form: str
    dose_form_family: DoseFormFamily
    route: str
    ingredients: list[ActiveIngredientDoc]
    default_package_id: str
    fixed_dose_device: bool
    narrow_therapeutic_index: bool
    radiosensitive: bool
    degradant_profile_reviewed: bool
    potency_adjustment: PotencyAdjustment
    freezing_point_k: SourcedValue | None
    do_not_freeze: bool
    regime_thresholds: RegimeThresholds
    photostable_per_q1b: bool | None
    solution_ph: SourcedValue | None
    inspection_criteria: list[str]
    formulary_membership: FormularyMembership
    label_shelf_life_months_text: str | None
    basis: Basis
    source_ids: list[str]
    note: str


class FormularyDoc(DocumentHeader):
    products: list[ProductDoc]


# --- stability-models.json ------------------------------------------------------------------------------
class StudyData(TypedDict):
    description: str
    matrix: str
    ph: float | None
    concentration_text: str | None
    temperatures_c: list[float]
    rate_constants: list[float]
    rate_unit: Literal["per_s", "per_min", "per_h", "per_day", "per_month", "ml_per_mg_per_h"]
    reaction_order: int


class KindParameters(TypedDict):
    order_n: float | None
    alpha0: float | None
    avrami_n: float | None


class ParameterVector(TypedDict):
    names: list[ParameterName]
    mean: list[float]
    covariance: list[list[float]]


class FitStatistics(TypedDict):
    method: str
    n: int
    dof: int
    rss: float
    r2: float | None
    published_ea_j_per_mol: float | None
    ea_gap_j_per_mol: float | None


class StabilityModelDoc(TypedDict):
    model_id: str
    scope: Literal["product", "reference_study"]
    product_id: str | None
    ingredient_id: str | None
    package_id: str | None
    study: StudyData | None
    rate_law_kind: RateLawKind
    kind_parameters: KindParameters
    reference_temperature_k: float
    reference_rh_percent: float | None
    parameters: ParameterVector
    fit: FitStatistics
    validated_range: ValidatedRangeDoc
    max_study_duration_s: float | None
    covers: list[str]
    measured_degradant_ids: NotRequired[list[str]]
    dataset_sha256: str
    status: Literal["reproduces", "known_mismatch"]
    basis: Basis
    source_ids: list[str]
    note: str


class StabilityModelsDoc(DocumentHeader):
    models: list[StabilityModelDoc]


# --- environment-profiles.json ---------------------------------------------------------------------------
class TemperatureProfile(TypedDict):
    setpoint_k: SourcedValue
    diurnal_amplitude_k: SourcedValue
    ou_sd_k: SourcedValue
    ou_time_constant_s: SourcedValue
    nominal_min_k: SourcedValue
    nominal_max_k: SourcedValue


class HumidityProfile(TypedDict):
    setpoint_percent: SourcedValue
    diurnal_amplitude_percent: SourcedValue
    ou_sd_percent: SourcedValue
    ou_time_constant_s: SourcedValue
    nominal_min_percent: SourcedValue
    nominal_max_percent: SourcedValue


class RadiationProfile(TypedDict):
    absorbed_dose_rate_gy_per_day: SourcedValue
    absorbed_dose_rate_sd_gy_per_day: SourcedValue | None
    dose_equivalent_rate_sv_per_day: SourcedValue | None
    dosimeter_material: str
    note: str


class LightProfile(TypedDict):
    illuminance_lux: SourcedValue
    uv_w_per_m2: SourcedValue
    hours_on_per_day: SourcedValue


class AtmosphereProfile(TypedDict):
    total_pressure_kpa: SourcedValue | None
    oxygen_fraction: SourcedValue | None


class SensorDefaults(TypedDict):
    sampling_interval_s: SourcedValue
    temperature_noise_sd_k: SourcedValue
    rh_noise_sd_percent: SourcedValue
    dose_rate_noise_relative_sd: SourcedValue
    dropout_probability: SourcedValue
    temperature_resolution_k: SourcedValue
    rh_resolution_percent: SourcedValue
    dose_rate_resolution_gy_per_s: SourcedValue
    sensor_bias_temperature_sd_k: SourcedValue
    sensor_bias_rh_sd_percent: SourcedValue


class EnvironmentProfileDoc(TypedDict):
    profile_id: str
    name: str
    description: str
    temperature: TemperatureProfile
    relative_humidity: HumidityProfile
    radiation: RadiationProfile
    light: LightProfile | None
    atmosphere: AtmosphereProfile
    sensor_defaults: SensorDefaults
    basis: Basis
    source_ids: list[str]
    note: str


class SpeTemplate(TypedDict):
    template_id: str
    description: str
    total_absorbed_dose_gy: SourcedValue
    rise_s: SourcedValue
    decay_time_constant_s: SourcedValue
    shielding_g_per_cm2: SourcedValue | None


class ThermalFailureTemplate(TypedDict):
    template_id: str
    description: str
    target_temperature_k: SourcedValue
    target_rh_percent: SourcedValue | None
    ramp_s: SourcedValue
    hold_s: SourcedValue
    recover_s: SourcedValue


class EnvironmentProfilesDoc(DocumentHeader):
    profiles: list[EnvironmentProfileDoc]
    spe_templates: list[SpeTemplate]
    thermal_failure_templates: list[ThermalFailureTemplate]


# --- lots.json ---------------------------------------------------------------------------------------------
class InitialAssay(TypedDict):
    ingredient_id: str
    mean_percent: SourcedValue
    sd_percent: SourcedValue


class CustodyInterval(TypedDict):
    zone_id: str
    from_utc: str
    to_utc: str | None


class PackageInterval(TypedDict):
    package_id: str
    from_utc: str


class LotQuantity(TypedDict):
    value: float
    unit: str


class LotDoc(TypedDict):
    lot_id: str
    product_id: str
    manufacturer_lot: str
    manufactured_at_utc: str
    labeled_expiry_utc: str
    quantity: LotQuantity
    initial_assay: list[InitialAssay]
    custody: list[CustodyInterval]
    package_history: list[PackageInterval]
    status: Literal["active", "recalled", "consumed"]
    basis: Basis
    source_ids: list[str]
    note: str


class LotsDoc(DocumentHeader):
    lots: list[LotDoc]


# --- inputs.schema.json (runtime inputs as JSON) ------------------------------------------------------
class SensorPointRecord(TypedDict):
    t_utc: str
    t_s: float
    value: float


class SensorSeriesRecord(TypedDict):
    series_id: str
    sensor_id: str
    zone_id: str
    quantity: Quantity
    unit: str
    points: list[SensorPointRecord]


class AssayRecord(TypedDict):
    """JSON form of ``AssayObservation`` (same members)."""

    assay_id: str
    lot_id: str
    ingredient_id: str
    measured_at_utc: str
    measured_at_s: float
    measurand: AssayMeasurand
    value_percent: float
    sd_percent: float
    site: AssaySite
    method: str
    laboratory: str
    note: str


class InspectionRecord(TypedDict):
    """JSON form of ``InspectionObservation`` (same members)."""

    inspection_id: str
    lot_id: str
    inspected_at_utc: str
    inspected_at_s: float
    finding: InspectionFinding
    inspector_role: str
    note: str


class EngineInputsDoc(TypedDict):
    """Everything the hub hands the engine besides configuration (spec 12.1)."""

    schema: str
    as_of_utc: str
    sensor_series: list[SensorSeriesRecord]
    assays: list[AssayRecord]
    inspections: list[InspectionRecord]


# --- fixtures/pharmacy/*.json (scenario documents for envsim and scenario tests) ----------------------
ScenarioEventKind = Literal[
    "thermal_control_failure", "solar_particle_event", "sensor_outage", "light_exposure_change"
]
ExpectationKind = Literal[
    "state_in",
    "flag_present",
    "flag_absent",
    "estimate_basis_in",
    "forecast_status_in",
    "lower_quantile_between",
    "coverage_low_is",
    "ordered_before",
    "true_potency_within_interval",
]


class ScenarioSensor(TypedDict):
    sensor_id: str
    quantity: Quantity
    sampling_interval_s: float


class ScenarioZone(TypedDict):
    zone_id: str
    profile_id: str
    sensors: list[ScenarioSensor]
    seed_label: str


class ScenarioEvent(TypedDict):
    """A scripted event. thermal_control_failure uses ``template_id`` from
    environment-profiles.json thermal_failure_templates; solar_particle_event uses an SPE
    template and ``shielding_scale`` multiplies its total dose; sensor_outage silences
    ``sensor_ids`` for ``duration_s``."""

    event_id: str
    kind: ScenarioEventKind
    zone_id: str
    start_utc: str
    duration_s: float | None
    template_id: str | None
    sensor_ids: list[str]
    shielding_scale: float | None
    note: str


class Expectation(TypedDict):
    """An assertion the scenario tests make about engine output at ``as_of_utc``. Members
    that a kind does not use are null or empty. Acceptance rules: spec section 15.4."""

    expectation_id: str
    kind: ExpectationKind
    lot_id: str
    ingredient_id: str | None
    as_of_utc: str
    values: list[str]
    lower: float | None
    upper: float | None
    other_lot_id: str | None
    rationale: str


class ScenarioMission(TypedDict):
    mission_id: str
    name: str
    start_utc: str
    end_utc: str
    seed: int
    description: str


class ScenarioDoc(DocumentHeader):
    mission: ScenarioMission
    zones: list[ScenarioZone]
    events: list[ScenarioEvent]
    lot_ids: list[str]
    assays: list[AssayRecord]
    inspections: list[InspectionRecord]
    assessment_times_utc: list[str]
    expectations: list[Expectation]


# =========================================================================================
# Runtime inputs (frozen dataclasses)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class SensorPoint:
    """One reading. ``t_utc`` and ``t_s`` must denote the same instant within 1 ms."""

    t_utc: str
    t_s: float
    value: float


@dataclasses.dataclass(frozen=True, slots=True)
class SensorSeries:
    """Raw readings of one sensor as received (before calibration). Timestamps must be
    strictly increasing. ``unit`` must be one of ``CANONICAL_UNITS[quantity]``."""

    series_id: str
    sensor_id: str
    zone_id: str
    quantity: Quantity
    unit: str
    points: tuple[SensorPoint, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class AssayObservation:
    """A potency assay of one ingredient of one lot (decision 11). ``value_percent`` is in
    percent of label claim; ``sd_percent`` is the analytical standard uncertainty (k = 1)
    reported by the laboratory. ``measurand`` says whether the method separates enantiomers
    (``active``) or reports total drug (``total``)."""

    assay_id: str
    lot_id: str
    ingredient_id: str
    measured_at_utc: str
    measured_at_s: float
    measurand: AssayMeasurand
    value_percent: float
    sd_percent: float
    site: AssaySite
    method: str
    laboratory: str
    note: str


@dataclasses.dataclass(frozen=True, slots=True)
class InspectionObservation:
    """A crew visual inspection of a lot (decision 11). Any finding other than
    ``no_change`` at or before ``as_of`` marks the lot do_not_use."""

    inspection_id: str
    lot_id: str
    inspected_at_utc: str
    inspected_at_s: float
    finding: InspectionFinding
    inspector_role: str
    note: str


# =========================================================================================
# Stage outputs (frozen dataclasses)
# =========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class Segment:
    """One piecewise-constant interval [t0_s, t1_s) of storage conditions.

    ``relative_humidity_percent`` is the outside (zone) RH used when a pathway's humidity
    sensitivity B >= 0. ``relative_humidity_low_percent`` equals it except in gap-filled
    segments, where it carries the lower worst-case bound used when B < 0 (spec 3.5).
    ``gap_quantities`` lists quantities filled by the gap policy; ``declared_quantities``
    lists quantities taken from declared conditions. ``package_id`` is None at zone level
    and set at lot level.
    """

    t0_s: float
    t1_s: float
    zone_id: str
    package_id: str | None
    temperature_k: float
    relative_humidity_percent: float
    relative_humidity_low_percent: float
    dose_rate_gy_per_s: float
    illuminance_lux: float
    uv_w_per_m2: float
    gap_quantities: tuple[Quantity, ...]
    declared_quantities: tuple[Quantity, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class GapRecord:
    zone_id: str
    quantity: Quantity
    t0_s: float
    t1_s: float
    fill_kind: GapFillKind
    fill_value: float
    fill_value_low: float | None


@dataclasses.dataclass(frozen=True, slots=True)
class QuantityCoverage:
    zone_id: str
    quantity: Quantity
    measured_s: float
    held_s: float
    gap_s: float
    declared_s: float
    not_applicable_s: float
    longest_gap_s: float
    coverage_fraction: float


@dataclasses.dataclass(frozen=True, slots=True)
class SeriesPin:
    series_id: str
    sha256: str


@dataclasses.dataclass(frozen=True, slots=True)
class ZoneHistory:
    """Aligned, calibrated, gap-bounded history of one zone over [t_start_s, t_end_s)."""

    zone_id: str
    t_start_s: float
    t_end_s: float
    segments: tuple[Segment, ...]
    gaps: tuple[GapRecord, ...]
    coverage: tuple[QuantityCoverage, ...]
    series_pins: tuple[SeriesPin, ...]
    coverage_low: bool


@dataclasses.dataclass(frozen=True, slots=True)
class LotExposure:
    """The concatenated exposure of one lot from manufacture to ``as_of`` across its
    custody intervals, with the package of each segment set."""

    lot_id: str
    t_start_s: float
    t_end_s: float
    segments: tuple[Segment, ...]
    zone_histories: tuple[ZoneHistory, ...]
    coverage_low: bool


@dataclasses.dataclass(frozen=True, slots=True)
class WindowMkt:
    window_id: str
    duration_s: float
    mkt_k: float | None


@dataclasses.dataclass(frozen=True, slots=True)
class TimeAbove:
    threshold_k: float
    seconds: float


@dataclasses.dataclass(frozen=True, slots=True)
class ExposureSummary:
    """Pharmacist-facing summaries (display only; potency never uses MKT)."""

    t_start_utc: str
    t_end_utc: str
    duration_s: float
    mkt_k: float | None
    mkt_activation_energy_j_per_mol: float
    mkt_windows: tuple[WindowMkt, ...]
    mean_temperature_k: float | None
    min_temperature_k: float | None
    max_temperature_k: float | None
    time_above: tuple[TimeAbove, ...]
    cumulative_absorbed_dose_gy: float
    visible_dose_lux_h: float
    uv_dose_w_h_per_m2: float
    coverage: tuple[QuantityCoverage, ...]
    longest_gap_s: float


@dataclasses.dataclass(frozen=True, slots=True)
class MicroclimateTrace:
    """Internal RH of the package at every segment boundary for the two forcing variants
    (high: outside RH as recorded or gap-filled high; low: gap-filled low), plus the
    exchange time constant used in each segment (``math.inf`` for sealed, 0 for none)."""

    package_ids: tuple[str, ...]
    rh_in_start_high: tuple[float, ...]
    rh_in_start_low: tuple[float, ...]
    rh_in_end_high: tuple[float, ...]
    rh_in_end_low: tuple[float, ...]
    time_constant_s: tuple[float, ...]
    moisture_kind: tuple[MoistureModelKind, ...]


# --- Chemistry ------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True, slots=True)
class MoleculeInfo:
    smiles_input: str
    canonical_smiles: str
    inchikey: str
    formula: str
    molar_mass_g_per_mol: float
    defined_stereocenters: tuple[int, ...]
    heavy_atom_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class ReactiveSite:
    """A match of a reaction class. ``site_atom`` is the RDKit atom index of the mapped
    atom named by the class's ``site_atom_map``; ``mapped_atoms`` lists (map number, atom
    index) pairs of the SMARTS match that produced it."""

    class_id: str
    site_atom: int
    mapped_atoms: tuple[tuple[int, int], ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ParameterEstimate:
    """Provenance of one resolved parameter of one pathway (spec 9.2). For normal-family
    parameters ``mu`` and ``sigma`` describe the marginal at the engine reference
    conditions; ``source_layer`` is the best layer that informed it."""

    name: ParameterName
    family: DistributionFamily
    mu: float
    sigma: float
    lower: float | None
    upper: float | None
    unit: str
    source_layer: SourceLayer
    method: str
    source_ids: tuple[str, ...]
    basis: Basis
    note: str


@dataclasses.dataclass(frozen=True, slots=True)
class ParameterBlock:
    """Joint normal distribution of the parameters of one pathway at the engine reference
    conditions (spec 9.4). ``names`` orders ``mean`` and ``covariance``. Ea (when present)
    is truncated below at ``ea_lower_j_per_mol``. ``shared_weight`` is w in spec 9.5: the
    share of the ln k variance that comes from the ingredient-level class prior."""

    names: tuple[ParameterName, ...]
    mean: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    ea_lower_j_per_mol: float
    shared_weight: float
    estimates: tuple[ParameterEstimate, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ValidatedRange:
    temperature_min_k: float
    temperature_max_k: float
    rh_min_percent: float
    rh_max_percent: float
    source_layer: SourceLayer
    source_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class DegradantSpec:
    degradant_id: str
    name: str
    smiles: str | None
    formed_by_class_id: str
    yield_mol_per_mol: float
    molar_mass_g_per_mol: float | None
    limit_percent: float | None
    toxicity_flag: ToxicityFlag
    product_specific: bool
    source_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ComputedDetail:
    """What the computed layer did for one pathway, for the trace."""

    method: ComputedMethodKind
    anchor_id: str | None
    delta_ln_k: float
    sd_ln_k: float
    flags: tuple[str, ...]
    descriptors: tuple[tuple[str, float], ...]
    cache_keys: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class FreeShare:
    """Membership of a pathway in its ingredient's free group (spec 9.5): split members
    without an anchor share part of the class total by log-softmax of their Evans-Polanyi
    shifts. ``delta_ln`` is delta_i (0 when unshifted); ``method_sd_ln`` is s_m,i in ln
    units (0 when no BDE-based shift was applied at any site). Draws add the exact change
    of the log-share, so the free group's rates sum to the same total in every draw."""

    delta_ln: float
    method_sd_ln: float


@dataclasses.dataclass(frozen=True, slots=True)
class ZoneCorrection:
    """A zone rate multiplier already contained in a pathway's reference rate (spec 9.3).

    ``weight`` is the anchor's precision weight lambda in the 9.4 combination (0 to 1) and
    ``factor_sd_ln`` the multiplier's ln standard deviation used at conversion. Every draw
    subtracts ``weight`` times the zone multiplier's drawn ln deviation (spec 9.7).
    """

    zone_id: str
    weight: float
    factor_sd_ln: float


@dataclasses.dataclass(frozen=True, slots=True)
class DegradantIdentification:
    """Evidence specific to a degradant's forming pathway (spec 5.6).

    An identified prediction names the model or anchor and its sources. Total assay
    loss alone supplies no identification. Defaults represent unidentified loss.
    """

    identified: bool = False
    evidence_kind: Literal["measured_model", "anchor"] | None = None
    evidence_id: str | None = None
    source_ids: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Pathway:
    pathway_id: str
    ingredient_id: str
    reaction_class_id: str
    mechanism: Mechanism
    driver: Driver
    role: PathwayRole
    sites: tuple[ReactiveSite, ...]
    parameters: ParameterBlock
    validated_range: ValidatedRange
    degradant: DegradantSpec | None
    computed: ComputedDetail | None
    free_share: FreeShare | None = None
    degradant_identification: DegradantIdentification = DegradantIdentification()
    zone_correction: ZoneCorrection | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class StabilityModel:
    """Everything kinetics needs for one ingredient of one product in one package history.
    All parameters are expressed at ``reference_temperature_k`` and
    ``reference_rh_percent`` (engine settings)."""

    model_id: str
    product_id: str
    ingredient_id: str
    dose_form_family: DoseFormFamily
    rate_law_kind: RateLawKind
    order_n: float | None
    alpha0: float | None
    avrami_n: float | None
    reference_temperature_k: float
    reference_rh_percent: float
    pathways: tuple[Pathway, ...]
    measured_model_id: str | None
    max_study_duration_s: float | None
    initial_active_fraction: float
    freezing_point_k: float | None
    phase_change_temperature_k: float | None
    deliquescence_rh_percent: float | None
    source_ids: tuple[str, ...]


# --- Outputs ------------------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True, slots=True)
class QuantileValue:
    probability: float
    value: float


@dataclasses.dataclass(frozen=True, slots=True)
class CoverageInterval:
    """JCGM 101 coverage interval (never called a confidence interval)."""

    probability: float
    low: float
    high: float
    kind: Literal["probabilistically_symmetric"]


@dataclasses.dataclass(frozen=True, slots=True)
class SpecificationLimits:
    lower_percent: float
    upper_percent: float


@dataclasses.dataclass(frozen=True, slots=True)
class DegradantEstimate:
    degradant_id: str
    name: str
    pathway_id: str
    unit: Literal["percent_of_label_api_mass"]
    quantiles: tuple[QuantileValue, ...]
    mean: float
    limit_percent: float | None
    p_above_limit: float | None
    toxicity_flag: ToxicityFlag
    product_specific: bool
    identification: DegradantIdentification = DegradantIdentification()


@dataclasses.dataclass(frozen=True, slots=True)
class RadiationReport:
    cumulative_absorbed_dose_gy: float
    included_in_potency: bool
    central_loss_fraction_median: float
    upper_bound_loss_fraction: float
    upper_bound_k_per_gy: float
    dose_rate_safety_factor: float
    method: Literal["g_value_direct", "g_value_indirect", "none"]
    radiosensitive: bool
    radiosensitive_threshold_gy: float
    threshold_exceeded: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ParameterLayer:
    name: ParameterName
    layer: SourceLayer
    method: str


@dataclasses.dataclass(frozen=True, slots=True)
class PathwayContribution:
    pathway_id: str
    reaction_class_id: str
    driver: Driver
    role: PathwayRole
    share_nominal: float
    contributes: bool
    layer: SourceLayer
    parameter_layers: tuple[ParameterLayer, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Placard:
    """NASA-STD-7009B [M&S 26] style warning: type of limit, extent, consequence."""

    kind: PlacardKind
    extent: float
    unit: str
    duration_s: float
    share_nominal: float
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class MonteCarloReport:
    n_draws: int
    n_batches: int
    batch_size: int
    converged: bool
    n_dig: int
    numerical_tolerance: float
    coverage_probability: float
    effective_sample_size: float | None
    seed_hex: str


@dataclasses.dataclass(frozen=True, slots=True)
class ConfigPin:
    document_id: str
    version: str
    sha256: str


@dataclasses.dataclass(frozen=True, slots=True)
class Pins:
    """Everything needed to reproduce an output bit for bit (decision 12)."""

    engine_version: str
    config: tuple[ConfigPin, ...]
    sensor_series: tuple[SeriesPin, ...]
    observations_sha256: str
    seed_hex: str
    rng: Literal["numpy.PCG64"]
    numpy_version: str
    rdkit_version: str
    tblite_version: str
    computed_cache_keys: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class TraceEntry:
    """One traced number: what it is, its value and unit, where it came from (config
    document and JSON pointer, or the computation that produced it) and its sources."""

    quantity: str
    value: float | str | None
    unit: str
    origin: str
    source_ids: tuple[str, ...]
    note: str


@dataclasses.dataclass(frozen=True, slots=True)
class PotencyEstimate:
    """Remaining active ingredient of one ingredient of one lot at ``as_of``.

    ``quantiles``, ``mean`` and ``coverage_interval`` are in percent of label claim of the
    active measurand (for racemizing ingredients the active enantiomer; ``total_quantiles``
    then gives total drug). ``estimate_basis`` is the weakest layer that contributed to the
    reported interval; ``prior_basis`` is the same ignoring assays; ``assay_ids_used`` lists
    the assays the update used (spec 9.6 and 10).
    """

    estimate_id: str
    lot_id: str
    product_id: str
    ingredient_id: str
    as_of_utc: str
    as_of_s: float
    unit: Literal["percent_label_claim"]
    quantiles: tuple[QuantileValue, ...]
    mean: float
    standard_uncertainty: float
    coverage_interval: CoverageInterval
    total_quantiles: tuple[QuantileValue, ...] | None
    specification: SpecificationLimits
    p_within_spec: float
    p_below_lower: float
    p_above_upper: float
    degradants: tuple[DegradantEstimate, ...]
    radiation: RadiationReport
    exposure: ExposureSummary
    flags: tuple[EstimateFlag, ...]
    estimate_basis: EstimateBasis
    prior_basis: SourceLayer
    assay_ids_used: tuple[str, ...]
    inspection_ids_used: tuple[str, ...]
    inspection_override: InspectionFinding | None
    pathway_contributions: tuple[PathwayContribution, ...]
    placards: tuple[Placard, ...]
    monte_carlo: MonteCarloReport
    pins: Pins
    trace: tuple[TraceEntry, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class Projection:
    kind: ProjectionKind
    window_s: float | None
    zone_id: str
    package_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class Forecast:
    """When the ``quantile_probability`` quantile of potency crosses the lower limit under
    the declared projection of future conditions (spec 11.4)."""

    forecast_id: str
    lot_id: str
    ingredient_id: str
    as_of_utc: str
    as_of_s: float
    quantile_probability: float
    lower_limit_percent: float
    projection: Projection
    status: ForecastStatus
    crossing_utc: str | None
    crossing_s: float | None
    horizon_s: float
    crossing_delay_quantiles_s: tuple[QuantileValue, ...]
    pins: Pins


@dataclasses.dataclass(frozen=True, slots=True)
class Reason:
    code: ReasonCode
    severity: ReasonSeverity
    ingredient_id: str | None
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class EligibilityCheck:
    name: str
    passed: bool
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class AdjustmentEligibility:
    """The engine-level part of decision 9 (the dose-rule part is the dose layer's)."""

    eligible: bool
    checks: tuple[EligibilityCheck, ...]
    estimate_bases: tuple[tuple[str, EstimateBasis], ...]
    assay_ids_used: tuple[str, ...]
    degradant_profile_reviewed: bool


@dataclasses.dataclass(frozen=True, slots=True)
class OrderingKey:
    """Lot ordering (decision 8). Sort ascending by (state_rank, primary is None,
    primary if not None else 0.0, lot_id); see spec 11.5."""

    state_rank: int
    primary: float | None
    lot_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class LotDecision:
    decision_id: str
    lot_id: str
    product_id: str
    as_of_utc: str
    as_of_s: float
    state: LotState
    reasons: tuple[Reason, ...]
    ordering_key: OrderingKey
    estimate_ids: tuple[str, ...]
    forecast_ids: tuple[str, ...]
    adjustment: AdjustmentEligibility
    alternative_lot_ids: tuple[str, ...]
    alternative_product_ids: tuple[str, ...]
    past_labeled_expiry: bool
    pins: Pins


@dataclasses.dataclass(frozen=True, slots=True)
class LotAssessment:
    lot_id: str
    as_of_utc: str
    estimates: tuple[PotencyEstimate, ...]
    forecasts: tuple[Forecast, ...]
    decision: LotDecision


@dataclasses.dataclass(frozen=True, slots=True)
class InventoryAssessment:
    as_of_utc: str
    assessments: tuple[LotAssessment, ...]


# =========================================================================================
# JSON conversion (implemented; pure)
# =========================================================================================


def to_json(obj: object) -> JSONValue:
    """Convert a dataclass tree (or JSON-shaped data) to plain JSON values.

    Dataclasses become objects with their fields in declaration order, tuples and lists
    become arrays, and mappings must have string keys. Floats must be finite (raises
    ``ValueError`` on NaN or infinity, which JSON cannot represent).
    """
    if obj is None or isinstance(obj, bool | str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"non-finite float cannot be serialized: {obj!r}")
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        out: dict[str, JSONValue] = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON object keys must be strings: {key!r}")
            out[key] = to_json(value)
        return out
    if isinstance(obj, Sequence):
        return [to_json(item) for item in obj]
    raise ValueError(f"cannot convert {type(obj).__name__} to JSON")


def sensor_series_from_json(data: Mapping[str, JSONValue]) -> SensorSeries:
    """Build a ``SensorSeries`` from its JSON form (contracts/pharmacy/inputs.schema.json
    ``#/$defs/SensorSeries``). The caller validates the JSON against the schema first; this
    function only converts types."""
    raw_points = data["points"]
    if not isinstance(raw_points, list):
        raise ValueError("points must be an array")
    points: list[SensorPoint] = []
    for raw in raw_points:
        if not isinstance(raw, dict):
            raise ValueError("each point must be an object")
        points.append(SensorPoint(t_utc=str(raw["t_utc"]), t_s=_num(raw["t_s"]), value=_num(raw["value"])))
    quantity = str(data["quantity"])
    if quantity not in QUANTITIES:
        raise ValueError(f"unknown quantity {quantity!r}")
    return SensorSeries(
        series_id=str(data["series_id"]),
        sensor_id=str(data["sensor_id"]),
        zone_id=str(data["zone_id"]),
        quantity=_quantity(quantity),
        unit=str(data["unit"]),
        points=tuple(points),
    )


def _num(value: JSONValue) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)


def _quantity(name: str) -> Quantity:
    for q in QUANTITIES:
        if q == name:
            return q
    raise ValueError(f"unknown quantity {name!r}")


def assay_from_record(record: AssayRecord) -> AssayObservation:
    """Convert a schema-valid ``AssayRecord`` to an ``AssayObservation``."""
    return AssayObservation(**record)


def inspection_from_record(record: InspectionRecord) -> InspectionObservation:
    """Convert a schema-valid ``InspectionRecord`` to an ``InspectionObservation``."""
    return InspectionObservation(**record)
