# Coordinator decisions on the open requests

Binding for every builder. Where a decision below names a value, field or rule, use it exactly; where it leaves a
choice to the implementer, the implementer documents the choice in the spec and in the report.

## 0. Why the independent tests fail: root causes found

The independent tests (V1, V6, V2 and the virtual studies) fail because the engine misrepresents reality in five
places. The fixes are engine fixes; the tests are corrected only where a test compared the wrong quantities (section
8 and the V2 count in section 4).

1. **Calibration and prediction conditions disagree** (V1). The class priors were derived from Du 2011 ground
   controls stored in a JSC chamber at 36 to 44 %RH, then treated as if measured at 25 C / 40 %RH, and V1 validation
   placed the same samples at 25 C / 60 %RH: repackaged rows were predicted 2.2 times too fast. Section 4.
2. **The class priors are overconfident** (V1, V6). Each dose-form family's spread came from 4 to 11 products with no
   allowance for estimating it (the semisolid sd of 0.20 rests on four rows). Measured offline on the held-out rows:
   the current construction covers 9 of 16 even at the right condition; pooled between-product variance with a
   Student-t predictive covers 12 of 16. Section 4.
3. **The spaceflight factor contradicts the data** (V2, V1 flight). Du 2011 flight samples degraded 1.3 to 1.8 times
   faster than identical ground kits (median ratio 1.47 over 34 rows) although the flight kit was drier; the engine's
   factor has median 1. Predicted failures at day 880: 5, documented 23 to 24. Section 4.
4. **Study fits ignore the package** (VS-1, VS-3, VS-5). Packaged ICH samples experience the package's internal
   humidity (the aspirin bottle's moisture time constant is 2449 days at 25 C, so a 6-month 40 C / 75 %RH pull sees
   about 41 %RH), but the fit is told the chamber's 60 to 75 %RH; the engine then applies the fitted humidity term to
   the internal humidity again, counting the package twice and predicting too little loss (VS-3 medians 0.8 to 3.7
   points above the truth). Section 6.
5. **Anchors measured in flight count the spaceflight factor twice.** Two anchors were measured on the ISS
   (`aspirin_tablet_iss_550d`, MDP05; `acetaminophen_tablet_iss_6mo`, MDP16), so their rates already contain the
   factor of root cause 3, and the engine multiplies a station lot by the factor again. Once the factor is calibrated
   (median about 1.47), aspirin and acetaminophen in a station kit would be predicted about 1.5 times too fast.
   Section 11.

## 1. Requests accepted as proposed

