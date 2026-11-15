"""What the twin films: the real sample video, and a synthetic video with a known pulse and breathing rate.

Synthetic ground truth follows the classic remote photoplethysmography validation technique: take a real face image
and modulate its skin colour at a chosen heart rate with the blood volume pulse's colour signature, so the injected
rate is known exactly (Verkruysse, Svaasand and Nelson, Optics Express 16(26), 2008, showed the pulse in ordinary video
of skin; McDuff et al., SCAMPS, arXiv:2206.04197, 2022, validate rPPG on synthetic video with known injected
signals). The signature is the relative pulsatile strength per colour channel, PBV = (0.33, 0.77, 0.53) for red,
green and blue (de Haan and van Leest, Physiol. Meas. 35(9), 2014), which is also the direction POS and CHROM expect.
Breathing is a small periodic vertical shift of the image, as shoulders rise and fall. Seeded sensor noise is added so
no frame is ever a pixel-identical copy. None of these numbers are physiological claims: they are injected test
values inside the worker's analysis bands, and NASA's catalog holds no camera rPPG method (provenance in the worker's
camera-quality.json, non_nasa_scope).

Faults reuse the synthetic scene: head motion, low light, an intermittent second face, the face leaving the frame,
a stream that drops, and a camera whose auto exposure and white balance are still ramping when the stream starts.
Each one is the input a real station camera produces in that situation.

The auto exposure ramp is a team measurement, not a published value: a laptop camera opened fresh (macOS FaceTime
camera, 2026-11-15) moved its frame mean from R 75.9, G 67.3, B 65.1 to R 97.8, G 70.9, B 54.8 over its first
1.28 s, then stayed flat. The scenario applies those per channel gains at stream start to a face whose pulse is as
weak as that recording's (POS SNR +0.64 dB on the real face; PULSE_DEPTH_WEAK gives about +2 dB here), so the ramp
alone decides whether a window that starts at the first frame is measured.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from crew_health_camera_twin.sample import BASE_FRAME_S, FACE_BOX, STILL_LENGTH_S, STILL_START_S, STREAM_SIZE

PBV_RGB = (0.33, 0.77, 0.53)  # de Haan and van Leest 2014, relative pulsatile strength per channel
PULSE_DEPTH = 0.012  # skin colour change at the peak, relative, scaled by PBV (green about 0.9 percent)
PULSE_DEPTH_WEAK = 0.002  # a real face's pulse strength in ordinary light (see the auto exposure ramp above)
BREATH_SHIFT_PX = 3.0  # vertical image shift amplitude at 360 rows (about 0.8 percent of the frame height)
NOISE_SIGMA = 1.5  # sensor noise, 8 bit levels
JPEG_QUALITY = 92
# Classic YCrCb skin box (Chai and Ngan, IEEE TCSVT 9(4), 1999): the pixels whose colour the pulse modulates.
SKIN_CR = (133, 173)
SKIN_CB = (77, 127)

type Frame = Any  # a numpy uint8 BGR image, STREAM_SIZE


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    kind: str  # "sample" or "synthetic"
    pulse_per_min: float | None
    breaths_per_min: float | None
    fault: str | None
    expected: str  # what the worker must report: "reading" or the refusal reason
    pulse_depth: float = PULSE_DEPTH


SYNTHETIC_PULSE_PER_MIN = 72.0
SYNTHETIC_BREATHS_PER_MIN = 15.0
SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("sample", "sample", None, None, None, "reading_or_reason"),
        Scenario(
            "synthetic", "synthetic", SYNTHETIC_PULSE_PER_MIN, SYNTHETIC_BREATHS_PER_MIN, None, "reading"
        ),
        Scenario(
            "head_motion", "synthetic", SYNTHETIC_PULSE_PER_MIN, None, "head_motion", "too_much_movement"
        ),
        Scenario("low_light", "synthetic", SYNTHETIC_PULSE_PER_MIN, None, "low_light", "too_dark"),
        Scenario(
            "second_face", "synthetic", SYNTHETIC_PULSE_PER_MIN, None, "second_face", "ambiguous_subject"
        ),
        Scenario(
            "face_leaves", "synthetic", SYNTHETIC_PULSE_PER_MIN, None, "face_leaves", "face_not_visible"
        ),
        Scenario("stream_drop", "synthetic", SYNTHETIC_PULSE_PER_MIN, None, "stream_drop", "capture_failed"),
        Scenario(
            "auto_exposure_ramp",
            "synthetic",
            SYNTHETIC_PULSE_PER_MIN,
            None,
            "auto_exposure_ramp",
            "reading",
            pulse_depth=PULSE_DEPTH_WEAK,
        ),  # fmt: skip
    )
}
# (seconds since the stream started, R, G, B frame mean) as measured; gains are relative to the settled last row.
EXPOSURE_RAMP: tuple[tuple[float, float, float, float], ...] = (
    (0.00, 75.9, 67.3, 65.1),
    (0.33, 83.0, 72.7, 62.9),
    (0.51, 86.1, 69.6, 55.1),
    (0.76, 94.1, 70.0, 53.8),
    (1.00, 97.0, 70.4, 53.0),
    (1.28, 97.8, 70.9, 54.8),
)
HEAD_MOTION_PX = 80.0  # horizontal sway amplitude at 640 columns (0.125 of the width), 0.3 Hz
LOW_LIGHT_GAIN = 0.22  # exposure as a fraction of normal: a dim cabin
SECOND_FACE_EVERY_S = (5.0, 1.0)  # a crewmate leans in for 1 s of every 5 s
FACE_AWAY_S = (6.0, 15.0)  # the subject is out of the frame between these seconds of the stream
STREAM_DROP_AFTER_S = 6.0


def exposure_gains(t: float) -> tuple[float, float, float]:
    """BGR gains of the measured auto exposure ramp at stream time t, interpolated linearly; 1 once it settled."""
    settled = EXPOSURE_RAMP[-1][1:]
    for (a, *low), (b, *high) in zip(EXPOSURE_RAMP, EXPOSURE_RAMP[1:], strict=False):
        if a <= t < b:
            w = (t - a) / (b - a)
            r, g, bl = (x + (y - x) * w for x, y in zip(low, high, strict=True))
            return bl / settled[2], g / settled[1], r / settled[0]
    return 1.0, 1.0, 1.0


def base_frame(video: Path) -> Frame:
    """The still face image of the synthetic scenes: the sample's frame at BASE_FRAME_S, at stream size."""
    capture = cv2.VideoCapture(str(video))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, BASE_FRAME_S * 1000.0)
        ok, image = capture.read()
    finally:
        capture.release()
    if not ok:
        raise RuntimeError("sample frame unreadable")
    return cv2.resize(image, STREAM_SIZE, interpolation=cv2.INTER_AREA)


