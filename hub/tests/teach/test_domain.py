"""Domain rules of the teach module: the agreement rule, rounding, the completeness rule CR1 to CR4, the guided check
state machine and the coaching derivation (E30 3.3, 3.4, 3.7, 3.9). Example based golden cases are the CONTRACTS 20
vectors, checked independently of the catalog; property tests cover the rule engines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health.teach.application.content import parse_catalog
from crew_health.teach.domain.agreement import (
    comparable_origins,
    display_decimals,
    evaluate_count_agreement,
    exact_text,
    parse_exact,
    rounded,
)
from crew_health.teach.domain.capture import (
    NS_PER_SECOND,
    Beat,
    ClockReading,
    CountWindow,
    completeness,
    count_per_minute,
)
from crew_health.teach.domain.catalog import CompressionRateCheck, SkillDefinition
from crew_health.teach.domain.coaching import RawRateSample, accept_raw, derive
from crew_health.teach.domain.errors import BeatRefused, IncompleteCapture, InvalidTransition, InvalidValue
from crew_health.teach.domain.guided_check import CameraResult, GuidedCheck, presence

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
TEN = Fraction(10)


def skill(skill_id: str = "pulse_check_wrist") -> SkillDefinition:
    found = parse_catalog((ROOT / "config/skills-catalog.json").read_bytes()).skill(skill_id)
    assert found is not None
    return found


def window(seconds: int = 60) -> CountWindow:
    return CountWindow(1, T0, 0, "gen1", seconds, T0)


def beats(times_s: list[Fraction]) -> dict[int, Beat]:
    return {
        i: Beat(i, int(t * NS_PER_SECOND), T0 + timedelta(seconds=float(t)), "h")
        for i, t in enumerate(times_s, start=1)
    }


# Agreement (3.4) -------------------------------------------------------------------------------------------------


@pytest.mark.trace("T30-01", "T30-02", "T30-03", "I30-06")
def test_contract_vectors() -> None:
    agree = evaluate_count_agreement(Fraction(92), Fraction(96), TEN)
    assert (agree.result, agree.difference, agree.phrase_key) == ("agree", Fraction(-4), "ok")
    disagree = evaluate_count_agreement(Fraction(65), Fraction(96), TEN)
    assert (disagree.result, disagree.difference, disagree.phrase_key) == (
        "disagree",
        Fraction(-31),
        "too_slow",
    )
    assert evaluate_count_agreement(Fraction(106), Fraction(96), TEN).result == "agree"  # inclusive
    assert evaluate_count_agreement(Fraction(107), Fraction(96), TEN).phrase_key == "too_fast"


@pytest.mark.trace("I30-07")
def test_no_reference_without_camera_or_tolerance() -> None:
    assert evaluate_count_agreement(Fraction(92), None, TEN).result == "no_reference"
    pending = evaluate_count_agreement(Fraction(92), Fraction(92), None)
    assert (pending.result, pending.difference) == ("no_reference", None)


@pytest.mark.trace("I30-06")
@given(st.integers(0, 400), st.integers(0, 400), st.integers(0, 50), st.integers(1, 120))
def test_agreement_property(beats_n: int, camera: int, tolerance: int, seconds: int) -> None:
    count = count_per_minute(beats_n, seconds)
    outcome = evaluate_count_agreement(count, Fraction(camera), Fraction(tolerance))
    assert outcome.difference == count - camera
    assert (outcome.result == "agree") == (abs(count - camera) <= tolerance)
    if outcome.result == "disagree":
        assert outcome.phrase_key == ("too_slow" if count < camera else "too_fast")


@pytest.mark.trace("T30-24")
@given(st.integers(-(10**9), 10**9), st.integers(1, 10**6), st.integers(0, 6))
def test_rounding_is_half_even(numerator: int, denominator: int, decimals: int) -> None:
    value = Fraction(numerator, denominator)
    expected = Decimal(round(value * 10**decimals)).scaleb(
        -decimals
    )  # Fraction.__round__ rounds half to even
    assert rounded(value, decimals) == expected
    assert rounded(Fraction(5, 2), 0) == 2 and rounded(Fraction(7, 2), 0) == 4
    assert parse_exact(exact_text(value)) == value


def test_rounding_refuses_out_of_range_decimals() -> None:
    with pytest.raises(InvalidValue):
        rounded(Fraction(1), 7)


@pytest.mark.trace("H3")
def test_only_equal_origins_compare() -> None:
    assert comparable_origins("LIVE", "LIVE") and comparable_origins("SIM", "SIM")
    for count, camera in (
        ("LIVE", "SIM"),
        ("LIVE", "REPLAY"),
        ("SIM", "LIVE"),
        ("REPLAY", "REPLAY"),
        ("LIVE", None),
    ):
        assert not comparable_origins(count, camera)


# Completeness CR1 to CR4 (3.3) -----------------------------------------------------------------------------------


@pytest.mark.trace("T30-18", "T30-34", "H4")
@pytest.mark.parametrize(
    ("times", "reasons"),
    [
        ([Fraction(k, 2) for k in range(1, 120)], set()),
        ([], {"no_beats"}),
        ([Fraction(k, 2) for k in range(8, 120)], {"late_start"}),
        ([Fraction(k, 2) for k in range(1, 60)], {"lost_end"}),
        ([Fraction(k, 2) for k in range(1, 120) if not 40 <= k <= 50], {"inner_gap"}),
    ],
)
def test_completeness_reasons(times: list[Fraction], reasons: set[str]) -> None:
    assert completeness(beats(times), window(), frozenset(), max_gap_seconds=3) == reasons


def test_sequence_gap_and_flags() -> None:
    complete = beats([Fraction(k, 2) for k in range(1, 120)])
    del complete[7]
    assert completeness(complete, window(), frozenset(), max_gap_seconds=3) == {"sequence_gap"}
    flagged = completeness(complete, window(), frozenset({"hub_restart", "other"}), max_gap_seconds=3)
    assert flagged == {"sequence_gap", "hub_restart"}


@pytest.mark.trace("H4")
@given(st.integers(20, 300))
def test_even_taps_at_any_plausible_rate_are_complete(rate: int) -> None:
    times = [Fraction(60 * (2 * i - 1), 2 * rate) for i in range(1, rate + 1)]
    assert completeness(beats(times), window(), frozenset(), max_gap_seconds=3) == frozenset()
    assert count_per_minute(rate, 60) == rate


# State machine (3.2) ---------------------------------------------------------------------------------------------


def new_check() -> GuidedCheck:
    pulse = skill()
    return GuidedCheck(
        "gck_1", 1, "pulse_check_wrist", "pulse_count_against_cameras", None, "tomas", "mara", "mara", "LIVE",
        None, None, None, None, "teaching", 1, [], None, None, None, None, None, None, None, None, None, None,
        pulse.count_check is not None and pulse.count_check.experimental, {}, "rel_1", T0, "ses_1",
    )  # fmt: skip


def reading(seconds: float, generation: str = "gen1", wall_offset_s: float = 0) -> ClockReading:
    ns = int(seconds * NS_PER_SECOND)
    return ClockReading(T0 + timedelta(seconds=seconds + wall_offset_s), ns, generation)


def counting(check: GuidedCheck) -> SkillDefinition:
    pulse = skill()
    for step_no in (1, 2, 3):
        check.step_done(pulse, step_no, reading(0))
    check.start_count(pulse, reading(0), T0)
    return pulse


@pytest.mark.trace("I30-03")
def test_disagree_goes_to_retry_never_checked() -> None:
    check = new_check()
    pulse = counting(check)
    for i in range(1, 66):
        check.accept_beat(i, "h", T0, reading(i * 0.9), max_beats_per_second=5, clock_tolerance_ms=500)
    check.answer(pulse, "regular", reading(61))
    count = check.prepare_finish(pulse, reading(61), max_gap_seconds=3)
    assert count == 65
    camera = CameraResult("obs_c", Fraction(96), "/min", None, "LIVE")
    check.apply_finish(
        result="disagree", difference=Fraction(-31), count=count, camera=camera, result_text="t",
        observation_id="obs_1", r=reading(61),
    )  # fmt: skip
    assert (
        check.state == "retry"
        and check.observation_id is None
        and check.allowed_actions(pulse, reading(61)) == ["retry", "cancel"]
    )
    check.retry(pulse, reading(70), T0 + timedelta(seconds=70), max_attempts=2)
    assert (
        str(check.state) == "counting" and check.attempt is not None and check.attempt.window.attempt_no == 2
    )
    assert check.result is None and check.helper_answer is None and check.previous_answer_id is None
    check.state = "retry"
    with pytest.raises(InvalidTransition):
        check.retry(pulse, reading(200), T0, max_attempts=2)


def test_beat_refusals_and_resends() -> None:
    check = new_check()
    counting(check)
    tol = {"max_beats_per_second": 1, "clock_tolerance_ms": 500}
    with pytest.raises(BeatRefused) as earlier:
        check.accept_beat(1, "h", T0 - timedelta(seconds=1), reading(1), **tol)
    assert not earlier.value.recorded
    assert check.accept_beat(1, "h", T0, reading(1), **tol) is not None
    assert check.accept_beat(1, "h", T0, reading(1.5), **tol) is None  # a resend changes nothing
    with pytest.raises(BeatRefused) as outside:
        check.accept_beat(2, "h", T0, reading(60), **tol)
    assert outside.value.reason == "outside_count_window"
    for i in range(2, 61):
        check.accept_beat(i, "h", T0, reading(i * 0.5), **tol)
    with pytest.raises(BeatRefused) as full:
        check.accept_beat(61, "h", T0, reading(59), **tol)
    assert full.value.reason == "beat_capacity"
    with pytest.raises(BeatRefused) as conflict:
        check.accept_beat(1, "other", T0, reading(59.5), **tol)
    assert (
        conflict.value.recorded and check.attempt is not None and "sequence_conflict" in check.attempt.flags
    )


def test_clock_divergence_is_flagged_and_beat_time_is_anchored() -> None:
    check = new_check()
    counting(check)
    beat = check.accept_beat(
        1, "h", T0, reading(1, wall_offset_s=30), max_beats_per_second=5, clock_tolerance_ms=500
    )
    assert beat is not None and beat.at_utc == T0 + timedelta(seconds=1)
    assert check.attempt is not None and check.attempt.flags == {"clock_discontinuity"}


def test_finish_guards() -> None:
    check = new_check()
    pulse = skill()
    with pytest.raises(InvalidValue):
        check.step_done(pulse, 2, reading(0))
    with pytest.raises(InvalidValue):
        check.start_count(pulse, reading(0), T0)
    pulse = counting(check)
    with pytest.raises(InvalidTransition):
        check.prepare_finish(pulse, reading(30), max_gap_seconds=3)
    with pytest.raises(InvalidTransition):
        check.prepare_finish(pulse, reading(61), max_gap_seconds=3)  # the question step is pending
    check.answer(pulse, "not_sure", reading(61))
    with pytest.raises(IncompleteCapture) as incomplete:
        check.prepare_finish(pulse, reading(61), max_gap_seconds=3)
    assert incomplete.value.fields == ("/beats/no_beats",)
    check.cancel()
    assert (
        check.state == "cancelled"
        and check.visible_beats() == []
        and check.allowed_actions(pulse, reading(62)) == []
    )
    with pytest.raises(InvalidTransition):
        check.cancel()


def test_presence_mapping() -> None:
    assert [presence(a) for a in ("regular", "irregular", "not_sure")] == ["present", "absent", "unknown"]
    with pytest.raises(InvalidValue):
        presence("maybe")


# Coaching (3.7) --------------------------------------------------------------------------------------------------


def cpr() -> CompressionRateCheck:
    check = skill("cpr").compression_check
    assert check is not None
    return check


@pytest.mark.trace("I30-14", "H6", "H7")
def test_coaching_derivation() -> None:
    check = cpr()
    blind = derive(RawRateSample(T0, 10, None, "cannot_see_hands"), check)
    assert (blind.rate, blind.in_band, blind.direction, blind.phrase_key) == (None, None, None, "cannot_see")
    middle = (check.target_min + check.target_max) / 2
    assert derive(RawRateSample(T0, 10, middle, None), check).in_band is True
    assert derive(RawRateSample(T0, 10, check.target_min - 1, None), check).direction == "too_slow"
    assert derive(RawRateSample(T0, 10, check.target_max + 1, None), check).direction == "too_fast"
    with pytest.raises(InvalidValue):
        derive(RawRateSample(T0, 10, None, None), check)
    stale = T0 + timedelta(seconds=check.update_interval_seconds + 1)
    assert not accept_raw(
        RawRateSample(T0, 10, middle, None), stale, check, max_uncertainty_ms=200, last_captured=None
    )
    assert not accept_raw(
        RawRateSample(T0, 500, middle, None), T0, check, max_uncertainty_ms=200, last_captured=None
    )
    assert not accept_raw(
        RawRateSample(T0, 10, middle, None), T0, check, max_uncertainty_ms=200, last_captured=T0
    )
    assert accept_raw(
        RawRateSample(T0, 10, middle, None), T0, check, max_uncertainty_ms=200, last_captured=None
    )


@pytest.mark.trace("I30-06")
@given(
    st.integers(0, 600), st.sampled_from([15, 30, 45, 60, 90, 120]), st.integers(0, 3000), st.integers(0, 30)
)
def test_shown_numbers_agree_with_the_verdict(
    beats_n: int, seconds: int, camera_tenths: int, tolerance: int
) -> None:
    count = count_per_minute(beats_n, seconds)
    camera = Fraction(camera_tenths, 10)
    tol = Fraction(tolerance)
    outcome = evaluate_count_agreement(count, camera, tol)
    decimals = display_decimals(count, camera, tol, outcome.result, 0)
    shown_difference = Fraction(rounded(abs(count - camera), decimals))
    shown_gap = abs(Fraction(rounded(count, decimals)) - Fraction(rounded(camera, decimals)))
    if outcome.result == "agree":
        assert shown_difference <= tol and shown_gap <= tol
    else:
        assert shown_difference > tol and shown_gap > tol
