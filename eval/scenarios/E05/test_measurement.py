"""E05 scenario: a camera pulse request, then an ECG request on a kit without an ECG.

A webcam pulse request keeps its first attempt as failed with its reason, and its second is a LIVE camera
value with its camera and UCUM unit. An ECG request on a kit with no ECG stays a durable unavailable request
with an unresolved gap. Contract examples supply every identifier and text; no value is typed here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parents[3] / "hub" / "tests" / "measurement"
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

from measurement_harness import (  # noqa: E402
    attempt,
    build_hub,
    ecg_body,
    failed_attempt,
    headers,
    seed_crew,
    started_pulse,
    success_attempt,
)

pytestmark = pytest.mark.trace("T-E05-02", "T-E05-04", "T-E05-14", "R07", "R08", "D08", "D09", "D11", "D12")


def test_pulse_then_ecg_gap(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_crew(hub)

    request_id = str(started_pulse(hub)["request_id"])
    failed = attempt(hub, "camera", request_id, failed_attempt(1), "idem-key-1").json()
    assert failed["attempts"][-1]["outcome"] == "failed"
    assert failed["attempts"][-1]["failure_reason"] == failed_attempt(1)["failure_reason"]
    assert failed["result_observation_ids"] == []

    live = attempt(hub, "camera", request_id, success_attempt(2), "idem-key-2").json()
    assert live["state"] == "completed"
    assert [a["outcome"] for a in live["attempts"]] == ["failed", "success"]
    (value,) = hub.observations.drafts
    assert (value.origin_mode, value.acquisition_method, value.device_id) == (
        "LIVE",
        "camera_estimate",
        "camera_2",
    )
    assert value.quantity.unit.code == "/min"

    ecg = hub.client.post("/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-3"))
    assert ecg.status_code == 422
    assert ecg.json()["code"] == "KIT_CANNOT_PERFORM"
    ecg_id = ecg.json()["current_state"]["request_id"]
    stored = hub.client.get(f"/v1/measurement-requests/{ecg_id}", headers=headers("mara")).json()
    assert stored["state"] == "unavailable"
    assert stored["kit_check"]["can"] is False
    gaps = hub.uows.reader.db.execute("SELECT capability_code, state FROM measurement_gap").fetchall()
    assert gaps == [("ecg_rhythm", "open")]
