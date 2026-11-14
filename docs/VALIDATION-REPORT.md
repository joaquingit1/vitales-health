# Independent verification, round 2, CX-D

This report supersedes the earlier validation summary. This round changes verification code only.
It does not claim that the engine passes the published-data or 500-replicate studies. Engine and
calibration owners edited the shared repository during these runs, so intermediate failures are
identified separately from their rechecks. No configuration value, published vector, tolerance,
replicate requirement or statistical acceptance rule was relaxed.

The oracle was developed from ENGINE-SPEC.md. No engine computational function bodies were inspected. Public signatures, dataclass
fields, docstrings and test failures were used to connect the tests. One interface-extraction
command also emitted the field declarations of private fit dataclasses _Study and
_StudyHistory. Those declarations were not used by the oracle or its numerical formulas;
this incidental disclosure is recorded for the coordinator's independence review. The
oracle implementation imports no engine module other than pharmacy.types; its DrawBlock input is
now a structural protocol. No network, git initialization or commit was used.

## Changes by file

| File | Change |
|---|---|
| `pharmacy/envsim.py` | No implementation change was needed. Its existing simulator behavior was verified; branch coverage is 99.76%. |
| `tests/oracle/kinetics.py` | D2 rate uses log1p near zero. |
| `tests/oracle/chemistry.py` | Independent reference-rate conversion, packaged mean-rate integral, uncertainty propagation, correlated anchor draw and exact covariance algebra. Existing Evans-Polanyi domain, hydrogen weights and free-group log-softmax oracle functions were retained and exercised. |
| `tests/oracle/draws.py` | Measured-model lower-temperature margin follows configured settings. |
| `tests/oracle/lot_truth.py` | Removed even the type-only engine import; copies radiation, light and frozen-rate parameters as well as thermal parameters into oracle draws. |
| `tests/oracle/test_agreement_round2.py` | New per-draw free-share and zone-correction checks, correction-only zones in the variate union, anchor conversion, calibration mean-rate factors and changed-zone-sd error test, all at 1e-9. |
| `tests/oracle/test_oracle_self_chemistry.py` | Exact variance/covariance identities, reference conversion, invalid-input paths and D2 at alpha = 1e-100. |
| `tests/oracle/test_agreement_draws.py` | Expected scalar integral includes each zone's configured nominal multiplier. |
| `tests/oracle/test_agreement_exact.py` | Supplies the configured Q3B settings through the new public API. |
| `tests/vectors/test_vectors.py` | Supplies Q3B settings and updates the TV-H-5 error explanation to the current nullable-AICc contract. Published data and pass bands are unchanged. |
| `tests/validation/products.py` | Ground zones derive from family-prior reference fields; flight conditions derive from calibration statistics; all V6 rows use repackaged_bag. Station test conditions retain station_kit identity for correlated anchors. Zone construction uses one bundle snapshot. |
| `tests/validation/conftest.py` | Builds the validation products, lots, family ground zones and V6 station override in a scratch bundle. |
| `tests/validation/runner.py` | Captures the actual adaptive draws, adds each measurement's own assay error and retains both predictive and true-potency intervals. |
| `tests/validation/rules.py` | Counts failed formulations using component-specific USP lower limits. |
| `tests/validation/test_validation.py` | Uses the corrected zones and predictive rows, skips flight if provenance is absent, applies one count per formulation and reports each row's limit. |
| `tests/validation/test_validation_products.py` | Regression checks for dynamic conditions, V6 packaging and V2 component/formulation counting. |
| `tests/validation/test_execution_smoke.py` | One measured ibuprofen row in each of V1 ground, V1 flight and V6, with execution timing. This is not a dataset acceptance test. |
| `tests/validation/report.json` | Measured one-row smoke outputs, clearly labelled as smoke sections. |
| `tests/scenarios/test_envsim.py` | Uses the specified 99.9% OU variance sampling band; removes all three strict xfails. |
| `tests/scenarios/parallel.py` | ProcessPoolExecutor with spawn, ordered results, a deterministic task/seed mapping and PHARMACY_TEST_WORKERS. |
| `tests/scenarios/predictive.py` | Observes the public adaptive result without changing it; deterministic independent normal assay error and weighted predictive quantiles. |
| `tests/scenarios/bundles.py` | Scratch zone overrides, valid reloaded configuration hashes and the expressly permitted one-batch mode. |
| `tests/scenarios/builders.py` | Demo assessment times can execute in separate processes and return in sorted time order. |
| `tests/scenarios/test_coverage_studies.py` | Public-interface replacement for 15.7, 2000 default replicates, one-batch settings, independent oracle truth on sensed history, Brier scores and reliability bins. CX-B removed the superseded private-helper suite. |
| `tests/scenarios/test_verification_helpers.py` | Serial/two-worker identity, invalid worker counts, predictive noise, flight provenance and shared station identity checks. |
| `tests/e2e/test_demo_end_to_end.py` | Reports the specified 120 s day-100, 300 s day-400 and 900 s suite targets. |
| `tests/e2e/test_execution_smoke.py` | One-lot day-100 assessment, schema validation, replay, store and demo-expectation smoke. |
| `tests/virtual/studies.py` | Builds chamber sensor logs, clips them exactly at pulls, passes the sample package, uses the public 4000-draw prediction API and hashes all observations/logs/packages. Execution failures replace stale report sections. |
| `tests/virtual/test_virtual_studies.py` | Process-level replicates; VS-2 one-sided rule plus confusion and two-sided coverage; independent history/range flag check for VS-3; both VS-4 subgroups with explicit empty-group reporting; specified event templates retained. |
| `tests/virtual/test_virtual_fixture.py` | Updated synthetic fit input hash and regression for replacing stale failed-study reports. |
| `tests/virtual/test_chamber_logs.py` | Verifies sensor readings differ from truth, exact pull clipping, sample/open-dish package identity and complete input hashes. |
| `tests/virtual/report.json` | Small-run results and execution status, not full-study acceptance. |
| `docs/requests/round2-CX-D.md` | Interface/ownership coordination, packaged-anchor variance clarification and integration findings. |
| `docs/VALIDATION-REPORT.md` | This report. |

