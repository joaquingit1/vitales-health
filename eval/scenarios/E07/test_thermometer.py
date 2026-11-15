"""E07 scenario: a kit thermometer frame in Fahrenheit under a Celsius-only profile never becomes a value.

The frame is SIM (GATT Temperature Measurement layout, 0x0003D6 x 10^-1 = 98.2 with the Fahrenheit flag
set) and labeled so in its asset. The adapter never converts units (V15); the hub keeps a failed attempt and
no reading.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import e07_harness as h  # noqa: E402
from crew_health.measurement.application.ports import AdapterAssignment  # noqa: E402

pytestmark = pytest.mark.trace("T-E07-TEMP-02", "T-E07-E2E-07", "V15")

THERMOMETER_ID = "adp_thermometer"
DEVICE = "dev_kit_thermometer"


def test_fahrenheit_frame_under_a_celsius_profile_is_a_failed_attempt_without_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    hub.sessions.other("thermometer", "adapter", "adapter", THERMOMETER_ID)
    hub.assignments.by_method["device"] = AdapterAssignment(THERMOMETER_ID, "device", DEVICE)
    process = h.adapter_process(
        tmp_path,
        h.hub_sender(hub, "thermometer"),
        "thermometer-fahrenheit.recording.json",
        family="thermometer",
        adapter_id=THERMOMETER_ID,
    )
    hub.readiness.ready = h.readiness_from(process)
    request_id = h.started_request(hub, h.request_body("temperature"), "temperature")

    assert (
        h.start_acquisition(process, h.binding_for(request_id, "temperature", device_id=DEVICE)) == "started"
    )

    (attempt,) = h.stored_request(hub, request_id)["attempts"]
    assert attempt["outcome"] == "rejected"
    assert attempt["failure_reason"] == "unsupported_unit:degree_fahrenheit"
    assert attempt["observation_ids"] == []
    assert hub.observations.drafts == []
    assert process.journal.records() == ()
