"""The camera quality policy: analysis bands, sufficiency and quality limits, selection tolerance, reason and note
wording (AGENTS rule 1). Parsed once at startup from the policy document; an invalid document refuses startup
(PLAN C3: missing policy keeps the worker off, never silently passes).

Reason codes are a closed set in code: which checks exist is a safety mechanism, their limits and words are content.
"""

from __future__ import annotations

import math
import string
from collections.abc import Mapping
from dataclasses import dataclass

REASON_CODES: tuple[str, ...] = (
    "time_discontinuous",
    "invalid_signal",
    "too_short",
    "frame_rate_too_low",
    "frame_gaps",
    "face_not_visible",
    "shoulders_not_visible",
    "too_dark",
    "overexposed",
    "stuck_input",
    "too_much_movement",
    "outside_analysis_range",
    "signal_quality_too_low",
    "methods_disagree",
    "not_paired",
    "ambiguous_subject",
    "pairing_expired",
    "pairing_revoked",
    "no_camera",
    "capture_failed",
    "camera_not_settled",
    "window_missed",
    "origin_mismatch",
)
NOTE_FIELDS: Mapping[str, frozenset[str]] = {
    "selected": frozenset({"device", "views"}),
    "disagree": frozenset({"spread", "unit"}),
    "refused": frozenset({"device", "reason"}),
}


class PolicyInvalid(ValueError):
    """The policy document is missing a field, has a wrong type or breaks a bound; the pointer names where."""

    def __init__(self, pointer: str) -> None:
        super().__init__(pointer)
        self.pointer = pointer


@dataclass(frozen=True, slots=True)
class Band:
    min_hz: float
    max_hz: float


@dataclass(frozen=True, slots=True)
class FrameRules:
    min_rate_hz: float
    max_gap_ms: float
    min_luma: float
    max_saturated_fraction: float
    max_face_motion: float
    max_torso_motion: float
    max_shoulder_excursion: float


@dataclass(frozen=True, slots=True)
class CaptureRules:
    """Limits the capture adapter applies while it reduces a frame to features (m6: in the policy, not the adapter)."""

    saturated_level: float
    face_region_margin: float
    min_landmark_visibility: float
    settled_change_per_s: float


@dataclass(frozen=True, slots=True)
class SignalRules:
    """One indicator's pipeline: wire code and UCUM unit, analysis band, minimum window and spectral limits."""

    code: str
    unit: str
    band: Band
    window_s: float
    min_coverage: float
    min_snr_db: float
    snr_halfwidth_hz: float
    edge_margin_hz: float
    frequency_step_hz: float
    decimals: int


@dataclass(frozen=True, slots=True)
class PulseRules:
    signal: SignalRules
    pos_window_s: float
    max_method_gap_per_min: float


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    policy_id: str
    version: str
    frames: FrameRules
    capture: CaptureRules
    pulse: PulseRules
    breathing: SignalRules
    agreement_tolerance_per_min: float
    reasons: Mapping[str, str]
    notes: Mapping[str, str]

    def rules_for(self, code: str) -> SignalRules | None:
        return {self.pulse.signal.code: self.pulse.signal, self.breathing.code: self.breathing}.get(code)


def _get(document: Mapping[str, object], key: str, pointer: str) -> object:
    if key not in document:
        raise PolicyInvalid(pointer + "/" + key)
    return document[key]


def _object(document: Mapping[str, object], key: str, pointer: str) -> Mapping[str, object]:
    value = _get(document, key, pointer)
    if not isinstance(value, Mapping):
        raise PolicyInvalid(pointer + "/" + key)
    return value


def _text(document: Mapping[str, object], key: str, pointer: str) -> str:
    value = _get(document, key, pointer)
    if not isinstance(value, str) or not value.strip():
        raise PolicyInvalid(pointer + "/" + key)
    return value