## Statistical and deterministic rules

The V1/V6 interval is the engine's actual potency draws plus independent normal noise with that
row's assay sd, then weighted quantiles. The seed is derived from the estimate and measurement
identity. Noise is not clipped. True-potency intervals remain in the report separately. Virtual
studies compare truth with potency intervals; noisy assays use the fitter's measurement-predictive
option when a measured value is compared. An assay used for updating is not treated as a noiseless
truth target.

V1 flight and V6 have different declared conditions but retain the physical station_kit zone ID
in separate scratch bundles. Renaming a declared copy would create an independent zone variate
and defeat decision 11. Replacing a zone writes and reloads the bundle so its pins describe the
actual conditions. Missing flight calibration statistics raise ConfigReferenceError and cause an
explicit flight-validation skip, never substitution of station profile RH.

Default virtual counts remain 500 per study/design/profile, and 15.7 remains 2000. Worker count
comes from PHARMACY_TEST_WORKERS, default 1. Replicate seeds do not use worker IDs or scheduling.
Serial and two-worker synthetic tasks produced bit-identical ordered results. B3_VS1_REPLICATES
through B3_VS5_REPLICATES and PHARMACY_COVERAGE_REPLICATES are small-run controls only. Their
reports retain the full statistical pass rules, so a one-replicate run can fail a coverage band
by construction. No xfail was added.

## Gates and execution evidence

All pytest commands use `.venv/bin/python`, `COVERAGE_FILE=.coverage.CX-D` and
`-p no:cacheprovider`. Mypy uses `--strict --cache-dir .mypy_cache/CX-D`.

