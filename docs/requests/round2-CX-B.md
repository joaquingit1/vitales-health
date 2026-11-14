# CX-B round 2 integration notes and requests

## Fit input and prediction interfaces

The approved contract requires chamber histories, packages and deterministic prediction intervals but does not name their Python arguments. The implementation retains the existing positional observation fields and adds `StudyObservation.chamber_log: tuple[Segment, ...] = ()` and `StudyObservation.package: PackageDoc | None = None`. Log times are elapsed seconds, contiguous from zero through the observation time. An empty log means the observation's constant setpoints. None means open dish. Each log is the complete history from condition start, not just the interval since a preceding pull.

`prediction_interval(fit, observation, *, assay_sd_percent=0.0)` returns the 2.5% and 97.5% potency quantiles from 4000 fit draws. The observation supplies prediction conditions; its response is ignored. `select_model(fits, *, condition=None)` accepts an explicit comparison observation. Otherwise it uses the retained longest study pull, then highest temperature, then group name to identify the long-term condition. Fits retain their observations, input hash and numerical settings. Model documents use the retained input hash, including logs and package, when present.

Coordinator confirmation requested: identify long-term conditions by longest duration when no explicit comparison observation is supplied. The existing observation group string does not encode a condition role. This is the least surprising interim behavior because long-term studies have the longest pulls, and callers can supply the comparison explicitly.

## Regulatory helper interface

The foundation's required Q3B settings are now keyword-only: `q3b_thresholds(dose_mg, *, settings=bundle.settings["q3b"])`. Existing external tests must pass that setting. `q3b_exceeds` accepts keyword-only `decimals` to preserve written threshold precision; the existing float-precision behavior remains available when it is omitted.

## Standalone adjustment eligibility

Proposed contract addition: `adjustment_eligibility(product, estimates, *, settings: DecisionSettings)` should receive the degradant decision quantile. Reason: its existing signature has no threshold, so it cannot determine when an unidentified degradant reaches the warning threshold without inventing a default. Interim behavior: `decide_lot`, which already receives settings, adds the unidentified-degradant eligibility check and warning. Every returned lot decision correctly forbids adjustment on the warning. The standalone helper still returns its documented five product, toxicity, range and coverage checks. No signature was changed pending coordinator review.

## Published TV-H-5 finding

The fitter now returns the four-observation, three-parameter fit with null AICc. The unchanged independent vector plausibility test then fails for lot 1: Ea = -33.601834753149696 kcal/mol, outside its predeclared 10 to 50 kcal/mol band. This is no longer an AICc implementation refusal. The constant-condition fit is checked independently against the linear solve of log(loss/time) on the Arrhenius and RH coordinates. Published observations, configuration and the test's band remain unchanged.

## Coverage-study integration

Retired `tests/unit/core/test_coverage_studies.py` after checking CX-D's replacement at `tests/scenarios/test_coverage_studies.py`. The removed suite monkeypatched the adaptive loop, bypassed the new initial-conflict hook and incorrectly marked its one-batch result unconverged. The replacement exercises the approved public one-batch configuration. This removes duplicate slow studies; it does not change the coverage acceptance rules.

The direct linear solve gives TV-H-5 lot 1 Ea = -33.6018347530703 kcal/mol and B = 0.32570975273648095 per percent RH; lot 2 Ea = -128.30344259053416 kcal/mol and B = 0.7589661516627098. The nonlinear fit agrees for lot 1 to about 8e-11 kcal/mol. The data confound temperature and RH strongly and do not support the vector's positive-Ea plausibility band under this model. This is evidence for the finding, not a proposed change to the band.

Both independently solved B values also exceed the vector's unchanged upper band of 0.10 per percent RH. The vector test stops at its first Ea assertion, so the second lot's values above are a separate diagnostic, not a second reported pytest failure.

## Zero anchor precision weight

Proposed clarification: require `0 < ZoneCorrection.weight <= 1` when a correction is present; represent an unused anchor by `zone_correction=None`. The type docstring currently says 0 to 1, while the sampling contract reconstructs `s_c^2 = s_post^2 / weight`. A zero weight does not identify that variance. Interim behavior: `draw_parameters` raises `ModelDomainError` for zero weight, instead of dividing by zero or inventing the missing evidence variance. No configured demo model emits this boundary case. If a deterministic class prior must retain a zero-weight anchor record, the contract should instead explicitly define that limiting case.

## Standalone forecast state

`assess_lot` and `assess_inventory` retain draws, effective initial content, cumulative Theta and the microclimate boundary for their forecasts. Their forecasts do not redraw or integrate past history. The existing standalone `forecast_lot(bundle, estimate, series, assays)` accepts only the serialized estimate summaries, which do not contain those arrays; it must reconstruct the estimate before forecasting.

Proposed API addition: a module-local `ForecastState` record containing the retained run state, an `estimate_potency_with_state(...) -> tuple[PotencyEstimate, ForecastState]` entry point, and keyword-only `state: ForecastState | None = None` on `forecast_lot`. A supplied state's input pins must match the estimate or raise `DeterminismError`. Keep the existing no-state call as an explicitly documented replay operation. Interim behavior preserves the existing standalone API and its input validation; assessment entry points fulfill the state-reuse requirement. No public type or signature was changed for this proposal.

## Demo day-100 finding

The full single-process inventory run at `2028-04-19T00:00:00Z` took 2591.421583833988 seconds, exceeding the unchanged 120-second target. The run uses reaction-classes and storage-zones version 0.5.0. Its 15 ingredient estimates had zero failed-probe fallbacks and a maximum probe relative error of 3.3648199449022743e-12. `lot-pmz-tab-001` reached 100 batches and correctly reports `mc_not_converged`.

Evaluating the saved output with CX-D's unchanged `builders.evaluate_expectation` finds `x-apap-002-in-spec` false: the lot is `do_not_use`, while the fixture expects `in_specification`. Its lower 5% potency quantile is 84.3750887607355%, below the unchanged 90% lower limit; the median is 93.97116991104033%. The product's toxic-degradant restriction also forbids adjustment. This is a potency-driven decision, not condemnation solely by an unidentified degradant. The other two day-100 fixture expectations pass. No expectation, prior or decision threshold was changed.