def _limit(document: Mapping[str, object], key: str, pointer: str, low: float, high: float) -> float:
    """A sourced limit: {"value": number, "basis": text}. bool is not a number here."""
    entry = _object(document, key, pointer)
    _text(entry, "basis", pointer + "/" + key)
    value = _get(entry, "value", pointer + "/" + key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PolicyInvalid(pointer + "/" + key + "/value")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise PolicyInvalid(pointer + "/" + key + "/value")
    return number


def _band(document: Mapping[str, object], pointer: str) -> Band:
    entry = _object(document, "band_hz", pointer)
    _text(entry, "basis", pointer + "/band_hz")
    low, high = _get(entry, "min", pointer + "/band_hz"), _get(entry, "max", pointer + "/band_hz")
    if isinstance(low, bool) or isinstance(high, bool) or not isinstance(low, int | float):
        raise PolicyInvalid(pointer + "/band_hz")
    if not isinstance(high, int | float) or not 0 < float(low) < float(high) <= 10:
        raise PolicyInvalid(pointer + "/band_hz")
    return Band(float(low), float(high))


# Every rate is computed as a frequency in Hz times 60 (analysis.py), so the only unit that describes it is UCUM
# per minute. A policy edit cannot relabel the same number (rule 8; a unit is not a weakenable setting).
RATE_UNIT = "/min"


def _signal(document: Mapping[str, object], pointer: str) -> SignalRules:
    band = _band(document, pointer)
    unit = _text(document, "unit", pointer)
    if unit != RATE_UNIT:
        raise PolicyInvalid(pointer + "/unit")
    rules = SignalRules(
        code=_text(document, "code", pointer),
        unit=unit,
        band=band,
        window_s=_limit(document, "window_s", pointer, 1, 600),
        min_coverage=_limit(document, "min_coverage", pointer, 0.01, 1),
        min_snr_db=_limit(document, "min_snr_db", pointer, -60, 60),
        snr_halfwidth_hz=_limit(document, "snr_halfwidth_hz", pointer, 0.001, 1),
        edge_margin_hz=_limit(document, "edge_margin_hz", pointer, 0.001, 1),
        frequency_step_hz=_limit(document, "frequency_step_hz", pointer, 0.0005, 0.5),
        decimals=int(_limit(document, "decimals", pointer, 0, 3)),
    )
    if rules.edge_margin_hz >= band.min_hz:
        raise PolicyInvalid(pointer + "/edge_margin_hz")
    return rules


def _wording(
    document: Mapping[str, object], key: str, required: Mapping[str, frozenset[str]]
) -> dict[str, str]:
    entries = _object(document, key, "")
    result: dict[str, str] = {}
    for name in sorted(required):
        text = _text(entries, name, "/" + key)
        fields = {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}
        if not fields <= required[name]:
            raise PolicyInvalid("/" + key + "/" + name)
        result[name] = text
    if set(entries) - set(required):
        raise PolicyInvalid("/" + key)
    return result


def parse_policy(document: Mapping[str, object]) -> QualityPolicy:
    """Validate and freeze a policy document. Every limit carries its basis; unknown reason keys are refused."""
    frames = _object(document, "frames", "")
    capture = _object(document, "capture", "")
    pulse = _object(document, "pulse", "")
    policy = QualityPolicy(
        policy_id=_text(document, "policy_id", ""),
        version=_text(document, "version", ""),
        frames=FrameRules(
            min_rate_hz=_limit(frames, "min_rate_hz", "/frames", 1, 240),
            max_gap_ms=_limit(frames, "max_gap_ms", "/frames", 1, 10000),
            min_luma=_limit(frames, "min_luma", "/frames", 0, 255),
            max_saturated_fraction=_limit(frames, "max_saturated_fraction", "/frames", 0, 1),
            max_face_motion=_limit(frames, "max_face_motion", "/frames", 0, 1),
            max_torso_motion=_limit(frames, "max_torso_motion", "/frames", 0, 1),
            max_shoulder_excursion=_limit(frames, "max_shoulder_excursion", "/frames", 0, 1),
        ),
        capture=CaptureRules(
            saturated_level=_limit(capture, "saturated_level", "/capture", 1, 255),
            face_region_margin=_limit(capture, "face_region_margin", "/capture", 0, 0.45),
            min_landmark_visibility=_limit(capture, "min_landmark_visibility", "/capture", 0, 1),
            settled_change_per_s=_limit(capture, "settled_change_per_s", "/capture", 0.01, 255),
        ),
        pulse=PulseRules(
            signal=_signal(pulse, "/pulse"),
            pos_window_s=_limit(pulse, "pos_window_s", "/pulse", 0.1, 10),
            max_method_gap_per_min=_limit(pulse, "max_method_gap_per_min", "/pulse", 0, 60),
        ),
        breathing=_signal(_object(document, "breathing", ""), "/breathing"),
        agreement_tolerance_per_min=_limit(
            _object(document, "selection", ""), "agreement_tolerance_per_min", "/selection", 0, 60
        ),
        reasons=_wording(document, "reasons", {code: frozenset() for code in REASON_CODES}),
        notes=_wording(document, "notes", NOTE_FIELDS),
    )
    if policy.pulse.signal.code == policy.breathing.code:
        raise PolicyInvalid("/breathing/code")
    return policy