The full fast run used `-m "not slow and not xtb and not validation" --cov=pharmacy.envsim
--cov-branch --cov-report=term-missing`: `8 failed, 1760 passed, 2 skipped, 104 deselected
in 445.10s`. Log: `/tmp/cxd-fast-final.log`. Four CX-B fixture failures subsequently passed:
`35 passed in 2.71s`, `/tmp/cxd-integration-recheck.log`. The anchor-variance mismatch also
subsequently passed after CX-C adopted the derivative. The oracle's unmultiplied expected
integral and the synthetic fit's missing input hash were corrected.

The subsequent owned fast run comprised `tests/oracle tests/vectors
 tests/validation/test_validation_products.py tests/scenarios/test_envsim.py
 tests/scenarios/test_verification_helpers.py tests/virtual/test_virtual_fixture.py
 tests/virtual/test_chamber_logs.py` with the same coverage options. It ended:
`2 failed, 764 passed, 1 skipped in 532.71s`. Log: `/tmp/cxd-owned-fast-final.log`.
One failure was TV-H-5. The other compared an earlier bundle with freshly reloaded calibration
metadata during concurrent edits. Zone construction now takes the same bundle snapshot; its
recheck ended `2 passed in 12.11s`, `/tmp/cxd-zone-snapshot-final.log`.

Environment simulator coverage: 302 statements, zero missed; 114 branches, one partial;
99.76% combined branch coverage. The remaining branch is the empty-innovation branch of the
private OU recurrence. Every public simulator function is exercised, including error paths.

The five truth spot checks passed in 247.94 s. Maximum step-halving error was
6.810761373128645e-7; maximum float/mpmath relative difference was 1.652304076195225e-11,
below the independently stated rounding bounds. Log: `/tmp/cxd-truth-smoke.log`.

The full 500/2000-replicate studies and full published-data validation were not run, as requested.
The all-inventory day-100/day-400 performance targets were therefore not measured.

## Open interpretation and findings

D1 is resolved: the public fields are StudyObservation.chamber_log and .package, with
fit.prediction_interval supplying the input-seeded 4000 draws. D2 is resolved: CX-B removed
the old tests/unit/core coverage suite. D3 remains a spec clarification: for a changing
internal RH, the variance sensitivity is minus the B derivative of the log mean humidity
multiplier. CX-C and the oracle now use that derivative. D4 records intermediate integration
failures and their successful rechecks, plus the remaining TV-H-5 finding.

TV-H-5 lot 1 returned Ea = -33.601834753149696 kcal/mol, outside the existing 10 to 50 kcal/mol
plausibility band. It remains a failing test. No value or bound was changed.

The first one-replicate VS-1 run with corrected logs found ComputationError for packaged
aspirin and triamcinolone ICH fits: `a fitted parameter does not affect any observation`.
The solution ICH fit and all three isoconversion fits completed. At n = 1, each coverage
fraction is 0 or 1 and cannot lie in [0.921, 0.979]. All six VS-1 acceptance tests therefore
fail; this does not estimate population coverage. The fit errors are separate engine findings.

The corrected VS-2 smoke had 3 misselections among 4 truth kinds, zero truths below the
selected 2.5% bound, and two-sided coverage 1/3. It passed its one-sided small-run rule;
no-selection count was zero. This is not a 500-replicate validation.

The earlier VS-3 attempt failed during concurrent implementation with AttributeError for the
then-missing kinetics.evaluate_draws_checked. Its later retry reached the 300 s execution
bound without completing the first replicate. The one-lot day-100 end-to-end smoke also
reached that bound. These are incomplete execution checks, not passed performance tests.
The older one-lot attempt was interrupted after 844.67 s; the older virtual attempt after
808.73 s. The sandbox denied shell signals, but the tool's interrupt control stopped those
runs. Subsequent bounded runs signal only their own subprocess groups.

## Skips, exclusions and remaining acceptance work

The emitted fast-suite skips were both TV-FIT-1: the AccelStab external dataset is absent
and unverified, in tests/unit/core/test_fit.py and tests/vectors/test_vectors.py. The owned
run emitted only the latter skip. No xfails remain in the CX-D suites.