Every request in `docs/requests/B1.md` (R-01 to R-21, R-FIT-1 to R-FIT-8, R-STORE-1, the R-19 list and the "Spec
findings"), `docs/requests/B2.md` (R1 to R24) and `docs/requests/B3.md` (R1 to R31) is accepted as proposed, except
where a section below decides otherwise. Where B1 and B3 proposed different shapes for the same point, the B1 shape
(already implemented) wins and the oracle adapts: B3-R4 uses B1 R-08 (molar basis in `evaluate_draws`, mass
conversion in the pipeline); B3-R7 uses B1 R-12 (`Forecast.quantile_probability`); B3-R20 uses B1 R-20 (AICc `None`
when n <= p + 1, `select_model` raises `InputError` on a candidate without AICc). B3-R1: the virtual-study fixture
stays at `tests/virtual/virtual-studies.json`; spec 15.8 and the build plan say so. B3-R28: the location is the module
docstring of `pharmacy.decision`. 15.7 one batch: `min_batches = max_batches = 1` means adaptive stopping off.

## 2. Performance (B1 R-21)

The engine must run the demo end to end in a normal test session. Exactness of the reference path is not negotiable;
speed comes from engineering and from approximations whose error is bounded and checked on every estimate.

1. Threads: `evaluate_draws` runs its fixed segment chunks on a thread pool; partial sums are reduced in chunk order,
   so results are bit identical for any thread count. Thread count comes from engine-settings (0 means the CPU count).
2. Compressed history: a fast evaluation of Theta over a compressed exposure history (for example blocks of
   consecutive segments replaced by quadrature nodes on each block's empirical distribution of the rate drivers),
   designed by the implementer and written into the spec with its error analysis. Rule: on every estimate the
   compressed and the exact evaluator are compared on a fixed probe set of draws that spans the drawn parameter
   support (smallest and largest Ea, B, zone offsets and multipliers of the batch, plus fixed quantile points); if the
   largest relative difference of any pathway's Theta exceeds `numerics.compression_relative_tolerance` (team default
   1e-7) the estimate uses the exact evaluator. The trace records the probe error and which evaluator ran. The exact
   evaluator stays public and is what the oracle checks at 1e-9.
3. Forecasts reuse the estimate's per-draw state at as_of; nothing up to as_of is recomputed.
4. Targets on this machine, one process, reported every time (not asserted in unit tests): the demo at day 100 for
   all lots through `assess_inventory` within 120 s; day 400 within 300 s; the end-to-end test of 15.6 within 15
   minutes.

## 3. Degradants: identified before they condemn a lot (B1 finding, demo day 100)

A degradant estimate is only as good as the evidence that the pathway forming it really runs at that rate. Class
priors and anchors from assay loss measure total loss, not which pathway caused it, so attributing that loss to a
toxic degradant is a worst case, not an estimate.

* A degradant prediction is identified when its forming pathway's rate comes from evidence specific to that pathway:
  a measured model that lists the degradant, or an anchor that measured the degradant's formation (a new anchor field
  naming the degradants it measured, empty by default). Otherwise it is unidentified.
* An identified degradant above its limit makes the lot `do_not_use` (as now).
* An unidentified degradant whose probability above its limit reaches the decision threshold gives the warning reason
  `degradant_unidentified_above_limit`. It forbids potency adjustment for the lot, the lot state follows potency, and
  the decision says to inspect the lot or assay the degradant. It never makes the lot `do_not_use` by itself.
* The trace states, per degradant, whether it is identified and by what.

## 4. Class-prior reference conditions (validation finding V1)

The total-rate priors were used as if measured at (T*, RH*) = (298.15 K, 40 %RH) while V1 validation placed the same
Du 2011 ground controls at 25 C and 60 %RH: calibration and validation disagreed by about exp(0.04 x 20), a factor of
2.2. Fix the inconsistency at its source:

* Each `total_rate_priors` entry gains the storage condition of its calibration data, with the same fields and
  semantics as an anchor (`temperature_k`, `temperature_sd_k`, `rh_percent`), and converts to (T*, RH*) by spec 9.3,
  adding the variance terms of 9.3.
* Du 2011 ground controls (MDP01, MDP02): "ground controls in a JSC environmental chamber", RH 36 to 44 % (read as
  40 +- 2 %RH, sourced). The chamber temperature is not in the research notes: 295.15 K (22 C) +- 1.5 K as a team
  default matching the ISS cabin, to be set by the chemistry lead from MDP03.
* V1 ground-control validation uses a declared zone built from these same fields (in tests), so calibration and
  validation can never disagree again. V1 flight and V6 keep the declared station zone (B3-R27).

### 4.1 Calibration is code, not hand arithmetic

A new offline module `pharmacy/calibration.py` (like `fit.py`: never called by an estimate) derives the class priors
and the spaceflight factor from the calibration rows of the Du 2011 dataset (odd rows plus the solution rows the
config names; `tests/data/vectors/validation-du2011.json` is its input file, passed as an argument) and writes the
config entries with full provenance: method, rows, statistics and the engine version. A unit test reruns it and
asserts the committed config values equal its output exactly, so no prior can drift from its derivation.

