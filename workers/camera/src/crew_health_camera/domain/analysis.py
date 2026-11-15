"""Independent pulse and breathing decisions for one camera's window (E06-01, R09, V05, V06).

Pulse reads the facial colour with POS (Wang, den Brinker, Stuijk and de Haan 2017) and reports CHROM (de Haan and
Jeanne 2013) only as corroboration: the value is POS's, never an average. Breathing reads the vertical motion of
both shoulders, after a gate on horizontal sway and on vertical excursion larger than breathing makes. Neither pipeline looks at the other's inputs, so a valid pulse with no shoulders is a pulse reading
and a breathing refusal. Each check refuses with its own reason; the first failing check, in a fixed order, wins.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from crew_health_camera.domain.features import FaceFeatures, FrameFeatures, TorsoFeatures, numbers
from crew_health_camera.domain.policy import Band, FrameRules, PulseRules, QualityPolicy, SignalRules
from crew_health_camera.domain.spectrum import bandpass, centered, peak


@dataclass(frozen=True, slots=True)
class Reading:
    code: str
    device_id: str
    value: Decimal
    unit: str
    snr_db: float
    frames: int


@dataclass(frozen=True, slots=True)
class NoReading:
    code: str
    device_id: str
    reason: str


type Decision = Reading | NoReading


def _timing(frames: Sequence[FrameFeatures], rules: FrameRules, window_s: float) -> str | None:
    """Time, finiteness and sufficiency checks shared by both pipelines."""
    if len(frames) < 2 or len({f.segment for f in frames}) != 1:
        return "time_discontinuous" if len(frames) >= 2 else "too_short"
    times = [f.t_ns for f in frames]
    if any(b <= a for a, b in zip(times, times[1:], strict=False)):
        return "time_discontinuous"
    if not all(math.isfinite(v) for f in frames for v in numbers(f)):
        return "invalid_signal"
    duration_s = (times[-1] - times[0]) / 1e9
    if duration_s < window_s:
        return "too_short"
    if (len(frames) - 1) / duration_s < rules.min_rate_hz:
        return "frame_rate_too_low"
    if max(b - a for a, b in zip(times, times[1:], strict=False)) / 1e6 > rules.max_gap_ms:
        return "frame_gaps"
    return None


def _motion(points: Sequence[tuple[float, float]]) -> float:
    cx = statistics.median(p[0] for p in points)
    cy = statistics.median(p[1] for p in points)
    return max(math.hypot(x - cx, y - cy) for x, y in points)


def _value(frequency_hz: float, decimals: int) -> Decimal:
    exponent = Decimal(1).scaleb(-decimals)
    return Decimal(repr(frequency_hz * 60.0)).quantize(exponent, rounding=ROUND_HALF_EVEN)


def _pos(times_s: Sequence[float], faces: Sequence[FaceFeatures], window_s: float) -> tuple[float, ...]:
    """POS overlap add with a window of `window_s` worth of frames (at least two)."""
    n = len(faces)
    span = times_s[-1] - times_s[0]
    length = max(2, min(n, round(window_s * (n - 1) / span) + 1))
    out = [0.0] * n
    for start in range(n - length + 1):
        part = faces[start : start + length]
        means = [statistics.fmean(getattr(f, c) for f in part) for c in ("red", "green", "blue")]
        s1 = [f.green / means[1] - f.blue / means[2] for f in part]
        s2 = [-2 * f.red / means[0] + f.green / means[1] + f.blue / means[2] for f in part]
        sd2 = statistics.pstdev(s2)
        alpha = 0.0 if sd2 == 0.0 else statistics.pstdev(s1) / sd2
        h = centered([a + alpha * b for a, b in zip(s1, s2, strict=True)])
        for offset, value in enumerate(h):
            out[start + offset] += value
    return tuple(out)


def _chrom(times_s: Sequence[float], faces: Sequence[FaceFeatures], band: Band) -> tuple[float, ...]:
    """CHROM (de Haan and Jeanne 2013): chrominance traces X and Y, band-passed to the pulse band, then
    S = Xf - alpha Yf with alpha = std(Xf) / std(Yf) taken over the filtered traces, as the paper does (m1)."""
    means = [statistics.fmean(getattr(f, c) for f in faces) for c in ("red", "green", "blue")]
    x = [3 * f.red / means[0] - 2 * f.green / means[1] for f in faces]
    y = [1.5 * f.red / means[0] + f.green / means[1] - 1.5 * f.blue / means[2] for f in faces]
    xf, yf = bandpass(times_s, x, band), bandpass(times_s, y, band)
    sy = statistics.pstdev(yf)
    alpha = 0.0 if sy == 0.0 else statistics.pstdev(xf) / sy
    return tuple(a - alpha * b for a, b in zip(xf, yf, strict=True))


def _spectral(
    code: str, device: str, times: Sequence[float], series: Sequence[float], rules: SignalRules
) -> tuple[Decision, float]:
    found = peak(times, series, rules)
    if found is None:
        return NoReading(code, device, "stuck_input"), math.nan
    if not found.in_band:
        return NoReading(code, device, "outside_analysis_range"), found.frequency_hz
    if found.snr_db < rules.min_snr_db:
        return NoReading(code, device, "signal_quality_too_low"), found.frequency_hz
    value = _value(found.frequency_hz, rules.decimals)
    return Reading(code, device, value, rules.unit, found.snr_db, len(series)), found.frequency_hz


def pulse(device: str, frames: Sequence[FrameFeatures], policy: QualityPolicy) -> Decision:
    rules: PulseRules = policy.pulse
    code = rules.signal.code
    reason = _timing(frames, policy.frames, rules.signal.window_s)
    if reason is not None:
        return NoReading(code, device, reason)
    with_face = [f for f in frames if f.face is not None]
    if len(with_face) < rules.signal.min_coverage * len(frames) or len(with_face) < 2:
        return NoReading(code, device, "face_not_visible")
    faces = [f.face for f in with_face if f.face is not None]
    checks: tuple[tuple[Callable[[], bool], str], ...] = (
        (lambda: statistics.fmean(f.luma for f in with_face) < policy.frames.min_luma, "too_dark"),
        (
            lambda: (
                statistics.fmean(f.saturated_fraction for f in with_face)
                > policy.frames.max_saturated_fraction
            ),
            "overexposed",
        ),
        (
            lambda: any(
                statistics.pstdev(getattr(f, c) for f in faces) == 0.0 for c in ("red", "green", "blue")
            ),
            "stuck_input",
        ),
        (
            lambda: any(
                statistics.fmean(getattr(f, c) for f in faces) <= 0.0 for c in ("red", "green", "blue")
            ),
            "too_dark",
        ),
        (
            lambda: _motion([(f.center_x, f.center_y) for f in faces]) > policy.frames.max_face_motion,
            "too_much_movement",
        ),
    )
    for failed, why in checks:
        if failed():
            return NoReading(code, device, why)
    times = [(f.t_ns - with_face[0].t_ns) / 1e9 for f in with_face]
    decision, pos_hz = _spectral(code, device, times, _pos(times, faces, rules.pos_window_s), rules.signal)
    if isinstance(decision, NoReading):
        return decision
    chrom = peak(times, _chrom(times, faces, rules.signal.band), rules.signal)
    if chrom is None or abs(chrom.frequency_hz - pos_hz) * 60.0 > rules.max_method_gap_per_min:
        return NoReading(code, device, "methods_disagree")
    return decision


def _torso_motion(torsos: Sequence[TorsoFeatures]) -> float:
    centre = statistics.median(t.center_x for t in torsos)
    return max(abs(t.center_x - centre) for t in torsos)


def _excursion(series: Sequence[float]) -> float:
    centre = statistics.median(series)
    return max(abs(v - centre) for v in series)


def breathing(device: str, frames: Sequence[FrameFeatures], policy: QualityPolicy) -> Decision:
    rules = policy.breathing
    reason = _timing(frames, policy.frames, rules.window_s)
    if reason is not None:
        return NoReading(rules.code, device, reason)
    with_torso = [f for f in frames if f.torso is not None]
    if len(with_torso) < rules.min_coverage * len(frames) or len(with_torso) < 2:
        return NoReading(rules.code, device, "shoulders_not_visible")
    torsos = [f.torso for f in with_torso if f.torso is not None]
    series = [(t.left_shoulder_y + t.right_shoulder_y) / 2 for t in torsos]
    # Both directions (m11): horizontal sway of the torso, and a vertical shoulder excursion larger than breathing
    # moves the shoulders (rocking or bobbing inside the breathing band would otherwise read as a breathing rate).
    if (
        _torso_motion(torsos) > policy.frames.max_torso_motion
        or _excursion(series) > policy.frames.max_shoulder_excursion
    ):
        return NoReading(rules.code, device, "too_much_movement")
    if statistics.pstdev(series) == 0.0:
        return NoReading(rules.code, device, "stuck_input")
    times = [(f.t_ns - with_torso[0].t_ns) / 1e9 for f in with_torso]
    decision, _ = _spectral(rules.code, device, times, series, rules)
    return decision


def decide(code: str, device: str, frames: Sequence[FrameFeatures], policy: QualityPolicy) -> Decision:
    """The pipeline for the requested indicator code; an unknown code is a programming error at the boundary."""
    if code == policy.pulse.signal.code:
        return pulse(device, frames, policy)
    if code == policy.breathing.code:
        return breathing(device, frames, policy)
    raise ValueError("unknown indicator code")
