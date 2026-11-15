"""E07 scenarios: a kit pulse oximeter reaches E05's real measurement router through the adapter process.

Every frame is SIM (synthesized from the GATT byte layouts and labeled so in its asset); every value asserted
below is decoded by the adapter from those frames. The hub to worker start call and the readiness bridge are
played by the scenario because their protocol is E06's (follow-up F3).
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import e07_harness as h  # noqa: E402
import measurement_harness as mh  # noqa: E402
from crew_health.measurement.application.ports import AdapterAssignment  # noqa: E402
from crew_health_device.application.delivery import deliver  # noqa: E402

pytestmark = pytest.mark.trace(
    "T-E07-E2E-01",
    "T-E07-E2E-02",
    "T-E07-E2E-03",
    "T-E07-E2E-04",
    "T-E07-E2E-05",
    "T-E07-E2E-06",
    "T-E07-E2E-07",
    "T-E07-E2E-08",
)

PULSE = "oximeter-pulse.recording.json"
DISCONNECT = "oximeter-disconnect.recording.json"


def _spo2_request(hub: mh.Hub, process: h.AdapterProcess) -> str:
    hub.readiness.ready = h.readiness_from(process)
    assert h.capability(hub, "spo2")["status"] == "available"
    return h.started_request(hub, h.spo2_body(), "spo2")


def _held(process: h.AdapterProcess) -> list[tuple[str, str | None]]:
    return [(r.state, r.detail) for r in process.journal.records() if r.kind == "send"]


def test_connected_sim_oximeter_reading_reaches_the_request_as_a_labeled_spo2_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    process = h.adapter_process(tmp_path, h.hub_sender(hub), PULSE)
    request_id = _spo2_request(hub, process)

    assert h.start_acquisition(process, h.binding_for(request_id, "spo2")) == "started"

    stored = h.stored_request(hub, request_id)
    assert stored["state"] == "completed"
    (attempt,) = stored["attempts"]
    assert (attempt["outcome"], attempt["adapter_id"]) == ("success", h.ADAPTER_ID)
    (draft,) = hub.observations.drafts
    # The steady reported sample (frames 2 to 4 hold SpO2 0x0062 = 98 %, C08), never an average of the window.
    assert (draft.code, draft.quantity.value, draft.quantity.unit.code) == ("spo2", Decimal(98), "%")
    assert (draft.origin_mode, draft.acquisition_method, draft.device_id) == ("SIM", "device", h.DEVICE_ID)
    assert process.journal.records() == ()
    assert process.health()["state"] == "connected"


def test_disconnect_records_a_failed_attempt_and_turns_the_capability_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    process = h.adapter_process(tmp_path, h.hub_sender(hub), DISCONNECT)
    request_id = _spo2_request(hub, process)

    h.start_acquisition(process, h.binding_for(request_id, "spo2"))

    (attempt,) = h.stored_request(hub, request_id)["attempts"]
    assert attempt["outcome"] != "success"
    assert attempt["failure_reason"] == "disconnected"
    assert hub.observations.drafts == []
    assert process.health()["state"] == "disconnected"
    hub.readiness.ready = h.readiness_from(process)
    capability = h.capability(hub, "spo2")
    assert (capability["status"], capability["unavailable_reason"]) == ("unavailable", "adapter_down")


@pytest.mark.parametrize(
    ("token", "patient", "expected"),
    [("rogue", "mara", "ADAPTER_NOT_TRUSTED"), (h.ADAPTER_TOKEN, "kai", "IDENTITY_MISMATCH")],
)
def test_rogue_adapter_or_wrong_subject_is_refused_and_the_evidence_is_held_not_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str, patient: str, expected: str
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    process = h.adapter_process(tmp_path, h.hub_sender(hub, token), PULSE)
    request_id = _spo2_request(hub, process)

    h.start_acquisition(process, h.binding_for(request_id, "spo2", patient=patient))

    assert h.stored_request(hub, request_id)["attempts"] == []
    assert hub.observations.drafts == []
    (held,) = _held(process)
    assert held[0] == "held"
    assert held[1] == expected


def test_response_loss_then_retry_stores_one_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    sender = h.LosesFirstResponse(h.hub_sender(hub))
    process = h.adapter_process(tmp_path, sender, PULSE)
    request_id = _spo2_request(hub, process)

    h.start_acquisition(process, h.binding_for(request_id, "spo2"))

    assert sender.calls == 2
    assert len(h.stored_request(hub, request_id)["attempts"]) == 1
    assert len(hub.observations.drafts) == 1
    assert process.journal.records() == ()


def test_restart_before_delivery_never_recaptures_and_sends_the_journaled_attempt_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    down = h.HubDown()
    first = h.adapter_process(tmp_path, down, PULSE)
    request_id = _spo2_request(hub, first)
    binding = h.binding_for(request_id, "spo2")
    assert h.start_acquisition(first, binding) == "started"
    assert down.calls >= 1
    journaled = [r.body for r in first.journal.records() if r.kind == "send"]

    restarted = h.adapter_process(tmp_path, h.hub_sender(hub), PULSE)
    assert h.start_acquisition(restarted, binding) == "duplicate"
    report = deliver(restarted.journal, h.hub_sender(hub), h.adapter_settings())

    assert report.sent == 1
    assert len(journaled) == 1
    assert len(h.stored_request(hub, request_id)["attempts"]) == 1
    assert len(hub.observations.drafts) == 1


def test_a_result_after_the_request_is_cancelled_is_kept_as_quarantined_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    process = h.adapter_process(tmp_path, h.hub_sender(hub), PULSE)
    request_id = _spo2_request(hub, process)
    mh.act(hub, "mara", request_id, "cancel", 4, "idem-key-spo2-cancel")

    h.start_acquisition(process, h.binding_for(request_id, "spo2"))

    stored = h.stored_request(hub, request_id)
    assert stored["state"] == "cancelled"
    (attempt,) = stored["attempts"]
    assert attempt["observation_ids"] == []
    assert stored["result_observation_ids"] == []
    assert hub.observations.drafts == []
    assert process.journal.records() == ()


def test_device_label_edit_changes_the_manifest_and_an_invalid_reload_keeps_the_last_good_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    scratch = tmp_path / "oximeter.profile.json"
    document = h.asset("oximeter.profile.json")
    scratch.write_text(json.dumps(document), encoding="utf-8")
    process = h.adapter_process(tmp_path, h.hub_sender(hub), PULSE, profile_path=scratch)
    assert process.health()["manifest"]["profile"]["device_label"] == document["device_label"]

    edited = dict(document, device_label="Kit oximeter, crew bay B", version="2")
    scratch.write_text(json.dumps(edited), encoding="utf-8")
    assert process.profiles.reload()
    assert process.health()["manifest"]["profile"]["device_label"] == "Kit oximeter, crew bay B"

    scratch.write_text(json.dumps(dict(edited, components=[])), encoding="utf-8")
    assert not process.profiles.reload()
    assert process.health()["manifest"]["profile"]["version"] == "2"


def test_the_manifest_separates_connected_recorded_and_manual_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    process = h.adapter_process(tmp_path, h.hub_sender(hub), PULSE)

    health = process.health()
    h.validate(health, "adapter-health")
    manifest = health["manifest"]

    modes = {op["mode"]: op for op in manifest["operations"]}
    assert modes["connected"]["status"] == "unavailable"
    assert (modes["recorded"]["status"], modes["recorded"]["origin"]) == ("available", "SIM")
    assert modes["manual"]["status"] == "hub_owned"
    assert manifest["hardware_compatibility"] == "unmeasured"
    assert manifest["protocol"]["service"] == "0x1822"


def test_camera_and_oximeter_pulse_that_disagree_are_both_kept_with_their_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = h.build_device_hub(tmp_path, monkeypatch)
    process = h.adapter_process(tmp_path, h.hub_sender(hub), PULSE)
    hub.readiness.ready = h.readiness_from(process) | {"camera_estimate"}
    camera = mh.example("clinical/observation-pulse.json")
    hub.assignments.by_method.clear()
    hub.assignments.by_method["camera_estimate"] = AdapterAssignment(
        "adp_camera", str(camera["acquisition_method"]), str(camera["device_id"])
    )
    camera_request = h.started_request(hub, mh.pulse_body(), "pulse-camera")
    reply = mh.attempt(hub, "camera", camera_request, mh.success_attempt(1, origin="SIM"), "idem-key-cam-1")
    assert reply.status_code == 200, reply.json()

    hub.assignments.by_method.clear()
    hub.assignments.by_method["device"] = AdapterAssignment(h.ADAPTER_ID, "device", h.DEVICE_ID)
    device_request = h.started_request(hub, mh.pulse_body(), "pulse-device")
    h.start_acquisition(process, h.binding_for(device_request, "pulse"))

    by_method = {d.acquisition_method: d for d in hub.observations.drafts}
    assert set(by_method) == {str(camera["acquisition_method"]), "device"}
    assert by_method["device"].quantity.value == Decimal(73)
    assert by_method["device"].quantity.value != by_method[str(camera["acquisition_method"])].quantity.value
    assert h.stored_request(hub, camera_request)["state"] == "completed"
    assert h.stored_request(hub, device_request)["state"] == "completed"


def test_scenario_assets_validate_against_the_adapter_schemas() -> None:
    h.validate(h.asset("adapter-settings.json"), "adapter-settings")
    for name in ("oximeter.profile.json", "thermometer.profile.json", "cuff.profile.json"):
        h.validate(h.asset(name), "device-profile")
    for name in (PULSE, DISCONNECT, "thermometer-fahrenheit.recording.json", "cuff-panel.recording.json"):
        h.validate(h.asset(name), "frame-recording")
