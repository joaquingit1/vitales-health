# Medicine potency engine: specification

Status: contract for the build phase. Package `pharmacy` (Python 3.14). Owners and order of work are in
docs/BUILD-PLAN.md. Research inputs: `reports/crew-health-medications-2026-11-14/research/` (degradation-science.md,
space-pharmacy.md, dosing-safety.md) and the design brief in the same folder. Source IDs (MDS, MDP, MDD, MDX) resolve
in `config/pharmacy/sources.json`.

The engine answers, per lot and per active ingredient: how much active drug remains at `as_of`, with a coverage
interval, what degradants formed, whether the lot is `in_specification`, `adjust` or `do_not_use` and why, when it is
forecast to leave specification, and in which order lots should be used. It never answers a dosing question; the dose
layer reads `estimate_basis` and `assay_ids_used` and applies its own policy.

Words used with a fixed meaning: **segment** (an interval of constant conditions), **Theta** (integrated rate,
dimensionless), **alpha** (fraction converted), **draw** (one Monte Carlo sample of all uncertain inputs),
**nominal** (evaluated at central parameter values with zero offsets), **layer** (where a parameter came from:
measured_model, computed, class_prior; plus assay_updated for estimates).

## Contents

1. Conventions
2. Types and contracts
3. Exposure
4. Package microclimate
5. Kinetics
6. Radiation
7. Chemistry layer
8. Regimes, validated range and placards
9. Parameters and uncertainty
10. Bayesian assay update and inspection override
11. Decision, forecast and ordering
12. Pipeline, store and replay
13. Environment simulator
14. Offline fit
15. Test vectors and acceptance criteria

---

## 1. Conventions

### 1.1 Units

| Quantity | Unit in code and JSON | Field suffix |
|---|---|---|
| Temperature | K (input series may be degC, converted with T = value + 273.15) | `_k` |
| Relative humidity | percent, 0 to 100 | `_percent`, `rh_` |
| Time and durations | s | `_s` |
| Activation energy | J/mol | `_j_per_mol` |
| Rate constant | 1/s; logarithms are natural logs of 1/s | `ln_k_..._per_s` |
| Humidity sensitivity B | 1/%RH | `b_per_percent_rh` |
| Absorbed dose, dose rate | Gy, Gy/s (absorbed dose, never dose equivalent) | `_gy`, `_gy_per_s` |
| Illuminance, near UV | lux, W/m2; doses in lux h and W h/m2 | `_lux`, `_w_per_m2` |
| Potency | percent of label claim | `_percent` |
| Degradant | percent of label API mass | `_percent` |
| Molar mass | g/mol | `_g_per_mol` |

R is `constants.GAS_CONSTANT_J_PER_MOL_K` = N_A k_B = 8.31446261815324 J/(mol K), exact. Published rates in other
units are converted with the constants in `pharmacy/constants.py` (minute 60 s, hour 3600 s, day 86400 s, month =
Julian year / 12 = 2629800 s, thermochemical calorie 4.184 J, `GRAMS_PER_KILOGRAM` = 1000.0
and `J_PER_KJ` = 1000.0).

### 1.2 Time

All instants are UTC. Every timestamp crosses the boundary twice: an ISO 8601 string with a `Z` suffix and float
seconds since the Unix epoch; they must agree within 1 ms (`SensorDataError` or `ObservationError` otherwise).
`types.iso_to_seconds` and `types.seconds_to_iso` are the only converters. Intervals are half open, [t0, t1). A reading
or observation exactly at `as_of` is used; one after `as_of` is never used and never changes a past result.

### 1.3 No hard-coded data

`pharmacy/constants.py` holds CODATA constants and unit definitions only. Every product, package, zone, prior,
threshold, tolerance and method parameter lives in versioned JSON under `config/pharmacy` with its basis
(`sourced`, `derived`, `team_default`) and source IDs; `team_default` values name the role in `to_be_set_by` that must
replace them. Code may hold structural choices (algorithms, the variate layout, enumerations) but no numbers that a
reviewer would want to change. Numerical-method tolerances are config (`engine-settings.json` `numerics`).

### 1.4 Canonical JSON, hashes and pins

Canonical JSON is `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`
encoded as UTF-8 (`config.canonical_json_bytes`). All hashes are SHA-256 of canonical JSON, lower-case hex.

* Config pin: `{document_id, version, sha256}` per document, sha256 of the parsed document. Pins are sorted by
  `document_id`.
* Sensor series pin: only series of zones in the lot's custody up to as_of, sorted by series_id.
  `{series_id, sha256}` over the series record (inputs schema `SensorSeriesRecord`) with points
  restricted to `t_s <= as_of_s`, exactly as received (before unit conversion and calibration).
* Observations hash: sha256 of `{"assays": [...], "inspections": [...]}` holding the records of the lot (all
  ingredients) with time `<= as_of`, each list sorted by its id.
* `Pins` also carries `engine_version` (`pharmacy.__version__`), the library versions of numpy, rdkit and tblite, the
  seed, the RNG name and the computed-layer cache keys used.

### 1.5 Determinism

The same pins give a bit-identical `InventoryAssessment` JSON on the same platform and library versions.
Rules: no wall clock inside the engine (only `recorded_at_utc` of the store, which is outside every hashed payload);
no global random state (only `numpy.random.Generator(PCG64)` built per spec 9.8); iterate over ids in sorted order
(Python string order) wherever a set or mapping is iterated; float64 everywhere; no multithreaded reductions whose
order can vary. Agreement with the independent oracle is required at 1e-9 relative, which is insensitive to
summation order; bit identity is required only between two runs of this engine.

### 1.6 Errors

Every failure raises a subclass of `errors.PharmacyError` with a stable `code`. The engine never returns NaN or
infinity in an output and never substitutes a default for a missing input. Configuration and input errors stop the
whole call (they are systemic). A computational failure for one lot also raises; the hub shows "no estimate
available" (NASA-STD-7009B [M&S 33], MDS37). Classes: `ConfigLoadError`, `ConfigSchemaError`,
`ConfigReferenceError`, `InputError`, `SensorDataError`, `UnitError`, `TimeWindowError`, `ObservationError`,
`StructureError`, `ModelDomainError`, `ComputationError`, `ConvergenceError`, `XtbError`, `DeterminismError`,
`StoreError`.

### 1.7 Accuracy targets

Deterministic quantities (everything that does not depend on random draws, plus every per-draw function evaluated at
given draws) must agree with the mpmath oracle (tests/oracle, 50 significant digits) within 1e-9 relative, or 1e-12
absolute where the value can be zero. The list is in 15.2. Monte Carlo outputs are judged by the statistical criteria
of 15.7 and by replay identity.

---

## 2. Types and contracts

### 2.1 Type families

`pharmacy/types.py` is the single source of truth for shapes.

* Configuration documents and JSON inputs are `TypedDict` classes mirroring the JSON key for key (required
  members and `NotRequired` additions; nullable members are `X | None`). Optional identification
  lists default to empty and calibration provenance defaults to null. Settings additions are optional in the Python
  type for caller compatibility; operations that need them require configured values, never code defaults.
* Runtime inputs and outputs are frozen, slotted dataclasses with floats, strings and tuples only. `types.to_json`
  serializes them (fields in declaration order). Numpy arrays appear only inside algorithms (`kinetics.DrawBlock`,
  `kinetics.DrawResult`, `uncertainty.AdaptiveResult`).

### 2.2 Main runtime types

| Type | Meaning |
|---|---|
| `SensorPoint`, `SensorSeries` | Raw readings of one sensor (quantity in `temperature_k`, `relative_humidity`, `dose_rate_gy_per_s`, `illuminance_lux`, `uv_w_per_m2`; unit; ISO and float timestamps) |
| `AssayObservation`, `InspectionObservation` | Observations of a lot (10) |
| `Segment`, `GapRecord`, `QuantityCoverage`, `ZoneHistory`, `LotExposure` | Exposure stage output |
| `ExposureSummary` | MKT, extremes, time above thresholds, cumulative doses, coverage (display) |
| `MicroclimateTrace` | Internal RH of the package at segment boundaries |
| `MoleculeInfo`, `ReactiveSite`, `Pathway`, `ParameterEstimate`, `ParameterBlock`, `StabilityModel` | Chemistry and parameter stage output |
| `PotencyEstimate`, `Forecast`, `LotDecision`, `LotAssessment`, `InventoryAssessment` | Engine outputs |

`PotencyEstimate` carries: quantiles, mean, standard uncertainty and the coverage interval of the active measurand;
total-drug quantiles for racemizing ingredients; specification; P(within), P(below), P(above); degradant estimates;
the radiation report; the exposure summary; flags; `estimate_basis` (weakest contributing layer including assays,
spec 9.6), `prior_basis` (same without assays), `assay_ids_used`, `inspection_ids_used`, `inspection_override`;
per-pathway contributions and parameter layers; placards; the Monte Carlo report; pins; and a trace of every number
with its origin.

`DegradantIdentification` is carried by `Pathway.degradant_identification` and
`DegradantEstimate.identification`: `identified` (bool, default false), `evidence_kind` (`measured_model`, `anchor`
or null), `evidence_id` (model or anchor id, or null), `source_ids` (tuple, default empty). Identification requires
non-null evidence and sources; an unidentified record has null evidence and no sources.

Algorithm-local records remain in their modules: `kinetics.NominalPathwayTheta` includes
`segment_theta`, `segment_d_rh_percent`, `segment_rh_in_max_percent` (tuples of floats, default empty, one per
lot segment). `uncertainty.AdaptiveResult.batch_statistics` is a tuple of (y, u(y), y_low, y_high) float tuples,
default empty. These records supply exact placard extents and batch-error reporting.

### 2.3 Configuration documents

#### 2.3.1 Bundle

`config.load_bundle()` loads eleven documents from `config/pharmacy`, each validated against
`contracts/pharmacy/<name>.schema.json` and hashed: `sources`, `engine-settings`, `reaction-classes`,
`lfer-constants`, `radiation-chemistry`, `packages`, `storage-zones`, `formulary`, `stability-models`,
`environment-profiles`, `lots`. Scenario fixtures (`fixtures/pharmacy/*.json`, schema `scenario`) and engine inputs
(schema `inputs`) are validated separately. Parsing is strict: NaN, Infinity and duplicate keys are errors.

#### 2.3.2 Reference checks

`config.reference_problems` must return an empty list: every `source_ids` entry resolves in sources.json; ids are
unique per document; products name known packages and degradant classes; lots name known products, ingredients,
zones and packages; zones name known environment profiles; product-scope stability models name known products,
ingredients and packages; anchors name known reference-study models; LFER series and Evans-Polanyi entries name known
classes; classes name known LFER series. A zone lists at most one sensor per quantity (the exposure stage uses it).

`measured_degradant_ids` on anchors must name degradants of that class, including product overrides. On product
models they must name the product ingredient's degradants or generic degradants of replaced classes. Anchor `storage_zone_id` and non-null `package_id` resolve in storage-zones and packages. A model's
`covers` names known classes. Calibration vector ids and row numbers resolve against local test-vector files;
`CalibrationProvenance.statistics` names are unique. A total prior's provenance vector and ordered rows equal its
`calibration` reference. Q3B dose bands increase and end with a null upper bound.

#### 2.3.3 Provenance rules (enforced by the schemas)

* `basis` `sourced` or `derived` requires at least one source ID.
* `basis` `team_default` requires a non-empty `to_be_set_by`.
* Distributions (`Prior`): `normal` and `lognormal` have no bounds; `truncated_normal` has at least one bound;
  `fixed` has sigma 0 and value `mu`; every other family has sigma > 0. Lognormal `mu` and `sigma` are in natural-log
  units of the value.

`TotalRatePrior` carries `temperature_k`, `temperature_sd_k`, `rh_percent` and `rh_sd_percent` as
`SourcedValue` records describing the calibration storage condition. Temperatures are positive; standard
uncertainties are nonnegative; RH lies in [0, 100]. An anchor uses the same first three fields; its unreported
RH uncertainty is not inferred. Every anchor additionally states `storage_zone_id` (string), `package_id`
(string or null), `duration_days` (SourcedValue or null), and optional `storage_context` (Provenance describing
the zone and package evidence). A non-null package requires a positive duration in days.

`CalibrationProvenance` records `method`, `vector_id`, positive unique `rows`, `statistics` (nonempty list of
`CalibrationStatistic` records with `name`, finite `value` and `unit`), `engine_version`, and the standard
`basis`, `source_ids`, `to_be_set_by`, `note`. It is optional and nullable on
`total_rate_priors[].calibration_provenance` and `zones[].rate_multiplier_calibration`. Null records do not
assert that a reproducible calibration has run. `pharmacy/calibration.py` populates them when it derives values.

### 2.4 Generated schemas and the validation script

`contracts/pharmacy/*.schema.json` (JSON Schema 2020-12, every object closed with `additionalProperties: false`) are
generated from the types by `python -m pharmacy.contracts --write`; `test-vectors.schema.json` is hand written. Ids
are `https://crew-health-check.invalid/schemas/pharmacy/<name>.schema.json` (never fetched). The unit test
`test_schemas_match_types` fails on drift. `scripts/validate_contracts.py` checks drift, the bundle, every fixture
(schema and `contracts.scenario_problems`), every vector file (schema and unique ids), calibration dataset and row references, and the absence of en and em
dashes. Provenance and reference-condition rules apply to the added fields too.

---

## 3. Exposure

### 3.1 Purpose

Turn raw sensor series into piecewise-constant, gap-bounded storage histories per zone and per lot, and summarize
them for the pharmacist. Module `pharmacy/exposure.py`.

### 3.2 Interface

```
normalize_series(series, sensor) -> SensorSeries
build_zone_history(zone, series, t_start_s, t_end_s) -> ZoneHistory
build_lot_exposure(lot, zones, packages, series_by_zone, as_of_s) -> LotExposure
mean_kinetic_temperature(temperatures_k, durations_s, activation_energy_j_per_mol, gas_constant) -> float
summarize_exposure(exposure, zone, settings) -> ExposureSummary
```

### 3.3 Normalization

For each series: the quantity must equal the configured sensor's quantity; the unit must be one of
`CANONICAL_UNITS[quantity]`; timestamps strictly increase; ISO and float times agree within 1 ms; values are finite.
Convert degC to K. Apply calibration: value + `calibration_offset.value`. Domain after calibration: T > 0, 0 <= RH <=
100, dose rate, illuminance and UV >= 0; violations raise `SensorDataError`. Readings after the window end are
ignored.

### 3.4 Segmentation and hold

Per quantity q of a sensed zone, with readings (t_j, v_j) and hold limit h = `max_hold_s`:

