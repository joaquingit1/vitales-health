"""E06-01: independent pulse and breathing pipelines and the quality gate (R09, V05, V06, AGENTS rule 5)."""

from __future__ import annotations

import math
import random
from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from crew_health_camera.adapters.sim_source import Scene, SimSource
from crew_health_camera.domain import analysis
from crew_health_camera.domain.analysis import NoReading, Reading, breathing, decide, pulse
from crew_health_camera.domain.features import FaceFeatures, FrameFeatures
from crew_health_camera.domain.policy import PolicyInvalid, parse_policy
from crew_health_camera.domain.spectrum import peak
from crew_health_camera.tests.support import STEADY, FakeClock, cameras, policy, policy_document

QUALITY = policy()


def frames(
    scene: Scene, *, seconds: float = 31.0, rate_hz: float = 30.0, seed: int = 3
) -> tuple[FrameFeatures, ...]:
    clock = FakeClock()
    source = SimSource(
        clock=clock, cameras=cameras("cam_1"), scenes={"cam_1": scene}, rate_hz=rate_hz, seed=seed
    )
    capture = source.capture(
        ("cam_1",),
        duration_ns=round(seconds * 1e9),
        max_frames=10_000,
        warmup_max_ns=5_000_000_000,
        should_stop=lambda: False,
    )
    return capture.frames


def pulse_window(scene: Scene, *, rate_hz: float = 30.0, seed: int = 3) -> tuple[FrameFeatures, ...]:
    return frames(scene, seconds=21.0, rate_hz=rate_hz, seed=seed)


@pytest.mark.trace("R09", "V05", "E06-01")
def test_pulse_passes_while_breathing_fails_without_shoulders() -> None:
    window = frames(replace(STEADY, shoulders=False))
    beat = pulse("cam_1", window, QUALITY)
    breath = breathing("cam_1", window, QUALITY)
    assert isinstance(beat, Reading) and beat.value == Decimal(96) and beat.unit == "/min"
    assert breath == NoReading("respiratory_rate", "cam_1", "shoulders_not_visible")


@pytest.mark.trace("R09", "E06-01")
def test_breathing_passes_while_pulse_fails_without_a_face() -> None:
    window = frames(replace(STEADY, faces=0))
    assert pulse("cam_1", window, QUALITY) == NoReading("pulse", "cam_1", "face_not_visible")
    breath = breathing("cam_1", window, QUALITY)
    assert isinstance(breath, Reading) and breath.value == Decimal(15)


@pytest.mark.trace("E06-01", "V05")
@pytest.mark.parametrize(("hz", "per_minute"), [(1.0, 60), (1.6, 96), (2.0, 120), (2.5, 150)])
def test_pulse_golden_rates_from_the_generated_frequency(hz: float, per_minute: int) -> None:
    """Expected values are hz times 60, computed by hand, not read from the policy."""
    reading = pulse("cam_1", pulse_window(Scene(pulse_hz=hz, breath_hz=None)), QUALITY)
    assert isinstance(reading, Reading) and reading.value == Decimal(per_minute)


@pytest.mark.trace("E06-01")
@pytest.mark.parametrize(("hz", "per_minute"), [(0.2, 12), (0.25, 15), (0.4, 24)])
def test_breathing_golden_rates_from_the_generated_frequency(hz: float, per_minute: int) -> None:
    reading = breathing("cam_1", frames(Scene(pulse_hz=None, breath_hz=hz)), QUALITY)
    assert isinstance(reading, Reading) and reading.value == Decimal(per_minute)


@pytest.mark.trace("V06", "E06-01")
@pytest.mark.parametrize("hz", [3.3, 0.55])
def test_pulse_outside_the_band_is_refused_never_clipped(hz: float) -> None:
    decision = pulse("cam_1", pulse_window(Scene(pulse_hz=hz, breath_hz=None)), QUALITY)
    assert decision == NoReading("pulse", "cam_1", "outside_analysis_range")


@pytest.mark.trace("V06", "E06-01")
def test_breathing_outside_the_band_is_refused_never_clipped() -> None:
    decision = breathing("cam_1", frames(Scene(pulse_hz=None, breath_hz=0.8)), QUALITY)
    assert decision == NoReading("respiratory_rate", "cam_1", "outside_analysis_range")


