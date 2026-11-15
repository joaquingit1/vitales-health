import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crew_health.contracts_gen.schemas.requests_schema import AttemptCreate
from crew_health_adapter_oximeter.plx import PLXDecoder
from crew_health_device.domain.acquisition import (
    CaptureEnd,
    ReceivedFrame,
    build_attempt,
    parse_binding,
)
from crew_health_device.domain.errors import BindingError, FrameError
from crew_health_device.domain.ieee11073 import Special
from crew_health_device.domain.readings import Component

from .helpers import binding, profile


def frames() -> tuple[ReceivedFrame, ...]:
    # Mantissa 975 (0x3CF), exponent -1 (0xF) gives SpO2 97.5.
    frame = PLXDecoder().decode("plx_continuous", bytes.fromhex("00 cf f3 48 00"))
    return (ReceivedFrame(frame, datetime(2026, 11, 14, tzinfo=UTC), 0),)


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_valid_frames_emit_only_requested_spo2_with_attribution_and_exact_decimal() -> None:
    result = build_attempt(
        binding(),
        profile(),
        frames(),
        started_at=frames()[0].received_at,
        ended_at=frames()[0].received_at,
        source_origin="LIVE",
        end="window_complete",
    )
    assert result.outcome == "success"
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.code == "spo2"
    assert observation.value == Decimal("97.5")
    assert observation.unit == profile().components[0].units["percent"]
    assert observation.device_id == "ox-1"
    assert observation.origin_mode == "LIVE"
    assert observation.measurement_request_id == binding().request_id
    from crew_health_device.application.capture import attempt_body

    body = attempt_body(result)
    AttemptCreate.model_validate(json.loads(body))
    assert b'"value":97.5' in body
    assert json.loads(body, parse_float=Decimal)["observations"][0]["value"] == Decimal("97.5")


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
@pytest.mark.parametrize("end", ["disconnected", "timeout", "radio_absent", "device_absent", "cancelled"])
def test_unavailable_input_ends_failed_without_fabricated_observations(end: CaptureEnd) -> None:
    result = build_attempt(
        binding(),
        profile(),
        (),
        started_at=frames()[0].received_at,
        ended_at=frames()[0].received_at,
        source_origin="LIVE",
        end=end,
    )
    assert result.outcome == "failed"
    assert result.failure_reason == end
    assert result.observations == ()


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
@pytest.mark.parametrize("flag", ["data_for_demonstration", "data_for_testing"])
def test_live_binding_rejects_demonstration_and_testing_even_if_profile_does_not(flag: str) -> None:
    data = frames()[0]
    if isinstance(data.frame, FrameError):
        raise RuntimeError("invalid test frame")
    result = build_attempt(
        binding(),
        profile(),
        (replace(data, frame=replace(data.frame, status=frozenset((flag,)))),),
        started_at=data.received_at,
        ended_at=data.received_at,
        source_origin="LIVE",
        end="window_complete",
    )
    assert result.outcome == "rejected"
    assert result.failure_reason == "device_status:" + flag
    assert result.observations == ()


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_storage_data_never_becomes_current_observation() -> None:
    data = frames()[0]
    if isinstance(data.frame, FrameError):
        raise RuntimeError("invalid test frame")
    stored = replace(data.frame, status=frozenset(("data_from_storage",)))
    result = build_attempt(
        binding(),
        profile(),
        (replace(data, frame=stored),),
        started_at=data.received_at,
        ended_at=data.received_at,
        source_origin="LIVE",
        end="window_complete",
    )
    assert result.observations == ()
    assert result.failure_reason == "insufficient_samples"
    assert result.quality_notes == ("stored_measurement_untrusted_time", "unchecked:rate:spo2")


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_recorded_source_cannot_cross_live_origin_boundary() -> None:
    data = frames()[0]
    result = build_attempt(
        binding(),
        profile(),
        frames(),
        started_at=data.received_at,
        ended_at=data.received_at,
        source_origin="SIM",
        end="window_complete",
    )
    assert result.outcome == "rejected" and result.failure_reason == "origin_mismatch"
    assert result.observations == ()


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09", "V15")
@pytest.mark.parametrize(
    "case,reason",
    [
        ("unit", "unsupported_unit:wrong_unit"),
        ("subject", "wrong_subject"),
        ("capability", "capability_not_in_profile"),
    ],
)
def test_wrong_unit_subject_and_capability_are_whole_attempt_rejections(case: str, reason: str) -> None:
    data = frames()[0]
    if isinstance(data.frame, FrameError):
        raise RuntimeError("invalid test frame")
    request = replace(binding(), expected_user_index=1)
    frame = data.frame
    if case == "unit":
        frame = replace(frame, components=(replace(frame.components[0], unit="wrong_unit"),))
    elif case == "subject":
        frame = replace(frame, user_index=2)
    else:
        request = replace(request, capability_code="temperature")
    result = build_attempt(
        request,
        profile(),
        (replace(data, frame=frame),),
        started_at=data.received_at,
        ended_at=data.received_at,
        source_origin="LIVE",
        end="window_complete",
    )
    assert result.outcome == "rejected" and result.failure_reason == reason
    assert result.observations == ()


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_bad_frames_and_missing_components_are_not_invented_and_notes_are_sorted() -> None:
    data = frames()[0]
    if isinstance(data.frame, FrameError):
        raise RuntimeError("invalid test frame")
    missing = replace(data.frame, components=(Component("spo2", None, Special.NOT_A_NUMBER, "percent"),))
    bad = ReceivedFrame(FrameError("truncated"), data.received_at, 0)
    rejected = replace(data.frame, status=frozenset(("invalid_measurement",)))
    noted = replace(data.frame, status=frozenset(("validated_data",)), site_code=2)
    result = build_attempt(
        binding(),
        profile(),
        (bad, bad, replace(data, frame=missing), replace(data, frame=rejected), replace(data, frame=noted)),
        started_at=data.received_at,
        ended_at=data.received_at,
        source_origin="LIVE",
        end="window_complete",
    )
    assert result.outcome == "success"
    assert result.quality_notes == (
        "component_missing:spo2:not_a_number",
        "device_status:invalid_measurement",
        "device_status:validated_data",
        "frame:truncated",
        "unchecked:rate:spo2",
    )
    assert ("device_site_code", "2") in result.observations[0].context


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_absent_requested_component_has_insufficient_samples() -> None:
    data = frames()[0]
    if isinstance(data.frame, FrameError):
        raise RuntimeError("invalid test frame")
    frame = replace(data.frame, components=data.frame.components[1:])
    result = build_attempt(
        binding(),
        profile(),
        (replace(data, frame=frame),),
        started_at=data.received_at,
        ended_at=data.received_at,
        source_origin="LIVE",
        end="window_complete",
    )
    assert result.failure_reason == "insufficient_samples"
    assert result.observations == ()


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_binding_parses_exact_ids_and_optional_attribution() -> None:
    assert parse_binding(asdict(binding())) == binding()
    request = replace(binding(), episode_id="episode.1", expected_user_index=0)
    assert parse_binding(asdict(request)) == request


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", ""),
        ("patient_id", "bad/id"),
        ("generation", True),
        ("generation", 0),
        ("reserved_sequence", 0),
        ("expected_user_index", -1),
        ("origin_mode", "manual"),
        ("method", ""),
        ("method", None),
    ],
)
def test_invalid_binding_fails_with_typed_reason(field: str, value: object) -> None:
    document = asdict(binding())
    document[field] = value
    with pytest.raises(BindingError):
        parse_binding(document)