* If t_{j+1} - t_j <= h, [t_j, t_{j+1}) is **measured** with value v_j.
* Otherwise [t_j, t_j + h) is **held** with value v_j and [t_j + h, t_{j+1}) is a **gap**.
* Before the first reading: the last reading before `t_start_s` (if any) holds for up to h past its own time; the
  rest is a gap. After the last reading: hold up to h, then gap until `t_end_s`.
* Quantities the zone does not sense: declared conditions (`mu` for normal or fixed priors, exp(mu), the median, for lognormal priors; **declared**), else 0 for dose
  rate, illuminance and UV (**not_applicable**). Temperature and RH must be sensed or declared (`ConfigReferenceError`
  otherwise). Illuminance and UV are 0 when `light_exposure` is false.

Segment boundaries are the union of all change points of all quantities plus `t_start_s` and `t_end_s`. Adjacent
segments with identical values, packages and flags merge. Zero-length segments never appear.

`declared_segment` uses these same rules: undeclared dose rate, illuminance and UV are zero and
`not_applicable`; undeclared temperature or RH raises `ConfigReferenceError`.

### 3.5 Gaps

Gap values come from the zone's `gap_policy`:

* `worst_case`: T = `worst_case.temperature_max_k`; RH high = `rh_max_percent`, RH low = `rh_min_percent`; dose rate
  = `dose_rate_max_gy_per_s`; illuminance and UV = their maxima when the zone has light exposure, else 0.
* `last_plus_bound`: T = last valid value + bound; RH high = min(100, last + bound), RH low = max(0, last - bound);
  dose, illuminance and UV = last + bound. No previous reading: worst case. A sensed quantity without a bound is a
  `ConfigReferenceError`.

Rates never use a low temperature bound (Ea >= 0, so the maximum is the worst case); the lower bound
(`worst_case.temperature_min_k`, or last value minus bound) feeds only the freezing check of 8.3.
`Segment.relative_humidity_percent` holds the high value and `relative_humidity_low_percent` the low value (equal
outside RH gaps). Kinetics uses the high variant for a pathway draw with B >= 0 and the low variant for B < 0 (worst
case in both directions). Each gap is recorded as a `GapRecord`.

### 3.6 Coverage

Per zone and quantity: measured, held, gap, declared and not-applicable seconds, the longest gap and
coverage_fraction = (measured + held + declared) / (total - not_applicable) (1.0 when the denominator is 0). A zone
history is `coverage_low` when, for temperature, RH, dose rate, or illuminance and UV in a light-exposed zone, the
longest gap exceeds `gap_limit_s` or the coverage fraction is below `min_coverage_fraction`. A lot exposure is
`coverage_low` if any zone history over its custody intervals is.

### 3.7 Lot exposure

Custody intervals must tile [manufacture, as_of]: the first starts at `manufactured_at_utc`, each `to_utc` equals the
next `from_utc`, only the last may have `to_utc` null (open), and none may overlap (`TimeWindowError`). The package
history must start at manufacture and be non-decreasing in time. Segments are the zone histories clipped to custody,
split at package changes, with `package_id` set. `as_of` before manufacture raises `TimeWindowError`; `as_of` equal to
manufacture gives an empty exposure (Theta = 0, potency = C0).

### 3.8 MKT and summaries (display only)

Time-weighted mean kinetic temperature (USP <1079.2>, MDS05, MDS06; vectors TV-MKT-1 to TV-MKT-8):

```
T_mk = (dH/R) / ( -ln( sum_i tau_i exp(-dH/(R T_i)) / sum_i tau_i ) )
```

computed as `dH/R / (-(logsumexp(-dH/(R T_i) + ln tau_i) - ln sum tau_i))`. dH = 83144 J/mol from settings. Window
MKTs use the last `duration_s` before `as_of` (None when the history is shorter). Time above each threshold of the
lot's current zone (the `zone` argument) is the total segment time with T > threshold. Gap-filled segments enter
every summary with their filled values. Doses: sum of dose_rate x tau (Gy), illuminance x tau / 3600 (lux h), UV x tau /
3600 (W h/m2), at the zone (package transmission not applied). Potency never uses MKT.

### 3.9 Edge cases

Empty series: all gap. A single reading: hold then gap. Duplicate timestamps: `SensorDataError`. as_of inside a gap:
the gap ends at as_of. A custody interval in a declared zone: declared coverage, no gaps. Negative durations cannot
arise (strictly increasing times).

### 3.10 Tests

Unit: every rule of 3.3 to 3.7 with hand-built series, including degC input, calibration offset, hold at exactly h,
gap fill both policies, merge, custody tiling errors, as_of equal to manufacture. Oracle: MKT (1e-9 relative) and
segment tables for three crafted series. Vectors TV-MKT-2 to TV-MKT-8. Property: MKT of a constant series equals the
constant (TV-MKT-4), MKT >= time-weighted arithmetic mean (TV-MKT-5), equal intervals reduce to the USP form
(TV-MKT-6), segment durations sum to the window, coverage fractions in [0, 1].

---

## 4. Package microclimate

### 4.1 Purpose

The drug sees the package's internal RH, not habitat RH (degradation-science.md section 3.4; MDS15). Module
`pharmacy/microclimate.py`.

### 4.2 Model

Kinds: `none` (RH_in = RH_out), `sealed_constant` (RH_in = `sealed_rh_percent`), `first_order_exchange`:

```
dRH_in/dt = (RH_out - RH_in) / tau_m
tau_m(T) = C_eff / P(T)                                   [s]
C_eff    = sum_j m_j s_j + headspace capacity              [mg per %RH]
P(T)     = (MVTR_test / dRH_test) / 86400 x exp(-(E_perm/R)(1/T - 1/T_test))   [mg/(s %RH)]
```

m_j is the dry mass (g) and s_j the sorption slope (mg/g per %RH) of component j. Over a segment of duration tau with
constant RH_out and T, exactly:

```
RH_in(t) = RH_out + b e^(-t/tau_m),   b = RH_in,start - RH_out
RH_in,end = RH_out + b e^(-tau/tau_m)
```

tau_m is evaluated at the segment's nominal temperature.

### 4.3 Algorithm over a lot exposure

Walk segments in time order carrying RH_in for both forcing variants (high and low, spec 3.5). `none` and
`sealed_constant` are memoryless: RH_in = RH_out of the segment, or the sealed value, at start and end.
`first_order_exchange` starts each segment from the previous segment's end value, so RH_in is continuous across
repackaging (the product carries its moisture); the first package interval of the lot starts at
`initial_internal_rh_percent` (null: the first segment's RH_out). Output per segment: RH_in at start and end for both
variants, tau_m (inf for sealed, 0 for none) and the kind.

### 4.4 Per-draw RH offsets

A draw adds an offset d_z (zone z RH offset, spec 9.7) to RH_out in the segments of zone z whose RH is not
gap-filled. Write e_z(s) = 1 for those segments and 0 otherwise. The model is linear in RH_out, so

```
RH_in,start'(s) = RH_in,start(s) + sum_z d_z h_z(s)
```

where h_z is the response to a unit offset in zone z (`unit_step_response`), computed with the same rules as RH_in:

```
none:                  h_z,start(s) = h_z,end(s) = e_z(s)
sealed_constant:       h_z,start(s) = h_z,end(s) = 0
first_order_exchange:  h_z,start(s) = h_z,end(s-1)   (first segment: e_z(0) if the start tracks RH_out, else 0)
                       h_z,end(s)   = h_z,start(s) e^(-tau/tau_m) + e_z(s) (1 - e^(-tau/tau_m))
```

A lot that crossed three zones carries three h vectors. Per-draw RH values are not clipped.

### 4.5 Edge cases

C_eff <= 0, missing MVTR fields for `first_order_exchange`, or a segment without a package: `ModelDomainError`.

### 4.6 Tests

Unit: each kind; repackaging continuity; tau_m(T) at T_test equals C_eff/(MVTR/dRH/86400). Oracle: trace of a
40-segment history at 1e-9 absolute in %RH. Property: RH_in stays within [min, max] of the initial value and all
forcing values; h in [0, 1]; linearity (offset d gives RH_in + d h within 1e-12).

---

## 5. Kinetics

Module `pharmacy/kinetics.py`.

### 5.1 Rate constant

Humidity-corrected Arrhenius in reference form (MDS12, MDS14, MDS18):

```
ln k = ln k* - (Ea/R)(1/T - 1/T*) + B (RH - RH*)
```

T* = 298.15 K and RH* = 40 %RH from settings; every parameter in the engine is expressed at (T*, RH*) (spec 9.3).
`rate_constant` returns exp of the summed exponent. Vectors TV-KIN-3e, TV-KIN-5, TV-H-2 (known mismatch), TV-H-3,
TV-H-4.

RH in a rate law is the RH experienced by the product. For packaged products it is internal RH from section 4.
Measured B and a measured model's validated RH range refer to that internal RH, including during offline fitting.

### 5.2 Integrated rate

For piecewise-constant conditions the integrated rate is exact and order independent:

```
Theta = sum_i k_i tau_i
```

`integrated_rate` evaluates it with RH as given. With a package, the humidity factor varies inside a segment and
spec 5.4 applies. Vectors TV-INT-1 (Theta 0.014985701370961854, potency 98.5126025453952 at 1e-9), TV-INT-3.

### 5.3 Model kinds

d(alpha)/dt = k f(alpha); g is the integral form, g(alpha) = Theta; G = g^-1.

Table 5.3.1:

| Kind | f(alpha) | g(alpha) | G(Theta) | Complete at |
|---|---|---|---|---|
| F0 | 1 | alpha | min(Theta, 1) | 1 |
| F1 | 1 - alpha | -ln(1 - alpha) | -expm1(-Theta) | never |
| F2 | (1 - alpha)^2 | alpha/(1 - alpha) | Theta/(1 + Theta) | never |
| Fn (n > 0, n != 1) | (1 - alpha)^n | ((1 - alpha)^(1-n) - 1)/(n - 1) | -expm1(log1p((n-1) Theta)/(1-n)) | 1/(1-n) if n < 1 |
| R2 | 2 (1 - alpha)^(1/2) | 1 - (1 - alpha)^(1/2) | Theta (2 - Theta) | 1 |
| R3 | 3 (1 - alpha)^(2/3) | 1 - (1 - alpha)^(1/3) | Theta (3 - 3 Theta + Theta^2) | 1 |
| A2, A3, A4, An (m = 2, 3, 4, avrami_n) | m (1 - alpha) (-ln(1 - alpha))^((m-1)/m) | (-ln(1 - alpha))^(1/m) | -expm1(-Theta^m) | never |
| B1 (seed alpha0 in (0, 1)) | see below | see below | see below | never |
| D1 | 1/(2 alpha) | alpha^2 | sqrt(Theta) | 1 |
| D2 | 1/(-ln(1 - alpha)) | (1 - alpha) ln(1 - alpha) + alpha | Newton on g | 1 |
| D3 (Jander) | (3/2)(1 - alpha)^(2/3) / (1 - (1 - alpha)^(1/3)) | (1 - (1 - alpha)^(1/3))^2 | s (3 - 3 s + s^2), s = sqrt(Theta) | 1 |
| D4 (Ginstling-Brounshtein) | (3/2) / ((1 - alpha)^(-1/3) - 1) | 1 - 2 alpha/3 - (1 - alpha)^(2/3) | Newton on g | 1/3 |

Beyond completion G = 1. Prout-Tompkins B1 is autocatalytic and needs a seed: the raw conversion obeys
d(a)/dt = k a (1 - a) from a(0) = alpha0, so a(Theta) = 1/(1 + ((1 - alpha0)/alpha0) e^(-Theta)); the engine reports
alpha = (a - alpha0)/(1 - alpha0) so alpha(0) = 0. Then g(alpha) = logit(alpha0 + (1 - alpha0) alpha) - logit(alpha0)
and f(alpha) = a (1 - a)/(1 - alpha0) with a = alpha0 + (1 - alpha0) alpha. The reduced Sestak-Berggren form with m = 0
is Fn and is not a separate kind.

D2 and D4 inverses: h(alpha) = g(alpha) - Theta is increasing on [0, 1). Start from the series (D2: g = sum_{n>=2}
alpha^n/(n(n-1)), start alpha = sqrt(2 Theta); D4: g = alpha^2/9 + 4 alpha^3/81 + ..., start alpha = 3 sqrt(Theta)) when
the start is below `small_alpha_series_threshold`, else 0.5; Newton steps with bisection fallback on the bracket [lo,
hi] updated every step; stop when the step is below `inverse_absolute_tolerance` (at most 200 iterations,
`ConvergenceError`). For alpha below the threshold evaluate g by its series to avoid cancellation.

Domain errors (`ModelDomainError`): Theta < 0; Fn with n <= 0 or n = 1; An with m <= 0; B1 without alpha0 in (0, 1);
missing kind parameters.

`conversion_from_theta` requires `numerics` for D2 and D4; omission raises `ModelDomainError`.
The safeguarded Newton step bisects when g' is infinite at alpha = 1. `theta_from_conversion` uses the table's
closed forms at every alpha with `expm1` and `log1p` where applicable; small-alpha series belong only to the D2
and D4 inverses. `rate_function` for D2 uses `-log1p(-alpha)` near zero, including in the independent oracle.

### 5.4 Humidity integral

Inside a segment the thermal rate of pathway p is k_T(T) exp(B (RH_in(t) - RH*)) with RH_in(t) = RH_out' + b'
e^(-t/tau_m) (primes: per-draw values, spec 4.4). Then

```
Theta_seg = k_T exp(B (RH_out' - RH*)) J(x, tau, tau_m),     x = B b'
J(x, D, tau_m) = integral_0^D exp(x e^(-t/tau_m)) dt
               = D + tau_m sum_{n>=1} x^n (1 - e^(-n D/tau_m)) / (n n!)          (x >= -5)
               = tau_m (Ei(x) - Ei(x e^(-D/tau_m)))                               (x < -5)
```

Limits: tau_m = inf (sealed) gives D e^x; tau_m = 0 (none) gives D. Use `-expm1(-n D/tau_m)` for the bracket. Stop the
series when a term is below `series_relative_tolerance` x the running sum after n > |x|, at most `series_max_terms`
(`ConvergenceError`). Required accuracy 1e-12 relative for |x| <= 50.

Let delta = D/tau_m and span = |x| (1 - exp(-delta)). Before the series or Ei branches, when span <= 1 and
delta <= 0.1, evaluate J by Gauss-Legendre quadrature. Use 16 nodes except when max(span, delta) is at most
one of (1e-5, 1e-3, 5e-3, 0.03, 0.1), which uses (2, 3, 4, 6, 8) nodes respectively, taking the first matching
rung. When |x exp(-delta)| is below the smallest normal float, evaluate its Ei from
Euler's constant + ln|x| - delta. This avoids underflow to Ei(0) while preserving float64 accuracy.

### 5.5 Light

Light is a dose, not a rate (degradation-science.md section 4; Q1B, MDS03). Only in zones with `light_exposure` true
and only for photolysis pathways:

```
Theta_L,seg = (k_vis Tr_vis E + k_uv Tr_uv U) tau / 3600
```

E in lux, U in W/m2, Tr the package transmissions, k_vis per lux h and k_uv per W h/m2. No Arrhenius factor and no
zone multiplier. A product with `photostable_per_q1b` true has no photolysis pathway.

### 5.6 Composition, potency and degradants

All destructive pathways of one ingredient share one conversion variable with the ingredient's rate-law kind:
Theta = sum over destructive pathways and segments of Theta_p,seg, alpha = G(Theta). The radiolysis pathway is part
of that sum only when `include_in_potency` is true (spec 6.1); otherwise its Theta is computed for the radiation report
and its nominal share (spec 9.6) is 0. Thermal contribution per segment
and pathway (spec 8 for the frozen and extrapolation terms, 9.7 for the draw variables):

```
RH_out'   = RH_out,seg + e(seg) dRH_zone                       (e = 0 in RH gaps, spec 4.4)
T'        = T_seg + dT_zone                                     (no offset in temperature gaps)
b'_seg    = RH_in,start'(seg) - RH_out'
ln k_p,seg = ln k*_p + ln m_zone + [frozen] ln m_frozen,p + z_T,p sd_T d_T,seg + z_RH,p sd_RH d_RH,seg
             - (Ea_p/R)(1/T' - 1/T*) + B_p (RH_out' - RH*)
Theta_p,seg = exp(ln k_p,seg) J(B_p b'_seg, tau_seg, tau_m,seg)
```

For `none`, b' = 0 and J = tau; for `sealed_constant`, J = tau e^(B b') so the rate sees the sealed RH. In RH gaps a
pathway draw with B < 0 uses the low variant of RH_out and RH_in.

