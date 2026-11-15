"""Exposure settling: the analysis window never holds a camera's auto exposure ramp (bug found on a real laptop camera:
a still face in good light read signal_quality_too_low because the first 1.3 s were ramping)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crew_health_camera.domain.exposure import Settling
from crew_health_camera.tests.support import policy

LIMIT = policy().capture.settled_change_per_s
# The measured ramp of the policy basis: (seconds after opening, R, G, B frame mean), then flat.
RAMP: tuple[tuple[float, float, float, float], ...] = (
    (0.00, 75.9, 67.3, 65.1),
    (0.33, 83.0, 72.7, 62.9),
    (0.51, 86.1, 69.6, 55.1),
    (0.76, 94.1, 70.0, 53.8),
    (1.00, 97.0, 70.4, 53.0),
    (1.28, 97.8, 70.9, 54.8),
)


def measured(t: float) -> tuple[float, float, float]:
    for (a, *low), (b, *high) in zip(RAMP, RAMP[1:], strict=False):
        if a <= t < b:
            w = (t - a) / (b - a)
            r, g, bl = (x + (y - x) * w for x, y in zip(low, high, strict=True))
            return r, g, bl
    return RAMP[-1][1], RAMP[-1][2], RAMP[-1][3]


def first_settled(means: object, *, fps: float = 30.0, seconds: float = 6.0) -> float | None:
    settling = Settling(LIMIT)
    for i in range(int(seconds * fps)):
        t = i / fps
        if settling.settled(round(t * 1e9), means(t)):  # type: ignore[operator]
            return t
    return None


@pytest.mark.trace("E06-01", "AGENTS-1.5")
def test_no_frame_of_the_measured_auto_exposure_ramp_counts_and_the_flat_picture_after_it_does() -> None:
    settled = first_settled(measured)
    assert settled is not None
    assert (
        RAMP[-2][0] < settled < RAMP[-1][0] + 1.0
    )  # after the ramp's last steep part, within a second of its end
    assert first_settled(lambda t: (97.8, 70.9, 54.8)) == pytest.approx(
        1.0, abs=0.04
    )  # a steady camera: one second


@pytest.mark.trace("E06-01", "AGENTS-1.5")
def test_a_picture_that_keeps_changing_never_settles() -> None:
    assert first_settled(lambda t: (60.0 + 10.0 * t, 70.0, 50.0)) is None


@pytest.mark.trace("E06-01")
@given(st.lists(st.floats(0.0, 255.0), min_size=31, max_size=120))
def test_a_settled_answer_always_compares_against_a_frame_at_least_a_second_old(levels: list[float]) -> None:
    settling = Settling(LIMIT)
    for i, level in enumerate(levels):
        answer = settling.settled(round(i / 30 * 1e9), (level, level, level))
        if i < 30:
            assert answer is False  # under one second of picture
    assert len(settling._history) <= 32  # bounded by one look back second of frames plus one


@pytest.mark.trace("E06-01")
def test_out_of_order_frames_and_a_non_positive_limit_are_refused() -> None:
    settling = Settling(LIMIT)
    settling.settled(10, (1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        settling.settled(10, (1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        Settling(0.0)
