# CX-CAL round 2 requests and findings

Status: CAL-01, CAL-02 and CAL-03 accepted as proposed. CAL-04 decided differently: the flight factor location is
the mean ln residual, the exact Student-t predictive pivot (COORDINATOR-DECISIONS section 12).

No contract, schema or configuration shape was changed. The following exact proposals clarify choices that
currently fit within the existing fields. The implementation and committed results use the interim rules below.
No tolerance, row selection, prior width or pass rule was selected using validation outcomes.

## CAL-01: define family Ea and B aggregation

Proposed addition to spec 9.5:

"For calibration, family Ea and B are equal-weight mixtures of the configured priors of destructive thermal
classes applicable to the family. Use the arithmetic mean of locations. Use the square root of the mean of
sigma_i squared plus (mu_i minus the family mean) squared as the family standard uncertainty. Include the
unclassified thermal class. Exclude light, radiation and racemization. Reuse solution calibration for suspension."

Reason: the contract says "family prior means" but provides class-specific priors. Solid and semisolid Ea are
identical across applicable classes. Solution hydrolysis uses 65595.4 J/mol, while oxidation uses 88000 J/mol.
There is no configured weighting over these classes. Equal weighting gives 78042.4 J/mol and sd
20208.600302181578 J/mol. Changing the class catalogue could change this aggregation.

Interim: implement exactly the proposed rule and record the resulting means and standard uncertainties in
calibration statistics. This is a modelling assumption, not a fit to validation products. No new setting was added.

## CAL-02: distinguish Student-t uncertainty from reference conversion uncertainty

Proposed addition to spec 9.5:

"Let sigma_t = sqrt(s_pooled squared times (1 + 1/n_f)) times t_ppf((1+c)/2,N-F) / normal_ppf((1+c)/2).
Store sqrt(sigma_t squared plus v_conversion_f), where v_conversion_f is the arithmetic mean of the row
conversion variances from 9.3. Record both sigma_t and v_conversion_f. Do not divide the shared calibration
condition and chemistry uncertainty by the family sample size. The central-interval equality to Student-t
applies before this independent conversion uncertainty is added."

Reason: spec 9.3 requires adding conversion variance; 9.5 also says the stored normal sd matches the Student-t
interval of the pooled product spread exactly. Both statements need an explicit composition rule. For solids,
sigma_t is 0.6273156859297896 and conversion variance is 0.0729095507804659, yielding stored sd
0.6829601164006784. The conversion component is not a variance estimated from more independent products.

Interim: implement exactly the proposed composition. The two components and total are stored separately; the
provenance states where interval equivalence applies. Conversion uses the three terms explicitly named in 9.3.
The calibration RH sd is used for predictive storage-condition uncertainty; an additional RH calibration term
is not silently inserted into the three-term 9.3 expression.

## CAL-03: remove the stale V1 flight table wording

Proposed replacement for the V1 flight procedure cell in spec 15.5:

"Same day-13 control initial assay procedure, using a declared zone from the four flight condition statistics
in station_kit rate_multiplier_calibration, with the station_kit multiplier."

Reason: the table still says "station profile setpoints", whose RH is 35 percent. The subsequent binding
paragraph and spec 9.5 require 24 percent RH from calibration statistics. The discrepancy is 11 RH points,
which is a rate factor exp(0.04 times 11) = 1.552707218511336 for a repackaged solid.

Interim: follow the binding narrative and use the recorded flight conditions, 296.15 +- 2.25 K and 24 +- 0.5
percent RH. V6 uses the station profile setpoints as required.

## CAL-04: clarify median location with Student-t predictive scaling

Proposed addition to spec 9.5:

"For flight residuals, use the median as the location and the ordinary sample standard deviation about the
arithmetic mean as s. Use sqrt(1+1/N), df=N-1, and the t-to-normal central-interval conversion. This is the
specified robust-location approximation, not an exact Student-t predictive pivot about a sample median."

Reason: the sqrt(1+1/N) Student-t formula is exact for a Gaussian sample mean and sample variance, not for a
sample median. The decision explicitly requires a median location. A future change to a median-estimation
variance model would change the statistical contract and must be a coordinator decision.

Interim: median location and ordinary sample sd, with the mandated predictive multiplier. This is recorded in
provenance. It was not changed in response to validation results.

## Findings requiring no contract change

- V1 offline ground misses: rows 2, 6, 8 and 12. Coverage 12/16 meets the 12-row threshold exactly. The flight
  approximation misses row 12 and covers 15/16. Clavulanate remains outside the family prior in both arms.
- V6 class-prior subset covers 3/5, meeting its subset threshold of 3. Ibuprofen and loratadine miss above the
  upper predictive bound. This does not establish the all-eight-row V6 engine gate.
- V6 loperamide's transcribed sd is 0.6 mg on a 2 mg label, or 30 percentage points. It is not 0.6 percent.
  The offline check preserves it; the upper measured-value interval can exceed 100 percent.
- V2 prior central predictions at days 13, 353, 596 and 880 are 0, 5, 14 and 24 failed formulations, compared
  with documented 1, 9 to 11, 17 and 23 to 24. Only the final count is in the documented range.
- Counting the transcribed measured grid strictly below each component's USP lower limit, once per formulation,
  gives 1, 9, 16 and 23. The day-596 measured-grid count 16 differs from the documented count 17. Neither the
  vector nor the strict lower-limit rule was changed.
- All 19 selected calibration products have positive losses in both arms. Only ground row 8 needs the
  nonpositive-loss rule in the complete 36-row rate listing; it is held out and cannot affect fitted values.
- Co-formulated components cross the calibration/validation split. These validation rows are not fully
  independent, as the supplied vector itself warns.

Numerical results, assumptions and reproducibility commands are in
[data/pharmacy/calibration/README.md](../../data/pharmacy/calibration/README.md),
[offline-check.json](../../data/pharmacy/calibration/offline-check.json) and
[calibration-summary.txt](../../data/pharmacy/calibration/calibration-summary.txt).
