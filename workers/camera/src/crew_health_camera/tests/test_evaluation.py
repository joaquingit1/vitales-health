"""E06-03: held-out error, bias, rejection and coverage, and the optional identity experiment kept apart (R46, V39,
V80). Expected numbers are computed by hand in each test."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from crew_health_camera.evaluation.identity import Comparison, identity_report
from crew_health_camera.evaluation.study import Trial, evaluate, split


def trial(
    person: str, reference: int | None, camera: int | None, *, condition: str = "rest", views: int = 1
) -> Trial:
    return Trial(
        person, condition, "pulse",
        None if reference is None else Decimal(reference),
        None if camera is None else Decimal(camera),
        None if camera is not None else "too_much_movement",
        views,
    )  # fmt: skip


@pytest.mark.trace("E06-03", "R46", "V80")
def test_held_out_error_bias_rejection_and_coverage() -> None:
    trials = [
        trial("p1", 70, 72),
        trial("p1", 80, 77, views=2),
        trial("p2", 90, None),
        trial("p2", None, 90),
        trial("p3", 60, 61),
    ]
    report = evaluate(trials, held_out=frozenset({"p1", "p2"}), code="pulse")
    assert (report.status, report.participants, report.trials, report.accepted) == ("measured", 2, 3, 2)
    assert report.mae == Decimal("2.5") and report.bias == Decimal("-0.5")
    assert report.rejection_rate == Fraction(1, 3) and report.coverage == Fraction(2, 3)
    assert report.multi_view_trials == 1


@pytest.mark.trace("E06-03", "V80")
def test_no_participants_or_no_accepted_reading_is_not_measured_never_zero() -> None:
    empty = evaluate([], held_out=frozenset(), code="pulse")
    assert empty.status == "not_measured" and empty.mae is None and empty.rejection_rate is None
    refused = evaluate([trial("p1", 70, None)], held_out=frozenset({"p1"}), code="pulse")
    assert refused.status == "not_measured" and refused.mae is None and refused.rejection_rate == Fraction(1)


@pytest.mark.trace("E06-03", "PRD-616")
def test_conditions_are_reported_separately() -> None:
    trials = [trial("p1", 12, 13, condition="paced"), trial("p1", 15, 15, condition="natural")]
    paced = evaluate(trials, held_out=frozenset({"p1"}), code="pulse", condition="paced")
    assert paced.trials == 1 and paced.mae == Decimal(1)


@pytest.mark.trace("E06-03")
def test_split_is_deterministic_disjoint_and_never_empty() -> None:
    people = [f"p{i}" for i in range(10)]
    development, held_out = split(people, held_out_fraction=Fraction(3, 10), seed="venue-1")
    assert (development, held_out) == split(
        reversed(people), held_out_fraction=Fraction(3, 10), seed="venue-1"
    )
    assert len(held_out) == 3 and not development & held_out and development | held_out == set(people)
    assert len(split(["p1", "p2"], held_out_fraction=Fraction(1, 10), seed="s")[1]) == 1
    assert split([], held_out_fraction=Fraction(1, 2), seed="s") == (frozenset(), frozenset())
    with pytest.raises(ValueError):
        split(people, held_out_fraction=Fraction(1), seed="s")


@pytest.mark.trace("E06-03", "V39")
def test_identity_experiment_reports_errors_and_uncertainty_as_rejection() -> None:
    report = identity_report(
        [
            Comparison("a", "a", 0.9),
            Comparison("a", "a", 0.4),
            Comparison("a", "b", 0.8),
            Comparison("a", "c", 0.1),
            Comparison("b", "b", None),
        ],
        threshold=0.5,
    )
    assert report.status == "measured" and report.comparisons == 5
    assert report.false_match_rate == Fraction(1, 2) and report.false_non_match_rate == Fraction(1, 2)
    assert report.rejection_rate == Fraction(1, 5)
    assert identity_report([], threshold=0.5).status == "not_measured"
    only_impostors = identity_report([Comparison("a", "b", 0.1)], threshold=0.5)
    assert only_impostors.false_non_match_rate is None
