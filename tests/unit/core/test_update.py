"""Unit tests of pharmacy.update (spec 10): the Gaussian assay likelihood, weights and ESS,
the two-tail conflict probability, the re-anchor plan and the inspection override, with a
decision table for every branch."""

import math

import numpy as np
import pytest
from scipy.stats import norm

from pharmacy import update
from pharmacy.config import ConfigBundle
from pharmacy.errors import InputError, ObservationError
from pharmacy.types import AssayObservation, InspectionObservation, UpdateSettings, iso_to_seconds


@pytest.fixture(scope="module")
def settings(bundle: ConfigBundle) -> UpdateSettings:
    return bundle.settings["update"]


def _assay(
    assay_id: str = "a1",
    *,
    value: float = 95.0,
    sd: float = 1.0,
    site: str = "ground",
    at: str = "2028-06-01T00:00:00Z",
    measurand: str = "total",
) -> AssayObservation:
    return AssayObservation(
        assay_id,
        "lot-1",
        "api",
        at,
        iso_to_seconds(at),
        measurand,  # type: ignore[arg-type]
        value,
        sd,
        site,  # type: ignore[arg-type]
        "hplc",
        "lab",
        "",
    )


def _inspection(inspection_id: str, at: str, finding: str, lot_id: str = "lot-1") -> InspectionObservation:
    return InspectionObservation(inspection_id, lot_id, at, iso_to_seconds(at), finding, "crew", "")  # type: ignore[arg-type]


def test_site_and_total_sd(settings: UpdateSettings) -> None:
    ground = settings["ground_inter_laboratory_sd_percent"]["value"]
    onboard = settings["onboard_method_sd_percent"]["value"]
    assert update.site_sd_percent(_assay(), settings) == ground
    assert update.site_sd_percent(_assay(site="onboard"), settings) == onboard
    assert update.total_sd_percent(_assay(sd=2.0, site="onboard"), settings) == pytest.approx(
        math.hypot(2.0, onboard)
    )
    for bad in (_assay(sd=0.0), _assay(sd=math.nan), _assay(value=math.inf)):
        with pytest.raises(ObservationError):
            update.total_sd_percent(bad, settings)


def test_log_likelihood_is_the_gaussian_density(settings: UpdateSettings) -> None:
    predicted = np.array([94.0, 95.0, 97.5])
    first = _assay("a1", value=95.0, sd=0.8)
    second = _assay("a2", value=93.0, sd=1.5, site="onboard")
    predictions = [update.AssayPrediction("a2", predicted - 1.0), update.AssayPrediction("a1", predicted)]
    got = update.assay_log_likelihood([second, first], predictions, settings)
    sd1 = update.total_sd_percent(first, settings)
    sd2 = update.total_sd_percent(second, settings)
    expected = norm.logpdf(95.0, predicted, sd1) + norm.logpdf(93.0, predicted - 1.0, sd2)
    assert np.allclose(got, expected, rtol=1e-14, atol=0.0)


def test_log_likelihood_errors(settings: UpdateSettings) -> None:
    good = update.AssayPrediction("a1", np.array([95.0, 96.0]))
    with pytest.raises(ObservationError):
        update.assay_log_likelihood([], [good], settings)
    with pytest.raises(ObservationError):
        update.assay_log_likelihood([_assay("a9")], [good], settings)
    for bad in (np.array([]), np.array([[95.0]]), np.array([95.0, math.nan])):
        with pytest.raises(ObservationError):
            update.assay_log_likelihood([_assay()], [update.AssayPrediction("a1", bad)], settings)
    with pytest.raises(ObservationError):
        update.assay_log_likelihood(
            [_assay("a1"), _assay("a2")], [good, update.AssayPrediction("a2", np.array([95.0]))], settings
        )


def test_weights_and_ess() -> None:
    weights = update.normalized_weights(np.array([-1000.0, -1000.0 + math.log(3.0)]))
    assert weights.tolist() == pytest.approx([0.25, 0.75], rel=1e-14)
    assert update.effective_sample_size(np.ones(10)) == pytest.approx(10.0)
    assert update.effective_sample_size(np.array([1.0, 0.0, 0.0])) == 1.0
    assert update.effective_sample_size(weights) == pytest.approx(1.0 / (0.25**2 + 0.75**2))
    for bad in (np.array([]), np.zeros((1, 1)), np.array([math.inf])):
        with pytest.raises(InputError):
            update.normalized_weights(bad)
    for bad in (np.array([]), np.zeros((1, 1)), np.array([-1.0, 2.0]), np.array([math.nan]), np.zeros(3)):
        with pytest.raises(InputError):
            update.effective_sample_size(bad)