* **Total-rate priors.** Per row, the two-point first-order ln k from day 13 to day 880 of the ground control, then
  converted to (T*, RH*) through the engine's own physics at the row's calibration condition (section 4) and package
  (repackaged rows in `repackaged_bag`, others in their original container), using the family's Ea and B prior means.
  Rows whose loss is not positive are kept through a censored likelihood or an equivalent documented rule, never
  dropped silently. Family means; one between-product variance pooled across families (a random-effects model fitted
  by REML or the pooled within-family variance, documented); predictive spread s sqrt(1 + 1/n_f) with Student-t
  degrees of freedom N - F. Because the engine draws ln k as normal, the stored sd is the normal sd whose central
  interval at the decision coverage (90 %) equals the Student-t predictive interval; the note says so.
* **Spaceflight factor.** Per calibration row, ln(k_flight / k_ground) minus the ratio the engine's physics predicts
  for the two arms (flight 23 to 25 %RH, ISS cabin temperature; ground as above; the row's package): this residual is
  the unexplained spaceflight factor. Its median and Student-t predictive spread become the lognormal rate multiplier of
  `station_kit` (basis derived, MDP01 and MDP02). `transit_kit`, `moon_base_pharmacy` and `mars_base_pharmacy` take the
  same prior with basis team_default and a note that no surface data exist, to be set by the pharmacist and flight
  surgeon.
* **V2 failure counts** compare each row with its own USP lower limit (the dataset's `usp_range_percent`), one count
  per formulation, flight arm; they are reported against the documented counts.

## 5. Validated range below the lowest study temperature (B3-R24)

A measured model fitted from long-term data at 25 C and accelerated data above it is routinely applied to storage a
few degrees cooler. Engine-settings gains `validated_range.temperature_below_min_margin_k` (team default 10 K, to be
set by the chemistry lead): temperatures down to the model's minimum minus this margin are inside the validated range;
the 8.2 widening distance below the range is measured from the extended limit. Above the maximum nothing changes. VS-3
and VS-4 use B3-R24's wording.

## 6. Study fits use what the product experienced (B3-R15, root cause 4)

The humidity term of a rate law is a function of the humidity at the product (spec 5.1), so a measured model's B and
its validated RH range refer to the product's internal RH. `fit_stability_study` accepts, per observation, the logged
piecewise-constant chamber history from the start of the condition to the pull (constant setpoints remain allowed as
a degenerate log) and the package the samples were stored in (none for open dish). It computes the internal RH trace
with the engine's own microclimate functions and Theta with the engine's own humidity integral, so a fit and an
estimate use identical physics. The virtual studies hand the fits the simulator's chamber sensor readings and the
package, as a laboratory would. "The selected model's 95%
prediction interval" is the 2.5 and 97.5 % quantiles of potency over 4000 draws from the multivariate normal of the fit,
with a seed derived from the fit's inputs.

## 7. Model selection when kinds cannot be told apart (B3-R29)

Among the kinds within 2 AICc units of the lowest, `select_model` chooses the one that predicts the lowest potency at
the longest time and highest temperature of the study's long-term condition (the conservative choice, in the spirit of
ICH Q1E); ties go to fewer parameters, then kind order. VS-2 becomes one sided: over misselections, the truth's potency
at 25 C / 60 %RH and 24 months lies below the selected model's 2.5 % prediction bound in a fraction no larger than
the binomial allowance for 0.025 (3 standard errors); two-sided coverage and the confusion matrix are reported.

## 8. Covering a noisy measurement (B3-R31)

Wherever a published or simulated measured value is compared with the engine, the comparison is against the
predictive distribution of that measurement: the engine's potency draws, each plus a normal assay error with the
measurement's own sd, before quantiles are taken.

## 9. Tests never write the committed BDE cache (B3-R25)

The cache directory can be set by the environment variable `PHARMACY_XTB_CACHE_DIR` (location only; entries are
content addressed and verified on read). A session-scoped autouse fixture in `tests/conftest.py` copies the committed
cache to a temporary directory and points the variable at it, and a session-end check fails the run if the committed
cache's bytes changed.

## 10. Defects to fix

* `placards[].share_nominal` can exceed 1 by rounding (B3 finding 3); shares are clamped to [0, 1] after
  normalization, with a property test.
* Oracle D2 `rate_function` must use `log1p` near alpha = 0 (B1 finding for B3).
* Simulator OU variance self-test uses the sampling-distribution band of B3-R2, and the three strict xfails go.

## 11. Anchors carry where they were measured (root cause 5)

An anchor states the storage zone and package its rate was measured in, with the same meaning as the calibration
storage condition of a total-rate prior (section 4):

* `storage_zone_id`: a zone of `storage-zones.json` (contract validation checks it exists). Ground and laboratory
  data name the ground zone, whose multiplier is fixed at 1; the two ISS anchors name `station_kit`.
* `package_id`: the package the product was stored in, or null when the rate refers to the product itself (solution
  kinetics, open dish). When it is set, the anchor's `rh_percent` is the ambient RH and the conversion to (T*, RH*)
  uses the internal RH that the engine's microclimate gives for that package at the anchor's constant condition over
  the anchor's duration, so an anchor and an estimate share identical physics (the reasoning of section 6). The anchor
  therefore also states `duration_days`; the start of the internal RH is the package's configured
  `initial_internal_rh_percent` (spec 4.3), exactly as for a lot. The anchor's ln k is matched to the mean rate over
  the duration (Theta divided by the duration), which is what a two-point first-order rate measures.

Conversion to the ground-equivalent rate at (T*, RH*) subtracts the zone multiplier's ln mean. Its uncertainty is
correlated with the lot's own zone factor, not independent of it: the factor that sped up the measured sample speeds
up a lot stored in the same zone. Adding its variance independently (the first draft of this section) would count it
twice for exactly the lots the anchor describes best. Spec 9.3 gives the exact rule: the anchor enters the 9.4
combination with the factor's variance included, and the pathway's ln k draw subtracts lambda times the zone
multiplier's drawn deviation through `Pathway.zone_correction`, sharing the zone's variate column (spec 9.7). The
multiplier is read from configuration at conversion time, so recalibrating the factor (section 4.1) updates every
flight anchor without editing it.

Packages (research notes, space-pharmacy.md): Wotring 2016 (MDP05) stored its nine products repackaged, so
`aspirin_tablet_iss_550d` uses `repackaged_bag`, and the V6 rows (the same study) use `repackaged_bag`, not
`original_bottle`. Bogomolov 2015 (MDP16) does not state the package: `original_bottle` stays a team assumption
recorded in `storage_context`. The GCRSim amoxicillin control arm (MDP11, MDP12) was in sealed manufacturer
containers: `original_bottle`.

## 12. Calibration choices (CX-CAL requests)

* **CAL-01 accepted.** Family Ea and B for calibration are equal-weight mixtures of the applicable destructive
  thermal class priors (mean of locations; sd from the mixture variance), unclassified thermal included.
* **CAL-02 accepted.** The stored sd is sqrt(sigma_t^2 + v_conversion), where sigma_t matches the Student-t
  predictive interval and v_conversion is the family's mean 9.3 conversion variance (shared, not divided by n).
* **CAL-03 accepted.** The V1 flight zone comes from the four flight condition statistics; spec 15.5 says so.
* **CAL-04 decided differently.** The flight factor's ln location is the mean ln residual, not the median: with the
  sample sd and sqrt(1 + 1/N) on N - 1 degrees of freedom it is the exact Student-t predictive for a normal sample,
  and "median" in section 4.1 meant the multiplier's median, exp(mean ln). The factor's median is 2.02 (ln sd 0.40).
* **CX-C R1 accepted.** For a packaged anchor or calibration row the 9.3 humidity variance term uses the
  rate-weighted internal RH (the derivative of the log mean rate with respect to B), not a rate-equivalent RH. Both
  chemistry and calibration use it; spec 9.3 gives the closed form.
* **CX-C R2 accepted.** `chemistry.parameters.anchor_at_reference` takes a required keyword-only `bundle`.