Remaining drug, percent of label claim:

```
C_total  = C0 (1 - alpha)
C_active = C0 (1 - alpha) rho          (rho = 1 for non-racemizing ingredients)
```

Degradants: per segment the conversion increment is split in proportion to the pathways' Theta in that segment
(exact for piecewise-constant conditions): delta alpha_p = (Theta_p,seg / Theta_seg) (G(Theta_cum,seg) -
G(Theta_cum,seg-1)). Degradant percent = C0 x yield x (M_degradant / M_API) x alpha_p, with M_degradant / M_API = 1 when
the degradant's molar mass is unknown (noted in the trace). Product degradants (formulary) override the class's
generic degradant for the same class. Interactions between co-formulated ingredients (clavulanate accelerating
amoxicillin, MDS102) are not modeled; each ingredient has its own model.

`evaluate_draws` returns degradants on a molar basis (percent of label API in moles). The pipeline multiplies by
M_degradant / M_API from the formulary and traces `degradant_mass_ratio:<degradant_id>`; when the degradant's mass
is unknown it retains the molar basis with ratio 1 and states that limitation in the trace.

A degradant prediction is identified only when the forming pathway's rate uses evidence specific to it:
a selected measured model listing its id in `measured_degradant_ids`, or a selected anchor listing that id because
it measured the degradant's formation. Both lists default to empty. Total assay loss, a class prior, structure
matching, and a named toxic product alone do not identify the forming rate. Chemistry supplies
`degradant_identification`; the pipeline carries it to `identification` and traces, per degradant,
`degradant_identified:<id>` (string "true" or "false"), `degradant_evidence_kind:<id>` and
`degradant_evidence_id:<id>`, with the evidence's sources. Unidentified predictions represent worst-case
attribution of total loss and follow section 11.1.

### 5.7 Racemization

Racemization at a labile stereocenter changes activity, not total assay (MDS108). With one-directional inversion
rate k_r (L to D, equilibrium 50:50) and Theta_r = sum k_r tau over the racemization pathway:

```
rho(Theta_r) = (1 + (2 f0 - 1) e^(-2 Theta_r)) / 2
```

f0 = `racemization.initial_active_fraction`. Racemization is assumed independent of the destructive pathways (both
enantiomers degrade alike). Estimates for racemizing ingredients report the active measurand and also the total.

### 5.8 Vectorized evaluation

`evaluate_draws` evaluates a `DrawBlock` over a lot exposure: Theta per pathway, alpha, rho, potencies, degradant
percents and the central radiation loss (spec 6), with no random numbers. `nominal_thetas` evaluates one nominal draw
(posterior means, Ea at its location mu, multipliers at their medians, offsets and z values 0) and also returns the
nominal Theta accrued outside the validated range, while frozen and during phase change (spec 8).

The low outside and internal RH variants are used for B < 0 in draw evaluation, nominal Thetas and forecast
marches alike. `NominalPathwayTheta` carries the per-segment values of section 2.2. Fixed chunks contain 256
segments and are reduced in chunk order (12.5).

### 5.9 ODE reference

`integrate_ode_reference` integrates d(alpha)/dt = k_i f(alpha) with scipy `solve_ivp` (Radau, rtol 1e-11, atol
1e-14), restarting at every segment boundary. F and R kinds start at alpha = 0, B1 at its seed (relative alpha 0).
Kinds with zero or singular f at alpha = 0 (D1 to D4, A2, A3, A4, An) start at alpha_s = 1e-9 and consume
g(alpha_s) of Theta from the start of the history, so the endpoint corresponds to the full Theta. Tests only; agreement with
the closed forms within 1e-8 absolute in alpha for every kind; vector TV-INT-4 at 1e-8 relative.

### 5.10 Edge cases

Empty history: Theta = 0, alpha = 0, rho = f0. B < 0 is allowed and uses the low RH variant in gaps. Very large Theta:
G saturates without overflow (expm1 and log1p forms). k underflow to 0 is valid.

### 5.11 Tests

Unit: each kind at hand-computed points; F1 TV-INT-1 and TV-INT-2; rate-constant vectors. Oracle (1e-9): G, g, f for
every kind on a grid of Theta in [0, 10] and parameters; J on x in [-50, 50], D/tau_m in [1e-6, 1e3]; evaluate_draws on
five hand-built DrawBlocks over a 30-segment exposure with a bottle, a bag and a sealed blister. Property: G(g(alpha))
= alpha within 1e-12; G increasing; Theta order independent and split invariant (TV-INT-3, 1e-12 relative); potency
non-increasing in time; ODE agreement (TV-INT-4); J between D e^min(x,0) and D e^max(x,0).

---

## 6. Radiation

Module `pharmacy/chemistry/radiolysis.py`; the per-draw central loss is computed in `kinetics.evaluate_draws`.

### 6.1 Policy

Radiation at mission doses is not a measurable driver for solid products (space-pharmacy.md finding 3; V9). It is
reported as a separate term with an upper bound and enters potency only when `settings.radiation.include_in_potency`
is true (default false).

### 6.2 Direct effect (solids)

```
k_D = G M / 1000      [1/Gy]  (G in mol/J, M in g/mol; 1 Gy = 1 J/kg)
```

G is the radiolytic loss yield (lognormal prior, MDS73, MDS63: 1e-8 to 1e-6 mol/J). k_D does not depend on the drug's
mass fraction (energy deposited in the drug scales with it). Vector TV-RAD-2: at 1 Gy the loss is below 1e-4 for
solids.

### 6.3 Indirect effect (aqueous solutions)

Hydroxyl radicals from water radiolysis attack the drug; the drug scavenges them with efficiency rising with
concentration c (mol/kg; MDS68):

```
G_eff(c) = G_max c / (c + c_half),   k_D = G_eff / c = G_max / (c + c_half)
```

In frozen solution segments (T below the product's freezing point) radical diffusion is suppressed in ice, so the
direct effect of 6.2 replaces the indirect one (radiation-chemistry.json `frozen_solution_uses_direct_effect`,
MDS68). An ingredient without `concentration_mol_per_kg` uses the direct effect throughout. The radiolysis pathway of
a solution or suspension therefore carries `ln_k_dose_per_gy` (indirect, ln G_max - ln(c + c_half)) and
`ln_k_dose_frozen_per_gy` (direct, ln G + ln(M/1000)); both share one standard normal (spec 9.7). Central loss per draw = 1 - exp(-sum_seg k_D,seg dose_rate tau); reported as the median
over draws.

### 6.4 Upper bound and flag

```
loss_upper = 1 - exp(-k_upper F D_cum)
```

k_upper per dose-form family (radiation-chemistry.json `upper_bounds`), F = `dose_rate_safety_factor` (10), D_cum the
cumulative absorbed dose. A product with `radiosensitive` true and D_cum above `radiosensitive_dose_threshold_gy`
(0.9 Gy, the highest dose tested on kit solids, MDP11) raises flag `radiosensitive_dose_exceeded` (info reason).

`RadiationReport.method` is the radiolysis pathway's `ComputedDetail.method` (`g_value_direct` or
`g_value_indirect`), or `none` when absent.

### 6.5 Tests

Unit and oracle: k_D direct, indirect and the bound (exact). Vector TV-RAD-2. Validation 15.5 (V9 to V12).

---

## 7. Chemistry layer

Owner CX-C. Modules `pharmacy/chemistry/*.py`. Output: `StabilityModel` via `parameters.build_stability_model`.

### 7.1 Structures

RDKit (pinned version): `MolFromSmiles` with sanitization (`StructureError` on failure), isomeric canonical SMILES,
InChIKey, Hill formula, average molar mass (`Descriptors.MolWt`), defined stereocenters
(`FindMolChiralCenters(includeUnassigned=False)`). The formulary SMILES are PubChem isomeric SMILES (MDX01).

### 7.2 Pathway identification

Classes that do not apply to the product (family, photolysis or racemization conditions below) are removed first, so
they never take a site from one that applies. For each remaining reaction class in config order: run each SMARTS with
`GetSubstructMatches(uniquify=False)` (all mappings, at most RDKit's default `maxMatches` of 1000; reaching it raises
`StructureError`); the site is the atom carrying `site_atom_map`; matches at the same site collapse (first pattern,
first match wins). With
`requires_defined_stereocenter` the site must be a defined stereocenter. Two classes of the same mechanism matching
the same site atom: the class whose matching pattern has more atoms keeps the site (tie: config order); so a catechol
oxygen belongs to `catechol_autoxidation`, not `phenol_autoxidation`. A class with at least one site and whose
`applies_to_families` contains the product family gives one pathway `<ingredient_id>:<class_id>`. Photolysis
pathways need a match, `photostable_per_q1b` not true and a package in the lot history with light transmission > 0.
`radiolysis` applies to every ingredient. `racemization_*` needs the ingredient's `racemization` block. An ingredient
with no destructive thermal match gets `unclassified_thermal`. Expected pathways for the formulary are tabulated by
the CX-C unit tests (for example acetaminophen: amide_hydrolysis, phenol_autoxidation, radiolysis; epinephrine:
catechol_autoxidation, amine_alpha_oxidation, racemization_benzylic_alcohol, radiolysis).

### 7.3 Computed layer

#### 7.3.1 Anchor choice

The class anchor whose `dose_form_family` equals the product's (semisolid falls back to solid, suspension to
solution); first in config order. The anchor's ln k is converted to a ground-equivalent rate at (T*, RH*) using its storage zone,
package and duration per spec 9.3. Without an anchor the
computed layer has no estimate for that pathway, except for Evans-Polanyi classes (7.3.4).

If the ingredient is the anchor molecule (equal RDKit canonical isomeric SMILES), delta ln k = 0 with
`same_molecule_sd_log10`. Otherwise the class's method applies; if it cannot (no rho, no applicable series, no
substituent match) the result is delta 0 with the method error (`anchor_only`). Standard deviations combine as
variances: method error, plus `unparameterized_substituent_sd_log10` per unmatched substituent, plus
`ortho_substituent_sd_log10` per ortho substituent, plus `heteroaromatic_ring_sd_log10` when the reacting ring is
heteroaromatic; log10 units convert to ln by multiplying by ln 10.

Each site gets its own delta (0 where no series applies) and the sites combine by 7.3.5, so an ingredient with n
sites against an anchor with n_ref sites and no applicable series gets ln(n / n_ref). Only the series listed in the
class's `computed_method.series_ids` run; every applicable listed series adds its delta at a site; the method label is
the single kind applied, else the class kind, else `anchor_only`. Substituents identical in fragment and position on
the ingredient and anchor rings cancel; every remaining ortho or unmatched substituent, on either ring, adds its
variance.

#### 7.3.2 Hammett

For a series with `ring_attachment_map` m: the ring is the smallest aromatic ring containing the atom mapped m or, when
that atom is not aromatic, the ring of its aromatic neighbor with the lowest atom index (benzene only;
other rings add the heteroaromatic variance and use sigma 0). Substituents on that ring other than the reacting group
are identified by canonical fragment SMILES with a dummy atom at the attachment (`structure.substituent_fragments`)
and looked up in `hammett_substituents`; meta (position 3) and para (4) use sigma_meta and sigma_para; ortho adds
variance and uses 0.

```
delta ln k = ln(10) rho (sum sigma_ingredient - sum sigma_anchor)
```

Constants: Hammett 1937 via Wikipedia (MDX02); rho 2.494 for alkaline hydrolysis of ethyl benzoates. These differ from
the Hansch, Leo and Taft 1991 compilation (MDX04).

#### 7.3.3 Taft

For aliphatic esters (substituent at `substituent_attachment_map`, not aromatic):

```
delta ln k = ln(10) (rho* (sigma*_ing - sigma*_anchor) + delta (Es_ing - Es_anchor))
```

Constants via Anslyn and Dougherty 2006 (MDX03). Unmatched substituent: anchor_only plus the unparameterized variance.

#### 7.3.4 Evans-Polanyi

For autoxidation classes with an `evans_polanyi` entry: BDE of each site's X-H bond (spec 7.4) against the reference
molecule's same bond computed the same way:

```
delta_s = -alpha_EP (BDE_site - BDE_ref) x 1000 / (R T*)
```

alpha_EP = 0.5 (team default). A linear free-energy relation is used only inside the domain it describes (OECD QSAR
validation principle 3): when |BDE_site - BDE_ref| exceeds the entry's `max_abs_delta_bde_kj_per_mol` (team default
30), the relation is not applied at that site, delta_s = 0, the site keeps its statistical weight in 7.3.5, and the
pathway's `ComputedDetail.flags` gets `outside_applicability_domain`. A site far from its reference (for example the
captodative alpha carbon of amoxicillin against trimethylamine) is a different radical family, not a stronger case
of the same one.

