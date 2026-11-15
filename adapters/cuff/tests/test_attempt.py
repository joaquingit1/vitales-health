"""Cuff-specific rules consumed by the shared attempt builder."""

from dataclasses import replace
from decimal import Decimal

import pytest

from crew_health_adapter_cuff.bpm import BPMDecoder
from crew_health_device.domain.acquisition import AttemptDraft, ReceivedFrame, build_attempt
from crew_health_device.domain.ieee11073 import Special
from crew_health_device.domain.profile import DeviceProfile

from .helpers import Clock, binding, component_document, profile, profile_document


def attempt(
    payload: str, *, code: str = "systolic_bp", configured: DeviceProfile | None = None
) -> AttemptDraft:
    clock = Clock()
    frame = BPMDecoder().decode("blood_pressure_measurement", bytes.fromhex(payload))
    return build_attempt(
        replace(binding(), capability_code=code),
        profile() if configured is None else configured,
        (ReceivedFrame(frame, clock.now(), clock.monotonic_ns()),),
        started_at=clock.now(),
        ended_at=clock.now(),
        source_origin="SIM",
        end="window_complete",
    )


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_wrong_subject_rejects_entire_attempt() -> None:
    # Flag bit3 includes user; 3 differs from expected user 2.
    draft = attempt("08 78 00 50 00 5d 00 03")
    assert draft.outcome == "rejected" and draft.failure_reason == "wrong_subject"
    assert not draft.observations


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize("user", [2, 255])
def test_matching_or_unknown_user_accepts_frame_values_under_hub_subject_binding(user: int) -> None:
    payload = "08 78 00 50 00 5d 00 " + f"{user:02x}"
    frame = BPMDecoder().decode("blood_pressure_measurement", bytes.fromhex(payload))
    assert frame.user_index == (None if user == 255 else user)
    draft = attempt(payload)
    assert draft.outcome == "success" and draft.failure_reason is None
    assert len(draft.observations) == 1
    observation = draft.observations[0]
    assert observation.patient_id == "crew-1" and observation.value == Decimal(120)
    assert observation.unit == "mm[Hg]" and observation.code == "systolic_bp"


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_kpa_with_only_mmhg_mapping_is_rejected_without_conversion() -> None:
    # Flag bit0 changes all three pressure components to kPa.
    draft = attempt("01 a0 f0 6b f0 7c f0")
    assert draft.outcome == "rejected" and draft.failure_reason == "unsupported_unit:kilopascal"
    assert not draft.observations


def panel_profile() -> DeviceProfile:
    document = profile_document()
    document["panels"] = [
        {"capability_code": "blood_pressure", "indicator_codes": ["systolic_bp", "diastolic_bp"]}
    ]
    return profile(document)


@pytest.mark.trace("T-E07-BP-01", "T-E07-BP-02", "R08", "V15")
def test_blood_pressure_without_a_panel_is_not_served() -> None:
    draft = attempt("00 78 00 50 00 5d 00", code="blood_pressure")
    assert draft.outcome == "rejected" and draft.failure_reason == "capability_not_in_profile"


@pytest.mark.trace("T-E07-BP-01", "T-E07-BP-02", "R08", "V15")
def test_blood_pressure_panel_emits_systolic_and_diastolic_from_the_same_frame() -> None:
    draft = attempt("00 78 00 50 00 5d 00", code="blood_pressure", configured=panel_profile())
    assert draft.outcome == "success"
    values = [(o.code, o.value, o.unit) for o in draft.observations]
    assert values == [("systolic_bp", Decimal(120), "mm[Hg]"), ("diastolic_bp", Decimal(80), "mm[Hg]")]


@pytest.mark.trace("T-E07-BP-02", "V15")
def test_a_panel_with_one_unusable_component_is_rejected_whole() -> None:
    # Diastolic 0x07FF is NaN: no half panel is sent.
    draft = attempt("00 78 00 ff 07 5d 00", code="blood_pressure", configured=panel_profile())
    assert draft.outcome == "rejected" and draft.observations == ()
    assert draft.failure_reason == "insufficient_samples"
    assert "component_missing:diastolic:not_a_number" in draft.quality_notes