@pytest.mark.trace("V05", "D08", "E06-01")
@pytest.mark.parametrize(
    ("scene", "reason"),
    [
        (replace(STEADY, head_motion=0.2), "too_much_movement"),
        (replace(STEADY, luma=10.0), "too_dark"),
        (replace(STEADY, saturated=0.4), "overexposed"),
        (replace(STEADY, frozen=True), "stuck_input"),
        (replace(STEADY, noise=3.0), "signal_quality_too_low"),
        (replace(STEADY, gap_every_s=5.0, gap_s=0.5), "frame_gaps"),
        (replace(STEADY, faces=0), "face_not_visible"),
        (Scene(pulse_hz=None, breath_hz=None), "signal_quality_too_low"),
    ],
)
def test_pulse_quality_gate_refuses_with_its_reason(scene: Scene, reason: str) -> None:
    assert pulse("cam_1", pulse_window(scene), QUALITY) == NoReading("pulse", "cam_1", reason)


@pytest.mark.trace("V05", "E06-01")
@pytest.mark.parametrize(
    ("scene", "reason"),
    [
        (replace(STEADY, torso_motion=0.2), "too_much_movement"),
        (Scene(pulse_hz=1.6, breath_hz=None), "stuck_input"),
        (replace(STEADY, gap_every_s=5.0, gap_s=0.5), "frame_gaps"),
    ],
)
def test_breathing_quality_gate_refuses_with_its_reason(scene: Scene, reason: str) -> None:
    assert breathing("cam_1", frames(scene), QUALITY) == NoReading("respiratory_rate", "cam_1", reason)


@pytest.mark.trace("V14", "E06-01")
def test_short_slow_discontinuous_or_invalid_time_never_reads() -> None:
    window = pulse_window(STEADY)
    assert pulse("cam_1", window[:100], QUALITY).reason == "too_short"  # type: ignore[union-attr]
    assert pulse("cam_1", window[:1], QUALITY).reason == "too_short"  # type: ignore[union-attr]
    slow = pulse_window(STEADY, rate_hz=5.0)
    assert pulse("cam_1", slow, QUALITY).reason == "frame_rate_too_low"  # type: ignore[union-attr]
    jumped = window[:300] + tuple(replace(f, segment="seg_2") for f in window[300:])
    assert pulse("cam_1", jumped, QUALITY).reason == "time_discontinuous"  # type: ignore[union-attr]
    repeated = window[:300] + (window[299],) + window[300:]
    assert pulse("cam_1", repeated, QUALITY).reason == "time_discontinuous"  # type: ignore[union-attr]
    face = window[10].face
    assert face is not None
    broken = window[:10] + (replace(window[10], face=replace(face, red=math.nan)),) + window[11:]
    assert pulse("cam_1", broken, QUALITY).reason == "invalid_signal"  # type: ignore[union-attr]
    assert breathing("cam_1", broken[:5], QUALITY).reason == "too_short"  # type: ignore[union-attr]


@pytest.mark.trace("E06-01")
def test_colour_channel_without_light_is_refused() -> None:
    window = tuple(
        replace(f, face=replace(f.face, blue=-1.0 if i % 2 else 0.5))
        for i, f in enumerate(pulse_window(STEADY))
        if f.face is not None
    )
    assert pulse("cam_1", window, QUALITY) == NoReading("pulse", "cam_1", "too_dark")


@pytest.mark.trace("E06-01")
def test_chrom_corroborates_and_a_disagreement_refuses_instead_of_averaging() -> None:
    """Same noisy window: within the shipped tolerance POS's value stands; with no tolerance CHROM's one bin
    difference refuses. The value is never a mix of the two methods."""
    window = pulse_window(Scene(pulse_hz=1.6, breath_hz=None, noise=0.3), seed=4)
    reading = pulse("cam_1", window, QUALITY)
    assert isinstance(reading, Reading) and reading.value == Decimal(96)
    strict = replace(QUALITY, pulse=replace(QUALITY.pulse, max_method_gap_per_min=0.0))
    assert pulse("cam_1", window, strict) == NoReading("pulse", "cam_1", "methods_disagree")


@pytest.mark.trace("E06-01")
def test_decide_routes_by_configured_code_and_refuses_unknown_codes() -> None:
    window = frames(STEADY)
    assert isinstance(decide("pulse", "cam_1", window, QUALITY), Reading)
    assert isinstance(decide("respiratory_rate", "cam_1", window, QUALITY), Reading)
    with pytest.raises(ValueError):
        decide("spo2", "cam_1", window, QUALITY)


@pytest.mark.trace("V05", "V06", "E06-01")
@settings(max_examples=20, deadline=None)
@given(hz=st.floats(min_value=0.85, max_value=2.75), seed=st.integers(0, 1000))
def test_in_band_pulse_reads_within_one_per_minute(hz: float, seed: int) -> None:
    reading = pulse(
        "cam_1", pulse_window(Scene(pulse_hz=hz, breath_hz=None), rate_hz=15.0, seed=seed), QUALITY
    )
    assert isinstance(reading, Reading)
    assert abs(float(reading.value) - hz * 60) <= 1.0


