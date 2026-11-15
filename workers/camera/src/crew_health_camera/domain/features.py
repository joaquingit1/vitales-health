"""Per-frame features, the only thing the worker keeps from a frame (SYSTEM 7.1: raw frames are never stored).

A capture adapter reduces each frame on the capturing device to these numbers and drops the pixels. Coordinates are
normalized to the frame (0 to 1); colour and luminance are 8 bit means (0 to 255). Time is the worker's monotonic
clock inside one clock segment (SYSTEM 5.2): two frames from different segments are never on one time axis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FaceFeatures:
    """Mean colour of the facial skin region and where the face is."""

    red: float
    green: float
    blue: float
    center_x: float
    center_y: float


@dataclass(frozen=True, slots=True)
class TorsoFeatures:
    """Shoulder landmarks (vertical positions) and the torso's horizontal centre."""

    left_shoulder_y: float
    right_shoulder_y: float
    center_x: float


@dataclass(frozen=True, slots=True)
class FrameFeatures:
    """face is present only when exactly one face was found; torso only when both shoulders were visible."""

    device_id: str
    segment: str
    t_ns: int
    luma: float
    saturated_fraction: float
    face_count: int
    face: FaceFeatures | None
    torso: TorsoFeatures | None


def numbers(frame: FrameFeatures) -> tuple[float, ...]:
    """Every measured number of a frame, for the finiteness check."""
    values: list[float] = [frame.luma, frame.saturated_fraction]
    if frame.face is not None:
        f = frame.face
        values += [f.red, f.green, f.blue, f.center_x, f.center_y]
    if frame.torso is not None:
        t = frame.torso
        values += [t.left_shoulder_y, t.right_shoulder_y, t.center_x]
    return tuple(values)
