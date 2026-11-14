# CX-C round 2 requests and implementation notes

Status: R1 and R2 accepted (COORDINATOR-DECISIONS section 12); calibration now uses the same derivative rule.

## R1. Packaged-anchor humidity variance in spec 9.3

Proposal, agreeing with CX-D D3: define
`d_RH = -d log(mean(exp(B (RH_internal(t) - RH*)))) / dB` for a packaged
anchor. It is `RH* - RH_weighted`, with weights proportional to instantaneous
rate. At B = 0 use its continuous limit, the time mean of internal RH. Keep
the specified temperature variance term unchanged.

Reason: spec 9.3 defines the mean through Theta/duration, but does not define
RH0 in its variance term for a changing internal RH. Using a rate-equivalent
RH reproduces the mean but is not the derivative of that mean when B is
nonzero. The derivative is the first-order propagation of the stated mean
and reduces to the literal constant-RH formula exactly.

Interim: use the derivative. The engine microclimate trace and humidity
integral supply the mean. Its analytic derivative is
`J_x = tau (exp(x) - exp(x exp(-duration/tau))) / x`, with x = B times the
initial internal-to-ambient RH difference. Evaluate this with expm1/exprel
and the continuous B = 0 limit, with no fitted numerical constants. Direct
quadrature of the weighted internal RH independently checks the derivative.
This supersedes this file's initial rate-equivalent-RH interim proposal.

Calibration currently uses the rate-equivalent RH for this variance term
(`pharmacy/calibration.py::_conversion_variance`). CX-CAL should adopt the
same derivative if the coordinator accepts this clarification. Chemistry
cannot change that owned file or its derived configuration values. CX-CAL
has since implemented the continuous B = 0 limit. The nonzero-B variance
rule still uses rate-equivalent RH and remains part of this clarification.

## R2. Anchor helper needs configuration context

Proposal: document a required keyword-only `bundle: ConfigBundle` argument on
`chemistry.parameters.anchor_at_reference`. Its positional arguments and
`(mean, sd)` return stay unchanged.

Reason: complete conversion now requires the configured package and zone,
including its current multiplier. The old helper accepted only an anchor and
class parameters and could not resolve these references.

Interim: require that explicit keyword in this chemistry-owned helper and its
callers. Do not load global configuration or silently skip storage correction.
No externally specified scalar signature or runtime type is changed.

## N1. Total-rate priors convert once, offline

Task 1's wording asks for conversion before splitting. Binding spec 9.3 and
9.5 and BUILD-PLAN section 4 assign that conversion to CX-CAL, including
per-row package physics and variance terms. The stored `ln_k_ref_per_s` is
already at the engine reference. Chemistry therefore splits the stored prior
unchanged. Its storage fields remain calibration provenance, never a second
runtime conversion. A regression test enforces that boundary. CX-C changes
no calibration values or files.

## R3. Independent oracle self-test uses the wrong comparison variance

Observed in `test_anchor_correlated_variance_and_covariance_algebra` at
`tests/oracle/test_oracle_self_chemistry.py:237`: with s_p = 0.9,
v_a = 0.25 and s_Z = 0.4, the implemented oracle returns effective variance
0.2197540983606557377. The assertion expects 0.1869672131147540984.

Proposal for CX-D: the exact posterior variance of the measured flight rate
is `(s_p^2 + s_Z^2) v_a / (s_p^2 + v_a + s_Z^2)`. Add the spec's excess
`s_Z^4 / (s_p^2 + v_a + s_Z^2)` to this quantity. The assertion currently
uses only `s_p^2 v_a` in the first numerator, omitting `s_Z^2 v_a`.

Directly expanding the binding draw formula gives
`s_post^2 + s_Z^2 - 2 lambda s_Z^2`, which equals the oracle's result.
The discrepancy is exactly `0.16 * 0.25 / 1.22 = 0.0327868852459016393`.
No chemistry change, tolerance change or spec change is warranted. This
file records the finding because the oracle test belongs to CX-D.

Status: CX-D has corrected the comparison numerator to `(s_p^2 + s_Z^2) v_a`.
The corrected chemistry oracle self-tests and agreement tests pass (73 tests).
No oracle test was changed by CX-C.