@pytest.mark.trace("T-E07-OX-01", "R08", "E07-01", "V11", "V08", "V09")
def test_binding_rejects_unknown_and_missing_fields() -> None:
    document = asdict(binding())
    document["unknown"] = 1
    with pytest.raises(BindingError, match="binding_fields"):
        parse_binding(document)
    del document["unknown"]
    del document["device_id"]
    with pytest.raises(BindingError, match="binding_fields"):
        parse_binding(document)


def _received(component_unit: str, value: str, at_ns: int) -> ReceivedFrame:
    frame = PLXDecoder().decode("plx_continuous", bytes.fromhex("00 62 00 48 00"))
    if isinstance(frame, FrameError):
        raise RuntimeError("invalid test frame")
    spo2 = Component("spo2", Decimal(value), None, component_unit)
    return ReceivedFrame(replace(frame, components=(spo2,)), datetime(2026, 11, 14, tzinfo=UTC), at_ns)


@pytest.mark.trace("T-E07-OX-02", "E07-03", "V15")
def test_a_unit_change_inside_one_window_is_rejected_never_compared_or_converted() -> None:
    document = profile()
    rule = replace(document.components[0], units={"percent": "%", "fraction": "1"})
    changed = replace(document, components=(rule,), min_samples=1)
    result = build_attempt(
        binding(),
        changed,
        (_received("percent", "97", 0), _received("fraction", "0.97", 1_000_000_000)),
        started_at=datetime(2026, 11, 14, tzinfo=UTC),
        ended_at=datetime(2026, 11, 14, tzinfo=UTC),
        source_origin="LIVE",
        end="window_complete",
    )
    assert (result.outcome, result.failure_reason, result.observations) == ("rejected", "unit_changed", ())


@pytest.mark.trace("T-E07-OX-01", "E07-01", "V11")
def test_a_disconnect_after_implausible_samples_fails_and_keeps_the_quality_reason() -> None:
    document = profile()
    rule = replace(document.components[0], range_max=Decimal(90))
    result = build_attempt(
        binding(),
        replace(document, components=(rule,)),
        (_received("percent", "97", 0),),
        started_at=datetime(2026, 11, 14, tzinfo=UTC),
        ended_at=datetime(2026, 11, 14, tzinfo=UTC),
        source_origin="LIVE",
        end="disconnected",
    )
    assert (result.outcome, result.failure_reason, result.observations) == ("failed", "disconnected", ())
    assert "quality:out_of_device_range" in result.quality_notes