With an anchor, the reference is the anchor molecule: BDE_ref is the anchor's first site of the class, its sites form
the denominator of 7.3.5, and the result is a computed-layer delta with the method error (7.3.1). Without an anchor
(every autoxidation class today) the shift redistributes the class prior among the ingredient's pathways (spec 9.5)
and the layer stays `class_prior`. Every BDE and cache key used is reported in the trace (`ComputedDetail.cache_keys`,
sorted).

#### 7.3.5 Several sites

delta ln k = ln( sum_s exp(delta_s) / n_ref ), n_ref the number of sites of the class in the anchor or reference
molecule (log-sum-exp). For Evans-Polanyi classes each site is weighted by its abstractable hydrogens (reaction path
degeneracy): delta ln k = ln( sum_s n_H,s exp(delta_s) / sum_r n_H,r ), the denominator over the reference
molecule's sites of the class (trimethylamine 9, phenol 1, catechol 2).

`lfer.computed_adjustment` accepts keyword-only `xtb_settings=None`, `reference_temperature_k=None` and
`cache_dir=None`; the first two supply anchored Evans-Polanyi calculations. `parameters.build_stability_model`
accepts keyword-only `cache_dir=None`.

### 7.4 BDE with GFN2-xTB

`bde.bond_dissociation_energy`: RDKit add hydrogens, `xtb_n_conformers` ETKDGv3 conformers with `rdkit_embed_seed`
(`EmbedMultipleConfs`), MMFF94 optimization of each; then GFN2-xTB through tblite (`Calculator("GFN2-xTB", numbers,
positions_bohr, charge, uhf)`, positions in bohr = angstrom x `ANGSTROM_TO_BOHR`, energies in hartree) with the
configured accuracy and electronic temperature, and for open-shell species (radical and H atom) the spin-polarization
interaction at `xtb_spin_polarization_scale` (`Calculator.add("spin-polarization", scale)`; 0 leaves it out);
geometry optimization by scipy L-BFGS-B on energy and gradient until the largest gradient component is below
`optimizer_gtol_eh_per_bohr`, with at most `optimizer_max_steps` per conformer. Parent uhf 0, energy the lowest over
the optimized conformers; radical uhf 1, built from every optimized parent conformer (remove the lowest-index H on the
site atom, re-optimize), energy the lowest over them (a hydrogen-bonded radical is found only from some conformers); H
atom single point uhf 1. A conformer whose parent or radical optimization does not converge within
`optimizer_max_steps` is left out of that minimum and counted; the result records how many parent and radical
optimizations converged, and `ConvergenceError` is raised when fewer than half of the parent optimizations, or fewer
than half of the radical optimizations that ran (one per converged parent), converged. tblite's OpenMP and OpenBLAS runtimes run single-threaded (set by `bde` before the first
calculation), because threaded gradient reductions change the last bits and so the optimizer path.

```
BDE = (E_radical + E_H - E_parent) x HARTREE_TO_J_PER_MOL / 1000      [kJ/mol, electronic]
```

No zero-point or thermal correction (cancels to first order in the difference with the reference). Results are cached
by the SHA-256 of canonical JSON of {InChIKey, symmetry-class canonical rank of the site atom (RDKit
`CanonicalRankAtoms(breakTies=False)`; symmetry-equivalent sites share one entry, computed on the class member that
comes first in the canonical SMILES), bond, method, accuracy, electronic temperature, seed, number of conformers,
spin-polarization scale, optimizer gradient tolerance and maximum steps, tblite and rdkit versions}; the cache key
goes into `Pins.computed_cache_keys`. Entries live in
`data/pharmacy/xtb-cache/<cache_key>.json` (canonical JSON of `BdeResult`), committed by CX-C for every formulary site
and every Evans-Polanyi reference molecule, so estimates and replays never depend on running xTB and are identical
across machines. A miss computes and writes the entry. Where tblite is not installed (it has no Windows build), the tblite
version in the key and in `Pins` is `xtb_cache_tblite_version` of the engine settings, the version that computed
the committed cache: committed entries are read and a miss raises `XtbError`. Recomputing an entry on the same platform and versions must
give an identical value.

`PHARMACY_XTB_CACHE_DIR` overrides the cache location for a run; an explicit `cache_dir` takes precedence,
then this variable, then `data/pharmacy/xtb-cache`. This changes location only: entries remain content addressed
and verified on read. Write entries to a temporary file in the destination directory, then use `os.replace`
so concurrent readers cannot see partial JSON. A session-scoped autouse fixture copies the committed cache to a
temporary directory and sets the variable. Independently of chemistry honoring the variable, the fixture hashes
all committed cache file bytes before and after the session and fails on additions, deletions or changed bytes.

### 7.5 Tests

Unit: SMARTS matches for all formulary molecules (expected pathway table), site collapse, catechol precedence,
stereocenter requirement, substituent fragments, Hammett and Taft arithmetic, anchor selection, same-molecule rule.
xtb (marker `xtb`): BDE determinism (two runs identical), cache equality, phenol O-H BDE in [300, 420] kJ/mol, phenol
minus methanol O-H within 10 kJ/mol of the experimental -77.4 kJ/mol (Blanksby and Ellison 2003), catechol O-H <
phenol O-H, symmetry-equivalent sites share one key under atom permutations. Calibration (fast): for every ingredient
in the total-rate split, sum over its split pathways of exp(mu) equals exp(mu_total) within 1e-12 relative, with and
without Evans-Polanyi shifts. Coverage of `bde.py` is measured separately.

---

## 8. Regimes, validated range and placards

### 8.1 Validated range

Each pathway has a validated temperature and RH range: the measured model's (study range), else its class's
`validated_range` (team default today: 288.15 to 313.15 K and 10 to 75 %RH for the destructive thermal classes,
wider for racemization, photolysis and radiolysis). Distances are per segment and from nominal values.
For measured models let T_low = T_min - `validated_range.temperature_below_min_margin_k`
(team default 10 K, chemistry lead); for class ranges T_low = T_min. Temperatures down to T_low are inside the
range, the maximum is unchanged, and d_T = max(0, T - T_max, T_low - T).
d_RH = max(0, RH - RH_max, RH_min - RH) over the nominal high-variant RH_in at the segment start and end,
taking the larger. d_RH is 0 when B is absent or fixed at zero, meaning its mean and variance are both zero.

The outside share is the sum over pathways of (pathway share of nominal Theta) times (fraction of that pathway's
nominal Theta accrued outside its validated range).

### 8.2 Widening outside the range

Per draw and pathway, standard normals z_T and z_RH (spec 9.7) add model-form error that grows with the distance:
ln k += z_T `ln_k_sd_per_kelvin` d_T + z_RH `ln_k_sd_per_percent_rh` d_RH in every segment. The same z applies to all
segments (systematic error).

These temperature and RH widening terms apply only to thermal pathways. Light and radiation have no Arrhenius
or humidity extrapolation term; their range flags and placards can still be assessed. Distances below a measured
model's temperature range are measured from its extended lower limit in 8.1.

### 8.3 Regime switches

Regimes are judged at nominal values (no per-draw offsets).

* Frozen liquids: a solution or suspension segment with nominal T below the product's `freezing_point_k` multiplies
  the thermal rate by the frozen multiplier draw (beta-lactams: lognormal median e^2.117 = 8.3 fold, MDS103; others
  median 1, sigma 1.17). The Arrhenius factor is still evaluated at T, and the segment is outside the range so
  widening applies. A gap-filled temperature segment whose lower bound (3.5) is below the freezing point counts as
  possibly frozen only for solution and suspension dose forms; solids never are. For a possibly frozen liquid the flag and reason below apply, the rate does not change. Flag `regime_switch`; a product with
  `do_not_freeze` and any frozen or possibly frozen segment gets a blocking `regime_switch` reason (spec 11.1).
* Phase change: T above `regime_thresholds.phase_change_temperature_k` (for example a suppository melting): flag
  `regime_switch`, Arrhenius plus widening.
* Deliquescence: nominal RH_in above `regime_thresholds.deliquescence_rh_percent`: flag `regime_switch`, rate plus
  widening.
* Time extrapolation (measured models only): (as_of - manufacture) / `max_study_duration_s` above
  `max_time_extrapolation_ratio` (2, ICH Q1E, MDS02).

### 8.4 Flag and placards

`outside_validated_range` is raised when the nominal Theta accrued in out-of-range segments is at least
`flag_share_threshold` (0.01) of the total nominal Theta, or any frozen liquid segment, phase change or deliquescence
occurred, or the time extrapolation ratio is exceeded. Each limit type exceeded produces one `Placard`
(NASA-STD-7009B [M&S 26], MDS37): kind, the largest exceedance (`extent` with its `unit`), time spent, share of
nominal Theta and a one-sentence message naming the consequence (for example "rates above 40 C are extrapolated;
interval widened"). Kinds, in this order:

| Placard kind | Raised when | extent, unit |
|---|---|---|
| temperature_range | some segment has d_T > 0 for a contributing pathway | max d_T, K |
| humidity_range | some segment has d_RH > 0, or deliquescence | max d_RH, %RH |
| time_extrapolation | the ratio of 8.3 is exceeded | ratio, 1 |
| regime_frozen | a frozen or possibly frozen segment | lowest T below the freezing point, K |
| regime_phase_change | a phase-change segment | highest T above the threshold, K |
| coverage | the estimate is `coverage_low` | longest gap, s |
| monte_carlo | `mc_not_converged` | 2 s / sqrt(h) of u(y) at the last batch, percent |
| assay_conflict | `assay_model_conflict` | smallest tail probability, 1 |

`kinetics.placards_for` emits the first five; the pipeline appends the last three.

Deliquescence extent is the largest high-variant internal RH at either endpoint of a segment minus the
threshold, in %RH. Frozen and possibly frozen extents are K below the freezing point; phase-change extent is K
above its threshold. A time-extrapolation placard uses age / max_study_duration (unit 1); when a segment straddles
the cutoff, its Theta is treated as uniform in time for this display share only. After normalization, clamp
`Placard.share_nominal` to [0, 1] to remove rounding overshoot; a property test enforces this bound.

---

## 9. Parameters and uncertainty

`pharmacy/chemistry/parameters.py` (CX-C) resolves parameters; `pharmacy/uncertainty.py` (CX-B) draws them.

### 9.1 Hierarchy

Per pathway, the best available layer supplies each parameter:

1. **measured_model**: a product-scope model in stability-models.json for (product, ingredient) whose `package_id` is
   null or equals the package of every segment of the lot. It replaces all destructive thermal pathways of the
   ingredient by one pathway with its rate-law kind and multivariate normal parameters; classes listed in `covers` are
   replaced too. Reference-study models are never product models. If several match, a model with a non-null
   `package_id` wins over a package-independent one, then config order. The measured pathway has id
   `<ingredient_id>:<model_id>`, `reaction_class_id` equal to the model id, the mechanism and Ea lower bound of the
   first replaced class (config order) in the product's family, the union of the replaced sites, the first product or
   class degradant of the replaced classes, the model's validated range (layer `measured_model`) and shared weight 0.
   Liquids without `ln_frozen_rate_multiplier` in the model have no frozen multiplier.
2. **assay_updated**: posterior reweighting by assays (section 10); acts on the estimate, not on the block.
3. **computed**: ln k from the computed layer (7.3) combined with the class prior (9.4).
4. **class_prior**: ln k from the total-rate split (9.5) or the class's `ln_k_ref_per_s` (racemization); Ea, B and
   frozen multiplier from the class family priors; light constants from `light_priors`; radiolysis from
   radiation-chemistry.json.

The rate-law kind of a class-based ingredient is `rate_law_defaults` for its family (F1 everywhere today).

### 9.2 Provenance

Every resolved parameter yields a `ParameterEstimate` (family, mu, sigma, bounds, unit, layer, method, source IDs,
basis, note). A pathway's layer is the layer of its rate-level parameter (ln k* for thermal pathways, ln k_vis for
light, ln k_dose for radiolysis); Ea and B layers are listed separately in `parameter_layers`.

### 9.3 Conversion to the reference conditions

Parameters given at (T0, RH0) move to (T*, RH*) by the exact linear map

```
ln k* = ln k0 - Ea (1/T* - 1/T0)/R + B (RH* - RH0)
```

with mean through A and covariance A C A^T (other parameters unchanged). Anchors measured at an uncertain
temperature T0 +- s_T convert with the class Ea and B means and add variance (x sd_Ea)^2 + (d_RH sd_B)^2 + (Ea s_T /
(R T0^2))^2, x = (1/T* - 1/T0)/R, d_RH = RH* - RH0. When the internal RH changes over the stored duration (a packaged
anchor or calibration row), RH0 is the rate-weighted internal RH, RH_out + d ln(J)/dB, the derivative of the
log mean rate with respect to B: with x_h = B (RH_start - RH_out) and r = exp(-duration/tau),
dJ/dx_h = tau exp(x_h r) (1 - r) exprel(x_h (1 - r)), so RH0 = RH_out + (RH_start - RH_out) (dJ/dx_h) / J. It is the
time mean at B = 0 and the constant internal RH for sealed or non-exchanging packages.

An anchor's `storage_zone_id` declares where its rate was measured: laboratory and ground data use
`ground_storage` (fixed multiplier 1); `aspirin_tablet_iss_550d` (MDP05) and
`acetaminophen_tablet_iss_6mo` (MDP16) use `station_kit`. Its `package_id` is null for product-level solution
kinetics or open dish. When a package is given, `rh_percent` is ambient RH. Compute internal RH over
`duration_days` at the anchor's constant condition with the engine microclimate, starting at the package's
`initial_internal_rh_percent` under 4.3. Match the measured ln k to the mean rate Theta/duration, as appropriate
for a two-point first-order rate. Use the humidity integral to infer the rate at (T*, RH*) from that packaged
exposure, rather than applying the ambient RH directly to the rate law.

Convert to the ground-equivalent rate by subtracting the storage zone's ln multiplier mean m_Z (lognormal: mu;
fixed: ln(mu), so fixed 1 changes nothing). As evidence about the ground rate the anchor's variance is
s_c^2 = s_a^2 + (9.3 terms) + s_Z^2, with s_Z the multiplier's ln sd (lognormal: sigma; fixed: 0), and the anchor
combines with the class prior by 9.4 with precision weight lambda = s_post^2 / s_c^2. The multiplier's uncertainty is
not independent of the lot's: the same unknown factor that sped up the measured sample speeds up a lot stored in that
zone. A pathway whose anchor has s_Z > 0 therefore carries `Pathway.zone_correction` (zone_id, lambda, s_Z), and its
ln k* draw is

```
ln k* = mu_post + (s_post^2 / s_p) z_cls + lambda sqrt(s_c^2 - s_Z^2) z_lnk - lambda (ln m_Z,draw - m_Z)
```

