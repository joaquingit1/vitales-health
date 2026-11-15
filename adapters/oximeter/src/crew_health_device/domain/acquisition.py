"""Hub-bound acquisitions and deterministic attempt construction."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from .errors import BindingError, FrameError
from .ieee11073 import DeviceDateTime
from .profile import ComponentRule, DeviceProfile
from .quality import Accepted, Refused, Sample, assess
from .readings import Component, DecodedFrame

# Hub to worker protocol owned by E06 (crew_health_camera); structural copy until it is published (follow-up F3).
OriginMode = Literal["LIVE", "REPLAY", "SIM"]
StartReply = Literal["started", "busy", "duplicate", "conflict", "rejected"]
CancelReply = Literal["stopped", "cannot_stop", "unknown"]
AcquisitionState = Literal["started", "completed", "failed", "rejected", "stopped", "unknown"]
CaptureEnd = Literal[
    "window_complete", "disconnected", "timeout", "cancelled", "radio_absent", "device_absent"
]
Outcome = Literal["success", "failed", "rejected"]


@dataclass(frozen=True, slots=True)
class Binding:
    request_id: str
    acquisition_id: str
    generation: int
    patient_id: str
    episode_id: str | None
    capability_code: str
    method: str
    device_id: str
    origin_mode: OriginMode
    reserved_sequence: int
    expected_user_index: int | None


def _identifier(value: object, key: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
        raise BindingError("invalid_" + key)
    return value


def _binding_integer(value: object, key: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise BindingError("invalid_" + key)
    return value


def parse_binding(document: Mapping[str, object]) -> Binding:
    keys = frozenset(Binding.__dataclass_fields__)
    if set(document) != keys:
        raise BindingError("binding_fields")
    origin = document["origin_mode"]
    if origin not in ("LIVE", "REPLAY", "SIM"):
        raise BindingError("invalid_origin_mode")
    method = document["method"]
    if not isinstance(method, str) or not method:
        raise BindingError("invalid_method")
    episode = document["episode_id"]
    user = document["expected_user_index"]
    return Binding(
        _identifier(document["request_id"], "request_id"),
        _identifier(document["acquisition_id"], "acquisition_id"),
        _binding_integer(document["generation"], "generation", 1),
        _identifier(document["patient_id"], "patient_id"),
        None if episode is None else _identifier(episode, "episode_id"),
        _identifier(document["capability_code"], "capability_code"),
        method,
        _identifier(document["device_id"], "device_id"),
        origin,
        _binding_integer(document["reserved_sequence"], "reserved_sequence", 1),
        None if user is None else _binding_integer(user, "expected_user_index", 0),
    )


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    patient_id: str
    episode_id: str | None
    code: str
    value: Decimal
    unit: str
    observed_at: datetime
    origin_mode: OriginMode
    acquisition_method: str
    device_id: str
    context: tuple[tuple[str, str], ...]
    measurement_request_id: str


@dataclass(frozen=True, slots=True)
class AttemptDraft:
    sequence: int
    started_at: datetime
    ended_at: datetime
    outcome: Outcome
    failure_reason: str | None
    observations: tuple[ObservationDraft, ...]
    quality_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReceivedFrame:
    frame: DecodedFrame | FrameError
    received_at: datetime
    monotonic_ns: int


@dataclass(frozen=True, slots=True)
class _Selection:
    samples: tuple[Sample, ...]
    unit: str | None
    site_code: int | None
    notes: frozenset[str]
    rejection: str | None


def _frame_component(
    frame: DecodedFrame, binding: Binding, profile: DeviceProfile, rule: ComponentRule, notes: set[str]
) -> Component | str | None:
    for flag in ("data_for_demonstration", "data_for_testing"):
        if binding.origin_mode == "LIVE" and flag in frame.status:
            return "device_status:" + flag
    rejecting = frame.status & profile.rejecting_status
    if rejecting:
        notes.update("device_status:" + flag for flag in rejecting)
        return None
    notes.update("device_status:" + flag for flag in frame.status & profile.noted_status)
    if "data_from_storage" in frame.status:
        notes.add("stored_measurement_untrusted_time")
        return None
    if (
        frame.user_index is not None
        and binding.expected_user_index is not None
        and frame.user_index != binding.expected_user_index
    ):
        return "wrong_subject"
    component = next((item for item in frame.components if item.field == rule.field), None)
    if component is None:
        return None
    if component.missing is not None:
        notes.add(f"component_missing:{component.field}:{component.missing}")
        return None
    if component.unit not in rule.units:
        return "unsupported_unit:" + component.unit
    return component


def _select(
    frames: Sequence[ReceivedFrame], binding: Binding, profile: DeviceProfile, rule: ComponentRule
) -> _Selection:
    samples: list[Sample] = []
    notes: set[str] = set()
    unit: str | None = None
    site: int | None = None
    for received in frames:
        frame = received.frame
        if isinstance(frame, FrameError):
            notes.add("frame:" + frame.reason)
            continue
        component = _frame_component(frame, binding, profile, rule, notes)
        if isinstance(component, str):
            return _Selection((), None, None, frozenset(notes), component)
        if component is None or component.value is None:
            continue
        if unit is not None and component.unit != unit:
            # Samples in two units are never compared or converted here (V15).
            return _Selection((), None, None, frozenset(notes), "unit_changed")
        samples.append(
            Sample(
                component.value,
                received.received_at,
                received.monotonic_ns,
                None if frame.device_time is None else _device_time(frame.device_time),
                frame.device_clock_set,
            )
        )
        unit, site = component.unit, frame.site_code
    return _Selection(tuple(samples), unit, site, frozenset(notes), None)


def _observation(
    binding: Binding, profile: DeviceProfile, rule: ComponentRule, selection: _Selection, accepted: Accepted
) -> ObservationDraft:
    if selection.unit is None:
        raise ValueError("accepted_without_unit")
    context: tuple[tuple[str, str], ...] = (("adapter_profile", profile.profile_id + "@" + profile.version),)
    if selection.site_code is not None:
        context += (("device_site_code", str(selection.site_code)),)
    sample = accepted.sample
    if sample is not None and sample.device_time is not None:
        # The device's own clock, kept as evidence; observed_at stays the adapter's arrival time (UTC clock).
        context += (("device_time", sample.device_time),)
        if sample.device_clock_set is not None:
            context += (("device_clock_set", "true" if sample.device_clock_set else "false"),)
    return ObservationDraft(
        binding.patient_id,
        binding.episode_id,
        rule.indicator_code,
        accepted.value,
        rule.units[selection.unit],
        accepted.observed_at,
        binding.origin_mode,
        binding.method,
        binding.device_id,
        context,
        binding.request_id,
    )


def _device_time(value: DeviceDateTime) -> str:
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}T{value.hours:02d}:{value.minutes:02d}:{value.seconds:02d}"


def _repeated_measurement(frames: Sequence[ReceivedFrame]) -> bool:
    """A stuck sensor, from evidence: a device-timestamped measurement that arrives again, identical down to its
    timestamp, is the device repeating an old value, not a new sample. Untimestamped frames carry no such
    evidence, so equal values alone never count as stuck (a steady value is a good reading, C08)."""
    seen: set[DecodedFrame] = set()
    for received in frames:
        frame = received.frame
        if isinstance(frame, FrameError) or frame.device_time is None:
            continue
        if frame in seen:
            return True
        seen.add(frame)
    return False


def _interrupted(profile: DeviceProfile, end: CaptureEnd) -> bool:
    """A reading that ended before it was complete: a continuous device (completion `window`) that lost its link or
    went silent before its window or frame budget was used, or any device whose source never opened."""
    if end in ("window_complete", "cancelled"):
        return False
    return not (profile.completion == "measurement" and end in ("timeout", "disconnected"))


def _interrupted_notes(
    binding: Binding, profile: DeviceProfile, rules: Sequence[ComponentRule], frames: Sequence[ReceivedFrame]
) -> tuple[str, ...]:
    """Evidence kept from an interrupted reading: frame notes and any quality refusal of what did arrive."""
    notes: set[str] = set()
    for rule in rules:
        selection = _select(frames, binding, profile, rule)
        notes |= selection.notes
        verdict = assess(selection.samples, rule, profile.min_samples, steady=profile.steady_reading)
        if isinstance(verdict, Refused) and verdict.reason not in ("insufficient_samples", "not_steady"):
            notes.add("quality:" + verdict.reason)
    return tuple(sorted(notes))


def _unchecked(rules: Sequence[ComponentRule]) -> set[str]:
    notes: set[str] = set()
    for rule in rules:
        if rule.range_min is None:
            notes.add("unchecked:range:" + rule.indicator_code)
        if rule.max_rate_per_second is None:
            notes.add("unchecked:rate:" + rule.indicator_code)
    return notes


def build_attempt(
    binding: Binding,
    profile: DeviceProfile,
    frames: Sequence[ReceivedFrame],
    *,
    started_at: datetime,
    ended_at: datetime,
    source_origin: OriginMode,
    end: CaptureEnd,
) -> AttemptDraft:
    outcome: Outcome = "rejected"
    reason: str | None = None
    observations: tuple[ObservationDraft, ...] = ()
    notes: tuple[str, ...] = ()
    rules = profile.rules_for_capability(binding.capability_code)
    if source_origin != binding.origin_mode:
        reason = "origin_mismatch"
    elif not rules:
        reason = "capability_not_in_profile"
    elif _interrupted(profile, end):
        # E07-01: a lost connection makes the reading unavailable; no value from half a window (PLAN.md 45).
        outcome, reason, notes = "failed", end, _interrupted_notes(binding, profile, rules, frames)
    elif _repeated_measurement(frames):
        reason, notes = "stuck", ("frame:repeated_measurement",)
    else:
        outcome, reason, observations, notes = _assess_rules(binding, profile, rules, frames, end)
    return AttemptDraft(binding.reserved_sequence, started_at, ended_at, outcome, reason, observations, notes)


def _assess_rules(
    binding: Binding,
    profile: DeviceProfile,
    rules: Sequence[ComponentRule],
    frames: Sequence[ReceivedFrame],
    end: CaptureEnd,
) -> tuple[Outcome, str | None, tuple[ObservationDraft, ...], tuple[str, ...]]:
    # A panel is all or none: one rejected or refused component rejects the attempt (no half panel).
    accepted: list[ObservationDraft] = []
    collected: set[str] = _unchecked(rules)
    for rule in rules:
        selection = _select(frames, binding, profile, rule)
        collected |= selection.notes
        if selection.rejection is not None:
            return "rejected", selection.rejection, (), tuple(sorted(collected))
        verdict = assess(selection.samples, rule, profile.min_samples, steady=profile.steady_reading)
        if isinstance(verdict, Refused):
            if end != "window_complete" and (end == "cancelled" or verdict.reason == "insufficient_samples"):
                if verdict.reason != "insufficient_samples":
                    collected.add("quality:" + verdict.reason)
                return "failed", end, (), tuple(sorted(collected))
            return "rejected", verdict.reason, (), tuple(sorted(collected))
        accepted.append(_observation(binding, profile, rule, selection, verdict))
    if end == "cancelled":
        collected.add("acquisition_cancelled")
    return "success", None, tuple(accepted), tuple(sorted(collected))
