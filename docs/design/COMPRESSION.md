# Checked compression of exposure histories

Owner: CX-B. Implements decisions 2 and spec 12.5. The reference remains
`kinetics.evaluate_draws`; `kinetics.evaluate_draws_checked` is the accelerated entry point.

## Exact dictionary method

The compressor builds a separate dictionary for each thermal pathway. The dictionary key
contains all of its environmental rate drivers: temperature, moisture model and state,
zone, sensor-offset masks, all custody-zone humidity step responses, freezing state and
both validated-range distances. Equality is exact float equality, with deterministic
lexicographic ordering through NumPy unique. There are no bins, fitted tolerances,
clustering thresholds or changes to the sensor history.

For an exchanging package the key also includes duration, outside RH, internal starting
RH and the exchange time constant. A repeated key reuses the same complete humidity
integral. For instantaneous exchange and sealed packages, the rate is constant within
the segment. Duration factors out exactly in real arithmetic. For sealed packages,
RH_out cancels analytically from `exp(B RH_out) J(B (RH_in - RH_out), D, infinity)`;
the canonical representative therefore uses RH_in and a unit duration. A pathway without
a humidity term similarly ignores moisture state. The factorized duration multiplies
the representative's rate when reconstructing each original segment.

Only dictionaries smaller than the original segment sequence and no larger than one
256-segment chunk are used. This reuses the exact evaluator's existing memory bound
and prevents almost-unique exchanging histories from materializing an entire history
by draw matrix. Uncompressible
pathways use the exact kernel. Light and radiation use their original linear kernels.
The full original segment sequence is reconstructed in fixed 256-segment chunks. The
same ordered reduction and chronological degradant attribution then run on that sequence.
Compression never substitutes an average pathway share for the per-segment share.

The method is particularly useful for sealed packages and pathways without humidity
sensitivity, since their histories often contain many repetitions of quantized temperature.
An exchanging package with a unique moisture state at every boundary may compress poorly.
That limitation is reported by measurements rather than relaxed accuracy rules.

## Bounded temperature interpolation

When a dictionary does not reduce a thermal pathway and its rate is constant within each
segment, the evaluator can instead interpolate its rate in temperature. This includes
sealed packages, instantaneous exchange, and pathways without a humidity term. Exchanging
humidity histories retain the exact integral. Partition by zone, offset masks, freezing
state, nominal RH, RH widening distance, and the lower, inside or upper temperature-range
branch. All other rate factors are constant with respect to temperature inside a group.

Write T = c + u, |u| <= h, and divide the rate by its value at c. Its temperature dependence
is exp(a (1/(c + delta) - 1/(T + delta)) + v u), where a = Ea/R, delta is the zone offset,
and v is the widening slope. Let m be the minimum c + delta over every draw in the batch,
A the maximum |Ea|/R, and V the maximum absolute widening slope. The real-interval exponent
bound is L = A h/[m(m-h)] + V h. On a Bernstein ellipse with rho > 1 and
H = h(rho + 1/rho)/2 < m, the modulus bound is exp(K), where
K = A H/[m(m-H)] + V H. The relative Chebyshev interpolation error of degree d is bounded by
4 exp(K + L) rho^(-d)/(rho - 1). All extrema use the complete batch, not just the probes.

The implementation evaluates admissible rho values 2, 4, 8 and successive powers of two,
and chooses the smallest degree satisfying that bound. These are numerical search choices,
not storage or chemistry settings. It subtracts a conservative float64 arithmetic budget
epsilon (2 x 256 + 32) (1 + S) exp(2 L) from the configured compression tolerance, where S
bounds absolute rate-exponent contributions across the batch. The allowance covers the
node rate evaluation and the bounded-size weighted reconstruction under the usual
floating-point arithmetic model. If that budget cannot be met, a nonpositive drawn
temperature is possible, or normal finite exponent evaluation cannot be established, the
group uses the exact path. Small groups are retained exactly. Total retained nodes are
bounded by the existing 256-segment memory bound.

Chebyshev-Lobatto nodes carry the same zone, humidity and regime state as their group.
Barycentric interpolation weights reconstruct rates at every original segment temperature,
and original durations multiply them. Thus the chronological degradant calculation still
receives every original segment. No block-average pathway share is substituted.

## Error analysis and probes

For the dictionary method the compression error is zero in real arithmetic. Floating-point differences arise only
from factoring duration and algebraically cancelling the outside-RH terms of sealed
packages. The summation order of reconstructed segments is unchanged. For normal finite
intermediates, the standard arithmetic model gives relative product error bounded by
gamma_m = m epsilon / (1 - m epsilon), with exponential sensitivity proportional to the
absolute error in its exponent. Nonnegative Theta sums cannot amplify the maximum
relative error of their terms. Chronological degradant differences can amplify roundoff
near zero; degradants therefore receive an additional check against the exact evaluator.
Dictionary reuse is lossless in real arithmetic. Temperature interpolation must first
satisfy the analytic bound above. Finite probes are an additional check, not the basis of
a uniform error claim.

For every batch, select draw indices at ranks floor((n - 1) q), q in
{0, 1/4, 1/2, 3/4, 1}, separately for every pathway parameter, both widening variates,
and every zone's temperature offset, RH offset and ln multiplier. Include indices 0 and
n - 1. Stable sorting resolves equal values; union the indices and sort them numerically.
The extrema therefore span each drawn marginal support, including anchor correction zones.
This is deterministic, draws no new random numbers and does not alter Monte Carlo streams.

Evaluate the reference and dictionary histories on those draws. The reported error is
the maximum relative difference over every pathway's Theta and every degradant amount.
If both exact and compressed values are zero, the error is zero. A nonzero approximation
against exact zero rejects compression and reports a null error. An error above
`numerics.compression_relative_tolerance` also rejects compression. There is no change
to that setting, the oracle tolerance, or any acceptance rule.

