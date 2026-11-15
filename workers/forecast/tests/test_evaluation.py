from collections.abc import Sequence
from decimal import Decimal

import pytest

from crew_health_forecast.domain.evaluation import evaluate


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_hand_computed_golden_comparators() -> None:
    report = evaluate([Decimal(x) for x in ("1", "2", "3", "4", "5")], 2, 2, 2, None)
    assert report.origins == 2
    assert report.methods["persistence"].mae == Decimal("1.5")
    assert report.methods["persistence"].mae_by_step == (Decimal("1"), Decimal("2"))
    assert report.methods["rolling_mean"].mae == Decimal("2")
    assert report.methods["rolling_mean"].mae_by_step == (Decimal("1.5"), Decimal("2.5"))


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_gaps_split_segments_without_imputation() -> None:
    report = evaluate(
        [Decimal(1), Decimal(2), Decimal(3), None, None, Decimal(10), Decimal(11), Decimal(12)],
        1,
        2,
        2,
        None,
        unavailable_reason="weights_absent",
    )
    assert (report.history_days, report.missing_days, report.segments, report.origins) == (8, 2, 2, 2)
    assert report.methods["persistence"].mae == Decimal("1")
    assert report.methods["rolling_mean"].mae == Decimal("1.5")
    assert report.coverage.denominator == 0
    assert report.coverage.value is None
    assert report.model_status == "weights_absent"


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_future_cannot_change_an_origins_development_or_comparators() -> None:
    seen: list[tuple[Decimal, ...]] = []

    def forecast(context: Sequence[Decimal], horizon: int) -> list[tuple[Decimal, Decimal, Decimal]]:
        seen.append(tuple(context))
        return [(context[-1], context[-1], context[-1])] * horizon

    original = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    changed = original[:2] + [Decimal(300), Decimal(400), Decimal(500)]
    evaluate(original, 2, 2, 2, forecast)
    first = seen[0]
    seen.clear()
    evaluate(changed, 2, 2, 2, forecast)
    assert first == seen[0] == (Decimal(1), Decimal(2))
    # Recover each comparator's first-step prediction from absolute error on increasing targets.
    base_score = evaluate(original[:3], 1, 2, 2, None)
    changed_score = evaluate(changed[:3], 1, 2, 2, None)
    for name in ("persistence", "rolling_mean"):
        base_error = base_score.methods[name].mae
        changed_error = changed_score.methods[name].mae
        assert base_error is not None and changed_error is not None
        assert Decimal(3) - base_error == Decimal(300) - changed_error


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_model_failure_counts_and_coverage_denominator() -> None:
    def forecast(context: Sequence[Decimal], horizon: int) -> list[tuple[Decimal, Decimal, Decimal]]:
        if len(context) == 2:
            raise RuntimeError("failed_origin")
        if len(context) == 3:
            return [(Decimal(3), Decimal(2), Decimal(1))] * horizon
        return [(Decimal(5), Decimal(5), Decimal(5))] * horizon

    report = evaluate([Decimal(x) for x in (1, 2, 3, 4, 5, 6)], 2, 2, 2, forecast)
    assert report.origins == 3
    assert report.failures == 2
    assert report.model_status == "evaluated"
    assert report.methods["model"].mae == Decimal("0.5")
    assert report.methods["model"].mae_by_step == (Decimal("0"), Decimal("1"))
    assert report.coverage.covered_steps == 1
    assert report.coverage.denominator == report.methods["model"].scored_steps == 2
    assert report.coverage.value == Decimal("0.5")


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_failed_model_origin_is_excluded_from_every_method_so_scores_are_paired() -> None:
    def forecast(context: Sequence[Decimal], horizon: int) -> list[tuple[Decimal, Decimal, Decimal]]:
        if len(context) == 2:
            raise RuntimeError("failed_origin")
        return [(context[-1], context[-1], context[-1])] * horizon

    report = evaluate([Decimal(x) for x in (1, 2, 30, 4, 5)], 1, 2, 2, forecast)
    assert report.origins == 3
    assert report.failures == 1
    assert {score.scored_steps for score in report.methods.values()} == {2}
    assert report.methods["persistence"].mae == Decimal("13.5")
    assert report.methods["rolling_mean"].mae == Decimal("12")
    assert report.methods["model"].mae == Decimal("13.5")


@pytest.mark.trace("E14-03", "R26")
@pytest.mark.parametrize("series", [[], [None, None], [Decimal(1), None, Decimal(2)]])
def test_insufficient_evaluation_remains_unscored(series: list[Decimal | None]) -> None:
    report = evaluate(series, 2, 2, 2, None)
    assert report.origins == 0
    assert report.methods["persistence"].mae is None
    assert report.methods["persistence"].mae_by_step == (None, None)
    assert report.coverage.value is None


@pytest.mark.trace("E14-03", "R26")
@pytest.mark.parametrize(("horizon", "minimum", "window"), [(0, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_invalid_evaluation_bounds(horizon: int, minimum: int, window: int) -> None:
    with pytest.raises(ValueError, match="invalid_evaluation_bounds"):
        evaluate([], horizon, minimum, window, None)


@pytest.mark.trace("E14-03", "R26")
def test_nonfinite_evaluation_value_rejected() -> None:
    with pytest.raises(ValueError, match="nonfinite_evaluation_value"):
        evaluate([Decimal("NaN")], 1, 1, 1, None)


@pytest.mark.trace("E14-03", "R26", "VALIDATION-233")
def test_failed_model_origin_never_partially_scores() -> None:
    def forecast(context: Sequence[Decimal], horizon: int) -> list[tuple[Decimal, Decimal, Decimal]]:
        return [(Decimal(3), Decimal(3), Decimal(3)), (Decimal("1E999999999"),) * 3]

    report = evaluate([Decimal(1), Decimal(2), Decimal(3), Decimal(4)], 2, 2, 2, forecast)
    assert report.origins == report.failures == 1
    assert report.methods["model"].scored_steps == 0
    assert report.methods["model"].mae is None
    assert report.coverage.denominator == 0
    assert all(score.scored_steps == 0 for score in report.methods.values())
