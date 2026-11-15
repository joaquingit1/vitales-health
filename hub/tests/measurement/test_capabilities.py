"""Capability view and gate inputs: configuration edits, corrupt or missing inputs, devices absent (E05 3, 4, 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from measurement_harness import (
    KIT_MAP,
    build_hub,
    ecg_body,
    headers,
    pulse_body,
    scratch_kit_map,
    seed_crew,
)

from crew_health.measurement.application.ports import KitCapabilitiesPort, KitGate, MissionStatePort


def _capabilities(hub: Any) -> dict[str, dict[str, Any]]:
    response = hub.client.get("/v1/capabilities?limit=200", headers=headers("mara"))
    assert response.status_code == 200, response.json()
    return {c["code"]: c for c in response.json()["items"]}


@pytest.mark.trace("T-E05-09", "V75", "R07", "I1")
def test_kit_map_edit_changes_content_without_code(tmp_path: Path) -> None:
    shipped = KIT_MAP.read_bytes()

    def edit(document: dict[str, Any]) -> None:
        for capability in document["capabilities"]:
            if capability["code"] == "pulse":
                capability["label"] = "Heart rate"
        document["kits"]["lunar_surface"].append("ecg_single_lead")

    hub = build_hub(tmp_path, kit_map=scratch_kit_map(tmp_path, edit))
    seed_crew(hub)
    capabilities = _capabilities(hub)
    assert capabilities["pulse"]["label"] == "Heart rate"
    assert capabilities["ecg_rhythm"]["status"] == "available"
    created = hub.client.post(
        "/v1/measurement-requests", json=ecg_body(), headers=headers("mara", "idem-key-cfg-1")
    )
    assert created.status_code == 201
    assert created.json()["kit_check"]["text"] == "This kit can: single-lead ECG"
    assert hub.uows.count("measurement_gap") == 0
    assert KIT_MAP.read_bytes() == shipped


@pytest.mark.trace("T-E05-09", "T-E05-01", "R07", "I1")
def test_instrument_removed_from_kit_makes_pulse_camera_only(tmp_path: Path) -> None:
    def edit(document: dict[str, Any]) -> None:
        document["kits"]["lunar_surface"].remove("pulse_oximeter")

    hub = build_hub(tmp_path, kit_map=scratch_kit_map(tmp_path, edit), ready=None)
    seed_crew(hub)
    pulse = _capabilities(hub)["pulse"]
    assert pulse["instrument_ids"] == ["camera_station"] and pulse["methods"] == ["camera_estimate"]
    assert (pulse["status"], pulse["unavailable_reason"]) == ("unavailable", "adapter_down")
    hub.readiness.ready = frozenset({"camera_estimate"})
    assert _capabilities(hub)["pulse"]["status"] == "available"


@pytest.mark.trace("T-E05-01", "T-E05-10", "E05-01", "R08", "V69", "I1")
def test_device_absent_records_unavailable_with_device_gap(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, ready=None)
    seed_crew(hub)
    body = dict(pulse_body(), capability_code="respiratory_rate")
    refused = hub.client.post(
        "/v1/measurement-requests", json=body, headers=headers("mara", "idem-key-dev-1")
    )
    assert refused.status_code == 422 and refused.json()["code"] == "CAPABILITY_UNAVAILABLE"
    assert refused.json()["current_state"]["state"] == "unavailable"
    gap = hub.uows.reader.db.execute("SELECT reason, state FROM measurement_gap").fetchall()
    assert gap == [("device_absent", "open")]
    manual = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-dev-2")
    )
    assert manual.status_code == 201


@pytest.mark.trace("T-E05-09", "T-E05-10", "R07", "V69", "I1")
def test_corrupt_kit_map_fails_loud_and_persists_nothing(tmp_path: Path) -> None:
    broken = tmp_path / "kit-map.json"
    broken.write_text("{ not json", encoding="utf-8")
    hub = build_hub(tmp_path, kit_map=broken)
    seed_crew(hub)
    listing = hub.client.get("/v1/capabilities", headers=headers("mara"))
    assert listing.status_code == 500 and listing.json()["code"] == "INTERNAL_ERROR"
    created = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-bad-1")
    )
    assert created.status_code == 500
    assert hub.uows.count("measurement_request") == 0 and hub.uows.state.events == []


@pytest.mark.parametrize("missing", [KitGate, KitCapabilitiesPort, MissionStatePort])
@pytest.mark.trace("T-E05-10", "T-E05-01", "R08", "V69", "I1")
def test_missing_provider_never_guesses_a_capability(tmp_path: Path, missing: type[object]) -> None:
    hub = build_hub(tmp_path, omit=frozenset({missing}))
    seed_crew(hub)
    listing = hub.client.get("/v1/capabilities", headers=headers("mara"))
    if missing is KitGate:
        assert listing.status_code == 200
    else:
        assert listing.status_code == 500 and listing.json()["retryable"] is True
    created = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-miss")
    )
    assert created.status_code in (422, 500)
    if created.status_code == 422:
        assert created.json()["code"] == "CAPABILITY_UNAVAILABLE"
    assert hub.uows.count("measurement_request") == 0


@pytest.mark.trace("T-E05-10", "R08", "V69")
def test_unknown_environment_is_not_a_capability_list(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, environment=None)
    seed_crew(hub)
    assert hub.client.get("/v1/capabilities", headers=headers("mara")).status_code == 500
    created = hub.client.post(
        "/v1/measurement-requests", json=pulse_body(), headers=headers("mara", "idem-key-env-1")
    )
    assert created.status_code == 422 and created.json()["code"] == "CAPABILITY_UNAVAILABLE"
    assert hub.uows.count("measurement_request") == 0


@pytest.mark.trace("T-E05-10", "R08")
def test_records_port_absent_refuses_a_usable_value_loudly(tmp_path: Path) -> None:
    from measurement_harness import attempt, started_pulse, success_attempt

    from crew_health.measurement.application.ports import ObservationCommands

    hub = build_hub(tmp_path, omit=frozenset({ObservationCommands}))
    seed_crew(hub)
    request_id = str(started_pulse(hub)["request_id"])
    response = attempt(hub, "camera", request_id, success_attempt(1), "idem-key-rec-1")
    assert response.status_code == 500 and response.json()["retryable"] is True
    assert hub.uows.count("measurement_attempt") == 0
