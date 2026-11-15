"""SIM frame source: generated frame features for the explicit SIM profile and for tests (AGENTS rule 2).

Its origin is always SIM, so nothing it produces can pass for a LIVE reading. Each camera plays a Scene: a colour
pulse at a stated frequency, shoulder motion at a breathing frequency, face count, light, saturation, head motion and
seeded noise. A scene with no pulse or no breathing frequency carries no such signal: nothing is filled in.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from crew_health_camera.application.ports import Camera, Capture, CaptureFailed, OriginMode, WorkerClock
from crew_health_camera.domain.features import FaceFeatures, FrameFeatures, TorsoFeatures

_SKIN = (150.0, 100.0, 80.0)  # mean skin colour of the generated face, 8 bit
_PULSE_WEIGHTS = (
    0.33,
    0.77,
    0.53,
)  # relative blood volume pulse strength per channel (de Haan 2013 skin model)


@dataclass(frozen=True, slots=True)
class Scene:
    pulse_hz: float | None
    breath_hz: float | None
    faces: int = 1
    shoulders: bool = True
    luma: float = 120.0
    saturated: float = 0.0
    head_motion: float = 0.0
    torso_motion: float = 0.0
    pulse_amplitude: float = 0.01
    noise: float = 0.05
    gap_every_s: float | None = None
    gap_s: float = 0.0
    frozen: bool = False


class SimSource:
    def __init__(
        self,
        *,
        clock: WorkerClock,
        cameras: tuple[Camera, ...],
        scenes: Mapping[str, Scene],
        rate_hz: float,
        seed: int,
        failing: bool = False,
    ) -> None:
        self._clock, self._cameras, self._scenes = clock, cameras, dict(scenes)
        self._rate, self._seed, self._failing = rate_hz, seed, failing

    @property
    def origin_mode(self) -> OriginMode:
        return "SIM"

    def cameras(self) -> tuple[Camera, ...]:
        return self._cameras

    def set_scene(self, device_id: str, scene: Scene) -> None:
        self._scenes[device_id] = scene

    def capture(
        self,
        device_ids: tuple[str, ...],
        *,
        duration_ns: int,
        max_frames: int,
        warmup_max_ns: int,
        should_stop: Callable[[], bool],
    ) -> Capture:
        """Generated frames have no exposure ramp: they are settled from the first one, so no warm up is spent."""
        if self._failing:
            raise CaptureFailed("sim")
        start, segment = self._clock.monotonic_ns(), self._clock.segment()
        step = round(1e9 / self._rate)
        count = min(max_frames, duration_ns // step + 1)
        frames: list[FrameFeatures] = []
        for device in device_ids:
            scene = self._scenes.get(device)
            if scene is None:
                continue
            noise = random.Random(f"{self._seed}:{device}")
            for i in range(count):
                t = i * step / 1e9
                if scene.gap_every_s is not None and (t % scene.gap_every_s) < scene.gap_s:
                    continue
                frames.append(_frame(device, segment, start + i * step, t, scene, noise))
        frames.sort(key=lambda f: (f.t_ns, f.device_id))
        self._clock.wait_until_ns(start + duration_ns, should_stop)
        return Capture(tuple(frames), segment, start, start + (count - 1) * step)


def _frame(
    device: str, segment: str, t_ns: int, t: float, scene: Scene, noise: random.Random
) -> FrameFeatures:
    face = None
    if scene.faces == 1:
        wave = 0.0 if scene.pulse_hz is None or scene.frozen else math.sin(2 * math.pi * scene.pulse_hz * t)
        rgb = [
            base * (1 + scene.pulse_amplitude * w * wave)
            + (0.0 if scene.frozen else noise.gauss(0, scene.noise))
            for base, w in zip(_SKIN, _PULSE_WEIGHTS, strict=True)
        ]
        sway = scene.head_motion * math.sin(2 * math.pi * 0.25 * t)
        face = FaceFeatures(rgb[0], rgb[1], rgb[2], 0.5 + sway, 0.4)
    torso = None
    if scene.shoulders:
        lift = 0.0 if scene.breath_hz is None else 0.01 * math.sin(2 * math.pi * scene.breath_hz * t)
        jitter = noise.gauss(0, 0.0005)
        drift = scene.torso_motion * math.sin(2 * math.pi * 0.2 * t)
        torso = TorsoFeatures(0.7 + lift + jitter, 0.7 + lift - jitter, 0.5 + drift)
    return FrameFeatures(device, segment, t_ns, scene.luma, scene.saturated, scene.faces, face, torso)
