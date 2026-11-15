"""Spectral peak and signal to noise ratio of a short series sampled at the frames' own times.

The power at each candidate frequency is the squared magnitude of the series' Fourier sum taken at the actual sample
times, so frames are never interpolated onto a grid (AGENTS rule 5). The search range is the analysis band widened by
the edge margin on both sides: a peak outside the band, or on the search edge, is a rate the band cannot measure and is
reported as such, never clipped to the band edge (V06).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from crew_health_camera.domain.policy import Band, SignalRules


@dataclass(frozen=True, slots=True)
class Peak:
    frequency_hz: float
    in_band: bool
    snr_db: float


def power(times_s: Sequence[float], values: Sequence[float], frequency_hz: float) -> float:
    real = imaginary = 0.0
    for t, v in zip(times_s, values, strict=True):
        angle = 2.0 * math.pi * frequency_hz * t
        real += v * math.cos(angle)
        imaginary -= v * math.sin(angle)
    return real * real + imaginary * imaginary


def grid(rules: SignalRules) -> tuple[float, ...]:
    """Candidate frequencies from (band min minus margin) to (band max plus margin), both ends included."""
    low = rules.band.min_hz - rules.edge_margin_hz
    high = rules.band.max_hz + rules.edge_margin_hz
    steps = math.floor((high - low) / rules.frequency_step_hz + 1e-9)
    return tuple(low + i * rules.frequency_step_hz for i in range(steps + 1))


def centered(values: Sequence[float]) -> tuple[float, ...]:
    mean = math.fsum(values) / len(values)
    return tuple(v - mean for v in values)


def peak(times_s: Sequence[float], values: Sequence[float], rules: SignalRules) -> Peak | None:
    """None when the series has no power at all (flat input). SNR follows de Haan and Jeanne 2013: power within
    the half width of the peak and of its first harmonic over the rest of the band."""
    frequencies = grid(rules)
    series = centered(values)
    spectrum = tuple(power(times_s, series, f) for f in frequencies)
    best = max(range(len(spectrum)), key=lambda i: (spectrum[i], -i))
    if spectrum[best] <= 0.0:
        return None
    f0 = frequencies[best]
    on_edge = best in (0, len(frequencies) - 1)
    in_band = not on_edge and rules.band.min_hz <= f0 <= rules.band.max_hz
    signal = noise = 0.0
    for f, p in zip(frequencies, spectrum, strict=True):
        if not rules.band.min_hz <= f <= rules.band.max_hz:
            continue
        near = abs(f - f0) <= rules.snr_halfwidth_hz or abs(f - 2 * f0) <= rules.snr_halfwidth_hz
        if near:
            signal += p
        else:
            noise += p
    if signal <= 0.0:
        return Peak(f0, False, -math.inf)
    snr = math.inf if noise <= 0.0 else 10.0 * math.log10(signal / noise)
    return Peak(f0, in_band, snr)


def bandpass(times_s: Sequence[float], values: Sequence[float], band: Band) -> tuple[float, ...]:
    """Zero phase band-pass of a series at its mean sample rate: a second order resonator (Bristow-Johnson audio EQ
    cookbook, constant peak gain) centred on the band's geometric centre, run forward and then backward so no peak
    moves. CHROM's alpha is a ratio of the filtered traces' deviations (de Haan and Jeanne 2013, section III.B), so the
    filter must come before it. A band the sample rate cannot carry leaves the series centred but unfiltered."""
    series = centered(values)
    span = times_s[-1] - times_s[0]
    if len(series) < 3 or span <= 0.0:
        return series
    rate = (len(series) - 1) / span
    centre = math.sqrt(band.min_hz * band.max_hz)
    if band.max_hz >= rate / 2:
        return series
    w0 = 2.0 * math.pi * centre / rate
    alpha = math.sin(w0) * (band.max_hz - band.min_hz) / (2.0 * centre)
    a0 = 1.0 + alpha
    b = (alpha / a0, 0.0, -alpha / a0)
    a = (-2.0 * math.cos(w0) / a0, (1.0 - alpha) / a0)
    forward = _biquad(series, b, a)
    return tuple(reversed(_biquad(tuple(reversed(forward)), b, a)))


def _biquad(
    values: Sequence[float], b: tuple[float, float, float], a: tuple[float, float]
) -> tuple[float, ...]:
    out: list[float] = []
    x1 = x2 = y1 = y2 = 0.0
    for x in values:
        y = b[0] * x + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2
        out.append(y)
        x2, x1, y2, y1 = x1, x, y1, y
    return tuple(out)