Full V1/V6 binomial coverage, V2 formulation counts, full V9 and the all-lot demo remain for
integration. V5, V7, V8, V10, V11, V12, V13-LYON and V13-CANTRELL remain unverified-data
exclusions under the existing spec; V3 and V4 are not engine outputs. Those validation
skip tests were not invoked in this small-run round. Calibration/anchor rows remain excluded
from validation. V1 co-formulated rows still straddle the calibration split, so V1 is not
fully independent of calibration, as the spec cautions.

Final small-run measurements and the last gate outputs follow.

## Last targeted results

Reference, covariance, free-share and calibration-factor agreement: `8 passed in 5.68s`,
`COVERAGE_FILE=.coverage.CX-D .venv/bin/python -m pytest -q -p no:cacheprovider
 tests/oracle/test_agreement_round2.py`, `/tmp/cxd-round2-agreement-final.log`.

The final validation smoke command was `COVERAGE_FILE=.coverage.CX-D .venv/bin/python
-m pytest -q -s -p no:cacheprovider tests/validation/test_execution_smoke.py`:
`3 passed in 51.40s`, `/tmp/cxd-validation-final.log`. Row results below are predictive
measurement intervals; the smoke assertion checks execution, not coverage acceptance.

| Dataset | Row | Measured mean (sd) | Median | Predictive 90% interval | Covered | Wall s/row |
|---|---|---|---|---|---|---|
| SMOKE V1 | 6 Ibuprofen tablet | 97.6000 (0.2200) | 89.2276 | 70.8199 to 95.7818 | False | 7.111 |
| SMOKE V1-flight | 6 Ibuprofen tablet | 92.1000 (0.0200) | 87.0381 | 52.8992 to 96.1178 | True | 0.052 |
| SMOKE V6 | 3 Ibuprofen | 99.9000 (1.7750) | 88.5923 | 52.0339 to 98.8130 | False | 0.059 |

The latest 15.7 smoke used `PHARMACY_COVERAGE_REPLICATES=1 PHARMACY_TEST_WORKERS=2`
with `pytest -q -s -p no:cacheprovider tests/scenarios/test_coverage_studies.py`:
`1 failed in 7.77s`, `/tmp/cxd-coverage-final.log`. Both posteriors covered the single
truth, giving 1.0 coverage, outside the unchanged [0.88, 0.92] acceptance band. No
reliability bin had 30 observations, so reliability calibration was not established.

* plain: coverage 1.0000, Brier 0.00146689.

* assayed: coverage 1.0000, Brier 3.95969503035e-06.

Virtual smoke timing used one replicate per VS-1 design/product and one per VS-2 truth
kind, with PHARMACY_TEST_WORKERS=2. Wall cost includes process startup.

| Study | Wall s/replicate | Result |
|---|---|---|
| VS-1 solid ICH | 21.264 | Fitter ComputationError; acceptance fails |
| VS-1 solid isoconversion | 2.961 | Fit completes; n=1 acceptance fails |
| VS-1 solution ICH | 14.559 | Fit completes; n=1 acceptance fails |
| VS-1 solution isoconversion | 3.274 | Fit completes; n=1 acceptance fails |
| VS-1 semisolid ICH | 15.199 | Fitter ComputationError; acceptance fails |
| VS-1 semisolid isoconversion | 2.887 | Fit completes; n=1 acceptance fails |
| VS-2 | 2.036, amortized over four tasks | One-sided smoke passes; not full-size acceptance |

The virtual smoke command set B3_VS1_REPLICATES through B3_VS5_REPLICATES to 1 and ran
`pytest -q -s -p no:cacheprovider tests/virtual/test_virtual_studies.py -k 'not truth_float'`.
Its log is `/tmp/cxd-virtual-smoke-final.log`: `7 failed, 1 passed, 1 deselected in 808.73s`
before interruption during VS-4. VS-5 was then attempted separately.