def skin_mask(image: Frame) -> Any:
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    cr, cb = ycrcb[..., 1], ycrcb[..., 2]
    return (cr >= SKIN_CR[0]) & (cr <= SKIN_CR[1]) & (cb >= SKIN_CB[0]) & (cb <= SKIN_CB[1])


class Synthetic:
    """Frames of one synthetic scenario at stream time t (seconds since the client connected)."""

    def __init__(self, base: Frame, scenario: Scenario, *, seed: int) -> None:
        self._base = base.astype(np.float32)
        self._mask = skin_mask(base)
        self._scenario = scenario
        self._noise = np.random.default_rng(seed)
        x, y, w, h = FACE_BOX
        self._face = base[y : y + h, x : x + w][:, ::-1].copy()  # mirrored: a different looking second face

    def frame(self, t: float) -> Frame:
        s = self._scenario
        image = self._base.copy()
        if s.pulse_per_min is not None:
            wave = math.sin(2.0 * math.pi * s.pulse_per_min / 60.0 * t)
            gains = np.array(
                [1.0 + s.pulse_depth * w * wave for w in reversed(PBV_RGB)], dtype=np.float32
            )  # BGR
            image[self._mask] *= gains
        dx, dy = 0.0, 0.0
        if s.breaths_per_min is not None:
            dy = BREATH_SHIFT_PX * math.sin(2.0 * math.pi * s.breaths_per_min / 60.0 * t)
        if s.fault == "head_motion":
            dx = HEAD_MOTION_PX * math.sin(2.0 * math.pi * 0.3 * t)
        if s.fault == "face_leaves" and FACE_AWAY_S[0] <= t < FACE_AWAY_S[1]:
            dx = float(STREAM_SIZE[0])
        if dx or dy:
            shift = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
            image = cv2.warpAffine(image, shift, STREAM_SIZE, borderMode=cv2.BORDER_REPLICATE)
        if s.fault == "low_light":
            image *= LOW_LIGHT_GAIN
        if s.fault == "auto_exposure_ramp":
            image *= np.array(exposure_gains(t), dtype=np.float32)
        image += self._noise.normal(0.0, NOISE_SIGMA, image.shape).astype(np.float32)
        out = np.clip(image, 0, 255).astype(np.uint8)
        every, shown = SECOND_FACE_EVERY_S
        if s.fault == "second_face" and t % every < shown:
            h, w = self._face.shape[:2]
            out[FACE_BOX[1] : FACE_BOX[1] + h, 24 : 24 + w] = self._face
        return out


def sample_frames(video: Path, fps: float) -> Iterator[tuple[float, Frame]]:
    """The real sample from its still segment at stream time, looping back to the segment start. A segment that
    yields no frame ends the stream (a broken file never spins)."""
    length = round(STILL_LENGTH_S * fps)
    loop = 0
    while True:
        capture = cv2.VideoCapture(str(video))
        read = 0
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, STILL_START_S * 1000.0)
            for index in range(length):
                ok, image = capture.read()
                if not ok:
                    break
                read += 1
                yield (
                    (loop * length + index) / fps,
                    cv2.resize(image, STREAM_SIZE, interpolation=cv2.INTER_AREA),
                )
        finally:
            capture.release()
        if read == 0:
            return
        loop += 1


def encode(image: Frame) -> bytes:
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("jpeg encoding failed")
    return bytes(data.tobytes())