Every batch and every assay prefix is checked. If any check fails, the pipeline repeats
the final estimate using only the exact evaluator and the original deterministic batch
streams. Earlier compressed batches are not retained in such an estimate. Traces report
`history_evaluator`, the largest `compression_probe_relative_error` across checks, and
`compression_fell_back`. An exact evaluation because no dictionary reduces the history
has error zero and is not counted as a failed probe.

## Threads and forecasts

Work is submitted in fixed 256-segment chunks to a thread pool. The configured thread
count changes scheduling only. Results are consumed in ascending chunk order, with a
bounded pending queue. The reduction and degradant accumulation are serial and ordered.
The unit test compares complete result arrays bit for bit at 1, 2, 4 and automatic threads.

Forecasts retain the estimate's DrawBlocks, effective initial content and cumulative
destructive and racemization Theta. They do not redraw parameters or reevaluate any past
segment. The microclimate boundary state at as_of is computed once and projection windows
are shared across draw chunks. Forecast calculations omit unused degradant attribution.
Exact partial-window integration and periodic-window reuse remain in place.

This retained-state path serves `assess_lot` and `assess_inventory`. The standalone
`forecast_lot` API receives a serialized summary without draw state and reconstructs the
estimate before entering this path. `docs/requests/round2-CX-B.md` proposes an explicit
state-carrying API to remove that limitation without a hidden process-global cache.

The humidity kernel also shares each segment's quadrature decay factors across all draws.
It uses the same accepted Gauss-Legendre ladder, choosing a rung sufficient for the largest
span in that segment. This avoids recomputing a parameter-independent exponential for every
draw. Oracle tests continue to check the public exact path at the specified tolerance.

## Measured performance

The initial day-100 profiles preceded the performance implementation. Cumulative function
costs overlap and must not be added.

The acetaminophen assessment and its profile completed. Its harness then failed while
printing a nonexistent top-level `n_batches` attribute; the retained profile records 25
adaptive batch calls. Later harnesses read `estimate.monte_carlo.n_batches` correctly.

| Lot | assess_lot seconds | Batches | Estimate seconds | Forecast seconds | Dominant costs |
|---|---:|---:|---:|---:|---|
| lot-apap-001 | 324.624 | 25 | 157.033 | 167.113 | Humidity integral 208.998 s, including quadrature 164.251 s; accumulation 298.724 s. |
| lot-pmz-inj-001 | 114.344 | 36 | 73.092 | 40.840 | Thermal parts 35.714 s; cumulative sums 9.942 s; accumulation 93.751 s. |

The completed benchmark runs all demo lots through `assess_inventory` sequentially in
one process, at the fixture's exact timestamp strings. It uses the committed settings,
including automatic worker threads (8 CPUs on this host), and sets native BLAS/OpenMP
threads to 1. Reaction-classes and storage-zones are version 0.5.0; engine-settings is
0.3.0. These are shared-host timings, with verification work also running during part of
the benchmark. Totals include result serialization and output-file writing. Per-lot
measurements directly wrap each assessment, including its forecasts.

The earlier profiles used different calibration and timestamp seed material. They locate
the original costs but do not provide a controlled speedup comparison with this benchmark.

Each table cell below is seconds / batches. For the two amoxicillin/clavulanate lots,
batch counts are amoxicillin, clavulanic acid, in that order.

| Lot | Day 100 | Day 250 | Day 400 |
|---|---:|---:|---:|
| lot-amc-001 | 121.68 / 45,2 | 251.15 / 6,100 | 61.20 / 6,8 |
| lot-amc-002 | 923.73 / 81,76 | 575.37 / 2,84 | 923.40 / 6,100 |
| lot-apap-001 | 247.69 / 58 | 14.09 / 2 | 18.85 / 2 |
| lot-apap-002 | 22.80 / 2 | 53.53 / 6 | 21.35 / 2 |
| lot-asa-001 | 184.26 / 62 | 401.44 / 100 | 11.89 / 2 |
| lot-epi-ai-001 | 3.28 / 2 | 5.92 / 2 | 11.93 / 3 |
| lot-epi-ai-002 | 3.26 / 2 | 5.52 / 2 | 8.19 / 2 |
| lot-epi-syr-001 | 7.07 / 5 | 5.89 / 2 | 8.29 / 2 |
| lot-ibu-001 | 220.88 / 88 | 16.86 / 5 | 18.08 / 4 |
| lot-levo-001 | 103.13 / 66 | 6.94 / 3 | 6.93 / 2 |
| lot-pmz-inj-001 | 92.67 / 63 | 246.72 / 100 | 363.73 / 100 |
| lot-pmz-tab-001 | 331.22 / 100 | 36.56 / 6 | 18.92 / 2 |
| lot-vitc-001 | 329.23 / 78 | 13.20 / 2 | 43.63 / 5 |

| Day | Total seconds | Total batches | Compressed estimates | Exact estimates without a failed probe | Failed-probe fallbacks | Largest probe relative error |
|---|---:|---:|---:|---:|---:|---:|
| 100 | 2591.421583833988 | 730 | 7 | 8 | 0 | 3.3648199449022743e-12 |
| 250 | 1634.4597775830189 | 422 | 7 | 8 | 0 | 4.8301494963694365e-11 |
| 400 | 1518.44568574999 | 246 | 7 | 8 | 0 | 3.8722167902913496e-11 |

The 120-second day-100 and 300-second day-400 targets are missed. Day 100 alone exceeds
the 900-second end-to-end budget. Exchanging humidity histories remain exact and dominate
much of the cost; future projections also remain expensive. No setting, tolerance or
acceptance rule was changed to improve these measurements.