where ln m_Z,draw is the zone's multiplier draw of 9.7 (the same column the kinetics uses for a lot segment in that
zone). Its variance is s_post^2, exactly as without the correction, and its covariance with the zone's multiplier is
-lambda s_Z^2, which is the exact posterior covariance of the ground rate and the factor given the anchor. A lot in the
anchor's zone therefore recovers the anchor's rate without counting the factor's uncertainty twice, and a ground lot
carries it once. The zone factor itself is not updated by the anchor (it is shared by every product in the zone), which
leaves a lot in the anchor's zone with an excess variance of s_Z^4 / (s_p^2 + s_a^2 + s_Z^2), a conservative
approximation. Read the multiplier from configuration at conversion time, so a recalibrated factor changes every
flight anchor; the uncertainty layer raises `ModelDomainError` when the zone's configured ln sd differs from
`factor_sd_ln` or s_c^2 < s_Z^2. Package assumptions and their sources are recorded in `storage_context`.

The same conversion and variance terms apply to total-rate calibration data at their declared storage condition.
Their `rh_sd_percent` is an explicit standard uncertainty for constructing calibration and validation conditions.
Package effects are integrated by the engine physics during calibration (9.5), so outside chamber RH is not
substituted for internal product RH. Calibration converts each row once before storing `ln_k_ref_per_s` at the
engine reference; estimates must not convert that stored prior a second time.

### 9.4 Combination and the parameter block

Computed and class prior estimates of ln k* combine as normal-normal (precision weighting):

```
1/s^2 = 1/s_p^2 + 1/s_c^2,   mu = s^2 (mu_p/s_p^2 + mu_c/s_c^2)
```

The `ParameterBlock` of a class-based pathway is diagonal normal in (ln k*, Ea, B, ln k_vis, ln k_uv, ln k_dose,
ln k_dose_frozen, ln m_frozen) as applicable (lognormal priors enter as the normal of their logarithm; fixed priors
have variance 0); a measured model's block is its converted covariance. Ea is truncated below at
`ea_lower_j_per_mol` (the prior's lower bound, 0 today) by inverse-CDF sampling; its block mean and variance are the
location mu and scale sigma^2 of the truncated normal, not its moments. `shared_weight` is w of 9.5 (0 for every
pathway outside the total-rate split).

### 9.5 Shared class prior

The class prior for destructive thermal ln k comes from product-level totals of Du 2011 ground controls (MDP01,
MDP02). `total_rate_priors` records each family's calibration storage condition using anchor field semantics:
`temperature_k`, `temperature_sd_k`, `rh_percent`, plus `rh_sd_percent`. Ground controls were in a JSC chamber
at 36 to 44 %RH, read as 40 +- 2 %RH (sourced MDP01, MDP02). Temperature is 295.15 +- 1.5 K as a team default,
to be set by the chemistry lead from MDP03; the research notes do not give the chamber temperature. Suspension
reuses the solution entry's conditions and prior.

`pharmacy/calibration.py` is an offline derivation module, never called by an estimate. It receives
`tests/data/vectors/validation-du2011.json` as an argument and uses the declared calibration rows (odd rows plus
the solution rows named in configuration). For each row it derives two-point first-order ln k from ground
control day 13 to day 880. It converts to (T*, RH*) with the engine's physics at the row's calibration condition
and package (`repackaged_bag` when repackaged, otherwise its original container), using family Ea and B prior
means and the variance terms of 9.3. Nonpositive losses remain in a censored likelihood or an equivalent
explicitly documented rule; they are never silently dropped.

Derive a mean per family and one between-product variance pooled across families, using REML random effects
or pooled within-family variance, with the chosen method documented in the provenance. With N calibration
products, F fitted families and n_f products in family f, use predictive spread s sqrt(1 + 1/n_f) and Student-t
degrees of freedom N - F. Since engine draws of ln k are normal, store the normal standard deviation whose
central interval at decision coverage (90%) equals the Student-t predictive interval (sigma_t); state that
conversion in the note. The stored sd is sqrt(sigma_t^2 + v_f), where v_f is the family's mean 9.3 conversion
variance (shared by every row, so not divided by n_f); both components are recorded. Family Ea and B for the
conversion are equal-weight mixtures of the family's applicable destructive thermal class priors (unclassified
thermal included): the mean of their locations, and the sd sqrt(mean(sigma_i^2 + (mu_i - mean)^2)). The duplicated suspension prior contributes no additional observations or fitted family.

For the spaceflight factor, per calibration row compute ln(k_flight / k_ground) and subtract the log rate ratio
predicted by the engine physics for the two arms and the row's package. The flight arm condition is the
station profile's temperature setpoint with sd one quarter of its nominal range (environment-profiles.json, read
at calibration time) and 24 +- 0.5 %RH (MDP01 "flight RH 23 to 25%", read like the ground range: midpoint, sd one
quarter of the range); ground conditions are the fields above. The flight RH is a sourced input in
`data/pharmacy/calibration/` (a `SourcedValue` pair, validated on read), not a number in code. The provenance
statistics record the condition used as `flight_temperature_k`, `flight_temperature_sd_k`, `flight_rh_percent` and `flight_rh_sd_percent`. The residual is the unexplained spaceflight
factor. The mean ln residual (the multiplier's median) and the Student-t predictive spread s sqrt(1 + 1/N) with N - 1
degrees of freedom, stored as the normal sd with the same 90% central interval, define the lognormal multiplier of `station_kit` (basis
`derived`, MDP01 and MDP02). `transit_kit`, `moon_base_pharmacy` and `mars_base_pharmacy` reuse that prior with
basis `team_default`, to be set by the pharmacist and flight surgeon, noting that no surface data exist.

The module writes full `CalibrationProvenance` (method, vector id, rows, named statistics, engine version and
value provenance) on each prior and zone multiplier. Statistics record sample counts, location, pooled spread,
predictive degrees of freedom, coverage and stored normal spread, as applicable; the method and note specify
censoring, package and reference conversion. A unit test reruns calibration and compares committed values with
its output exactly. Null provenance does not establish reproducible derivation.

The calibrated total is split equally among the N class-based destructive thermal pathways of the ingredient: mu_p = mu_total - ln N, s_p = s_total. One standard normal
z_cls per ingredient is shared so the total keeps the spread of the data:

```
ln k_p = mu_post + s_post ( sqrt(w) z_cls + sqrt(1 - w) z_p ),   w = s_post^2 / s_p^2
```

With no computed estimate w = 1. Evans-Polanyi shifts (7.3.4) redistribute part of the calibrated total instead of
adding to it: chemistry decides which pathway dominates, the data keep the total. A split member with a computed-layer
estimate (an anchor, 7.3.1) is anchored; the others form the free group F (N_F members). A shift never moves an
anchored member, because its own measurement-backed estimate is stronger evidence than a prior-level shift:

```
anchored i:  mu_p,i = mu_total - ln N                                   (then combined by 9.4)
free i:      mu_p,i = mu_total + ln(N_F / N) + delta_i - ln( sum_{j in F} exp(delta_j) )
```

delta_j = 0 for free members without a shift; all shifts zero gives mu_total - ln N for every member, the equal split,
and the sum of exp(mu_p,i) over the split is exp(mu_total) in every case.

The shifts are uncertain (method error s_m,i in ln units; s_m,i = 0 when no BDE-based shift was applied at any of the
pathway's sites, for example when every site is outside the applicability domain, because the hydrogen-count term of
7.3.5 is exact). That uncertainty moves rate between free members and never adds to the total, so it is drawn exactly,
not linearized: each free member of a group of two or more carries `Pathway.free_share` (delta_i, s_m,i); its block
holds mu_p,i with variance s_p^2 and w = 1; and the draw adds the change of the log-share

```
ln k_i = mu_p,i + s_p z_cls + [ log_softmax_F(delta + s_m z_F)_i - log_softmax_F(delta)_i ]
```

where log_softmax_F(x)_i = x_i - ln sum_{j in F} exp(x_j) and z_F are the free members' own ln k columns of the
variate layout (9.7), which w = 1 leaves otherwise unused. In every draw the free group's rates sum to
exp(mu_total + s_p z_cls) N_F / N exactly; at nominal values (z = 0) ln k_i = mu_p,i. A free group of one carries no `free_share` and keeps mu_total - ln N and s_p whatever its shift: with
nothing to redistribute, the shift carries no information, so it is not computed. The trace reports delta_i, s_m,i and
pi_i = exp(delta_i) / sum_{j in F} exp(delta_j) as `free_share_delta_ln:<pathway_id>`,
`free_share_method_sd_ln:<pathway_id>` and `free_share_pi:<pathway_id>` (unit "1"). A class with an Evans-Polanyi entry and no anchor whose pathway is
outside the split is a configuration error (`ModelDomainError`). Every pathway therefore draws ln k* as
mu + s (sqrt(w) z_cls + sqrt(1 - w) z_lnk) from its block, with w = `shared_weight`.

A pathway with measured ln k* is never a member of F even if it carries `free_share`. A free member without
ln k* in its block, with `shared_weight` other than 1, or with a nonfinite or negative `method_sd_ln` raises
`ModelDomainError`.

### 9.6 Estimate basis

Strength order: measured_model (4) > assay_updated (3) > computed (2) > class_prior (1). A pathway contributes when
its nominal share is at least `basis.share_threshold` (0.01). Shares: destructive pathways share the nominal Theta;
for racemizing ingredients the racemization pathway's share is s_r = (1 - rho/f0) / (1 - (1 - alpha) rho/f0) at
nominal values and the destructive shares are scaled by (1 - s_r). If nothing contributes (empty history) every
pathway counts.

* `prior_basis` = weakest layer over contributing pathways.
* `estimate_basis` = weakest over contributing pathways and, when at least one assay was used, `assay_updated`.
* `assay_ids_used` lists the assays used (all reweighting assays, or the one re-anchoring assay).
* Flag `computed_only` when no measured model contributes and no assay was used.

So an assay on a class-prior product leaves `estimate_basis` = class_prior with a non-empty `assay_ids_used`: a dose
layer that requires a measured lot assay checks `assay_ids_used`; one that follows the brief checks `estimate_basis`.

### 9.7 Variate layout

One batch of n draws for one ingredient consumes, from its generator and in this order:

1. `Z = gen.standard_normal((n, K))`, columns:
   * 0: initial content z_C0 (C0 = mean + sd z_C0 from the lot's `initial_assay`; any C0 <= 0 raises
     `ComputationError`);
   * 1: z_cls;
   * per pathway in model order (sorted pathway_id), 8 slots, used or not: 0 ln k*, 1 B, 2 ln k_vis, 3 ln k_uv,
     4 ln k_dose (also used by ln k_dose_frozen), 5 ln m_frozen, 6 z_T, 7 z_RH (widening, spec 8.2); pathway p
     occupies columns 2 + 8 p to 9 + 8 p;
   * per zone in sorted zone_id order, 3 columns: zone multiplier, temperature offset, RH offset. The zones are the
     union of the lot's custody zones and the `zone_correction` zones of the model's pathways (9.3).
2. `U = gen.random((n, P))`: one uniform per pathway for Ea (used or not).

K = 2 + 8 P + 3 Z, with P pathways and Z zones. Ea = mu + sigma ndtri(Phi(a) + u (Phi(b) - Phi(a))) with a, b the
standardized bounds (`scipy.special.ndtr`, `ndtri`; an absent bound is an infinite one). A class-based block draws
every other parameter from its own slot: value = mean + sd z_slot (ln k* per 9.5). A measured block draws its other
parameters conditionally on Ea: mean mu_r + C_rE (Ea - mu_E)/C_EE, covariance C_rr - C_rE C_Er / C_EE (C_EE = 0:
unconditional), lower Cholesky factor (zero rows and columns kept at 0) times the slots of those parameters in the
block's `names` order. Zone variables: lognormal ln m = mu + sigma z (mu and sigma in ln units); fixed ln m = ln(mu),
since fixed mu is the multiplier itself and must be positive. Temperature offset =
`calibration_standard_uncertainty` of the zone's temperature sensor x z for sensed zones, sigma of the declared
temperature prior x z for declared zones; RH likewise with the RH sensor or prior; 0 when the zone has neither.
Offsets apply to measured, held and declared segments of that zone, never to gap-filled ones (worst-case values are
bounds).

### 9.8 Seed

```
material = {"engine_version", "config": [config pins], "sensor_series": [series pins sorted by series_id],
            "observations_sha256", "lot_id", "ingredient_id", "as_of_utc", "purpose": "potency_estimate"}
seed = int.from_bytes(sha256(canonical_json_bytes(material))[:16], "big");  seed_hex = 32 hex digits
batch r (r = 0, 1, ...): numpy.random.Generator(PCG64(SeedSequence(seed, spawn_key=(r,))))
```

Forecasts reuse the estimate's per-draw state at as_of (same batches, weights or re-anchoring, cumulative
Thetas and microclimate responses); nothing up to as_of is recomputed (12.5). Each ingredient of a combination product has its own seed. `seed_hex` is the seed as 32 lower-case hex
digits (big endian) and appears in `Pins` and `MonteCarloReport`.

A lot decision has no draws. Its `pins.seed_hex` uses the same seed material with `ingredient_id` equal to the
empty string and `purpose` equal to `lot_decision`.

### 9.9 Adaptive Monte Carlo (JCGM 101 7.9, MDS56)

p = `coverage_probability` (0.90); batch size M = max(ceil(100/(1 - p)), 10^4) (`jcgm_batch_size`, vector TV-MC-2:
10^4 at p = 0.90). Each batch r yields four statistics from its own draws, with weights normalized within the batch
when there are assays: y (mean of the active potency), u(y) (standard deviation), y_low and y_high (the
probabilistically symmetric interval ends). After h >= `min_batches` batches, for each statistic v, with s_v the sample
standard deviation (divisor h - 1) over the h batches, the standard deviation of the average is s_v / sqrt(h). The
numerical tolerance is delta = 0.5 x 10^l where u(y) over all draws so far (globally normalized weights) is written
with `n_dig` significant digits as c x 10^l (`numerical_tolerance`; u = 0 gives delta = 0). Stop when
2 s_v / sqrt(h) <= delta for all four and, with assays, the ESS over all draws is at least `ess_floor` (10.3). Stop
unconverged at `max_batches` with flag `mc_not_converged`.

The batch-size calculation reads p as the decimal number written in configuration, avoiding a binary roundoff
increment of ceil (for example p = 0.9995 gives 200000). `min_batches = max_batches = 1` disables adaptive
stopping and returns one batch without estimating a between-batch standard deviation. Otherwise at least two
batches are required. Per-batch statistics are retained in `AdaptiveResult.batch_statistics`.

### 9.10 Reported statistics

