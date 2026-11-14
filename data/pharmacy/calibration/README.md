# Reproducible Du 2011 calibration

Owner CX-CAL. This is an offline derivation, never part of estimating a lot. No network access, chemistry-cache
write, random generator or wall clock is used. The input data are the frozen Du vector supplied as an argument;
configuration chooses calibration rows, reference conditions, chemistry priors, packages and decision coverage.
The original vector, source catalogue and contract were not edited.

## Reproduce and write

From the repository root:

```sh
.venv/bin/python -m pharmacy.calibration tests/data/vectors/validation-du2011.json --config config/pharmacy --flight-condition data/pharmacy/calibration/du2011-flight-condition.json
```

The committed configuration documents are version 0.5.0, written by this command with `--write-version 0.5.0`.
A write requires a strictly newer version for
both documents, validates both complete documents and cross-references before writing, and touches only total
prior entries, the four named space-zone multipliers and calibration provenance. Ground storage stays fixed at
1. Do not bump versions just to reproduce: the read-only command and exact-equality test do that.

The command prints all 36 rows' signed two-point ground and flight rates, censoring flags, ground reference
rates and flight residuals. The committed output is `calibration-summary.txt`. `calibrate` additionally returns
structured row values, package ids, physics factors and conversion variances. Calibration records pin the engine
version, all bundle document versions, the canonical vector hash and the flight humidity input hash.

## Statistical method

For each arm, y = ln(C13) minus ln(C880); k = y / ((880 minus 13) times 86400). Each arm uses its own initial
assay for calibration. For validation prediction, both arms start from the day-13 control assay, as spec 15.5
requires. Those are different operations.

A nonpositive y is retained, with its signed observed rate reported. Its positive representative is the median
of N(y, s_y squared) restricted to true loss greater than zero, where
s_y = sqrt((sd13/C13) squared plus (sd880/C880) squared). This is likelihood imputation under a flat prior on
nonnegative true log loss. It is not a fixed rate floor. At y = 0, the representative loss is
s_y times normal_ppf(0.75). A nonpositive loss with zero assay uncertainty raises InputError. Numerical
failure raises ComputationError. This rule is tested on zero loss and apparent gains. It does not fully
propagate latent censored-rate uncertainty, so a future dataset with censored training rows would need that
limitation reviewed. No configured training row is censored; the choice cannot influence the present fit.

Family Ea/B locations are arithmetic averages over configured destructive thermal classes; their uncertainties
are mixture standard deviations, including differences between class locations. This catalogue-based equal
weighting is explicit because the configuration has no family aggregation weights. Suspension aliases solution
and is not counted again. The associated contract clarification is CAL-01.

Each rate is divided by the mean physics factor Theta/duration at k_ref = 1, using
`microclimate.exchange_time_constant_s`, `kinetics.rate_constant` and `kinetics.humidity_integral`. Repackaged
rows use `repackaged_bag`; otherwise solids use `original_bottle`, liquids and semisolids use `original_vial`.
Unknown repackaging uses that same original-container rule, explicitly including rows 25 and 26. Internal RH
starts at the package's configured initial value, or at ambient when null. The package clock starts at day 13,
matching the specified validation lot clock. No ambient RH is substituted for a protected package's internal RH.

Ground rates are converted once to 298.15 K and 40 percent RH, as read from configuration. Downstream estimates
must not convert them again. The converted log rates have arithmetic family means. Pooled variance is
sum_f sum_i (x_if minus mean_f) squared / (N minus F). Pooled within-family variance was chosen over REML
because the requested model has a single between-product spread, and the small groups do not justify extra
variance components. There is no robust trimming, quantile fitting or validation-driven selection.

N = 19 and F = 3, so df = 16. Counts are solid 11, solution 4 and semisolid 4. The pooled variance is
0.32018990710574297 in squared natural-log units. Let c be decision coverage. For each family:

```text
sigma_t = sqrt(pooled_variance) sqrt(1 + 1/n_f) t_ppf((1+c)/2, 16) / normal_ppf((1+c)/2)
sigma_stored = sqrt(sigma_t squared + mean_reference_conversion_variance_f)
```

The three variance terms of spec 9.3 are propagated independently after the Student-t conversion. They are
averaged within family without division by n because storage-condition and family chemistry uncertainty are
shared. The B term uses RH* minus the rate-weighted internal RH, the derivative of ln(Theta/duration) with respect to B
(spec 9.3, `rate_weighted_internal_rh`); at zero B it is the time-mean internal RH. Both the t-equivalent component and conversion variance are recorded. The
contract ambiguity about this composition is filed as CAL-02. RH sd is preserved in the calibration condition
and used in predictive condition uncertainty; no extra term is added to the three explicitly specified 9.3 terms.

| Family | ln rate mean, 1/s | Stored normal sd |
|---|---:|---:|
| solid | -19.9689527113604 | 0.682959111893558 |
| solution | -20.41151620281944 | 0.695630433489508 |
| suspension | -20.41151620281944 | 0.695630433489508 |
| semisolid | -20.190849026195124 | 0.7230250099190625 |

## Flight factor

