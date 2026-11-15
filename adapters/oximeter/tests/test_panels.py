"""Panel capabilities in the shared runtime: one observation per component, all or none."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crew_health_device.domain.acquisition import AttemptDraft, ReceivedFrame, build_attempt
from crew_health_device.domain.errors import ProfileError
from crew_health_device.domain.ieee11073 import Special
from crew_health_device.domain.profile import parse_profile
from crew_health_device.domain.readings import Component, DecodedFrame

from .helpers import binding, component_document, profile_document, validate

AT = datetime(2026, 11, 14, tzinfo=UTC)


def panel_document(panels: list[dict[str, object]]) -> dict[str, object]:
    document = profile_document()
    pulse = dict(component_document(document), field="pulse_rate", indicator_code="pulse")
    pulse["units"] = {"beats_per_minute": "/min"}
    document["components"] = [component_document(document), pulse]
    document["panels"] = panels
    validate(document, "device-profile")
    return document


PANEL: dict[str, object] = {"capability_code": "oximetry", "indicator_codes": ["spo2", "pulse"]}


def frame(spo2: Decimal | None) -> ReceivedFrame:
    components = (
        Component("spo2", spo2, None if spo2 is not None else Special.NOT_A_NUMBER, "percent"),
        Component("pulse_rate", Decimal(72), None, "beats_per_minute"),
    )
    decoded = DecodedFrame("plx_spot_check", components, frozenset(), None, None, None, None)
    return ReceivedFrame(decoded, AT, 1)


def run(spo2: Decimal | None, capability: str = "oximetry") -> AttemptDraft:
    return build_attempt(
        replace(binding(), capability_code=capability, origin_mode="SIM"),
        parse_profile(panel_document([PANEL])),
        (frame(spo2),),
        started_at=AT,
        ended_at=AT,
        source_origin="SIM",
        end="window_complete",
    )


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_panel_attempt_carries_each_component_under_its_own_indicator() -> None:
    draft = run(Decimal(97))
    assert draft.outcome == "success"
    assert [(o.code, o.value, o.unit) for o in draft.observations] == [
        ("spo2", Decimal(97), "%"),
        ("pulse", Decimal(72), "/min"),
    ]


@pytest.mark.trace("T-E07-OX-02", "V15")
def test_a_missing_component_rejects_the_whole_panel_and_single_codes_still_work() -> None:
    draft = run(None)
    assert (draft.outcome, draft.observations) == ("rejected", ())
    single = run(None, capability="pulse")
    assert [o.code for o in single.observations] == ["pulse"]
    profile = parse_profile(panel_document([PANEL]))
    assert profile.rules_for_capability("unknown") == ()


@pytest.mark.trace("T-E07-OX-02", "V35")
@pytest.mark.parametrize(
    ("panels", "reason"),
    [
        ([{"capability_code": "oximetry", "indicator_codes": ["spo2", "spo2"]}], "invalid_panel"),
        (
            [{"capability_code": "oximetry", "indicator_codes": ["spo2", "co2"]}],
            "panel_indicator_not_in_components",
        ),
        ([{"capability_code": "pulse", "indicator_codes": ["spo2", "pulse"]}], "duplicate_capability"),
        ([PANEL, PANEL], "duplicate_capability"),
    ],
)
def test_invalid_panels_are_refused(panels: list[dict[str, object]], reason: str) -> None:
    document = profile_document()
    pulse = dict(component_document(document), field="pulse_rate", indicator_code="pulse")
    document["components"] = [component_document(document), pulse]
    document["panels"] = panels
    with pytest.raises(ProfileError) as raised:
        parse_profile(document)
    assert raised.value.reason == reason