def test_conflict_probability() -> None:
    prior = np.array([96.0, 96.0])
    assert update.conflict_probability(_assay(value=96.0), prior, 1.0) == 1.0
    assert update.conflict_probability(_assay(value=94.0), prior, 1.0) == pytest.approx(
        2 * norm.cdf(-2.0), rel=1e-13
    )
    assert update.conflict_probability(_assay(value=98.0), prior, 1.0) == pytest.approx(
        2 * norm.cdf(-2.0), rel=1e-13
    )
    mixture = np.array([90.0, 100.0])
    expected = 2 * min(np.mean(norm.cdf((99.0 - mixture) / 2.0)), np.mean(norm.sf((99.0 - mixture) / 2.0)))
    assert update.conflict_probability(_assay(value=99.0), mixture, 2.0) == pytest.approx(expected, rel=1e-13)
    # Far tails do not cancel: the upper tail comes from its own CDF.
    assert update.conflict_probability(_assay(value=150.0), prior, 1.0) == pytest.approx(
        2 * norm.sf(54.0), rel=1e-10
    )
    for sd in (0.0, math.nan):
        with pytest.raises(ObservationError):
            update.conflict_probability(_assay(), prior, sd)
    for bad in (np.array([]), np.array([[96.0]]), np.array([math.inf])):
        with pytest.raises(ObservationError):
            update.conflict_probability(_assay(), bad, 1.0)


def test_plan_reanchor_picks_the_latest_then_the_highest_id(settings: UpdateSettings) -> None:
    early = _assay("a3", value=97.0, at="2028-01-01T00:00:00Z")
    late = _assay("a1", value=93.0, sd=0.5, at="2028-06-01T00:00:00Z", site="onboard")
    tie = _assay("a2", value=94.0, at="2028-06-01T00:00:00Z")
    plan = update.plan_reanchor([early, late], settings)
    assert (plan.assay_id, plan.mean_percent, plan.anchor_time_s) == ("a1", 93.0, late.measured_at_s)
    assert plan.sd_percent == update.total_sd_percent(late, settings)
    assert update.plan_reanchor([late, tie, early], settings).assay_id == "a2"
    with pytest.raises(ObservationError):
        update.plan_reanchor([], settings)


@pytest.mark.parametrize(
    ("inspections", "expected"),
    [
        ([], None),
        ([("i1", "2028-01-01T00:00:00Z", "no_change")], None),
        ([("i1", "2028-01-01T00:00:00Z", "color_change")], "i1"),
        ([("i2", "2028-02-01T00:00:00Z", "color_change"), ("i1", "2028-01-01T00:00:00Z", "no_change")], "i2"),
        # A later no_change never clears an earlier finding; the earliest finding wins.
        (
            [
                ("i1", "2028-01-01T00:00:00Z", "cracking"),
                ("i2", "2028-02-01T00:00:00Z", "no_change"),
                ("i3", "2028-03-01T00:00:00Z", "color_change"),
            ],
            "i1",
        ),
        # Same instant: the lower inspection id.
        (
            [("i9", "2028-01-01T00:00:00Z", "color_change"), ("i4", "2028-01-01T00:00:00Z", "precipitate")],
            "i4",
        ),
        # After as_of: ignored.
        ([("i1", "2028-09-01T00:00:00Z", "color_change")], None),
    ],
)
def test_inspection_override(inspections: list[tuple[str, str, str]], expected: str | None) -> None:
    observations = [_inspection(*item) for item in inspections]
    observations.append(_inspection("other", "2027-12-01T00:00:00Z", "color_change", lot_id="lot-2"))
    found = update.inspection_override(observations, "lot-1", iso_to_seconds("2028-06-01T00:00:00Z"))
    assert (None if found is None else found.inspection_id) == expected