Over all draws with globally normalized weights w (uniform without assays): quantiles by the type-1 inverse ECDF
(stable sort, smallest value with cumulative weight >= q - 1e-12) at the sorted union of
`monte_carlo.reported_quantiles`, `decision.lower_quantile` and `decision.upper_quantile`; mean; standard uncertainty
sqrt(sum w (y - mean)^2); coverage interval [q_(1-p)/2, q_(1+p)/2]; P(within) = sum w 1[L <= y <= U], P(below) = sum
w 1[y < L], P(above) = sum w 1[y > U]. Degradants likewise, plus P(above limit) = sum w 1[d > limit]. Racemizing
ingredients also report `total_quantiles` of C_total.

---

## 10. Bayesian assay update and inspection override

Module `pharmacy/update.py`.

### 10.1 Assays used

Assays of the lot and ingredient with time in [manufacture, as_of]; one before manufacture raises `ObservationError`.
sd_a must be positive.

### 10.2 Likelihood

Per draw j the engine predicts the assayed measurand (total or active) at the assay time with the same draw. Each
assay adds ln N(y_a; C_j(t_a), sd_a^2 + sd_site^2), sd_site = `onboard_method_sd_percent` for onboard assays and
`ground_inter_laboratory_sd_percent` for ground assays.

### 10.3 Weights, ESS and conflict

Weights are the normalized exponentiated log likelihoods (subtract the maximum first). ESS = 1 / sum w^2 over all
draws so far. Conflict: for each assay, the prior-predictive tail probability P = 2 min(F(y), 1 - F(y)), F the mean
over unweighted draws of the normal CDF of (y - C_j)/sd_total, sd_total = sqrt(sd_a^2 + sd_site^2);
P < `conflict_tail_probability` (0.001) is a conflict. Conflicts are checked on the draws of the first `min_batches` batches (10.4).

### 10.4 Fallback re-anchor

First pass: the adaptive loop of 9.9 with assay log weights. Check conflicts after its first `min_batches` batches;
a conflict switches immediately to the re-anchor pass without running the first pass to `max_batches`.
Fallback is triggered when any assay conflicts, or when
the first pass ends at `max_batches` with ESS below `ess_floor`. Second pass: the adaptive loop restarts from batch 0
with the same seed (so the same variates) and no weights; in every draw the latest assay a* (ties: highest assay_id)
re-anchors the level: y* = y_a* + sd_total z_C0 at t_a*, C0' = y* / ((1 - alpha_j(t_a*)) rho_j(t_a*)) (rho only for an
`active` measurand), and the draw keeps its own kinetics. Other assays are not used. Flags `assay_reanchored` (and
`assay_model_conflict` when triggered by a conflict); `assay_ids_used` = [a*]. Rationale: when the model and an assay
disagree, trust the measurement for the level and the model only for the change after it. Any C0' <= 0 raises
`ComputationError`.

### 10.5 Inspection override

The earliest inspection of the lot at or before as_of with a finding other than `no_change` sets
`inspection_override` (finding), `inspection_ids_used` and flag `inspection_override` on every ingredient estimate;
the decision is `do_not_use` (11.1). A later `no_change` never clears it. Estimates are still computed.

### 10.6 Tests

Unit: likelihood arithmetic, ESS, conflict probability, re-anchor rescaling (the re-anchored median at t_a equals y
within MC error), inspection ordering. Statistical (slow): posterior coverage study (15.7).

---

## 11. Decision, forecast and ordering

Module `pharmacy/decision.py`.

### 11.1 States and reasons

Per ingredient, with q_lo and q_hi the `lower_quantile` (0.05) and `upper_quantile` (0.95) of the active measurand:

| Condition (checked in order; all reasons are collected) | State | Reason (blocking) |
|---|---|---|
| inspection finding other than no_change | do_not_use | inspection_found_change |
| q_hi > upper limit | do_not_use | above_upper_limit |
| an identified degradant with a limit has P(above limit) > 1 - `degradant_quantile` | do_not_use | degradant_above_limit |
| `do_not_freeze` and a frozen or possibly frozen segment (8.3) | do_not_use | regime_switch |
| q_lo < lower limit and adjustment eligible (11.2) | adjust | below_lower_limit |
| q_lo < lower limit and not eligible | do_not_use | below_lower_limit plus each failed check of 11.2 |
| otherwise | in_specification | within_specification (info) |

The state of an ingredient is the worst state among the rows that apply. Info reasons (never change the state):
computed_only, mc_not_converged, assay_model_conflict, degradant_profile_not_reviewed (product
`degradant_profile_reviewed` false), radiosensitive_dose_exceeded, past_labeled_expiry. The lot state is the worst
ingredient state (in_specification < adjust < do_not_use). Reasons are listed in table order, then by ingredient_id;
`Reason.ingredient_id` is null for lot-level reasons (inspection, past_labeled_expiry).

An unidentified degradant whose P(above limit) reaches 1 - `degradant_quantile` produces
`degradant_unidentified_above_limit` with severity `warning`. It forbids potency adjustment for the entire lot,
leaves the lot state governed by potency and the other blocking conditions, and instructs the crew to inspect
the lot or assay that degradant. It never makes a lot `do_not_use` by itself. `within_specification` has severity
`info`. `degradant_profile_not_reviewed` is an info reason taken from the estimate's flag, which the pipeline
sets from the formulary.

### 11.2 Adjustment eligibility

Exactly the brief's checks, in this order, each named by its reason code:

| Check (passes when) | Reason code when failed |
|---|---|
| `potency_adjustment.allowed` is true | adjustment_not_allowed_by_product |
| `fixed_dose_device` is false | fixed_dose_device |
| no degradant of any ingredient has `toxicity_flag` "toxic" | toxic_degradant |
| no estimate of the lot is flagged `outside_validated_range` | estimate_outside_validated_range |
| no estimate of the lot is flagged `coverage_low` | estimate_coverage_low |
| no unidentified degradant reaches the above-limit probability threshold | degradant_unidentified_above_limit (warning) |

An unreviewed degradant profile and an assay-model conflict are info only. `AdjustmentEligibility` also carries each
ingredient's `estimate_basis`, the assay ids used and `degradant_profile_reviewed`, so the dose layer can require a
measured assay (decision of the dosing-safety research) or follow the brief without engine changes.

### 11.3 Combination products

Worst ingredient state; reasons name the ingredient. Whether a combination tablet with one low ingredient can be
dose-adjusted is the dose layer's decision.

### 11.4 Forecast

Projection of future conditions per the current zone's (last custody interval) `forecast_projection`:
`repeat_last_window` repeats the last `window_s` of the lot's recorded segments in that zone (shorter if the lot
arrived later; gap-filled segments keep their fill values) back to back from as_of, with the current package, the
microclimate continuing from RH_in(as_of) and the same draws (offsets, multipliers, weights or re-anchoring);
`declared_constant` holds the zone's declared means (`ConfigReferenceError` when T or RH is not declared). Per draw,
the crossing time T_j is the first time after as_of when the active potency falls below the lower limit
(`crossing_times`: vectorized bisection on [0, `horizon_s`] to `time_tolerance_s`; 0 if already below; inf beyond the
horizon). Since P(potency(t) < L) = P(T <= t), the time at which the `lower_quantile` of potency crosses L is the
weighted `lower_quantile` of T_j, written T_q.

| Status | Condition | crossing_s, crossing_utc |
|---|---|---|
| already_crossed | T_q = 0 | as_of_s |
| within_horizon | 0 < T_q <= horizon | as_of_s + T_q |
| beyond_horizon | T_q = inf | null |

`crossing_delay_quantiles_s` gives T at the reported quantiles with inf replaced by `horizon_s`.

A `repeat_last_window` zone with no recorded segment at arrival falls back to `declared_constant`;
missing declared temperature or RH raises `ConfigReferenceError`. The march starts from the estimate's retained
per-draw state. It detects a periodic window when high and low RH_in and every unit-step response at the start
and end agree within one float64 epsilon relative. Whole windows then reuse that window's Theta; per-draw
bisection on the window count finds the first below-limit end, and the crossing is located within that window.
A final window cut by the horizon is evaluated exactly over its remaining length.

### 11.5 Ordering

`OrderingKey(state_rank, primary, lot_id)`; sort ascending by (state_rank, primary is None, primary or 0.0, lot_id).
Primary: in_specification, the smallest `crossing_s` over the lot's forecasts (first expiring used first; None when
all are beyond the horizon); adjust, minus the lowest q_lo over ingredients (higher potency first); do_not_use, None.
Alternatives:
other active lots of the same product in ordering order, and products with the same ingredient set (sorted ids).
`past_labeled_expiry` = as_of >= labeled expiry.

For an adjust lot, q_lo is at the probability carried by `Forecast.quantile_probability`; no forecasts raises
`InputError`. `assess_lot` lists alternatives of the same product in sorted lot_id order; `assess_inventory`
reorders them using their own decisions' ordering keys.

### 11.6 Regulatory helpers