@pytest.mark.trace("T-E07-BP-02", "V35")
@pytest.mark.parametrize(
    ("panel", "reason"),
    [
        ({"capability_code": "blood_pressure", "indicator_codes": ["systolic_bp"]}, "invalid_panel"),
        (
            {"capability_code": "blood_pressure", "indicator_codes": ["systolic_bp", "mean_bp"]},
            "panel_indicator_not_in_components",
        ),
        (
            {"capability_code": "systolic_bp", "indicator_codes": ["systolic_bp", "diastolic_bp"]},
            "duplicate_capability",
        ),
    ],
)
def test_invalid_panels_are_refused_by_the_profile_parser(panel: dict[str, object], reason: str) -> None:
    from crew_health_device.domain.errors import ProfileError
    from crew_health_device.domain.profile import parse_profile

    document = profile_document()
    document["panels"] = [panel]
    with pytest.raises(ProfileError) as raised:
        parse_profile(document)
    assert raised.value.reason == reason


@pytest.mark.trace("T-E07-BP-01", "R08", "E07-01")
@pytest.mark.parametrize("code,value", [("systolic_bp", 120), ("diastolic_bp", 80)])
def test_each_numeric_capability_emits_only_its_requested_observation(code: str, value: int) -> None:
    draft = attempt("00 78 00 50 00 5d 00", code=code)
    assert draft.outcome == "success" and len(draft.observations) == 1
    observation = draft.observations[0]
    assert observation.code == code and observation.value == Decimal(value)
    assert observation.device_id == "cuff-1" and observation.unit == "mm[Hg]"


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
def test_nan_map_is_missing_and_does_not_block_systolic() -> None:
    # 0x07FF MAP is NaN; systolic 0x0078 remains a real 120 mmHg measurement.
    payload = "00 78 00 50 00 ff 07"
    frame = BPMDecoder().decode("blood_pressure_measurement", bytes.fromhex(payload))
    assert frame.components[2].value is None and frame.components[2].missing is Special.NOT_A_NUMBER
    draft = attempt(payload)
    assert draft.outcome == "success" and draft.observations[0].value == Decimal(120)


@pytest.mark.trace("T-E07-BP-02", "E07-03", "V15")
@pytest.mark.parametrize(
    "status,name",
    [
        (1, "body_movement"),
        (2, "cuff_too_loose"),
        (4, "irregular_pulse"),
        (32, "improper_measurement_position"),
    ],
)
def test_profile_edit_switches_each_status_between_rejecting_and_noted(status: int, name: str) -> None:
    payload = "10 78 00 50 00 5d 00 " + status.to_bytes(2, "little").hex()
    document = profile_document()
    document["rejecting_status"] = [name]
    rejected = attempt(payload, configured=profile(document))
    assert rejected.outcome == "rejected" and not rejected.observations
    assert rejected.failure_reason == "insufficient_samples"
    assert rejected.quality_notes == ("device_status:" + name, "unchecked:rate:systolic_bp")
    document["rejecting_status"] = []
    document["noted_status"] = [name]
    noted = attempt(payload, configured=profile(document))
    assert noted.outcome == "success" and noted.observations[0].value == Decimal(120)
    assert noted.quality_notes == ("device_status:" + name, "unchecked:rate:systolic_bp")


@pytest.mark.trace("T-E07-BP-04", "V75")
def test_range_max_scratch_configuration_edit_changes_verdict() -> None:
    document = profile_document()
    assert attempt("00 78 00 50 00 5d 00", configured=profile(document)).outcome == "success"
    component_document(document)["range_max"] = "119"
    draft = attempt("00 78 00 50 00 5d 00", configured=profile(document))
    assert draft.outcome == "rejected" and draft.failure_reason == "out_of_device_range"
    assert not draft.observations
