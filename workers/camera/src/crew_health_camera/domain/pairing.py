"""Explicit pairing of cameras to one acquisition's subject (E06-02, R29, V08, V39).

The hub names the subject when it starts an acquisition, after E05 and E02 have checked the operator, consent and
request; the worker pairs exactly the cameras the start names to that acquisition, until its deadline. A frame is
admitted only from a paired camera, inside the pairing's clock segment and lifetime, with exactly one face in view.
Everything else is discarded on the device, counted, and never analysed. One frame with a second face makes that
camera's whole window ambiguous (R29): the single-face frames around it may show either person, so the camera gives
no reading for the subject at all, however few such frames there were. Faces are never matched to people here:
likeness, station login and display names never authorize a write (PLAN Pairing).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from crew_health_camera.domain.features import FrameFeatures

DISCARD_ORDER: tuple[str, ...] = ("pairing_revoked", "pairing_expired", "not_paired", "ambiguous_subject")


@dataclass(frozen=True, slots=True)
class Pairing:
    acquisition_id: str
    patient_id: str
    device_ids: frozenset[str]
    segment: str
    expires_ns: int
    revoked: bool


@dataclass(frozen=True, slots=True)
class Admitted:
    frames: Mapping[str, tuple[FrameFeatures, ...]]
    discarded: Mapping[str, Mapping[str, int]]

    def reason_for(self, device: str) -> str | None:
        """Why a paired camera gives no reading (most decisive first), None when its admitted frames may be analysed.
        Any ambiguous frame refuses the whole window: admit keeps none of that camera's frames."""
        counts = self.discarded.get(device, {})
        if counts.get("ambiguous_subject", 0) > 0:
            return "ambiguous_subject"
        if self.frames.get(device):
            return None
        return next((r for r in DISCARD_ORDER if counts.get(r, 0) > 0), "no_camera")


def admission(pairing: Pairing, frame: FrameFeatures) -> str | None:
    """None admits the frame; otherwise the discard reason. A frame with no face is admitted so that face
    coverage counts it as missing; a frame with two or more faces is never analysed."""
    if pairing.revoked:
        return "pairing_revoked"
    if frame.device_id not in pairing.device_ids:
        return "not_paired"
    if frame.segment != pairing.segment or frame.t_ns > pairing.expires_ns:
        return "pairing_expired"
    if frame.face_count > 1:
        return "ambiguous_subject"
    return None


def admit(pairing: Pairing, frames: Sequence[FrameFeatures]) -> Admitted:
    kept: dict[str, list[FrameFeatures]] = {d: [] for d in sorted(pairing.device_ids)}
    dropped: dict[str, dict[str, int]] = {}
    for frame in frames:
        reason = admission(pairing, frame)
        if reason is None:
            kept[frame.device_id].append(frame)
        else:
            bucket = dropped.setdefault(frame.device_id, {})
            bucket[reason] = bucket.get(reason, 0) + 1
    for device, counts in dropped.items():
        if counts.get("ambiguous_subject", 0) and kept.get(device):
            # the window is ambiguous, not only the frame: nothing from this camera reaches any analysis (M5)
            counts["ambiguous_subject"] += len(kept[device])
            kept[device] = []
    return Admitted({d: tuple(f) for d, f in kept.items()}, {d: dict(c) for d, c in dropped.items()})