The bounded current end-to-end run ended `no tests ran in 302.77s` with exit 2; wrapper
wall time 305.191 s. The current VS-3 retry ended `8 deselected in 301.55s` with exit 2;
wrapper wall time 303.303 s. Logs: `/tmp/cxd-e2e-smoke-current.log` and
`/tmp/cxd-virtual-loop-current.log`. Neither produced a completed replicate, so a completed
wall-time-per-replicate estimate is unavailable. The execution bound is not a test pass rule.

D5 in the request file records this performance limitation. The coordinator still needs
complete demo/schema/replay/store execution and the long-study acceptance runs after integration.

The separate VS-4 run used B3_VS4_REPLICATES=1 and PHARMACY_TEST_WORKERS=1 with
`pytest -q -s -p no:cacheprovider
 tests/virtual/test_virtual_studies.py::test_vs4_environment_sweep`. It ended
`no tests ran in 304.91s`, interrupted at the 300 s bound; wrapper wall time 307.021 s.
No profile replicate completed. Log: `/tmp/cxd-vs4-current.log`.

The separate VS-5 run used B3_VS5_REPLICATES=1 and PHARMACY_TEST_WORKERS=1 with
`python -m cProfile -o /tmp/cxd-vs5.prof -m pytest -q -s -p no:cacheprovider
 tests/virtual/test_virtual_studies.py::test_vs5_assay_closes_the_gap`. It ended
`no tests ran in 306.46s`, interrupted at the 300 s bound; wrapper wall time 309.015 s.
The cProfile wrapper returned exit 0 despite pytest's KeyboardInterrupt; this is explicitly
classified as incomplete, never a pass. Log: `/tmp/cxd-vs5-current.log`.

The VS-5 profile shows that chamber simulation, fitting, model selection, model_document,
scratch-bundle loading and mission truth preparation completed before pipeline estimation
was interrupted. Cumulative times overlap and must not be summed:

| Public operation | Calls | Cumulative seconds |
|---|---|---|
| fit_stability_study | 12 | 163.362 |
| select_model | 1 | 0.000251 |
| model_document | 1 | 0.0354 |
| closed_loop_model | 1 | 211.528 |
| prepare_replicate | 1 | 238.319 |
| estimate_potency | 1, interrupted | 43.286 |
| evaluate_draws_checked | 2, second interrupted | 37.722 |

These profiled timings include instrumentation and concurrent-machine load. They identify
work still too expensive for this smoke, not isolated-machine benchmarks. A completed
VS-3/VS-4/VS-5 wall time per replicate is unavailable. Their JSON sections now explicitly
record interruption, with no stale rows from the earlier report.

The final 15.7 amortized wall time was 5.114 s/replicate with two configured workers and
one task, including process startup.

## Final static gates

| Command | Last output |
|---|---|
| `.venv/bin/python scripts/validate_contracts.py` | `0 problem(s)` |
| `.venv/bin/python -m mypy --strict --cache-dir .mypy_cache/CX-D pharmacy tests` | `Success: no issues found in 97 source files` |
| `.venv/bin/python -m ruff check pharmacy tests scripts` | `All checks passed!` |
| `.venv/bin/python -m ruff format --check pharmacy tests scripts` | `98 files already formatted` |
| `COVERAGE_FILE=.coverage.CX-D .venv/bin/python -m pytest --collect-only -q -p no:cacheprovider tests/scenarios/test_coverage_studies.py tests/virtual/test_virtual_studies.py tests/e2e tests/scenarios/test_demo_expectations.py` | `69 tests collected in 2.37s` |

Gate logs are `/tmp/cxd-contracts-final.log`, `/tmp/cxd-global-mypy-final.log`,
`/tmp/cxd-global-ruff-final.log`, `/tmp/cxd-global-format-final.log` and
`/tmp/cxd-slow-collection.log`. The additional helper regression ended
`2 passed in 0.96s`, `/tmp/cxd-interruption-helper.log`. No committed-cache mutation
was reported by the session fixture.

Acceptance remains open: TV-H-5 fails; the packaged ICH fit errors remain findings;
V1/V6 coverage and V2 counts require full integration runs; and complete demo/virtual-loop
execution is not yet demonstrated within the small-run time bounds.