For each selected row, residual = ln(k_flight/k_ground) minus ln(factor_flight/factor_ground). Both physics
factors use the same package. Flight temperature is read from station_kit's profile: 296.15 K with sd
(300.15 minus 291.15)/4 = 2.25 K. Flight RH is the validated SourcedValue pair in
`du2011-flight-condition.json`: 24 +- 0.5 percent, sourced to MDP01 and MDP02. These four numbers are stored
under the exact four statistic names required by spec 9.5. The ground conditions come from the total-prior fields.

The mean ln residual is 0.700855462834577, so the lognormal multiplier has median 2.0154761351288 (the sample
median residual, 0.637063992890526, is recorded as a statistic only). Its normal sd is 0.402706292978003: sample
sd about the mean, times sqrt(1+1/19), times the t/normal quantile ratio with df=18. Location and spread together
form the exact Student-t predictive pivot for a normal sample (coordinator decision on CAL-04). No extra
flight-condition variance is fitted into the residual spread. Conditions and their standard uncertainties are recorded for validation-zone construction.

Station basis is derived. Transit, Moon and Mars use the same values with basis team_default and the explicit
note that surface data do not exist and the pharmacist and flight surgeon must set the prior.

## Prior-only check

```sh
.venv/bin/python -m tests.unit.calibration.offline_check --output data/pharmacy/calibration/offline-check.json --tolerance 1e-9
```

`offline-check.json` reports every requested row, its measured value, assay sd, median and interval, coverage
counts and formulation lists for V2. It is a prediction of engine validation, not a replacement for it.

The approximation uses exact nominal package exposure, an approximately normal log cumulative exposure, and
independent uncertainty in total ln rate, zone factor, family Ea/B and temperature/RH. Symmetric one-sd secants
of the engine physics propagate those last four uncertainties. It integrates the one-dimensional normal mixture
with deterministic quadrature and solves its quantiles numerically. Both initial assay uncertainty and the
row's final normal assay error are marginalized before quantiles. Measured-value intervals are not clipped at
100 percent. It does not run molecular pathways, measured models, computed anchors, sensor interpolation,
extrapolation widening, assay updates, adaptive Monte Carlo or correlated flight-anchor corrections. A normal
log-exposure approximation omits products and higher-order effects of uncertain drivers. V2 uses central rates
and ignores the small shift of the potency median from initial-assay noise.

V1 uses the 16 held-out even rows excluding the solution calibration rows 22 and 30. Both arms use their
own final assay sd and the day-13 control mean/sd as initial assay. The ground zone comes from prior conditions;
the flight zone comes from the calibration statistics. V6 uses station-profile RH and its quarter-range sd,
repackaged bags, initial 100 +- 1.5, and converts each measured mg sd to percent of its label.

V6 rows 2 to 6 are the class-prior subset identified by the existing validation report. Acetaminophen still has
class-prior basis because of its unanchored pathway, although this approximation omits its anchored pathway.
Aspirin is the study calibration anchor. Melatonin, zolpidem and modafinil had computed basis and are excluded
from this requested prior-only subset. The all-eight-row V6 acceptance gate remains the verification engineer's
responsibility. The subset's binomial threshold is computed for n=5, not reused from n=8.

| Check | Covered | Required by binom.ppf(0.05,n,0.9) | Missed rows |
|---|---:|---:|---|
| V1 ground | 12/16 | 12 | 2, 6, 8, 12 |
| V1 flight, report only | 15/16 | 12, comparison only | 12 |
| V6 class-prior subset | 3/5 | 3 | 3, 4 |

| V2 payload day | Prior central prediction | Transcribed measured flight grid | Documented flight failures |
|---|---:|---:|---|
| 13 | 0 | 1 | 1 |
| 353 | 5 | 9 | 9 to 11 |
| 596 | 15 | 16 | 17 |
| 880 | 26 | 23 | 23 to 24 |

The strict below-limit formulation count is used, including all four component pairs. The grid's day-596
count differs from the documented count; both are preserved. The early predicted counts are low and the day-880 count is two above the documented range. Clavulanate
still fails both V1 intervals. V6 loperamide's supplied sd is 0.6 mg on 2 mg, or 30 percentage points; this large
uncertainty is preserved. Calibration and validation components of co-formulations are not independent.

Requests CAL-01 through CAL-04 and these findings are in `docs/requests/round2-CX-CAL.md`. No acceptance rule,
configuration selection or tolerance was tuned to these outcomes.

## Verification record

Strict mypy on `pharmacy` and `tests/unit/calibration` reports no issues in 27 source files. Ruff check reports
all checks passed; Ruff format check reports 27 files already formatted. Contract validation reports
`0 problem(s)`.

The final focused pytest command was:

```sh
COVERAGE_FILE=.coverage.CX-CAL .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/calibration tests/unit/test_foundation.py --cov=pharmacy.calibration --cov-branch --cov-report=term-missing
```

Last line: `124 passed`. Calibration coverage: 320 statements, 130 branches, zero missed statements,
zero partial branches, 100 percent total. There were no failures, skips or xfails in this run. The full engine
validation, slow, virtual-study and xTB suites were not run by CX-CAL.

A second offline run at tolerance 1e-10 changed no coverage decisions or V2 counts. Its largest change over all
reported medians and interval endpoints was 2.390976305832737e-10 percentage points. These tolerances control
numerical integration only and do not change any statistical acceptance threshold.

An explicit before/after comparison found all unowned values unchanged in both configuration documents.
The estimator modules contain no imports of `pharmacy.calibration`.