@pytest.mark.trace("V06", "E06-01")
@settings(max_examples=20, deadline=None)
@given(
    hz=st.one_of(st.floats(min_value=3.25, max_value=6.0), st.floats(min_value=0.2, max_value=0.6)),
    seed=st.integers(0, 1000),
)
def test_out_of_band_pulse_never_reads(hz: float, seed: int) -> None:
    decision = pulse(
        "cam_1", pulse_window(Scene(pulse_hz=hz, breath_hz=None), rate_hz=15.0, seed=seed), QUALITY
    )
    assert isinstance(decision, NoReading)


@pytest.mark.trace("E06-01")
@settings(max_examples=25, deadline=None)
@given(
    index=st.integers(0, 300),
    bad=st.sampled_from([math.nan, math.inf, -math.inf]),
    field_name=st.sampled_from(["luma", "saturated_fraction"]),
)
def test_any_nonfinite_input_never_reads(index: int, bad: float, field_name: str) -> None:
    window = list(pulse_window(STEADY, rate_hz=15.0))
    window[index] = (
        replace(window[index], luma=bad)
        if field_name == "luma"
        else replace(window[index], saturated_fraction=bad)
    )
    assert pulse("cam_1", tuple(window), QUALITY) == NoReading("pulse", "cam_1", "invalid_signal")


def _lit_face(seconds: float, *, specular: float, blue_drift: float) -> list[FrameFeatures]:
    """A 72 /min skin pulse (blood volume signature 0.33, 0.77, 0.53) under a white specular flicker at 2 Hz, which
    CHROM's chrominance projection cancels, and a slow 0.1 Hz drift of the blue light only."""
    noise = random.Random(3)
    out = []
    for i in range(round(30 * seconds)):
        t = i / 30
        wave = math.sin(2 * math.pi * 1.2 * t)
        flicker = specular * math.sin(2 * math.pi * 2.0 * t)
        drift = blue_drift * math.sin(2 * math.pi * 0.1 * t)
        rgb = [
            base * (1 + 0.01 * weight * wave + flicker + (drift if channel == 2 else 0.0))
            + noise.gauss(0, 0.05)
            for channel, (base, weight) in enumerate(zip((150, 100, 80), (0.33, 0.77, 0.53), strict=True))
        ]
        face = FaceFeatures(rgb[0], rgb[1], rgb[2], 0.5, 0.4)
        out.append(FrameFeatures("c", "s", 10**9 + i * 33_333_333, 120, 0, 1, face, None))
    return out


@pytest.mark.trace("E06-01", "m1")
def test_chrom_band_passes_its_traces_before_alpha_so_slow_light_drift_cannot_set_it() -> None:
    """Unfiltered, the blue drift inflates std(Y), alpha shrinks, the flicker survives and CHROM peaks at 2 Hz
    (refused as methods_disagree). Band-passed first, as de Haan and Jeanne 2013 do, CHROM finds the 72 /min."""
    lit = _lit_face(22, specular=0.006, blue_drift=0.15)
    faces = [f.face for f in lit if f.face is not None]
    times = [(f.t_ns - lit[0].t_ns) / 1e9 for f in lit]
    found = peak(times, analysis._chrom(times, faces, QUALITY.pulse.signal.band), QUALITY.pulse.signal)
    assert found is not None and abs(found.frequency_hz - 1.2) < 0.01
    decision = pulse("c", lit, QUALITY)
    assert isinstance(decision, Reading) and decision.value == 72


@pytest.mark.trace("R09", "E06-01", "m11")
def test_rocking_inside_the_breathing_band_is_movement_not_a_breathing_rate() -> None:
    still = frames(STEADY)
    assert isinstance(breathing("cam_1", still, QUALITY), Reading)
    bob = tuple(
        replace(f, torso=replace(f.torso, left_shoulder_y=f.torso.left_shoulder_y + 0.06 * math.sin(f.t_ns / 1e9),
                                 right_shoulder_y=f.torso.right_shoulder_y + 0.06 * math.sin(f.t_ns / 1e9)))
        if f.torso is not None else f
        for f in still
    )  # fmt: skip
    refused = breathing("cam_1", bob, QUALITY)
    assert isinstance(refused, NoReading) and refused.reason == "too_much_movement"


@pytest.mark.trace("AGENTS-1.8", "m3")
def test_a_rate_unit_other_than_per_minute_is_refused_by_the_policy_validator() -> None:
    for signal in ("pulse", "breathing"):
        document = policy_document()
        document[signal]["unit"] = "/s"
        with pytest.raises(PolicyInvalid) as invalid:
            parse_policy(document)
        assert invalid.value.pointer == f"/{signal}/unit"
