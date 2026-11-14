"""Pass rules of the validation datasets (spec 15.5), fixed before any engine code runs.

A dataset that fails its rule is a finding reported in docs/VALIDATION-REPORT.md with the
failed rows; the rule is never changed to make it pass.
"""

from collections.abc import Mapping
from typing import Final

from scipy import stats

INTERVAL_PROBABILITY: Final[float] = 0.90
"""Nominal coverage of the engine's reported interval (q05 to q95)."""

BINOMIAL_RULE_QUANTILE: Final[float] = 0.05
"""Covered count k must satisfy k >= binom.ppf(0.05, n, 0.90)."""

V9_UPPER_BOUND_LIMIT: Final[float] = 0.01
V9_CENTRAL_LIMIT: Final[float] = 1e-4
TV_RAD_2_LIMIT_AT_1_GY: Final[float] = 1e-4

SF2_DOCUMENTED_RANGES: Final[dict[int, tuple[int, int]]] = {2: (9, 11), 4: (23, 24)}
"""TV-SF-2 and V2: flight failure counts at payloads 2 and 4, from the Table S II grid
count to the Table III count (report only)."""


def minimum_covered(n: int) -> int:
    """The binomial rule of 15.5: the smallest covered count that passes for n rows."""
    if n <= 0:
        raise ValueError("n must be positive")
    return int(stats.binom.ppf(BINOMIAL_RULE_QUANTILE, n, INTERVAL_PROBABILITY))


def failed_formulations(
    medians: Mapping[int, float], lower_limits: Mapping[int, float], components: Mapping[int, int]
) -> dict[int, bool]:
    """V2: each row uses its own USP limit; any failing component fails its formulation once."""
    formulations: dict[int, bool] = {}
    for row in sorted(medians):
        formulation = components.get(row, row)
        failed = medians[row] < lower_limits[row]
        formulations[formulation] = formulations.get(formulation, False) or failed
    return formulations
