# CX-D round 2 requests and integration dependencies

## D1. Public chamber-log fit interface

Resolved during this turn: CX-B now exposes `StudyObservation.chamber_log: tuple[Segment, ...]`
and `StudyObservation.package: PackageDoc | None`, plus `prediction_interval`. CX-D uses
these exact interfaces. The original request follows for context.

Spec 14.3 requires chamber logs and a sample package per observation, but the public
`StudyObservation` currently has only time, setpoint temperature, setpoint RH, response,
sd and group. Please have CX-B publish its new public field and segment type names.
Proposed minimal shape: `StudyObservation.chamber_history` as a tuple of constant
segments `(t0_s, t1_s, temperature_k, rh_percent)`, and `StudyObservation.package` as
`PackageDoc | None`. Times are seconds since the condition started; logs are clipped
at the pull. This is an interface coordination request, not permission to change data.
CX-D will supply simulator sensor logs and the declared ICH package, with open dish for
isoconversion. It will not substitute setpoints when logs are required.

## D2. Coverage-study ownership

Resolved during this turn: CX-B removed its superseded coverage-study file. The CX-D
replacement is the sole spec 15.7 suite.

The existing spec 15.7 suite is `tests/unit/core/test_coverage_studies.py`, owned by CX-B.
It calls private pipeline helpers and monkeypatches the adaptive loop through a private
statistics helper. Please retire that suite when integrating the CX-D public-interface
replacement in `tests/scenarios/test_coverage_studies.py`, or let CX-B delegate to it.
CX-D will not edit the CX-B file. The replacement uses the approved one-batch settings,
2000 replicates by default, and deterministic process-level parallelism.

## D3. Packaged-anchor uncertainty sensitivity

Spec 9.3 defines the packaged mean by a humidity integral but retains the constant-RH
variance phrase `(d_RH sd_B)^2` without defining d_RH for a changing internal RH.
Please state `d_RH = -d log(mean(exp(B (RH_internal(t) - RH*)))) / dB` for a packaged
anchor. This is first-order propagation of the specified mean and reduces exactly to
`RH* - RH0` for constant internal RH. CX-D's interim oracle uses this derivative, keeps
the explicitly stated temperature variance term, and reports disagreement rather than
changing a tolerance. No foundation file was edited.

D3 implementation update: CX-C adopted the derivative during this round. All six anchor
conversions now agree with the independent oracle; the new reference and correlation
checks pass at 1e-9. The written spec still needs the clarification. Calibration should
use the same derivative wherever internal RH changes during a calibration exposure.

## D4. Integration findings outside CX-D ownership

The full fast run ending in `/tmp/cxd-fast-final.log` had four CX-B test failures that
CX-D cannot edit. Please rerun and update the owned fixtures as appropriate:

* `tests/unit/core/test_fit.py::test_study_input_errors[observations11-kwargs11-needs n > p + 1]`
  expects the superseded AICc sample-count error; the fit now rejects the same fixture for
  fewer than two temperatures.
* `tests/unit/core/test_kinetics.py::test_nominal_thetas_and_shares` expects a multiplier
  of one. Actual Theta 0.007950672940794781; expected 0.004204656404184831 after the calibrated
  zone multiplier changed. The independent oracle self-test now reads the configured
  multiplier in its expected integral, without changing configuration or tolerance.
* `tests/unit/core/test_round2.py::test_one_batch_disables_stopping_and_initial_hook`
  constructs `BatchOutcome` without its required `log_weights` and `extras` arguments.
* `tests/unit/core/test_round2.py::test_measured_range_margin_distance` calls
  `unit_step_response` without `first_start_tracks_outside`.

The vector TV-H-5 plausibility failure remains an engine or data finding: lot 1 fitted
Ea = -33.601834753149696 kcal/mol, outside its existing 10 to 50 kcal/mol band. Neither
published values nor the pass band were changed. Later integration runs may supersede
these observations; the final validation report records the last checks.

D4 recheck: all four CX-B failure groups above now pass, 35 passed in 2.71 s,
`/tmp/cxd-integration-recheck.log`. The vector finding is tracked separately below.

## D5. Incomplete small integration runs

The current one-lot day-100 end-to-end smoke and one-replicate VS-3 retry each reached a
300 s execution bound without producing a completed replicate. These are execution
findings, not changes to acceptance criteria. Commands and logs are in
`docs/VALIDATION-REPORT.md`; full-size studies remain for the coordinator. CX-D cannot
fix numerical-core or fitter performance in its owned files.

D5 follow-up: separate one-replicate VS-4 and VS-5 attempts also reached 300 s before
completion. The profiled VS-5 reached model_document and pipeline estimation: 12 fitter
calls consumed 163.362 cumulative s, closed_loop_model 211.528 s, prepare_replicate
238.319 s, and the first estimate 43.286 s before interruption. These are overlapping
profile timings, not additive. Configuration and pass rules were not changed.