`q1e_max_shelf_life_months` (ICH Q1E Appendix A, MDS02; TV-Q1E-1 to TV-Q1E-4) and `q3b_thresholds`,
`q3b_exceeds` (ICH Q3B(R2) Attachment 1 with the guideline's rounding, MDS04; TV-Q3B-1, TV-Q3B-2). Q3B thresholds by
maximum daily dose: reporting 0.1% (<= 1 g) or 0.05% (> 1 g); identification 1.0% or 5 ug TDI (< 1 mg), 0.5% or 20
ug (1 to 10 mg), 0.2% or 2 mg (> 10 mg to 2 g), 0.10% (> 2 g); qualification 1.0% or 50 ug (< 10 mg), 0.5% or 200 ug
(10 to 100 mg), 0.2% or 3 mg (> 100 mg to 2 g), 0.15% (> 2 g); the lower of percent and TDI applies.

The `pharmacy.decision` module docstring states the ICH Q1E poolability test order: slopes, then intercepts,
at significance level 0.25 (MDS02).

All Q3B bands are configured in `engine-settings.q3b` (`reporting`, `identification`, `qualification`). Each
`Q3bBand` has `dose_upper_mg` (sourced value or null for unbounded), `upper_inclusive`, `percent` and nullable
`tdi`. Each threshold is a `Q3bThreshold` with explicit integer `decimals` and the full SourcedValue provenance
(MDS04). Read the first matching band. Percent decimals in band order are reporting [1, 2], identification
[1, 1, 1, 2], qualification [1, 1, 1, 2]. TDI thresholds retain their written units (ug for 5, 20, 50, 200;
mg for 2, 3) with decimals 0. Compare the observed value in each threshold's own unit, round half up to its
configured decimals, then test strictly greater. The lower percent/TDI limit still governs; unit conversion
must not erase the original rounding precision. `q3b_thresholds` receives keyword-only `settings: Q3bSettings`
containing the configured bands; `q3b_exceeds` receives keyword-only `decimals: int`. Neither infers precision
from a float's shortest representation.

### 11.7 Tests

Unit: every row of 11.1, eligibility combinations, combination products, ordering ties, alternatives, forecast
statuses with crafted potency functions, Q1E and Q3B vectors. Property: on estimates whose upper quantile is at or below the upper limit, lowering every draw never improves a state;
ordering is a total order.

---

## 12. Pipeline, store and replay

### 12.1 Inputs

`EngineInputsDoc` (schema `inputs`): `as_of_utc`, sensor series, assays, inspections. Semantic validation: series name
known zones and the zone's sensor for that quantity; observations name known lots and ingredients and are not before manufacture. Readings, assays and inspections after as_of are dropped, never used and
never change a past result.

### 12.2 Steps per lot and ingredient

1. Validate inputs (12.1), once per call.

Per lot and ingredient (`estimate_potency`, steps 2 to 9):

2. Lot exposure (3) and its summary.
3. Microclimate trace and unit-step responses (4).
4. Stability model (7, 9.1 to 9.6) over the packages of the lot's history.
5. Nominal thetas, regime flags and placards (8).
6. Seed (9.8).
7. Adaptive Monte Carlo (9.9) evaluating `kinetics.evaluate_draws` per batch with the assay likelihood (10.2); the
   fallback second pass when triggered (10.4).
8. Statistics (9.10), degradants, radiation report (6).
9. Inspection override (10.5), basis (9.6), flags, trace and pins: the `PotencyEstimate`.

Per lot: 10. forecasts (11.4); 11. decision (11.1 to 11.3). Per call: 12. ordering (11.5) over all lots with status
`active`. Ids: `estimate_id` = `<lot_id>:<ingredient_id>:<as_of_utc>`, `forecast_id` = `<estimate_id>:forecast`,
`decision_id` = `<lot_id>:<as_of_utc>:decision`.

Retain per-draw state at as_of for forecasting without reevaluating the history. Report per-degradant
identification and evidence (5.6) and compression probe error and selected evaluator (12.5) in the trace.

### 12.3 Append-only store

`store.ResultStore`: JSON Lines, one canonical JSON record per line with `sequence`, `recorded_at_utc`, `actor`,
`reason`, `kind`, `payload`, `prev_hash` (64 zeros first) and `record_hash` (SHA-256 of the record without it). No
update or delete; `verify` checks sequence, links and hashes (21 CFR 11.10(e), MHRA 2018).

Sequence numbers start at 0, exposed as `store.FIRST_SEQUENCE`. A missing or empty file is an empty store.
`append` raises `InputError` for an empty kind, actor or reason, a recorded_at_utc that is not ISO with Z,
or a payload that is not a canonical-serializable JSON object. `records()` requires canonical lines, the exact
eight keys, 64 lower-case hex hashes and a trailing newline, and raises `StoreError` otherwise.

### 12.4 Replay

`pipeline.replay` recomputes from the same bundle and inputs and compares canonical JSON; pins that do not match
raise `DeterminismError`.

### 12.5 Performance and checked acceleration

The exact public `evaluate_draws` is the reference path checked by the oracle at 1e-9. It evaluates fixed
256-segment chunks on a thread pool and reduces partial sums in chunk order, bit identically for any thread
count. `numerics.worker_threads` sets the count; 0 uses the CPU count. Exactness is not relaxed for speed.

A compressed exposure history may accelerate Theta evaluation. Its method is specified by its implementer
(CX-B) in this section, together with an error analysis, before that method is accepted. Every estimate compares
compressed and exact evaluations on a fixed probe set spanning the batch's drawn parameter support: smallest
and largest Ea, B, zone offsets and multipliers, plus fixed quantile points. If the maximum relative difference
of any pathway's Theta exceeds `numerics.compression_relative_tolerance` (team default 1e-7), the estimate uses
the exact evaluator. For exact Theta zero, two zeros have error zero and any nonzero approximation forces exact
evaluation. The trace records `compression_probe_relative_error` and `history_evaluator` (`exact` or
`compressed`); a zero-denominator failure is recorded as null error with an explanatory note, never infinity.
Probe construction, quantiles, treatment across batches and reuse of results must be deterministic and stated
in the implementer's method and error analysis.

Forecasts reuse the estimate's state at as_of; no history up to as_of is recomputed. Report targets on this
machine, one process: the day-100 demo through `assess_inventory` for all lots within 120 s; day 400 within
300 s; section 15.6 end to end within 15 minutes. Report timings every run; unit tests do not assert them.

---

## 13. Environment simulator

Owner CX-D. Module `pharmacy/envsim.py`. Never used by the engine.

### 13.1 Simulator

#### 13.1.1 Scenario documents

Schema `scenario`; `contracts.scenario_problems` must be empty: zones, profiles, sensors (configured in the zone for
that quantity), lots, templates and ingredients exist; times lie within the mission; expectations use assessment
times. The demo (`fixtures/pharmacy/demo-moon-base.json`): 400 days from 2028-01-10, 5-day transit, Moon base
pharmacy; a thermal-control failure at day 180 (45 C plateau for 5 days), an August 1972 class SPE behind 20 g/cm2 at
day 250, a 4-hour T and RH outage at day 300, an onboard clavulanate assay at day 200 and a color change found on the
epinephrine syringe lot at day 320.

#### 13.1.2 Truth model

On a uniform grid of step dt from mission start:

```
T(t)  = (1 - s(t)) [Tset + A_T sin(2 pi (t - t0)/86400) + X_T(t)] + s(t) T_target
X(t+dt) = X(t) e^(-dt/theta) + sigma sqrt(1 - e^(-2 dt/theta)) xi          (OU, exact; X(t0) ~ N(0, sigma^2))
RH(t) = clip((1 - s_RH(t)) [RHset + A_RH sin(2 pi (t - t0)/86400) + X_RH(t)] + s_RH(t) RH_target, 0, 100)
dose_rate(t) = GCR rate + sum over SPEs of r(t)
r(t)  = peak (t - t_s)/rise_s on [t_s, t_s + rise_s),  peak exp(-(t - t_s - rise_s)/tau) afterwards
peak  = D_total x shielding_scale / (rise_s/2 + tau)
light: on for hours_on_per_day from 06:00 mission time when the profile has light, else 0
```

t0 is the mission start; theta, sigma, setpoints and amplitudes come from the zone's environment profile; T_target,
RH_target, ramp, hold and recover from the thermal-failure template. s(t) is the trapezoid: 0 to 1 linearly over
ramp_s, 1 over hold_s, 1 to 0 over recover_s; s_RH = s when the template has a target RH, else 0. Overlapping thermal
events use the latest event's template. SPE: D_total and tau from the template; `shielding_scale` null means 1; the
pulse integral over [t_s, inf) is exactly D_total x shielding_scale. GCR rate in Gy/s =
`absorbed_dose_rate_gy_per_day` / 86400. `light_exposure_change` switches the light on continuously at the profile's
illuminance and UV for `duration_s` (`InputError` when the profile has no light). Each zone draws X_T, X_RH and the
OU initial values from its own truth generator (13.1.4) in the order X_T(t0), X_RH(t0), then per step xi_T, xi_RH.

The truth grid is t0 + k dt for k = 0..N with N = ceil((end - start)/dt). Sampling instants are
t0 + j dt_sample with t_j <= end (snap tolerance 1e-9 steps); outages cover [start, start + duration).

#### 13.1.3 Measurement model

Per sensor, from its own generator: bias b ~ N(0, `sensor_bias_*_sd`) drawn once (dose sensors: no bias); then per
sampling instant (every `sampling_interval_s` of the scenario sensor, from mission start, truth linearly interpolated
on the grid) one noise normal and one dropout uniform, in that order. Reading = truth + b + noise (temperature and RH:
N(0, sd)); dose rate: truth x (1 + rel_sd xi), clipped at 0; RH clipped to [0, 100]; quantized with
numpy.round(v / res) x res; dropped when the uniform is below `dropout_probability`; readings inside scripted
outages removed (their variates are still drawn, so an outage never shifts later readings). Temperatures are
reported in K. Timestamps carry ISO and float seconds. Light sensors follow the rules below when configured.

Illuminance and UV sensor readings have no profile noise or bias: quantize truth at the sensor resolution,
with dropout and outages as for every sensor and the same variate layout. After quantization, clip all
readings to their physical domains again, so RH roundoff cannot exceed 100.

#### 13.1.4 Seeds

zone_seed(mission_seed, seed_label, sensor_id) = first 16 bytes (big endian) of SHA-256 of canonical JSON of
[mission_seed, seed_label, sensor_id] (sensor_id null for the zone's truth). Generators are PCG64(SeedSequence(seed)).

---

## 14. Offline fit

Owner CX-B. Module `pharmacy/fit.py`. Offline only; outputs enter configuration through review.

### 14.1 Two-stage Arrhenius

x_i = -(1/T_i - 1/T_ref)/R, y_i = ln k_i; OLS y = a + Ea x; ln k_ref = a; ln A = a + Ea/(R T_ref); covariance s^2 (X^T
X)^-1 with s^2 = RSS/(n - 2); r^2 = 1 - RSS/TSS. Vectors TV-KIN-1 (68.12 +- 3.99 kJ/mol, ln A 18.532 per min),
TV-KIN-2, TV-KIN-3-1 to TV-KIN-3-7; TV-KIN-3x is a known mismatch (refit 70.2, published 55.46: assert the refit,
never the published value).

When every ln k is equal, TSS = RSS = 0 and `ArrheniusFit.r2` is 1.0, never NaN.

### 14.2 Log-linear first order

ln C = ln C0 - k x by OLS; with fixed C0, k = -sum x (ln C - ln C0) / sum x^2, se = sqrt(s^2 / sum x^2), s^2 = RSS/(n -
1). Vectors TV-KIN-6 (free k 1.1014038621325788e-3 per h, se 6.167737107236974e-5, C0 99.22621537586032; fixed C0 100:
1.1408237250843014e-3) and TV-RAD-1 (intercept fixed at the initial concentration: 0.082 per kGy).

For a free intercept, compute `c0_se_percent` by the delta method: se(C0) = C0 se(intercept).
TV-RAD-1 concentrations in ug/mL can be passed through the response argument because this log-linear fit is
scale invariant.

### 14.3 Whole-study WNLLS

`fit_stability_study` accepts, per observation, the logged piecewise-constant chamber history from the start
of the condition to its pull and the package the samples occupied (none for open dish). Constant setpoints are
a degenerate log. A packaged history includes the package's configured starting internal RH. Compute the
internal RH trace with the engine's microclimate functions, then Theta with the engine's humidity integral;
residuals are (C_obs - C0 (1 - G(Theta)))/sigma. Temperature is the logged chamber temperature, while the humidity
term uses product internal RH. Fit and estimate therefore use identical package physics; B and the validated
RH range describe internal RH. Do not fit to chamber RH and then apply package attenuation a second time.

Use scipy `least_squares` (trf, x_scale "jac", ftol = xtol = gtol = 1e-12). Covariance is
s^2 (J^T W J)^-1 with s^2 = 1 for absolute weights and RSS_w/dof otherwise. AIC = 2p + n ln(RSS_w/n),
AICc = AIC + 2p(p+1)/(n - p - 1), BIC = p ln n + n ln(RSS_w/n). `StudyFit.aic`, `aicc` and `bic` are
`float | None`: AICc is null when n <= p + 1; AIC, AICc and BIC are null when RSS_w = 0. Return the fitted
parameters and covariance when otherwise defined; never serialize an infinite information criterion.

Keyword-only parameters of `fit_stability_study` include `alpha0: float | None = None`, `avrami_n: float | None = None`,
`numerics: NumericsSettings | None = None`, `initial_percent: float | None = None`. B1 and An require their
kind parameter, and D2 and D4 require numerics. When `fit_initial` is false, C0 is `initial_percent` if supplied,
else the mean of time-zero observations, else 100 percent of label claim.

Construct starts by sorted observation group: for pulls with conversion in (0, 1), Theta = g(1 - C/C0), then
k_g = sum(t Theta)/sum(t^2); regress ln k_g on (1, -(1/T - 1/T*)/R, RH - RH*) with unit-norm columns and the
minimum-norm solution if underdetermined. For logs, temperature and internal RH summaries may initialize the
fit; every residual uses the full log integral. With no usable group start ln k* = -ln(t_max), Ea = B = 0.
Require at least two temperatures, at least two internal RH values when fitting B, at least one positive-time
pull, and sd_percent on all observations or none. Absolute weights require sd. Violations raise `InputError`.

Status 0 from least_squares returns `converged` false. A negative status or nonfinite estimate raises
`ConvergenceError`. A singular J^T W J, checked on unit-norm columns, raises `ComputationError`; no
pseudo-inverse is used. These covariance checks still apply when an information criterion is null.

`StudyFit` includes optional study metadata, each defaulting to None: `n_observations: int | None`;
`temperature_min_k`, `temperature_max_k`, `rh_min_percent`, `rh_max_percent`, `max_time_s`, `order_n`,
`alpha0`, `avrami_n`, `initial_percent` (all `float | None`). `fit_stability_study` fills these fields from the
study, with RH bounds taken from its computed internal traces.

Vectors TV-KIN-4 and TV-H-5 contain degradant tables: convert to 100 minus degradant and fix C0 at 100. They
are known mismatches and plausibility checks only; B < 0 is allowed for TV-KIN-4. TV-FIT-1 is unverified and is
skipped with a reason unless its external dataset is supplied.

### 14.4 Model selection

`select_model` raises `InputError` if any candidate has no AICc. Among candidates within 2 AICc units of the
lowest, choose the one predicting the lowest potency at the longest time and highest temperature of the study's
long-term condition, using that condition's chamber history and package (conservative extrapolation, MDS02).
Ties go to fewer parameters, then `RateLawKind` order. Candidates must retain or receive the study condition
needed for this comparison.

The standard study comparison uses F0, F1, F2, R2, R3, A2, A3, A4, D1, D2, D3 and D4. Fn, An and B1 can enter
only with explicitly supplied order_n, avrami_n or alpha0; no kind parameter is guessed.

The selected model's 95% prediction interval for potency is the 2.5 and 97.5 percent quantiles of potency over
4000 draws from the multivariate normal of the fit's mean and covariance, with seed deterministically derived
from the fit's inputs, including chamber logs and package. For comparison with a measured value, add its normal
assay error to each potency draw before taking quantiles (15.5).

### 14.5 Model document

`model_document` writes a product-scope stability-models.json entry: converted mean and covariance at (T*, RH*),
validated temperature range = logged study range, validated RH range = internal product RH range,
`max_study_duration_s` = longest time point, and dataset hash including the chamber logs and package inputs.
It sets `status` to `reproduces`, `basis` to `derived`, `study` to null and `covers` to []; the note says
review_state draft, since that field belongs to the wrapper header. Require nonempty `source_ids`, else
`InputError`. Drop `c0_percent` from the parameter vector, since it is not a `ParameterName`. Without a humidity
term write `reference_rh_percent` null and RH range [0, 100]. Set `measured_degradant_ids` to [] for total-assay
fits; only a study that measured formation of the named degradant may populate it. Validate the result through
a stability-models wrapper document.

---

## 15. Test vectors and acceptance criteria

All criteria below are fixed before code. A failing validation criterion is a finding to report (15.5), never a
reason to retune configuration without new sourced evidence.

### 15.1 Vector catalogue

`tests/data/vectors/*.json` (schema `test-vectors`), transcribed from degradation-science.md section 9 and
space-pharmacy.md "Validation datasets". Published numbers are verbatim; raw cell text sits beside parsed values.
`status`: `reproduces` (assert within tolerance), `known_mismatch` (assert the documented behaviour, never the
published number), `unverified` (skip with the reason unless the data are supplied).

| Vectors | Target | Assertion |
|---|---|---|
| TV-MKT-1 | oracle:delta_h_over_r | dH/R = 10000 K |
| TV-MKT-2, -3, -7, -8 | exposure.mean_kinetic_temperature | vector tolerance; full-precision values at 1e-9 |
| TV-MKT-4, -5, -6 | property tests | 1e-12 relative |
| TV-INT-1, TV-INT-2 | kinetics.integrated_rate, conversion_from_theta | 1e-9 relative (full precision) |
| TV-INT-3 | property test | 1e-12 relative |
| TV-INT-4 | kinetics.integrate_ode_reference | 1e-8 relative |
| TV-KIN-1, -2, -3-1 to -3-7 | fit.fit_arrhenius_two_stage | vector tolerance |
| TV-KIN-3x | fit.fit_arrhenius_two_stage | known mismatch: refit 70.2 +- 14.1 |
| TV-KIN-3e, TV-KIN-5, TV-H-3, TV-H-4 | kinetics.rate_constant | vector tolerance |
| TV-H-2 | kinetics.rate_constant | known mismatch: 1.296 years within 0.1, never the published 1.2 |
| TV-H-1 | oracle:water_loss_ratio | vector tolerance |
| TV-KIN-4, TV-H-5, TV-FIT-1 | fit.fit_stability_study | plausibility only / skipped |
| TV-KIN-6 | fit.fit_first_order_loglinear | 1e-9 relative (full precision) |
| TV-KIN-7 | constants.THERMOCHEMICAL_CALORIE_J | exact |
| TV-GCI-1 to -3 | oracle:gci | 0.06 |
| TV-MC-1 | oracle:coverage_simulation_count | 0.5 |
| TV-MC-2 | uncertainty.jcgm_batch_size | exact |
| TV-Q1E-1 to -4 | decision.q1e_max_shelf_life_months | exact |
| TV-Q1E-5 | pharmacy.decision module docstring | test order slopes then intercepts, alpha 0.25 |
| TV-Q3B-1 | decision.q3b_exceeds (the vector names q3b_thresholds) | exact |
| TV-Q3B-2 | decision.q3b_thresholds | exact |
| TV-Q1B-1 | settings and light priors | the confirmatory doses equal the prior derivation |
| TV-SF-1 | oracle:first_order_half_life | vector tolerance |
| TV-SF-2 | validation report | documented ranges 9 to 11 and 23 to 24 |
| TV-RAD-1 | fit.fit_first_order_loglinear | 0.0005 with the intercept fixed |
| TV-RAD-2 | chemistry.radiolysis.k_dose_direct | loss at 1 Gy < 1e-4 |
| V1 to V13 | tests/validation | 15.5 |

### 15.2 Unit and oracle tests

The oracle (tests/oracle) is written by CX-D from this document only, uses mpmath at 50 digits, and imports only
`pharmacy.types` (it restates R and other constants from CODATA itself). Required agreement at 1e-9 relative (1e-12
absolute near zero): `rate_constant`, `integrated_rate`, `conversion_from_theta` and `theta_from_conversion` (all
kinds), `rate_function`, `humidity_integral`, `relax_rh`, `unit_step_response`, `internal_rh_trace`,
`mean_kinetic_temperature`, `evaluate_draws` (fixed DrawBlocks), `nominal_thetas`, `k_dose_direct`,
`k_dose_indirect`, `upper_bound_loss_fraction`, `convert_reference`, `precision_weighted`, `class_prior_ln_k`,
`hammett_delta_ln_k`, `taft_delta_ln_k`, `evans_polanyi_delta_ln_k`, `combine_sites`,
`evans_polanyi_site_delta`, `combine_weighted_sites`. Exact equality:
`derive_seed`, `jcgm_batch_size`, `numerical_tolerance`, `weighted_quantile`, `q1e_max_shelf_life_months`,
`q3b_thresholds`, `q3b_exceeds`, `ordering_key`, `sort_lots`.

### 15.3 Property tests

Hypothesis with `derandomize=True` and at least 200 examples per property (profile in tests/property/conftest.py).
Invariants: those listed in 3.10, 4.6, 5.11, 11.7; plus canonical JSON independent of key order; changing any pin
changes the seed; two runs of the pipeline on the same inputs give identical JSON; weighted quantiles non-decreasing
in probability; rho in [1/2, f0] and non-increasing.

### 15.4 Scenario and simulator tests

Simulator self-tests (CX-D) over 30 simulated days at the profile's sampling: per-zone mean temperature within 3
standard errors of the setpoint (standard error of an OU mean, sigma sqrt(2 theta / D) for a span D >> theta);
empirical OU variance within the 99.9% sampling-distribution band
sigma^2 (1 +- 3.29 sqrt(2 tau / T_span)); these variance cases are ordinary passing tests with no expected-failure
markers; the SPE pulse integrated in closed form equals the template total times
the shielding scale within 1e-12 relative, and the trapezoid sum of the truth on a 60 s grid within 1e-3; an outage
produces one gap of the scripted length within one sampling interval; the same seed gives identical series; the
dropout fraction lies within 3 binomial standard errors of `dropout_probability`.

Demo expectations (`expectations` in the fixture), evaluated on the engine output at each `as_of_utc`:
`state_in` (state in values), `flag_present` (at least one of values present), `flag_absent` (none present),
`estimate_basis_in`, `forecast_status_in`, `lower_quantile_between` (q_lo in [lower, upper]), `coverage_low_is`
("true" or "false"), `ordered_before` (lot before other_lot_id), `true_potency_within_interval` (needs simulator
truth). For `x-amc-002-assay-id` the test also asserts the assay id is in `assay_ids_used`. All expectations must hold.

### 15.5 Validation (marker `validation`)

Calibration data never validate: Du 2011 calibration rows (odd rows, plus the solution rows 22 and 30 used in the
solution prior), the V6 aspirin row, the V7 acetaminophen row and the V9 amoxicillin control arm (anchors) are
excluded. Each validation run writes `tests/validation/report.json` with every row's prediction, interval, measured
value and pass or fail.

| Dataset | Procedure | Pass rule |
|---|---|---|
| V1 Du 2011 even rows with a day-13 and a day-880 control value (rows 22 and 30 excluded) | Class-based engine (computed layer allowed, no measured models, no assays) from the day-13 control mean and sd as initial assay to day 880, in a declared test zone built from the matching total prior's `temperature_k`, `temperature_sd_k`, `rh_percent` and `rh_sd_percent`, package `repackaged_bag` when the row says repackaged, else `original_bottle` for solids and `original_vial` for liquids and semisolids | covered count k >= scipy `binom.ppf(0.05, n, 0.90)` (n = 16: k >= 12) |
| V1 flight arm | Same rows and procedure, in a declared test zone built from the four flight condition statistics of the `station_kit` multiplier's calibration provenance (9.5), with the `station_kit` multiplier. The factor is calibrated on the odd rows only, so the even rows are held out | same binomial rule |
| V6 Wotring 550 d (rows other than aspirin) | Same procedure and declared station zone, package `repackaged_bag` for every row (MDP05), initial assay 100 +- 1.5 (the lots.json team default) | same binomial rule |
| V7 Bogomolov | | report only (n = 1, unverified) |
| V9 GCRSim (amoxicillin arms excluded) | Radiation report at the delivered doses | upper-bound loss < 0.01 and central loss < 1e-4 for every arm; potency rows report only (promethazine falls in every arm, controls included: a time trend, not radiation) |
| V10, V11, V12 high dose | Upper bound at the stated doses | runs only once the rows are verified (status unverified today: skip with the reason). Then: bound >= measured loss for every product not marked `radiosensitive`; clavulanate (radiosensitive) must raise `radiosensitive_dose_exceeded` at the tested dose |
| V2, TV-SF-2 failure counts | Flight arm: compare each row with its own `usp_range_percent` lower limit at each payload and count each formulation once, including co-formulated rows | report only, against the documented ranges: the counts pool calibration and held-out rows, so they are not independent (the held-out rows are gated per row by V1) |
| V5, V8, V13 | | report only (unverified data) |
| V3, V4 | | not engine outputs; not used |

The products of the V1 and V6 rows are not in the formulary: CX-D builds them in tests/validation from each row (dose
form family from the row's dose form, API SMILES and CID from PubChem, MDX01, recorded with the row number), never in
config/pharmacy. Expected failure, reported and not tuned: clavulanate (V1 row 12) degrades about 30 times faster than
the class median, so its interval misses; it counts against the binomial rule. Co-formulated rows straddle the split
(the V1 `calibration_split.caution`), so V1 is not fully independent; the report says so. A dataset that fails its rule is reported in
docs/VALIDATION-REPORT.md by CX-D with the rows that failed; marking the test `xfail` needs the team lead's sign-off and
a finding id, never a configuration change.

Whenever a published or simulated measured value is compared with an engine prediction, use the predictive
distribution of that measurement: add normal assay error with the measurement's own sd to each potency draw
before taking quantiles. For V1 and V6 coverage, a row is covered when its measured value lies within this
90% predictive interval; retain the table's binomial count rule. Reports distinguish this interval from the
coverage interval for true potency.

V1 flight uses a declared test zone built from the flight condition statistics recorded in the `station_kit`
multiplier's calibration provenance (9.5), read at test time, and the `station_kit` multiplier, so calibration and
validation of the flight arm cannot disagree. V6 uses a declared station zone in tests with means at the station
profile setpoints (296.15 K, 35 %RH), sd equal to one quarter of each profile nominal range, and the `station_kit`
multiplier; its rows were stored repackaged (MDP05), so every V6 row uses `repackaged_bag`. The V1 ground
zone derives its means and sd directly from the calibration reference fields, not from the general warehouse
zone. In validation lot fixtures the labeled expiry is the last measurement plus 365 days. V1 row 10's
"Triamcinolone cream" is triamcinolone acetonide (MDX01, CID 6436).

V2 uses each component row's USP lower limit; a formulation failing any component counts once. Flight rows
8, 12, 26 and 36 share formulations with 7, 11, 25 and 35 respectively. Report predicted formulation counts
against the documented counts at each payload, including the 9 to 11 and 23 to 24 ranges (MDP01, MDP02).

### 15.6 End to end (marker `slow`)

The demo scenario through `pipeline.assess_inventory` at every assessment time: every output validates against its
schema; every expectation holds; `replay` returns true; the result store verifies after appending all results; the
runtime budget (12.5) is reported, not asserted.

### 15.7 Statistical coverage studies (marker `slow`)

Synthetic truth drawn from the engine's own priors (one draw as truth per replicate, history from the simulator, assay
noise from the configured sd): over 2000 replicates the 90% interval must contain the true potency in a fraction
within [0.88, 0.92] (3 binomial standard errors), both without assays and after one onboard assay. P(below lower
limit) must be calibrated: the Brier score is reported and every reliability bin (deciles of the predicted
probability) with at least 30 replicates must lie within 3 binomial standard errors of the diagonal. Replicates may run
one batch of M draws each with `min_batches = max_batches = 1` (adaptive stopping off), since coverage is a
property of the posterior, not of the stopping rule. Replicate r uses mission seed 20261114 + r.

Evaluate each truth draw, including its zone offsets, on the history the engine builds from sensor readings.
This tests the posterior itself. Section 15.8 tests inference against simulator truth histories.

### 15.8 Virtual stability studies (marker `slow`)

Purpose: show, with simulated chemistry only, that a stability study run in a virtual chamber, fitted offline (14) and
loaded as a measured model predicts the potency of a lot through a real mission history it never saw. No published
dataset enters these tests. They test the numerics and statistics from chamber to decision; they do not test the
chemistry layer's priors (the truth is drawn from them), which 15.5 tests against real data.

Owner CX-D. Study designs live in `tests/virtual/virtual-studies.json` (owned by CX-D, validated by a schema in
`tests/virtual/`), every value with `basis`, `source_ids` and `to_be_set_by` as in configuration:

* ICH design (MDS01): long-term 25 C / 60 %RH with pulls at 0, 3, 6, 9, 12, 18 and 24 months; intermediate 30 C /
  65 %RH at 0, 6, 9 and 12 months; accelerated 40 C / 75 %RH at 0, 3 and 6 months. Product in its declared package.
* Isoconversion design (MDS14): five conditions spanning 50 to 80 C and 10 to 75 %RH, open dish (internal RH equals
  chamber RH), each condition pulled at times chosen from the truth so the last pull brackets the specification edge.
* Chamber control: each condition is a simulator zone (13.1.2) with its setpoint at the condition and the design's
  declared fluctuation; assay noise per replicate from the design's declared sd and replicate count.

Truth. Replicate r uses mission seed 20261114 + r. Truth parameters are one draw from the engine's prior for the
product (`build_stability_model`, then `draw_parameters` with the replicate's seed); kind and kind parameters come
from that model. True potency is computed in tests/oracle from the simulator's truth series (never from sensor
readings): Theta = integral of k(T(t), RH_int(t)) dt by compensated summation (`math.fsum`) of the trapezoid rule on
the simulator grid with the oracle's own package model, its step-halving error reported and below 1e-6 relative;
alpha = G(Theta) by 5.3. mpmath at 50 digits checks five replicates.

Tests and pass rules (fixed before code):

| Test | Procedure | Pass rule |
|---|---|---|
| VS-1 Parameter recovery | 500 replicates per design on three products of distinct dose-form families, each with exactly one destructive thermal pathway; build the semisolid as triamcinolone acetonide cream from V1 row 10 in tests and the solution as ciprofloxacin ophthalmic solution from V1 row 5; `fit_stability_study` with the true kind fixed | each 95% confidence interval (ln k*, Ea, B) contains the truth in a fraction within [0.921, 0.979]; mean of (estimate minus truth)/se within plus or minus 0.134 |
| VS-2 Model selection | Truth kinds F1, A2, R3 and D3 on the isoconversion design; `select_model` over the standard kinds of 14.4 | over misselections, the fraction whose truth potency at 25 C / 60 %RH and 24 months lies below the selected model's 2.5% prediction bound is no larger than 0.025 + 3 sqrt(0.025 (1 - 0.025) / m), with m misselections; report two-sided coverage and the confusion matrix; no misselections is reported as not applicable |
| VS-3 Closed loop | ICH plus isoconversion study, fit, `select_model`, `model_document`; the draft entry is loaded into a test bundle (never config/pharmacy); the lot then lives through the demo mission and the engine estimates it from the simulator's sensor readings (noise, bias, dropout and outages included) | 500 replicates: before day 180 the 90% interval contains the truth in a fraction within [0.86, 0.94]; after the 45 C excursion the fraction is at least 0.86; every estimate whose history leaves the measured model's validated range carries `outside_validated_range`; median of (engine median minus truth) within 3 standard errors of 0 |
| VS-4 Environment sweep | VS-3 with 400-day histories from each environment profile (station, transit, moon, mars) and its templates | per-profile coverage and flag counts reported; pooled coverage within [0.86, 0.94] for estimates without `outside_validated_range`, and at least 0.86 for estimates with it; an empty subgroup is reported explicitly and cannot establish coverage |
| VS-5 Assay closes the gap | VS-3 plus one simulated onboard assay at day 200 (the lot's configured assay sd) | coverage after the update within [0.86, 0.94]; the interval width at day 250 is smaller than without the assay in at least 95% of replicates |

Results are written to `tests/virtual/report.json` and summarized in docs/VALIDATION-REPORT.md. A failed rule is a
finding reported with its replicates; never a reason to change configuration or the rule.

Between simulator grid nodes the truth is linear in T and RH_out; step halving refines that interpolant.
Truth chemistry has z_T = z_RH = 0, so its drawn ln k*, Ea and B describe Arrhenius chemistry everywhere.
Fits receive the simulator's chamber sensor logs and the sample package, as a laboratory would, and compute
internal RH by section 14.3. Prediction intervals use the 4000 fit draws of 14.4. Compare truth with potency
intervals; compare noisy simulated assays with measurement-predictive intervals.

VS-4 uses `tcs_failure_hot` at day 180 and an SPE at day 250: `mars_surface_sep_2017` on Mars,
`aug1972_behind_20_g_cm2` elsewhere. Validity includes the measured-model lower-temperature margin in 8.1.

### 15.9 Quality gates

```
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python -m mypy --strict pharmacy
.venv/bin/python -m ruff check pharmacy tests scripts && .venv/bin/python -m ruff format --check pharmacy tests scripts
.venv/bin/python -m pytest -q -m "not slow and not xtb and not validation" --cov --cov-branch --cov-report=term-missing
.venv/bin/python -m pytest -q -m xtb
.venv/bin/python -m pytest -q -m slow
.venv/bin/python -m pytest -q -m validation
```

Coverage: 95% branch coverage on `pharmacy/` (bde.py excluded, configured in pyproject).

Greenlight requires every command above to pass, including the `slow` (virtual studies, end to end) and
`validation` (independent real data) suites at the replicate counts this section states. A dataset is report only
for one of two stated reasons: its data are unverified, or they are not independent of calibration. Every other
dataset is a gate. A failing gate is resolved by finding its cause in the engine, or in a test that compares the
wrong quantity, with evidence; never by changing configuration values, tolerances, row selections or pass rules to
fit the outcome. The statistical gates are binomial: a correctly calibrated engine passes each with probability at
least 0.95, so a failure is evidence against the engine, not a one-row miss.

Coverage exclusions for ellipsis stubs match only a whole line, `^\s*\.\.\.\s*$`, so tuple annotations
cannot exclude complete functions. The cache-isolation fixture of 7.4 applies to every test session.
