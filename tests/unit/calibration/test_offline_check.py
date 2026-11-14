"""The offline approximation must integrate the final assay error, without clipping."""

import math
from typing import Any

import pytest
from scipy.stats import norm

from pharmacy.errors import ComputationError, InputError
from tests.unit.calibration import offline_check as check


def test_negligible_loss_recovers_gaussian_assays() -> None:
    dist = check._Distribution(-1000, 1, 100, 1, 2)
    for p in (0.05, 0.5, 0.95):
        value = check._quantile(dist, p, 1e-10)
        assert value == pytest.approx(100 + math.sqrt(5) * norm.ppf(p), abs=1e-8)
    assert check._quantile(dist, 0.95, 1e-10) > 100


def test_predictive_input_and_integration_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    good = check._Distribution(-5, 1, 100, 1, 1)
    for p, tolerance in ((0, 1e-9), (0.5, 0)):
        with pytest.raises(InputError):
            check._quantile(good, p, tolerance)
    for bad in (check._Distribution(math.nan, 1, 100, 1, 1), check._Distribution(-5, 1, 100, 0, 1)):
        with pytest.raises(InputError):
            check._quantile(bad, 0.5, 1e-9)

    def inaccurate(*args: Any, **kwargs: Any) -> tuple[float, float]:
        return 0.5, 1

    monkeypatch.setattr(check, "quad", inaccurate)
    with pytest.raises(ComputationError, match="error bound"):
        check._quantile(good, 0.5, 1e-9)
